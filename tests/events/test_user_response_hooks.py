"""Tests for the ``user.message`` and ``response.complete`` lifecycle hooks.

Coverage
--------
- ``user.message.before`` fires and can mutate the context when registered.
- ``user.message.after`` cannot be registered (phase not in HOOK_PAYLOAD_MAP).
- ``response.complete.after`` fires and can mutate the result when registered.
- ``response.complete.before`` cannot be registered (phase not in HOOK_PAYLOAD_MAP).
- Zero registered hooks produce deterministic no-ops for both lifecycle points.
- Cancellation via ``user.message.before`` returning ``None``.
- Cancellation via ``user.message.before`` raising ``CancelOperation``.
- Before-hook content mutation propagates correctly.
"""

from __future__ import annotations

import pytest

from AgentCrew.modules.events.hook_payloads import (
    HOOK_PAYLOAD_MAP,
    ResponseCompleteResult,
    UserMessageContext,
)
from AgentCrew.modules.events.hooks import (
    CancelOperation,
    Hook,
    HookPhase,
    HookPoints,
    HookRegistry,
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


@pytest.fixture
def hooks():
    return HookRegistry.get_instance()


SAMPLE_RAW_INPUT = "Hello, agent!"
SAMPLE_DISPLAY_TEXT = "Hello, @agent!"
SAMPLE_CONTENT = "Hello, agent!"
SAMPLE_RESPONSE = "Hi there! How can I help?"
SAMPLE_MODIFIED_CONTENT = "Modified: Hello, agent!"


# ===========================================================================
#  HOOK_PAYLOAD_MAP — verify restricted phases
# ===========================================================================


class TestPayloadMapRestrictions:
    """``user.message`` only exposes BEFORE; ``response.complete`` only exposes AFTER."""

    def test_user_message_has_only_before(self):
        phases = HOOK_PAYLOAD_MAP.get(HookPoints.USER_MESSAGE, {})
        assert HookPhase.BEFORE in phases
        assert HookPhase.AFTER not in phases

    def test_response_complete_has_only_after(self):
        phases = HOOK_PAYLOAD_MAP.get(HookPoints.RESPONSE_COMPLETE, {})
        assert HookPhase.AFTER in phases
        assert HookPhase.BEFORE not in phases


# ===========================================================================
#  Zero hooks — deterministic no-op
# ===========================================================================


class TestZeroHooks:
    """With no hooks registered, hooks must return the context/result unchanged."""

    @pytest.mark.asyncio
    async def test_user_message_before_noop(self, hooks):
        ctx = await hooks.run_before(
            HookPoints.USER_MESSAGE,
            raw_input=SAMPLE_RAW_INPUT,
            display_text=SAMPLE_DISPLAY_TEXT,
            content=SAMPLE_CONTENT,
        )
        assert ctx is not None
        assert ctx["raw_input"] == SAMPLE_RAW_INPUT
        assert ctx["content"] == SAMPLE_CONTENT

    @pytest.mark.asyncio
    async def test_response_complete_after_noop(self, hooks):
        envelope: ResponseCompleteResult = {
            "response": SAMPLE_RESPONSE,
            "memory_stored": True,
        }
        result = await hooks.run_after(
            HookPoints.RESPONSE_COMPLETE,
            result=envelope,
        )
        assert result is not None
        assert result["response"] == SAMPLE_RESPONSE
        assert result["memory_stored"] is True


# ===========================================================================
#  Opposite phases — not registerable / do not fire
# ===========================================================================


class TestOppositePhasesDoNotFire:
    """Only the restricted phases should be active; opposite phases are no-ops."""

    def test_user_message_after_not_in_payload_map(self):
        """user.message.after is not in HOOK_PAYLOAD_MAP."""
        phases = HOOK_PAYLOAD_MAP.get(HookPoints.USER_MESSAGE, {})
        assert HookPhase.AFTER not in phases, (
            "user.message.after must not appear in HOOK_PAYLOAD_MAP"
        )

    def test_response_complete_before_not_in_payload_map(self):
        """response.complete.before is not in HOOK_PAYLOAD_MAP."""
        phases = HOOK_PAYLOAD_MAP.get(HookPoints.RESPONSE_COMPLETE, {})
        assert HookPhase.BEFORE not in phases, (
            "response.complete.before must not appear in HOOK_PAYLOAD_MAP"
        )


# ===========================================================================
#  user.message.before — mutation
# ===========================================================================


class TestUserMessageBeforeMutation:
    """Before hooks can modify the user message context."""

    @pytest.mark.asyncio
    async def test_before_hook_modifies_content(self, hooks):
        async def modify_content(ctx: UserMessageContext) -> UserMessageContext:
            ctx["content"] = SAMPLE_MODIFIED_CONTENT
            return ctx

        hooks.register(
            Hook(
                HookPoints.USER_MESSAGE,
                HookPhase.BEFORE,
                modify_content,
                description="test_modify_content",
            )
        )
        ctx = await hooks.run_before(
            HookPoints.USER_MESSAGE,
            raw_input=SAMPLE_RAW_INPUT,
            display_text=SAMPLE_DISPLAY_TEXT,
            content=SAMPLE_CONTENT,
        )
        assert ctx is not None
        assert ctx["content"] == SAMPLE_MODIFIED_CONTENT
        assert ctx["raw_input"] == SAMPLE_RAW_INPUT  # unchanged

    @pytest.mark.asyncio
    async def test_before_hook_cancels_with_none(self, hooks):
        async def cancel(ctx: UserMessageContext) -> None:
            return None

        hooks.register(
            Hook(
                HookPoints.USER_MESSAGE,
                HookPhase.BEFORE,
                cancel,
                description="test_cancel",
            )
        )
        ctx = await hooks.run_before(
            HookPoints.USER_MESSAGE,
            raw_input=SAMPLE_RAW_INPUT,
            display_text=SAMPLE_DISPLAY_TEXT,
            content=SAMPLE_CONTENT,
        )
        assert ctx is None

    @pytest.mark.asyncio
    async def test_before_hook_cancels_with_exception(self, hooks):
        async def cancel_op(ctx: UserMessageContext) -> UserMessageContext:
            raise CancelOperation("cancelled by test")

        hooks.register(
            Hook(
                HookPoints.USER_MESSAGE,
                HookPhase.BEFORE,
                cancel_op,
                description="test_cancel_op",
            )
        )
        ctx = await hooks.run_before(
            HookPoints.USER_MESSAGE,
            raw_input=SAMPLE_RAW_INPUT,
            display_text=SAMPLE_DISPLAY_TEXT,
            content=SAMPLE_CONTENT,
        )
        assert ctx is None


# ===========================================================================
#  response.complete.after — mutation
# ===========================================================================


class TestResponseCompleteAfterMutation:
    """After hooks can modify the response result envelope."""

    @pytest.mark.asyncio
    async def test_after_hook_modifies_response(self, hooks):
        MODIFIED_RESPONSE = "Modified: " + SAMPLE_RESPONSE

        async def modify_response(
            ctx: dict, result: ResponseCompleteResult
        ) -> ResponseCompleteResult:
            result["response"] = MODIFIED_RESPONSE
            return result

        hooks.register(
            Hook(
                HookPoints.RESPONSE_COMPLETE,
                HookPhase.AFTER,
                modify_response,
                description="test_modify_response",
            )
        )
        envelope: ResponseCompleteResult = {
            "response": SAMPLE_RESPONSE,
            "memory_stored": True,
        }
        result = await hooks.run_after(
            HookPoints.RESPONSE_COMPLETE,
            result=envelope,
        )
        assert result["response"] == MODIFIED_RESPONSE
        assert result["memory_stored"] is True  # unchanged

    @pytest.mark.asyncio
    async def test_after_hook_modifies_memory_stored(self, hooks):
        async def modify_memory(
            ctx: dict, result: ResponseCompleteResult
        ) -> ResponseCompleteResult:
            result["memory_stored"] = False
            return result

        hooks.register(
            Hook(
                HookPoints.RESPONSE_COMPLETE,
                HookPhase.AFTER,
                modify_memory,
                description="test_modify_memory",
            )
        )
        envelope: ResponseCompleteResult = {
            "response": SAMPLE_RESPONSE,
            "memory_stored": True,
        }
        result = await hooks.run_after(
            HookPoints.RESPONSE_COMPLETE,
            result=envelope,
        )
        assert result["memory_stored"] is False
        assert result["response"] == SAMPLE_RESPONSE  # unchanged


# ===========================================================================
#  Priority ordering
# ===========================================================================


class TestPriorityOrdering:
    """Hooks run in priority order (highest first)."""

    @pytest.mark.asyncio
    async def test_user_message_before_priority(self, hooks):
        order: list[str] = []

        async def low(ctx: UserMessageContext) -> UserMessageContext:
            order.append("low")
            return ctx

        async def high(ctx: UserMessageContext) -> UserMessageContext:
            order.append("high")
            return ctx

        hooks.register(
            Hook(
                HookPoints.USER_MESSAGE,
                HookPhase.BEFORE,
                low,
                priority=0,
                description="low",
            )
        )
        hooks.register(
            Hook(
                HookPoints.USER_MESSAGE,
                HookPhase.BEFORE,
                high,
                priority=10,
                description="high",
            )
        )
        await hooks.run_before(
            HookPoints.USER_MESSAGE,
            raw_input=SAMPLE_RAW_INPUT,
            display_text=SAMPLE_DISPLAY_TEXT,
            content=SAMPLE_CONTENT,
        )
        assert order == ["high", "low"]

    @pytest.mark.asyncio
    async def test_response_complete_after_priority(self, hooks):
        order: list[str] = []

        async def low(
            ctx: dict, result: ResponseCompleteResult
        ) -> ResponseCompleteResult:
            order.append("low")
            return result

        async def high(
            ctx: dict, result: ResponseCompleteResult
        ) -> ResponseCompleteResult:
            order.append("high")
            return result

        hooks.register(
            Hook(
                HookPoints.RESPONSE_COMPLETE,
                HookPhase.AFTER,
                low,
                priority=0,
                description="low",
            )
        )
        hooks.register(
            Hook(
                HookPoints.RESPONSE_COMPLETE,
                HookPhase.AFTER,
                high,
                priority=10,
                description="high",
            )
        )
        envelope: ResponseCompleteResult = {
            "response": SAMPLE_RESPONSE,
            "memory_stored": True,
        }
        await hooks.run_after(
            HookPoints.RESPONSE_COMPLETE,
            result=envelope,
        )
        assert order == ["high", "low"]
