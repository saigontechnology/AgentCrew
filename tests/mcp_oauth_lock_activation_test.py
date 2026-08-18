"""Unit tests for the cross-event-loop OAuth lock and durable async agent activation.

Covers:
- ``MCPService._get_oauth_lock``: locks are scoped per server per event loop
  so a cached ``asyncio.Lock`` can never be bound to a foreign loop.
- ``LocalAgent.activate_async`` / ``MCPSessionManager.discover_mcps_for_agent_background``:
  durable manager-owned background MCP discovery that survives the temporary
  ``asyncio.run()`` loops used by CLI/GUI, with per-agent deduplication, a
  bounded fail-open wait before the first LLM request, and safe deactivation.
"""

import asyncio
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from mcp.types import Tool

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.manager import AgentManager
from AgentCrew.modules.mcpclient import MCPSessionManager
from AgentCrew.modules.mcpclient.service import MCPService


class FakeLLM:
    """Minimal LLM double supporting the attributes activation touches.

    Records every tool registered via ``register_tool`` so tests can assert
    exactly which definitions reached the LLM (built-ins vs MCP tools).
    """

    provider_name = "test"
    model = "test-model"

    def __init__(self):
        self.temperature = None
        self.system_prompt = None
        self.registered = []

    def set_system_prompt(self, prompt):
        self.system_prompt = prompt

    def get_system_prompt(self):
        return self.system_prompt or ""

    def clear_tools(self):
        self.registered = []

    def register_tool(self, tool_def, handler):
        if isinstance(tool_def, dict):
            name = tool_def.get("name")
            if name is None and isinstance(tool_def.get("function"), dict):
                name = tool_def["function"].get("name")
            self.registered.append(name)
        else:
            self.registered.append(str(tool_def))

    def set_tools(self, tools):
        pass


# ---------------------------------------------------------------------------
# Fake managers
# ---------------------------------------------------------------------------


class _BlockedDiscoveryManager:
    """Returns a future settled by an externally released threading.Event."""

    initialized = True

    def __init__(self):
        self.release = threading.Event()

    def discover_mcps_for_agent_background(self, agent_name):
        future = Future()
        release = self.release

        def _worker():
            release.wait(timeout=10)
            future.set_result(None)

        threading.Thread(target=_worker, daemon=True).start()
        return future

    async def deregister_tools_for_agent(self, agent_name=None):
        return None


class _NeverSettlingManager:
    """Returns a future that never settles (deliberately blocked discovery)."""

    initialized = True

    def discover_mcps_for_agent_background(self, agent_name):
        return Future()

    async def deregister_tools_for_agent(self, agent_name=None):
        return None


class _FailingDiscoveryManager:
    """Background discovery fails with an exception on the future."""

    initialized = True

    def discover_mcps_for_agent_background(self, agent_name):
        future = Future()

        def _worker():
            future.set_exception(RuntimeError("discovery boom"))

        threading.Thread(target=_worker, daemon=True).start()
        return future

    async def deregister_tools_for_agent(self, agent_name=None):
        return None


class _DoneDiscoveryManager:
    """Discovery completes immediately (cached/fast path)."""

    initialized = True

    def discover_mcps_for_agent_background(self, agent_name):
        future = Future()
        future.set_result(None)
        return future

    async def deregister_tools_for_agent(self, agent_name=None):
        return None


def _build_agent(name, monkeypatch, manager):
    """Build a LocalAgent with a stubbed tool registration and fake manager."""
    agent = LocalAgent(
        name=name,
        description=f"{name} agent",
        llm_service=FakeLLM(),
        services={},
        tools=[],
    )
    monkeypatch.setattr(agent, "register_tools", lambda: None)
    monkeypatch.setattr(MCPSessionManager, "get_instance", lambda: manager)
    return agent


def _make_preprocess_ready(agent, monkeypatch):
    """Stub pre_process internals unrelated to the discovery wait under test."""
    monkeypatch.setattr(agent, "_refresh_agent_skills", lambda: None)
    monkeypatch.setattr(agent, "_clean_shrinkable_tool_result", lambda messages: None)
    monkeypatch.setattr(agent, "_enhance_agent_context_messages", lambda messages: None)
    return agent


