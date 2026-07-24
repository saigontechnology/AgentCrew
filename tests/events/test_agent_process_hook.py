"""Tests for the ``agent.process`` lifecycle hook.

Coverage
--------
- Zero registered hooks: deterministic no-op for both before and after phases.
- Before hook can modify ``model_id`` in the mutable context.
- Before hook can modify ``messages`` in the mutable context.
- After hook can modify ``tool_uses`` in the result envelope.
- After hook can modify ``token_usage`` in the result envelope.
- Cancellation via before-hook returning ``None``.
- Cancellation via before-hook raising ``CancelOperation``.
- Hook priority ordering (higher priority runs first).

.. important::
   ``model_id`` mutations apply temporarily to the LLM service for the
   duration of the LLM call and are restored after the after-hook completes.
"""

from __future__ import annotations

import copy

import pytest

from AgentCrew.modules.events.hook_payloads import (
    AgentProcessContext,
    AgentProcessResult,
)
from AgentCrew.modules.events.hooks import (
    CancelOperation,
    Hook,
    HookPhase,
    HookPoints,
    HookRegistry,
)
from AgentCrew.modules.llm.token_usage import TokenUsage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_hook_registry():
    """Reset the singleton before every test so state is isolated."""
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()


SAMPLE_MESSAGES: list[dict] = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "Hi there!"},
]
SAMPLE_MODEL_ID = "gpt-4o"
SAMPLE_TOOL_USES: list[dict] = [
    {"id": "tool_1", "name": "get_weather", "input": {"location": "NYC"}}
]
SAMPLE_TOKEN_USAGE = TokenUsage(input_tokens=50, output_tokens=100)


@pytest.fixture
def hooks():
    return HookRegistry.get_instance()


# ---------------------------------------------------------------------------
# 1. Zero hooks — deterministic no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_hooks_before_noop(hooks):
    """run_before returns the context unchanged when no hooks are registered."""
    ctx = await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert ctx is not None
    assert ctx["model_id"] == SAMPLE_MODEL_ID
    assert ctx["messages"] == SAMPLE_MESSAGES


