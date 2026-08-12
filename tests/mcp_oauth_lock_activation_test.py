"""Unit tests for the cross-event-loop OAuth lock and async agent activation fix.

Covers:
- ``MCPService._get_oauth_lock``: locks are scoped per server per event loop
  so a cached ``asyncio.Lock`` can never be bound to a foreign loop.
- ``LocalAgent.activate_async``: runs MCP discovery natively on the current
  event loop as a background task instead of spawning a thread with
  ``asyncio.run()``.
"""

import asyncio
import threading
from types import SimpleNamespace

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.mcpclient import MCPSessionManager
from AgentCrew.modules.mcpclient.service import MCPService


class FakeLLM:
    """Minimal LLM double supporting the attributes activation touches."""

    def __init__(self):
        self.temperature = None
        self.system_prompt = None

    def set_system_prompt(self, prompt):
        self.system_prompt = prompt


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
    def _build_agent(self, fake_manager, monkeypatch):
        agent = LocalAgent(
            name="TestAgent",
            description="A test agent",
            llm_service=FakeLLM(),
            services={},
            tools=[],
        )
        monkeypatch.setattr(agent, "register_tools", lambda: None)
        monkeypatch.setattr(MCPSessionManager, "get_instance", lambda: fake_manager)
        return agent

    def test_activate_async_runs_discovery_on_current_loop(self, monkeypatch):
        captured = {}

        class FakeManager:
            initialized = True

            async def discover_mcps_for_agent(self, agent_name):
                captured["loop"] = asyncio.get_running_loop()
                captured["thread"] = threading.current_thread()
                captured["agent_name"] = agent_name

        agent = self._build_agent(FakeManager(), monkeypatch)
        scenario_loop = None

        async def scenario():
            nonlocal scenario_loop
            scenario_loop = asyncio.get_running_loop()
            result = await agent.activate_async()
            assert result is True
            assert agent.is_active is True
            assert agent.llm.temperature == 0.4
            assert agent.llm.system_prompt is not None
            assert agent._mcp_discovery_task is not None
            await agent._mcp_discovery_task

        asyncio.run(scenario())

        # Discovery ran on the caller's loop and thread, not on a loop
        # created inside a spawned thread.
        assert captured["agent_name"] == "TestAgent"
        assert captured["loop"] is scenario_loop
        assert captured["thread"] is threading.main_thread()

    def test_activate_async_without_initialized_manager(self, monkeypatch):
        agent = self._build_agent(SimpleNamespace(initialized=False), monkeypatch)

        async def scenario():
            return await agent.activate_async()

        assert asyncio.run(scenario()) is True
        assert agent._mcp_discovery_task is None

    def test_activate_async_discovery_failure_is_swallowed(self, monkeypatch):
        class FailingManager:
            initialized = True

            async def discover_mcps_for_agent(self, agent_name):
                raise RuntimeError("boom")

        agent = self._build_agent(FailingManager(), monkeypatch)

        async def scenario():
            result = await agent.activate_async()
            assert result is True
            # The wrapped background task must not raise an unhandled
            # exception.
            await agent._mcp_discovery_task

        asyncio.run(scenario())

    def test_activate_async_returns_false_without_llm(self, monkeypatch):
        agent = self._build_agent(SimpleNamespace(initialized=False), monkeypatch)
        agent.llm = None

        async def scenario():
            return await agent.activate_async()

        assert asyncio.run(scenario()) is False
