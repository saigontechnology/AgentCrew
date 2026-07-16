"""
Tests for the ``context.build`` lifecycle hook.

Coverage
--------
- Zero registered hooks: deterministic no-op.
- Before hook can replace ``messages``.
- Before hook can replace ``system_prompt`` (always persisted on the LLM
  service, not turn-local).
- After hook receives a ``ContextBuildResult`` envelope via ``result=``
  and can replace ``messages`` / ``system_prompt``.
- Malformed (non-dict) after-hook return is safely ignored by the
  ``isinstance(modified, dict)`` guard.
- Cancellation via before-hook returning ``None``.
- Cancellation via before-hook raising ``CancelOperation``.
- Hook priority ordering (higher priority runs first).

.. note::
   ``system_prompt`` mutations call ``self.llm.set_system_prompt()`` and
   **persist** on the LLM service. They are not automatically scoped to a
   single turn — no restoration logic exists in this slice.
"""

from __future__ import annotations

import copy

import pytest

from AgentCrew.modules.events.hooks import (
    CancelOperation,
    Hook,
    HookPhase,
    HookPoints,
    HookRegistry,
)
from AgentCrew.modules.events.hook_payloads import (
    ContextBuildContext,
    ContextBuildResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_hook_registry():
    """Reset the singleton before every test so state is isolated."""
    HookRegistry.reset_instance()
    yield
    HookRegistry.reset_instance()


SAMPLE_MESSAGES = [{"role": "user", "content": "hello"}]
SAMPLE_SYSTEM_PROMPT = "You are a test agent"


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
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert ctx is not None
    assert ctx["messages"] == SAMPLE_MESSAGES
    assert ctx["system_prompt"] == SAMPLE_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_zero_hooks_after_noop(hooks):
    """run_after returns the envelope unchanged when no hooks are registered."""
    envelope: ContextBuildResult = {
        "messages": copy.deepcopy(SAMPLE_MESSAGES),
        "system_prompt": SAMPLE_SYSTEM_PROMPT,
    }
    result = await hooks.run_after(HookPoints.CONTEXT_BUILD, result=envelope)
    assert result == envelope
    assert isinstance(result, dict)
    assert result["messages"] == SAMPLE_MESSAGES


# ---------------------------------------------------------------------------
# 2. Before hook — replace messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_replaces_messages(hooks):
    """Before hook returning modified context with 'messages' replaces the list."""

    async def replace_msgs(ctx: ContextBuildContext) -> ContextBuildContext:
        ctx["messages"] = [{"role": "user", "content": "replaced"}]
        return ctx

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            replace_msgs,
            description="test_replace_msgs",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert ctx["messages"][0]["content"] == "replaced"


# ---------------------------------------------------------------------------
# 3. Before hook — replace system_prompt (persistent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_hook_replaces_system_prompt(hooks):
    """Before hook returning modified context with 'system_prompt' replaces it.

    .. note::
       In the real implementation this change persists via
       ``self.llm.set_system_prompt()``.  This test validates only that
       the hook infrastructure propagates the value.
    """

    async def replace_sp(ctx: ContextBuildContext) -> ContextBuildContext:
        ctx["system_prompt"] = "Modified SP"
        return ctx

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            replace_sp,
            description="test_replace_sp",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert ctx["system_prompt"] == "Modified SP"


# ---------------------------------------------------------------------------
# 4. After hook — modify messages in ContextBuildResult envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_modifies_messages_in_envelope(hooks):
    """After hook receives a ContextBuildResult via result= and can modify messages."""

    async def modify_envelope(ctx: dict, result: ContextBuildResult) -> ContextBuildResult:
        result["messages"] = [{"role": "assistant", "content": "modified after"}]
        return result

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.AFTER,
            modify_envelope,
            description="test_after_modify_msgs",
        )
    )
    envelope: ContextBuildResult = {
        "messages": copy.deepcopy(SAMPLE_MESSAGES),
        "system_prompt": SAMPLE_SYSTEM_PROMPT,
    }
    result = await hooks.run_after(HookPoints.CONTEXT_BUILD, result=envelope)
    assert result["messages"][0]["content"] == "modified after"


# ---------------------------------------------------------------------------
# 5. After hook — modify system_prompt in ContextBuildResult envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hook_modifies_system_prompt_in_envelope(hooks):
    """After hook modifies system_prompt in the result envelope."""

    async def modify_sp(ctx: dict, result: ContextBuildResult) -> ContextBuildResult:
        result["system_prompt"] = "After modified SP"
        return result

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.AFTER,
            modify_sp,
            description="test_after_modify_sp",
        )
    )
    envelope: ContextBuildResult = {
        "messages": copy.deepcopy(SAMPLE_MESSAGES),
        "system_prompt": SAMPLE_SYSTEM_PROMPT,
    }
    result = await hooks.run_after(HookPoints.CONTEXT_BUILD, result=envelope)
    assert result["system_prompt"] == "After modified SP"


# ---------------------------------------------------------------------------
# 6. Malformed (non-dict) after-hook return — guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_after_return_guard(hooks):
    """Non-dict after-hook return is propagated; caller guards with isinstance."""

    async def bad_return(ctx: dict, result: ContextBuildResult) -> str:
        return "not_a_dict"

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.AFTER,
            bad_return,
            description="test_malformed",
        )
    )
    envelope: ContextBuildResult = {
        "messages": copy.deepcopy(SAMPLE_MESSAGES),
        "system_prompt": SAMPLE_SYSTEM_PROMPT,
    }
    result = await hooks.run_after(HookPoints.CONTEXT_BUILD, result=envelope)
    assert result == "not_a_dict"
    # The caller (LocalAgent.process_messages) uses:
    #   if isinstance(modified, dict): final_messages = modified.get(...)
    assert not isinstance(result, dict)


# ---------------------------------------------------------------------------
# 7. Cancellation via before-hook returning None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_return_none(hooks):
    """Before hook returning None cancels the operation (run_before returns None)."""

    async def cancel(ctx: ContextBuildContext) -> None:
        return None

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            cancel,
            description="test_cancel_none",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert ctx is None


# ---------------------------------------------------------------------------
# 8. Cancellation via CancelOperation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_via_exception(hooks):
    """Before hook raising CancelOperation cancels the operation."""

    async def cancel_op(ctx: ContextBuildContext) -> ContextBuildContext:
        raise CancelOperation("cancelled by test")

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            cancel_op,
            description="test_cancel_op",
        )
    )
    ctx = await hooks.run_before(
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert ctx is None


# ---------------------------------------------------------------------------
# 9. Hook priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_ordering(hooks):
    """Hooks with higher priority value run first."""

    order: list[str] = []

    async def low(ctx: ContextBuildContext) -> ContextBuildContext:
        order.append("low")
        return ctx

    async def high(ctx: ContextBuildContext) -> ContextBuildContext:
        order.append("high")
        return ctx

    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            low,
            priority=0,
            description="low",
        )
    )
    hooks.register(
        Hook(
            HookPoints.CONTEXT_BUILD,
            HookPhase.BEFORE,
            high,
            priority=10,
            description="high",
        )
    )

    await hooks.run_before(
        HookPoints.CONTEXT_BUILD,
        system_prompt=SAMPLE_SYSTEM_PROMPT,
        messages=copy.deepcopy(SAMPLE_MESSAGES),
    )
    assert order == ["high", "low"], f"Expected high then low, got {order}"
