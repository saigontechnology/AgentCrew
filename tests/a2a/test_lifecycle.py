"""
Tests for centralized application teardown: ApplicationSetup.shutdown(),
RemoteAgent cleanup, and application mode finally-block wiring.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from AgentCrew.modules.agents.manager import AgentManager
from AgentCrew.modules.agents.remote_agent import RemoteAgent


class TestApplicationSetupShutdown:
    """Verify ApplicationSetup.shutdown() behavior."""

    def test_shutdown_closes_plugins_and_remote_agents(self):
        """shutdown() calls shutdown_plugins() then close_all_remote_agents()."""
        from AgentCrew.modules.config import ConfigManagement
        from AgentCrew.setup import ApplicationSetup

        setup = ApplicationSetup(config_manager=ConfigManagement())

        call_order = []

        async def mock_shutdown_plugins():
            call_order.append("plugins")

        async def mock_close_remote():
            call_order.append("remote")

        setup.shutdown_plugins = mock_shutdown_plugins  # type: ignore
        setup.agent_manager = MagicMock(spec=AgentManager)
        setup.agent_manager.close_all_remote_agents = mock_close_remote  # type: ignore

        asyncio.run(setup.shutdown())

        assert call_order == ["plugins", "remote"]

    def test_remote_agents_closed_even_if_plugins_fail(self):
        """If shutdown_plugins() raises, remote agents are still closed."""
        from AgentCrew.modules.config import ConfigManagement
        from AgentCrew.setup import ApplicationSetup

        setup = ApplicationSetup(config_manager=ConfigManagement())

        async def failing_shutdown_plugins():
            raise RuntimeError("plugin unload failed")

        setup.shutdown_plugins = failing_shutdown_plugins  # type: ignore
        setup.agent_manager = MagicMock(spec=AgentManager)
        setup.agent_manager.close_all_remote_agents = AsyncMock()  # type: ignore

        with pytest.raises(RuntimeError, match="plugin unload failed"):
            asyncio.run(setup.shutdown())

        setup.agent_manager.close_all_remote_agents.assert_called_once()

    def test_shutdown_noop_when_agent_manager_is_none(self):
        """shutdown() does nothing with agent_manager if it is None."""
        from AgentCrew.modules.config import ConfigManagement
        from AgentCrew.setup import ApplicationSetup

        setup = ApplicationSetup(config_manager=ConfigManagement())
        setup.agent_manager = None

        async def mock_shutdown_plugins():
            pass

        setup.shutdown_plugins = mock_shutdown_plugins  # type: ignore

        # Should not raise
        asyncio.run(setup.shutdown())

    def test_shutdown_plugins_called_exactly_once(self):
        """shutdown() calls shutdown_plugins exactly once."""
        from AgentCrew.modules.config import ConfigManagement
        from AgentCrew.setup import ApplicationSetup

        setup = ApplicationSetup(config_manager=ConfigManagement())
        plugin_call_count = 0

        async def mock_shutdown_plugins():
            nonlocal plugin_call_count
            plugin_call_count += 1

        setup.shutdown_plugins = mock_shutdown_plugins  # type: ignore
        setup.agent_manager = MagicMock(spec=AgentManager)
        setup.agent_manager.close_all_remote_agents = AsyncMock()  # type: ignore

        asyncio.run(setup.shutdown())

        assert plugin_call_count == 1


class TestRemoteAgentCloseIdempotency:
    """Verify RemoteAgent.close() is safe to call multiple times."""

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """Calling close() multiple times does not raise."""
        agent = RemoteAgent.__new__(RemoteAgent)
        agent.name = "test-remote"
        agent.agent_url = "http://localhost:9999"
        agent.headers = {}
        agent._client = None
        agent._client_own_http = None

        # Call close multiple times — should not raise
        await agent.close()
        await agent.close()

    @pytest.mark.asyncio
    async def test_close_closes_http_client_if_present(self):
        """close() closes the HTTP client if it exists."""
        mock_httpx = AsyncMock()
        agent = RemoteAgent.__new__(RemoteAgent)
        agent.name = "test-remote"
        agent.agent_url = "http://localhost:9999"
        agent.headers = {}
        agent._client = None
        agent._client_own_http = mock_httpx

        await agent.close()

        mock_httpx.aclose.assert_called_once()
        assert agent._client_own_http is None

    @pytest.mark.asyncio
    async def test_close_logs_and_continues_on_exception(self):
        """close_all_remote_agents() logs but continues if one agent's close fails."""
        mgr = AgentManager()
        good_remote = AsyncMock(spec=RemoteAgent)
        good_remote.name = "good"
        good_remote.close = AsyncMock()
        bad_remote = AsyncMock(spec=RemoteAgent)
        bad_remote.name = "bad"
        bad_remote.close = AsyncMock(side_effect=RuntimeError("bad close"))
        mgr.agents = {"good": good_remote, "bad": bad_remote}

        # close_all_remote_agents catches exceptions and continues
        await mgr.close_all_remote_agents()

        good_remote.close.assert_called_once()
        bad_remote.close.assert_called_once()