class TestPerEventLoopOAuthLocks:
    def test_same_loop_returns_same_lock(self):
        service = MCPService()

        async def acquire_twice():
            first = service._get_oauth_lock("mindcase")
            second = service._get_oauth_lock("mindcase")
            assert first is second

        asyncio.run(acquire_twice())

    def test_different_loops_return_distinct_locks(self):
        service = MCPService()
        lock_a = None
        loop_a = None

        async def acquire_first():
            nonlocal lock_a, loop_a
            loop_a = asyncio.get_running_loop()
            lock_a = service._get_oauth_lock("mindcase")

        asyncio.run(acquire_first())

        async def acquire_second():
            loop_b = asyncio.get_running_loop()
            # Hold loop_a alive so its id cannot be reused by the new loop,
            # keeping this assertion deterministic.
            assert loop_b is not loop_a
            lock_b = service._get_oauth_lock("mindcase")
            assert lock_b is not lock_a

        asyncio.run(acquire_second())

    def test_different_servers_get_distinct_locks(self):
        service = MCPService()

        async def acquire():
            assert service._get_oauth_lock("server_a") is not service._get_oauth_lock(
                "server_b"
            )

        asyncio.run(acquire())


class TestActivateAsyncDiscovery:
    def test_activate_async_starts_durable_background_discovery(self, monkeypatch):
        captured = {}
        release = threading.Event()

        class FakeManager:
            initialized = True

            def discover_mcps_for_agent_background(self, agent_name):
                future = Future()

                def _worker():
                    captured["thread"] = threading.current_thread()
                    captured["agent_name"] = agent_name
                    release.wait(timeout=5)
                    future.set_result(None)

                threading.Thread(target=_worker, daemon=True).start()
                return future

        agent = _build_agent("TestAgent", monkeypatch, FakeManager())
        scenario_loop = None

        async def scenario():
            nonlocal scenario_loop
            scenario_loop = asyncio.get_running_loop()
            result = await agent.activate_async()
            assert result is True
            assert agent.is_active is True
            assert agent.llm.temperature == 0.4
            assert agent.llm.system_prompt is not None
            assert isinstance(agent._mcp_discovery_future, Future)
            assert not agent._mcp_discovery_future.done()

        asyncio.run(scenario())

        # Discovery is manager/thread-owned, so it survives the temporary
        # activation loop closing (which would cancel an asyncio.Task).
        release.set()
        assert agent._mcp_discovery_future.result(timeout=5) is None
        assert captured["agent_name"] == "TestAgent"
        assert captured["thread"] is not threading.main_thread()

    def test_activate_async_without_initialized_manager(self, monkeypatch):
        agent = _build_agent(
            "TestAgent", monkeypatch, SimpleNamespace(initialized=False)
        )

        async def scenario():
            return await agent.activate_async()

        assert asyncio.run(scenario()) is True
        assert agent._mcp_discovery_future is None

    def test_activate_async_discovery_failure_surfaces_via_future(self, monkeypatch):
        agent = _build_agent("TestAgent", monkeypatch, _FailingDiscoveryManager())

        async def scenario():
            result = await agent.activate_async()
            assert result is True
            return agent._mcp_discovery_future

        future = asyncio.run(scenario())
        with pytest.raises(RuntimeError, match="discovery boom"):
            future.result(timeout=5)

    def test_activate_async_returns_false_without_llm(self, monkeypatch):
        agent = _build_agent(
            "TestAgent", monkeypatch, SimpleNamespace(initialized=False)
        )
        agent.llm = None

        async def scenario():
            return await agent.activate_async()

        assert asyncio.run(scenario()) is False


class TestSelectAgentPromptSwitch:
    @pytest.mark.asyncio
    async def test_select_agent_async_returns_promptly_while_discovery_blocked(
        self, monkeypatch
    ):
        from AgentCrew.modules.agents.manager import AgentManager

        blocked = _NeverSettlingManager()
        alice = _build_agent("alice", monkeypatch, blocked)
        bob = _build_agent("bob", monkeypatch, blocked)
        alice.is_active = True

        manager = AgentManager()
        try:
            manager.register_agent(alice)
            manager.register_agent(bob)
            manager.current_agent = alice

            result = await asyncio.wait_for(
                manager.select_agent_async("bob"), timeout=2
            )

            assert result is True
            assert manager.current_agent is bob
            assert bob.is_active is True
            assert bob._mcp_discovery_future is not None
            assert not bob._mcp_discovery_future.done()
        finally:
            manager._instance = None