@pytest.mark.asyncio
async def test_zero_hooks_after_noop(hooks):
    """run_after returns the envelope unchanged when no hooks are registered."""
    envelope: AgentProcessResult = {
        "tool_uses": copy.deepcopy(SAMPLE_TOOL_USES),
        "token_usage": SAMPLE_TOKEN_USAGE,
    }
    result = await hooks.run_after(
        HookPoints.AGENT_PROCESS,
        result=envelope,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert result is not None
    assert result["tool_uses"] == SAMPLE_TOOL_USES
    assert result["token_usage"] == SAMPLE_TOKEN_USAGE


# ---------------------------------------------------------------------------
# 2. Before hook — modify model_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_modifies_model_id(hooks):
    """Before hook can replace ``model_id`` in the mutable context."""

    async def replace_model(ctx: AgentProcessContext) -> AgentProcessContext:
        ctx["model_id"] = "claude-3-opus"
        return ctx

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            replace_model,
            description="test_replace_model_id",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert ctx["model_id"] == "claude-3-opus"


# ---------------------------------------------------------------------------
# 3. Before hook — modify messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_modifies_messages(hooks):
    """Before hook can replace ``messages`` in the mutable context."""

    async def replace_messages(ctx: AgentProcessContext) -> AgentProcessContext:
        ctx["messages"] = [{"role": "user", "content": "modified input"}]
        return ctx

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            replace_messages,
            description="test_replace_messages",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert ctx["messages"][0]["content"] == "modified input"
    assert ctx["model_id"] == SAMPLE_MODEL_ID  # unchanged


# ---------------------------------------------------------------------------
# 4. After hook — modify tool_uses in the result envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_modifies_tool_uses(hooks):
    """After hook receives an AgentProcessResult and can modify tool_uses."""

    async def modify_tool_uses(
        ctx: dict, result: AgentProcessResult
    ) -> AgentProcessResult:
        result["tool_uses"] = [
            {"id": "tool_2", "name": "get_time", "input": {"timezone": "UTC"}}
        ]
        return result

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.AFTER,
            modify_tool_uses,
            description="test_modify_tool_uses",
        )
    )
    envelope: AgentProcessResult = {
        "tool_uses": copy.deepcopy(SAMPLE_TOOL_USES),
        "token_usage": SAMPLE_TOKEN_USAGE,
    }
    result = await hooks.run_after(
        HookPoints.AGENT_PROCESS,
        result=envelope,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert len(result["tool_uses"]) == 1
    assert result["tool_uses"][0]["name"] == "get_time"
    assert result["tool_uses"][0]["id"] == "tool_2"


# ---------------------------------------------------------------------------
# 5. After hook — modify token_usage in the result envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_modifies_token_usage(hooks):
    """After hook can modify ``token_usage`` in the result envelope."""

    modified_usage = TokenUsage(input_tokens=999, output_tokens=888)

    async def modify_token_usage(
        ctx: dict, result: AgentProcessResult
    ) -> AgentProcessResult:
        result["token_usage"] = modified_usage
        return result

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.AFTER,
            modify_token_usage,
            description="test_modify_token_usage",
        )
    )
    envelope: AgentProcessResult = {
        "tool_uses": copy.deepcopy(SAMPLE_TOOL_USES),
        "token_usage": SAMPLE_TOKEN_USAGE,
    }
    result = await hooks.run_after(
        HookPoints.AGENT_PROCESS,
        result=envelope,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    tu = result["token_usage"]
    assert tu.input_tokens == 999
    assert tu.output_tokens == 888


# ---------------------------------------------------------------------------
# 6. After hook — modify both tool_uses and token_usage simultaneously
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_modifies_both(hooks):
    """After hook can modify both tool_uses and token_usage in one pass."""

    async def modify_both(ctx: dict, result: AgentProcessResult) -> AgentProcessResult:
        result["tool_uses"] = []
        result["token_usage"] = TokenUsage(input_tokens=0, output_tokens=0)
        return result

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.AFTER,
            modify_both,
            description="test_modify_both",
        )
    )
    envelope: AgentProcessResult = {
        "tool_uses": copy.deepcopy(SAMPLE_TOOL_USES),
        "token_usage": SAMPLE_TOKEN_USAGE,
    }
    result = await hooks.run_after(
        HookPoints.AGENT_PROCESS,
        result=envelope,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert result["tool_uses"] == []
    assert result["token_usage"].total_tokens == 0


# ---------------------------------------------------------------------------
# 7. Cancellation via before-hook returning None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_return_none(hooks):
    """Before hook returning None cancels the operation (run_before returns None)."""

    async def cancel(ctx: AgentProcessContext) -> None:
        return None

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            cancel,
            description="test_cancel_none",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert ctx is None


# ---------------------------------------------------------------------------
# 8. Cancellation via CancelOperation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_via_exception(hooks):
    """Before hook raising CancelOperation cancels the operation."""

    async def cancel_op(ctx: AgentProcessContext) -> AgentProcessContext:
        raise CancelOperation("cancelled by test")

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            cancel_op,
            description="test_cancel_op",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert ctx is None


# ---------------------------------------------------------------------------
# 9. Hook priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_ordering(hooks):
    """Hooks with higher priority value run first."""

    order: list[str] = []

    async def low(ctx: AgentProcessContext) -> AgentProcessContext:
        order.append("low")
        return ctx

    async def high(ctx: AgentProcessContext) -> AgentProcessContext:
        order.append("high")
        return ctx

    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            low,
            priority=0,
            description="low",
        )
    )
    hooks.register(
        Hook(
            HookPoints.AGENT_PROCESS,
            HookPhase.BEFORE,
            high,
            priority=10,
            description="high",
        )
    )

    await hooks.run_before(
        HookPoints.AGENT_PROCESS,
        model_id=SAMPLE_MODEL_ID,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
        provider="openai",
    )
    assert order == ["high", "low"], f"Expected high then low, got {order}"
