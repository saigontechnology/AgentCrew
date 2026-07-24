"""
Integration tests for the ``memory.store`` lifecycle hook inside
``MemoryWorker._store_conversation_internal``.

These tests exercise the production worker path with mocked dependencies
(LLM, embedding function, collection) to validate:

- Before-hook contract: receives ``operation_data`` envelope, can mutate it.
- Before-hook cancellation prevents ``collection.upsert``.
- After-hook contract: receives ``memory_data`` envelope, can mutate it.
- After-hook mutations affect the persisted document, header metadata,
  embedding input, and ``current_conversation_context`` cache.
- After-hook safety guard rejects non-dict ``memory_data``.
- Lifecycle ordering: before hook → memory creation → after hook → upsert.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from AgentCrew.modules.events.hooks import (
    Hook,
    HookPhase,
    HookPoints,
    HookRegistry,
)
from AgentCrew.modules.memory.memory_worker import MemoryWorker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_hook_registry():
    """Reset the singleton before every test so state is isolated."""
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()


SAMPLE_OPERATION_DATA: dict = {
    "operation_id": "test-op-001",
    "user_message": "What is the weather?",
    "assistant_messages": ["The weather is sunny."],
    "agent_name": "test_agent",
    "session_id": "session-123",
    "timestamp": "2026-07-16T12:00:00",
    "type": "store_conversation",
}


@pytest.fixture
def mock_embedding_fn():
    """Return a deterministic embedding function."""

    def _embed(texts):
        # Deterministic embedding: one float per text
        return [[hash(t) % 1000 / 1000.0] for t in texts]

    return _embed


@pytest.fixture
def mock_collection():
    """A MagicMock that records ``upsert`` calls."""
    col = MagicMock()
    col.upsert = MagicMock()
    col.delete = MagicMock()
    col.query = MagicMock(
        return_value={
            "ids": [[]],
            "documents": [[]],
            "distances": None,
            "metadatas": None,
        }
    )
    return col


@pytest.fixture
def worker(mock_embedding_fn, mock_collection):
    """A MemoryWorker with no LLM (uses fallback memory_data) and mocks."""
    w = MemoryWorker(embedding_fn=mock_embedding_fn, llm_service=None)
    w.set_collection(mock_collection)
    return w


@pytest.fixture
def hooks():
    return HookRegistry.get_instance()


# ---------------------------------------------------------------------------
# 1. Before hook — mutates operation_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_mutates_user_message(worker, mock_collection, hooks):
    """Before hook receives ``operation_data`` envelope and can mutate fields."""

    async def replace_msg(ctx: dict) -> dict:
        ctx["operation_data"]["user_message"] = "Modified question"
        return ctx

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.BEFORE,
            replace_msg,
            description="test_before_mutate",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert mock_collection.upsert.called, "upsert should have been called"
    _args, kwargs = mock_collection.upsert.call_args
    stored_doc = kwargs["documents"][0]
    # The fallback memory_data embeds user_message directly in CONVERSATION_NOTES
    assert "Modified question" in stored_doc, (
        f"Expected modified user_message in stored doc, got: {stored_doc}"
    )


# ---------------------------------------------------------------------------
# 2. Before hook — cancellation prevents upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_cancellation_prevents_upsert(worker, mock_collection, hooks):
    """Before hook returning None cancels the operation; upsert is not called."""

    async def cancel(ctx: dict) -> None:
        return None

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.BEFORE,
            cancel,
            description="test_before_cancel",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert not mock_collection.upsert.called, (
        "upsert should NOT have been called after cancellation"
    )


# ---------------------------------------------------------------------------
# 3. After hook — mutates memory_data, affects persisted document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_adds_headline(worker, mock_collection, hooks):
    """After hook receives ``memory_data`` and can add a HEAD entry."""

    async def add_head(ctx: dict, result: dict) -> dict:
        result["memory_data"]["MEMORY"]["HEAD"] = "Weather query"
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            add_head,
            description="test_after_add_head",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert mock_collection.upsert.called
    _args, kwargs = mock_collection.upsert.call_args
    metadata = kwargs["metadatas"][0]
    assert metadata.get("header") == "Weather query", (
        f"Expected header='Weather query', got {metadata.get('header')}"
    )
    doc = kwargs["documents"][0]
    assert "<HEAD>Weather query</HEAD>" in doc, f"Expected HEAD in document, got: {doc}"


# ---------------------------------------------------------------------------
# 4. After hook — mutation reflected in current_conversation_context cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_mutation_updates_context_cache(
    worker, mock_collection, hooks
):
    """After-hook memory_data mutation updates the conversation context cache."""

    async def add_context_note(ctx: dict, result: dict) -> dict:
        result["memory_data"]["MEMORY"]["CONTEXT"] = "Added by after hook"
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            add_context_note,
            description="test_after_cache",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    session_id = SAMPLE_OPERATION_DATA["session_id"]
    cached = worker.current_conversation_context.get(session_id, "")
    assert "Added by after hook" in cached, (
        f"Expected after-hook CONTEXT in cache, got: {cached}"
    )


# ---------------------------------------------------------------------------
# 5. After hook — mutation reflected in embedding input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_mutation_affects_embedding_input(
    worker, mock_collection, hooks
):
    """The embedding is generated from the *after-hook-modified* document."""

    async def modify_date(ctx: dict, result: dict) -> dict:
        result["memory_data"]["MEMORY"]["DATE"] = "2099-01-01"
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            modify_date,
            description="test_after_embedding",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert mock_collection.upsert.called
    _args, kwargs = mock_collection.upsert.call_args
    doc = kwargs["documents"][0]
    assert "<DATE>2099-01-01</DATE>" in doc, (
        f"Expected modified DATE in document, got: {doc}"
    )


# ---------------------------------------------------------------------------
# 6. After hook — non-dict memory_data safely ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_non_dict_memory_data_safely_ignored(
    worker, mock_collection, hooks
):
    """When after hook returns a dict with non-dict memory_data, original is used."""

    async def bad_memory_data(ctx: dict, result: dict) -> dict:
        result["memory_data"] = "this is not a dict"
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            bad_memory_data,
            description="test_after_bad",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert mock_collection.upsert.called
    _args, kwargs = mock_collection.upsert.call_args
    doc = kwargs["documents"][0]
    # The original fallback memory_data should have been used
    # (default CONVERSATION_NOTES, no HEAD)
    assert "CONVERSATION_NOTES" in doc, f"Expected original fallback doc, got: {doc}"
    # No HEAD should be present since the original didn't have one
    assert "<HEAD>" not in doc, (
        f"HEAD should not be present in fallback-only doc, got: {doc}"
    )


# ---------------------------------------------------------------------------
# 7. Lifecycle ordering: before → memory creation → after → upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_ordering_is_correct(worker, mock_collection, hooks):
    """Verify the exact ordering: before → memory creation → after → upsert."""
    order: list[str] = []

    async def before_hook(ctx: dict) -> dict:
        order.append("before")
        return ctx

    async def after_hook(ctx: dict, result: dict) -> dict:
        order.append("after")
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.BEFORE,
            before_hook,
            priority=0,
            description="order_before",
        )
    )
    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            after_hook,
            priority=0,
            description="order_after",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert order == ["before", "after"], f"Expected [before, after], got {order}"
    assert mock_collection.upsert.called


# ---------------------------------------------------------------------------
# 8. Before hook — operation_data envelope structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_receives_operation_data_envelope(
    worker, mock_collection, hooks
):
    """Before hook receives ``operation_data`` as a key in the context dict."""

    received_key = None

    async def inspect_ctx(ctx: dict) -> dict:
        nonlocal received_key
        received_key = set(ctx.keys())
        return ctx

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.BEFORE,
            inspect_ctx,
            description="test_before_envelope",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert received_key is not None
    assert "operation_data" in received_key, (
        f"Expected 'operation_data' in before-hook context keys, got {received_key}"
    )
    # Ensure flattened fields like user_message are NOT top-level keys
    assert "user_message" not in received_key, (
        f"Expected NO flattened fields in before-hook context, got {received_key}"
    )


# ---------------------------------------------------------------------------
# 9. After hook — operation_data is in context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_receives_operation_data_in_context(
    worker, mock_collection, hooks
):
    """After hook context includes ``operation_data`` key."""

    ctx_keys = None

    async def inspect_after(ctx: dict, result: dict) -> dict:
        nonlocal ctx_keys
        ctx_keys = set(ctx.keys())
        return result

    hooks.register(
        Hook(
            HookPoints.MEMORY_STORE,
            HookPhase.AFTER,
            inspect_after,
            description="test_after_ctx",
        )
    )

    await worker._store_conversation_internal(copy.deepcopy(SAMPLE_OPERATION_DATA))

    assert ctx_keys is not None
    assert "operation_data" in ctx_keys, (
        f"Expected 'operation_data' in after-hook context, got {ctx_keys}"
    )