class TestBackgroundDiscoverySurvival:
    def test_background_discovery_survives_temporary_loop_closure(self, monkeypatch):
        manager = MCPSessionManager()
        manager.initialized = True
        manager.config_manager = SimpleNamespace(
            load_config=lambda: None,
            get_enabled_servers=lambda agent_name: {"s1": SimpleNamespace(name="s1")},
        )
        captured = {}

        async def fake_discover(agent_name):
            captured["agent_name"] = agent_name
            captured["loop"] = asyncio.get_running_loop()
            captured["thread"] = threading.current_thread()
            await asyncio.sleep(0.05)

        monkeypatch.setattr(manager, "discover_mcps_for_agent", fake_discover)

        # Simulate CLI/GUI: the command starts discovery inside a temporary
        # loop that closes as soon as the command returns.
        async def scenario():
            return manager.discover_mcps_for_agent_background("TestAgent")

        future = asyncio.run(scenario())
        assert not future.done()

        # The temporary loop is now closed; the manager-owned worker must
        # still complete discovery on its own thread/loop.
        assert future.result(timeout=5) is None
        assert captured["agent_name"] == "TestAgent"
        assert captured["thread"] is not threading.main_thread()
        assert captured["loop"] is not None


class TestBackgroundDiscoveryDeduplication:
    def test_repeated_background_discovery_returns_same_future(self, monkeypatch):
        manager = MCPSessionManager()
        manager.initialized = True
        manager.config_manager = SimpleNamespace(
            load_config=lambda: None,
            get_enabled_servers=lambda agent_name: {"s1": SimpleNamespace(name="s1")},
        )
        started = threading.Event()
        release = threading.Event()
        calls = []

        async def fake_discover(agent_name):
            calls.append(agent_name)
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.02)

        monkeypatch.setattr(manager, "discover_mcps_for_agent", fake_discover)

        future1 = manager.discover_mcps_for_agent_background("TestAgent")
        future2 = manager.discover_mcps_for_agent_background("TestAgent")

        assert future1 is future2
        assert started.wait(timeout=5)
        time.sleep(0.1)  # give any duplicate worker a chance to start
        assert calls == ["TestAgent"]

        release.set()
        assert future1.result(timeout=5) is None
        assert calls == ["TestAgent"]


class TestPreProcessDiscoverySync:
    def test_pre_process_waits_then_final_syncs(self, monkeypatch):
        blocked = _BlockedDiscoveryManager()
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, blocked), monkeypatch
        )
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            assert sync_calls == ["sync"]  # built-ins synced at activation
            waiter = asyncio.create_task(agent.pre_process_message([]))
            await asyncio.sleep(0.2)
            assert sync_calls == ["sync"]  # still waiting on discovery
            assert not waiter.done()
            blocked.release.set()
            result = await asyncio.wait_for(waiter, timeout=5)
            assert result is not None
            assert sync_calls == ["sync", "sync"]  # final sync after discovery
            assert agent._defer_tool_registration is False

        asyncio.run(scenario())


class TestPreProcessTimeoutFailOpen:
    def test_timeout_proceeds_with_builtins_and_retries_later(self, monkeypatch):
        never = _NeverSettlingManager()
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, never), monkeypatch
        )
        agent.MCP_DISCOVERY_WAIT_SECONDS = 0.2
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            assert sync_calls == ["sync"]
            result = await agent.pre_process_message([])
            assert result is not None  # fail-open: proceeds with built-ins
            assert agent._defer_tool_registration is True  # still pending
            assert not agent._mcp_discovery_future.done()  # not cancelled
            return agent._mcp_discovery_future

        future = asyncio.run(scenario())
        assert sync_calls == ["sync"]  # no final sync on timeout

        # Underlying discovery was not cancelled: complete it later, then a
        # later message performs the final sync.
        future.set_result(None)

        async def later():
            result = await agent.pre_process_message([])
            assert result is not None
            assert agent._defer_tool_registration is False
            return result

        assert asyncio.run(later()) is not None
        assert sync_calls == ["sync", "sync"]


class TestDiscoveryFailureUnblocks:
    def test_discovery_failure_unblocks_preprocessing(self, monkeypatch):
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, _FailingDiscoveryManager()),
            monkeypatch,
        )
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            result = await agent.pre_process_message([])
            assert result is not None  # failure does not block preprocessing
            assert agent._defer_tool_registration is False
            return agent._mcp_discovery_future

        future = asyncio.run(scenario())
        with pytest.raises(RuntimeError, match="discovery boom"):
            future.result(timeout=5)
        assert sync_calls == ["sync", "sync"]  # activation + final sync


