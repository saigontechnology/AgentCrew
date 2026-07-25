"""
Tests for async-native lifecycle migration: selection rollback, transfer
context bookkeeping, model update counts, and command wiring.

All tests in this file exercise the async lifecycle path directly; they
must pass without relying on ``nest_asyncio``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.manager import AgentManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubLLM:
    """Minimal stub satisfying what :class:`LocalAgent` expects from an LLM."""

    provider_name = "test"
    model = "test-model"
    temperature = 0.4

    def set_system_prompt(self, prompt):
        pass

    def clear_tools(self):
        pass

    def set_tools(self, tools):
        pass

    def calculate_cost(self, *a, **kw):
        return 0.0

    def set_think(self, val):
        return True


@pytest.fixture
def mgr():
    """Return an AgentManager with two stub agents registered.

    Sets ``current_agent`` directly rather than calling the real
    :meth:`select_agent` so tests start with a clean lifecycle slate.
    """
    m = AgentManager()
    for name in ("alice", "bob"):
        agent = LocalAgent(
            name=name,
            description=f"{name} agent",
            llm_service=_StubLLM(),
            services={},
            tools=[],
        )
        agent.is_active = True
        m.register_agent(agent)
    # Set current_agent directly; avoid real lifecycle side-effects
    m.current_agent = m.agents["alice"]
    yield m
    m._instance = None


# ---------------------------------------------------------------------------
# select_agent_async: deactivation failure
# ---------------------------------------------------------------------------


class TestSelectAgentAsyncDeactivationFailure:
    """When old-agent deactivation fails, the old agent stays current."""

    @pytest.mark.asyncio
    async def test_returns_false_when_old_deactivation_returns_false(self, mgr):
        old = mgr.current_agent
        old.deactivate_async = AsyncMock(return_value=False)

        result = await mgr.select_agent_async("bob")

        assert result is False
        assert mgr.current_agent is old

    @pytest.mark.asyncio
    async def test_returns_false_when_old_deactivation_raises(self, mgr):
        old = mgr.current_agent
        old.deactivate_async = AsyncMock(side_effect=RuntimeError("boom"))

        result = await mgr.select_agent_async("bob")

        assert result is False
        assert mgr.current_agent is old

    @pytest.mark.asyncio
    async def test_target_not_activated_when_old_fails(self, mgr):
        old = mgr.current_agent
        bob = mgr.agents["bob"]
        old.deactivate_async = AsyncMock(return_value=False)
        bob.activate_async = AsyncMock(return_value=True)

        await mgr.select_agent_async("bob")

        bob.activate_async.assert_not_called()


# ---------------------------------------------------------------------------
# select_agent_async: target activation failure and rollback
# ---------------------------------------------------------------------------


class TestSelectAgentAsyncTargetActivationFailure:
    """When target activation fails, the old agent should be genuinely
    reactivated (not merely flag-mutated)."""

    @pytest.mark.asyncio
    async def test_returns_false_when_target_returns_false(self, mgr):
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        result = await mgr.select_agent_async("bob")

        assert result is False

    @pytest.mark.asyncio
    async def test_rolls_back_to_old_agent_on_false(self, mgr):
        old = mgr.current_agent
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        await mgr.select_agent_async("bob")

        assert mgr.current_agent is old

    @pytest.mark.asyncio
    async def test_reactivates_old_agent_on_rollback(self, mgr):
        old = mgr.current_agent
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        await mgr.select_agent_async("bob")

        assert old.is_active is True

    @pytest.mark.asyncio
    async def test_returns_false_when_target_raises(self, mgr):
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(side_effect=RuntimeError("boom"))

        result = await mgr.select_agent_async("bob")

        assert result is False

    @pytest.mark.asyncio
    async def test_rolls_back_on_exception(self, mgr):
        old = mgr.current_agent
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(side_effect=RuntimeError("boom"))

        await mgr.select_agent_async("bob")

        assert mgr.current_agent is old

    @pytest.mark.asyncio
    async def test_rollback_reactivation_failure_leaves_old_inactive(self, mgr):
        old = mgr.current_agent
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)
        old.activate_async = AsyncMock(return_value=False)

        result = await mgr.select_agent_async("bob")

        assert result is False
        assert mgr.current_agent is old
        assert old.is_active is False


# ---------------------------------------------------------------------------
# perform_transfer_async: selection failure
# ---------------------------------------------------------------------------


class TestPerformTransferAsyncFailure:
    """When selection fails, transfer must not inject messages or claim success."""

    @pytest.mark.asyncio
    async def test_returns_success_false_on_selection_failure(self, mgr):
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        result = await mgr.perform_transfer_async("bob", "do something")

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_does_not_inject_messages_on_failure(self, mgr):
        mgr.current_agent.history = [
            {"role": "user", "content": "hello", "agent": "alice"}
        ]
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        result = await mgr.perform_transfer_async("bob", "do something")

        assert result["success"] is False
        assert len(mgr.agents["bob"].history) == 0

    @pytest.mark.asyncio
    async def test_does_not_mutate_shared_context_on_failure(self, mgr):
        mgr.current_agent.history = [
            {"role": "user", "content": "hello", "agent": "alice"}
        ]
        pool_before = mgr.current_agent.shared_context_pool.get("bob", []).copy()
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        await mgr.perform_transfer_async("bob", "do something")

        pool_after = mgr.current_agent.shared_context_pool.get("bob", [])
        assert pool_after == pool_before

    @pytest.mark.asyncio
    async def test_retry_includes_same_context(self, mgr):
        mgr.current_agent.history = [
            {"role": "user", "content": "important message", "agent": "alice"}
        ]
        bob = mgr.agents["bob"]
        bob.activate_async = AsyncMock(return_value=False)

        await mgr.perform_transfer_async("bob", "do something")

        bob.activate_async = AsyncMock(return_value=True)
        result = await mgr.perform_transfer_async("bob", "do something")

        assert result["success"] is True
        conversations = result["transfer"].get("included_conversations", [])
        assert any("important message" in c for c in conversations), (
            "Retry after failure lost context"
        )


# ---------------------------------------------------------------------------
# Successful transfer semantics
# ---------------------------------------------------------------------------


class TestPerformTransferAsyncSuccess:
    """Successful transfer preserves transfer record and direct-inject messages."""

    @pytest.mark.asyncio
    async def test_returns_success_true(self, mgr):
        result = await mgr.perform_transfer_async("bob", "do something")

        assert result["success"] is True
        assert mgr.current_agent.name == "bob"

    @pytest.mark.asyncio
    async def test_transfer_record_includes_reason(self, mgr):
        result = await mgr.perform_transfer_async("bob", "urgent task")

        assert result["transfer"]["reason"] == "urgent task"

    @pytest.mark.asyncio
    async def test_direct_messages_injected_on_success(self, mgr):
        msg = {"role": "user", "content": "Content of file.txt", "agent": "alice"}
        mgr.current_agent.history = [msg]

        await mgr.perform_transfer_async("bob", "handle file")

        assert len(mgr.agents["bob"].history) > 0


# ---------------------------------------------------------------------------
# update_llm_service_async: exactly-once per applicable agent
# ---------------------------------------------------------------------------


class TestUpdateLLMServiceAsyncCount:
    """Each applicable agent should be updated exactly once."""

    @pytest.mark.asyncio
    async def test_current_agent_updated_once(self, mgr):
        call_counts: dict[str, int] = {}
        original_service = mgr.current_agent.llm

        async def tracking_update(svc):
            call_counts[mgr.current_agent.name] = (
                call_counts.get(mgr.current_agent.name, 0) + 1
            )

        mgr.current_agent.update_llm_service_async = tracking_update

        await mgr.update_llm_service_async(original_service)

        assert call_counts.get("alice", 0) == 1


# ---------------------------------------------------------------------------
# Prompt evolution coordinator wiring
# ---------------------------------------------------------------------------


class TestPromptEvolutionCoordinatorAsync:
    """The coordinator must await the async persistence path."""

    def test_apply_is_async(self):
        import inspect

        from AgentCrew.modules.chat.message.prompt_evolution_coordinator import (
            PromptEvolutionCoordinator,
        )

        assert inspect.iscoroutinefunction(PromptEvolutionCoordinator._apply), (
            "_apply must be async"
        )


# ---------------------------------------------------------------------------
# Command wiring: /jump and /fork
# ---------------------------------------------------------------------------


class TestCommandWiringAsync:
    """Verify that /jump and /fork are awaited in async context."""

    def test_handle_jump_is_async(self):
        import inspect

        from AgentCrew.modules.chat.message.commands.conversation_commands import (
            ConversationCommands,
        )

        assert inspect.iscoroutinefunction(ConversationCommands.handle_jump)

    def test_handle_fork_is_async(self):
        import inspect

        from AgentCrew.modules.chat.message.commands.conversation_commands import (
            ConversationCommands,
        )

        assert inspect.iscoroutinefunction(ConversationCommands.handle_fork)

    def test_command_processor_awaits_handlers(self):
        import ast

        with open("AgentCrew/modules/chat/message/command_processor.py") as f:
            tree = ast.parse(f.read())
        found_jump = False
        found_fork = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Await):
                call = node.value
                if isinstance(call, ast.Call) and hasattr(call.func, "attr"):
                    if call.func.attr == "handle_jump":
                        found_jump = True
                    if call.func.attr == "handle_fork":
                        found_fork = True
        assert found_jump, "command_processor must await handle_jump"
        assert found_fork, "command_processor must await handle_fork"