class TestAgentManagerCloseAll:
    """Verify AgentManager.close_all_remote_agents()."""

    @pytest.mark.asyncio
    async def test_closes_all_remote_agents(self):
        """Closes all RemoteAgent instances in the manager."""
        mgr = AgentManager()
        # Use fresh dict for testing
        mock_remote1 = AsyncMock(spec=RemoteAgent)
        mock_remote1.name = "remote1"
        mock_remote1.close = AsyncMock()
        mock_remote2 = AsyncMock(spec=RemoteAgent)
        mock_remote2.name = "remote2"
        mock_remote2.close = AsyncMock()
        mgr.agents = {"r1": mock_remote1, "r2": mock_remote2}

        await mgr.close_all_remote_agents()

        mock_remote1.close.assert_called_once()
        mock_remote2.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_exception_does_not_block_others(self):
        """If one remote agent fails to close, others still close."""
        mgr = AgentManager()
        good_remote = AsyncMock(spec=RemoteAgent)
        good_remote.name = "good"
        good_remote.close = AsyncMock()
        bad_remote = AsyncMock(spec=RemoteAgent)
        bad_remote.name = "bad"
        bad_remote.close = AsyncMock(side_effect=RuntimeError("bad close"))
        mgr.agents = {"good": good_remote, "bad": bad_remote}

        await mgr.close_all_remote_agents()

        good_remote.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_remote_agents_ignored(self):
        """Non-RemoteAgent agents are ignored."""
        mgr = AgentManager()
        local_mock = MagicMock()
        local_mock.name = "local"
        mgr.agents = {"local": local_mock}

        # Should not raise
        await mgr.close_all_remote_agents()

    @pytest.mark.asyncio
    async def test_noop_when_no_agents(self):
        """No-op when agents dict is empty."""
        mgr = AgentManager()
        mgr.agents = {}

        await mgr.close_all_remote_agents()


class TestApplicationModeWiring:
    """Verify AgentCrewApplication modes call setup.shutdown() in finally."""

    def test_run_server_finally_calls_shutdown(self):
        """run_server finally block calls setup.shutdown() then MCP cleanup."""
        import inspect

        from AgentCrew.app import AgentCrewApplication

        # Verify run_server source contains shutdown call
        source = inspect.getsource(AgentCrewApplication.run_server)
        assert "self.setup.shutdown()" in source
        assert "MCPSessionManager.get_instance().cleanup()" in source

    def test_run_console_finally_calls_shutdown(self):
        """run_console finally block calls setup.shutdown() then MCP cleanup."""
        import inspect

        from AgentCrew.app import AgentCrewApplication

        source = inspect.getsource(AgentCrewApplication.run_console)
        assert "self.setup.shutdown()" in source
        assert "MCPSessionManager.get_instance().cleanup()" in source

    def test_run_acp_finally_calls_shutdown(self):
        """run_acp finally block calls setup.shutdown() then MCP cleanup."""
        import inspect

        from AgentCrew.app import AgentCrewApplication

        source = inspect.getsource(AgentCrewApplication.run_acp)
        assert "self.setup.shutdown()" in source
        assert "MCPSessionManager.get_instance().cleanup()" in source

    def test_run_job_finally_calls_shutdown(self):
        """run_job finally block calls setup.shutdown() then MCP cleanup."""
        import inspect

        from AgentCrew.app import AgentCrewApplication

        source = inspect.getsource(AgentCrewApplication.run_job)
        assert "self.setup.shutdown()" in source
        assert "MCPSessionManager.get_instance().cleanup()" in source

    def test_run_gui_finally_calls_shutdown(self):
        """run_gui finally block calls setup.shutdown() then MCP cleanup."""
        import inspect

        from AgentCrew.app import AgentCrewApplication

        source = inspect.getsource(AgentCrewApplication.run_gui)
        assert "self.setup.shutdown()" in source
        assert "MCPSessionManager.get_instance().cleanup()" in source


class TestRemoteAgentHttpClientBoundary:
    """Documents the accepted httpx -> httpx2 residual risk at the a2a-sdk
    boundary: the client handed to ClientConfig is httpx2, while a2a-sdk types
    and catches httpx exceptions, so httpx2 transport errors bypass the SDK's
    A2AClientError conversion."""

    def test_client_config_uses_httpx2_client_not_httpx(self):
        import httpx
        import httpx2
        from a2a.client import ClientConfig

        async def run():
            client = httpx2.AsyncClient()
            try:
                config = ClientConfig(httpx_client=client)
                assert isinstance(config.httpx_client, httpx2.AsyncClient)
                assert not isinstance(config.httpx_client, httpx.AsyncClient)
            finally:
                await client.aclose()

        asyncio.run(run())