class TestNoMCPAndFastDiscovery:
    def test_without_initialized_manager_syncs_immediately(self, monkeypatch):
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, SimpleNamespace(initialized=False)),
            monkeypatch,
        )
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            assert agent._mcp_discovery_future is None
            result = await agent.pre_process_message([])
            assert result is not None
            assert agent._defer_tool_registration is False

        asyncio.run(scenario())
        assert sync_calls == ["sync", "sync"]

    def test_done_discovery_syncs_without_waiting(self, monkeypatch):
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, _DoneDiscoveryManager()),
            monkeypatch,
        )
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            assert agent._mcp_discovery_future.done()
            result = await agent.pre_process_message([])
            assert result is not None
            assert agent._defer_tool_registration is False

        asyncio.run(scenario())
        assert sync_calls == ["sync", "sync"]


class TestDeactivationDetaches:
    def test_deactivation_detaches_and_stale_completion_does_not_sync(
        self, monkeypatch
    ):
        blocked = _BlockedDiscoveryManager()
        sync_calls = []
        agent = _make_preprocess_ready(
            _build_agent("TestAgent", monkeypatch, blocked), monkeypatch
        )
        monkeypatch.setattr(
            agent, "_register_tools_with_llm", lambda: sync_calls.append("sync")
        )

        async def scenario():
            await agent.activate_async()
            assert agent._mcp_discovery_future is not None
            # Switch away: must not await a running background discovery.
            await asyncio.wait_for(agent.deactivate_async(), timeout=2)
            assert agent._mcp_discovery_future is None
            assert agent._defer_tool_registration is False
            return agent

        agent = asyncio.run(scenario())
        baseline = len(sync_calls)
        blocked.release.set()  # discovery completes AFTER deactivation
        time.sleep(0.2)  # give the worker a moment
        assert len(sync_calls) == baseline  # stale completion must not sync


# ---------------------------------------------------------------------------
# Stale registration guard (real manager/service registration path)
# ---------------------------------------------------------------------------


def _fake_server_config():
    # Fake MCP server config exposing only what registration reads.
    return SimpleNamespace(name="s1", includeTools=None)


def _make_real_manager():
    """A real MCPSessionManager with one enabled fake server config."""
    manager = MCPSessionManager()
    manager.initialized = True
    manager.config_manager = SimpleNamespace(
        load_config=lambda: None,
        configs={"s1": _fake_server_config()},
        get_enabled_servers=lambda agent_name: {"s1": _fake_server_config()},
    )
    return manager


def _make_agent_with_builtin(name, manager, monkeypatch):
    """Agent whose ``register_tools`` (re)adds one fake built-in tool.

    Mirrors real activation: deactivation clears all tool definitions and a
    later activation re-registers built-ins before MCP discovery.
    """

    def _register_builtin():
        agent.register_tool(
            lambda: {
                "name": "builtin_tool",
                "description": "built-in test tool",
                "input_schema": {"type": "object"},
            },
            lambda *a, **k: None,
        )

    agent = _build_agent(name, monkeypatch, manager)
    monkeypatch.setattr(agent, "register_tools", _register_builtin)
    _register_builtin()
    return agent


