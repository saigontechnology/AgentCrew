"""Fictional extraction diagnostics contracts; no remote calls or private inputs."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.events.hooks import HookRegistry
from AgentCrew.modules.llm.base import LLMResponseError
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import Model
from AgentCrew.modules.memory.memory_record_builder import MemoryRecordBuilder
from AgentCrew.modules.memory.resources import ResourceAccess

SECRET = "PRIVATE_CV_TEXT https://example.invalid/cv?token=PRIVATE_TOKEN"
OPERATION = {
    "user_message": SECRET,
    "assistant_messages": [SECRET],
    "resources": (ResourceAccess("attachment://private-cv"),),
}
VALID = "<MEMORY><HEAD>Fictional review</HEAD></MEMORY>"


@pytest.fixture(autouse=True)
def clean_hooks():
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()


@pytest.fixture
def logs():
    records = []
    sink = logger.add(
        lambda message: records.append(message.record),
        level="DEBUG",
        filter=lambda record: record["name"].endswith("memory_record_builder"),
    )
    yield records
    logger.remove(sink)


def service(*, reply=VALID, error=None):
    return SimpleNamespace(
        provider_name="openai_codex",
        model="gpt-6.1-sol",
        api_key=SECRET,
        process_message=AsyncMock(return_value=reply, side_effect=error),
    )


def assert_private(records):
    for record in records:
        assert record["exception"] is None
        content = json.dumps({"message": record["message"], "extra": record["extra"]})
        for forbidden in ("PRIVATE", "example.invalid", "attachment://", "<MEMORY>"):
            assert forbidden not in content


@pytest.mark.parametrize(
    ("reply", "error", "category", "exception_class"),
    [
        (None, None, "empty_response", "ValueError"),
        ("  ", None, "empty_response", "ValueError"),
        (SECRET, None, "missing_memory_block", "ValueError"),
        (f"<MEMORY><HEAD>{SECRET}</MEMORY>", None, "malformed_xml", "ExpatError"),
        (f"<MEMORY>{SECRET}</MEMORY>", None, "invalid_memory_root", "ValueError"),
        (None, RuntimeError(SECRET), "provider_error", "RuntimeError"),
        (
            None,
            LLMResponseError(SECRET, output_truncated=True),
            "output_truncated",
            "LLMResponseError",
        ),
    ],
)
def test_failure_categories_and_private_error_level_fallback(
    reply, error, category, exception_class, logs
):
    llm = service(reply=reply, error=error)
    built = asyncio.run(MemoryRecordBuilder(llm).build(OPERATION))
    assert "CONVERSATION_NOTES" in built.memory_data["MEMORY"]
    assert "attachment://private-cv" in built.document
    assert llm.process_message.await_count == 3
    attempts = [record for record in logs if record["level"].name == "WARNING"]
    final = [record for record in logs if record["level"].name == "ERROR"]
    assert [record["extra"]["attempt"] for record in attempts] == [1, 2, 3]
    assert len(final) == 1
    fields = final[0]["extra"]
    assert fields["category"] == fields["final_fallback_reason"] == category
    assert fields["retry_count"] == 2
    assert fields["exception_class"] == exception_class
    assert fields["response_characters"] == (
        len(reply) if isinstance(reply, str) else 0
    )
    assert fields["provider"] == "openai_codex"
    assert fields["model"] == "gpt-6.1-sol"
    assert_private(logs)


def test_success_does_not_log_extracted_memory(logs):
    llm = service(reply=f"<MEMORY><HEAD>{SECRET}</HEAD></MEMORY>")
    built = asyncio.run(MemoryRecordBuilder(llm).build(OPERATION))
    assert SECRET in built.memory_data["MEMORY"]["HEAD"]
    assert llm.process_message.await_count == 1
    assert not logs


def test_recovered_attempt_does_not_emit_final_error(logs):
    llm = service(error=[RuntimeError(SECRET), VALID])
    built = asyncio.run(MemoryRecordBuilder(llm).build(OPERATION))
    assert built.header == "Fictional review"
    assert llm.process_message.await_count == 2
    assert len(logs) == 1 and logs[0]["extra"]["category"] == "provider_error"
    assert_private(logs)


def test_no_llm_fallback_is_not_an_extraction_error(logs):
    built = asyncio.run(MemoryRecordBuilder().build(OPERATION))
    assert "CONVERSATION_NOTES" in built.memory_data["MEMORY"]
    assert not logs


def test_resource_enforcement_failure_is_distinct(monkeypatch, logs):
    from AgentCrew.modules.memory import resources

    original = resources.enforce_resources
    calls = 0

    def enforce(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(SECRET)
        return original(*args)

    monkeypatch.setattr(resources, "enforce_resources", enforce)
    built = asyncio.run(MemoryRecordBuilder(service()).build(OPERATION))
    assert "CONVERSATION_NOTES" in built.memory_data["MEMORY"]
    assert "attachment://private-cv" in built.document
    assert len(logs) == 1
    assert logs[0]["level"].name == "ERROR"
    assert logs[0]["extra"]["category"] == "resource_enforcement_failed"
    assert logs[0]["extra"]["attempt"] == 1
    assert logs[0]["extra"]["retry_count"] == 0
    assert_private(logs)


def test_identity_uses_registry_without_initialization(monkeypatch):
    model = Model(
        id="gpt-6.1-sol",
        provider="openai_codex",
        name="Fictional",
        description="Fictional identity fixture",
        capabilities=[],
        service_name="openai_codex",
    )
    registry = SimpleNamespace(
        get_model=lambda key: model if key == "openai_codex/gpt-6.1-sol" else None
    )
    monkeypatch.setattr(ModelRegistry, "_instance", registry)
    identity = ServiceManager.describe_service(service())
    assert identity == {
        "provider": "openai_codex",
        "model": "gpt-6.1-sol",
        "qualified_model": "openai_codex/gpt-6.1-sol",
        "service_family": "openai_codex",
        "service_class": "SimpleNamespace",
    }
    monkeypatch.setattr(ModelRegistry, "_instance", None)
    monkeypatch.setattr(
        ModelRegistry, "get_instance", lambda: pytest.fail("initialized registry")
    )
    assert ServiceManager.describe_service(service())["service_family"] == "unknown"


@pytest.mark.parametrize(
    "unsafe",
    [
        SECRET,
        "https://user:secret@host/model",
        "/home/private/model",
        "../private",
        "model\nPRIVATE",
        "x" * 201,
    ],
)
def test_identity_rejects_unsafe_values(unsafe):
    identity = ServiceManager.describe_service(
        SimpleNamespace(provider_name=unsafe, model=unsafe)
    )
    assert identity["provider"] == identity["model"] == "unknown"
    assert "PRIVATE" not in json.dumps(identity)


@pytest.mark.parametrize(
    ("event_type", "reason", "truncated"),
    [
        ("response.incomplete", "max_output_tokens", True),
        ("response.incomplete", "content_filter", False),
        ("response.failed", "max_output_tokens", False),
        ("error", None, False),
    ],
)
def test_stream_terminal_failure_metadata(event_type, reason, truncated):
    from AgentCrew.modules.openai.response_stream import ResponsesStreamGuard

    event = SimpleNamespace(
        type=event_type,
        response=SimpleNamespace(incomplete_details=SimpleNamespace(reason=reason)),
    )

    async def events():
        yield event

    async def consume():
        async for _event in ResponsesStreamGuard(events(), "openai_codex"):
            pytest.fail("failed event was yielded")

    with pytest.raises(LLMResponseError) as failure:
        asyncio.run(consume())
    assert failure.value.output_truncated is truncated
    assert SECRET not in str(failure.value)


def test_stream_unterminated_is_provider_error_not_assumed_truncation():
    from AgentCrew.modules.openai.response_stream import ResponsesStreamGuard

    async def events():
        yield SimpleNamespace(type="response.output_text.delta", delta=SECRET)

    async def consume():
        return [event async for event in ResponsesStreamGuard(events(), "openai_codex")]

    with pytest.raises(LLMResponseError) as failure:
        asyncio.run(consume())
    assert not failure.value.output_truncated
    assert SECRET not in str(failure.value)


def test_missing_service_identity():
    identity = ServiceManager.describe_service(None)
    assert identity["service_class"] == "none"
    assert identity["provider"] == identity["model"] == "unknown"