class TestStaleRegistrationGuard:
    """Background discovery must never repopulate a deactivated agent.

    Uses the real manager/service registration path with discovery gated
    before registry mutation so both race orderings are deterministic.
    """

    def _register_in_manager(self, agent):
        agent_manager = AgentManager()
        agent_manager.register_agent(agent)
        return agent_manager

    def _gated_tool_discovery(self, gate):
        async def gated_discover(server_config, agent_name):
            gate.wait(timeout=10)
            return [
                Tool(
                    name="mcp_tool",
                    description="mcp test tool",
                    input_schema={"type": "object"},
                )
            ]

        return gated_discover

    def test_stale_background_completion_skips_inactive_agent(self, monkeypatch):
        """Deactivation wins: a stale completion must not mutate the inactive agent."""
        manager = _make_real_manager()
        service = manager.mcp_service
        gate = threading.Event()
        monkeypatch.setattr(
            service, "discover_server_tools", self._gated_tool_discovery(gate)
        )
        agent = _make_agent_with_builtin("TestAgent", manager, monkeypatch)
        agent_manager = self._register_in_manager(agent)
        try:
            future = None

            async def scenario():
                nonlocal future
                await agent.activate_async()
                assert agent.is_active is True
                future = agent._mcp_discovery_future
                assert future is not None and not future.done()
                # Switch away while discovery is still blocked.
                await asyncio.wait_for(agent.deactivate_async(), timeout=2)
                assert agent._mcp_discovery_future is None
                return agent

            agent = asyncio.run(scenario())
            gate.set()
            assert future.result(timeout=5) is None

            # The stale completion must not repopulate the inactive agent.
            assert agent.tool_definitions == {}
            assert agent.mcp_resources == {}
            assert agent.mcps_loading == []
            assert agent.llm.registered == []
        finally:
            agent_manager._instance = None

    def test_reactivation_does_not_expose_stale_mcp_tools(self, monkeypatch):
        """Stale MCP tools never appear in a reactivation's immediate sync."""
        manager = _make_real_manager()
        service = manager.mcp_service
        gate = threading.Event()
        monkeypatch.setattr(
            service, "discover_server_tools", self._gated_tool_discovery(gate)
        )
        agent = _make_agent_with_builtin("TestAgent", manager, monkeypatch)
        agent_manager = self._register_in_manager(agent)
        try:
            # Phase 1: activate, switch away while discovery is blocked, then
            # release the stale worker after deactivation.
            future1 = None

            async def phase_one():
                nonlocal future1
                await agent.activate_async()
                future1 = agent._mcp_discovery_future
                await agent.deactivate_async()
                return agent

            agent = asyncio.run(phase_one())
            gate.set()
            assert future1.result(timeout=5) is None
            assert agent.tool_definitions == {}

            # Phase 2: reactivation — the immediate built-in sync must not
            # include the stale MCP tool; fresh discovery still registers it.
            future2 = None

            async def phase_two():
                nonlocal future2
                await agent.activate_async()
                assert agent.llm.registered == ["builtin_tool"]
                future2 = agent._mcp_discovery_future
                return agent

            agent = asyncio.run(phase_two())
            assert future2.result(timeout=5) is None
            assert any("mcp_tool" in name for name in agent.tool_definitions)

            # Final sync on the first message includes built-ins and MCP tools.
            async def first_message():
                _make_preprocess_ready(agent, monkeypatch)
                result = await agent.pre_process_message([])
                assert result is not None
                return agent.llm.registered

            registered = asyncio.run(first_message())
            assert "builtin_tool" in registered
            assert any("mcp_tool" in name for name in registered)
            assert agent._defer_tool_registration is False
        finally:
            agent_manager._instance = None

    def test_registration_wins_lock_then_deactivation_clears(self, monkeypatch):
        """Registration wins the lock first: deactivation then clears all MCP entries."""
        manager = _make_real_manager()
        service = manager.mcp_service
        gate = threading.Event()
        monkeypatch.setattr(
            service, "discover_server_tools", self._gated_tool_discovery(gate)
        )
        agent = _make_agent_with_builtin("TestAgent", manager, monkeypatch)
        agent_manager = self._register_in_manager(agent)

        arrived = threading.Event()
        release = threading.Event()
        original_register_tool = agent.register_tool

        def blocking_register_tool(
            definition_func, handler_factory, service_instance=None
        ):
            # Block only the MCP tool registration (background worker), not
            # the built-in registration performed during activation.
            tool_def = (
                definition_func() if callable(definition_func) else definition_func
            )
            name = tool_def.get("name") if isinstance(tool_def, dict) else None
            if name is None and isinstance(tool_def.get("function"), dict):
                name = tool_def["function"].get("name")
            if name and "mcp_tool" in name:
                arrived.set()
                release.wait(timeout=10)
            return original_register_tool(
                definition_func, handler_factory, service_instance
            )

        monkeypatch.setattr(agent, "register_tool", blocking_register_tool)
        try:
            future = None

            async def scenario():
                nonlocal future
                await agent.activate_async()
                future = agent._mcp_discovery_future
                return agent

            agent = asyncio.run(scenario())
            gate.set()
            # The worker is now inside the registry lock, mid-mutation.
            assert arrived.wait(timeout=5)

            result = {}

            def do_deactivate():
                result["value"] = asyncio.run(agent.deactivate_async())

            deact_thread = threading.Thread(target=do_deactivate, daemon=True)
            deact_thread.start()
            time.sleep(0.2)
            assert deact_thread.is_alive()  # waiting on the registry lock

            release.set()
            deact_thread.join(timeout=5)
            assert not deact_thread.is_alive()
            assert result.get("value") is True
            assert future.result(timeout=5) is None

            # Registration mutated first; deactivation then cleared everything.
            assert agent.tool_definitions == {}
            assert agent.mcp_resources == {}
            assert agent.mcps_loading == []
            assert agent.llm.registered == []
        finally:
            agent_manager._instance = None
