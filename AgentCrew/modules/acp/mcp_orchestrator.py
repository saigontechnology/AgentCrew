from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from AgentCrew.modules.acp.mcp import normalize_acp_mcp_servers
from AgentCrew.modules.acp.session_state import AcpSessionState
from AgentCrew.modules.mcpclient import MCPSessionManager

if TYPE_CHECKING:
    from AgentCrew.modules.agents import AgentManager


class McpOrchestrator:
    def __init__(self, agent_manager: AgentManager):
        self._agent_manager = agent_manager

    async def setup_session_mcp_servers(
        self,
        session_id: str,
        state: AcpSessionState,
        mcp_servers: list[Any] | None,
    ):
        configs = normalize_acp_mcp_servers(session_id, state.agent_name, mcp_servers)
        if not configs:
            return
        await self.cleanup_session_mcp_servers(state, clear_configs=True)
        state.acp_mcp_server_configs = configs
        await self.start_session_mcp_configs(state)

    async def start_session_mcp_configs(self, state: AcpSessionState):
        """Register ACP-provided MCP server tools (stateless discovery)."""
        mcp_manager = MCPSessionManager.get_instance()
        if not mcp_manager.initialized:
            mcp_manager.initialize()

        service = mcp_manager.mcp_service
        active_configs: list[Any] = []

        for config in list(state.acp_mcp_server_configs):
            try:
                service._acp_server_configs[config.name] = config
                await service.register_tools_for_agent(config, state.agent_name)
                active_configs.append(config)
            except Exception:
                logger.exception(
                    f"ACP MCP server '{config.name}' failed during discovery"
                )
                await self.cleanup_single_mcp_server(
                    service, config.name, state.agent_name, ""
                )

        state.acp_mcp_server_configs = active_configs
        state.acp_mcp_server_ids = []

    async def cleanup_single_mcp_server(
        self, service: Any, server_name: str, agent_name: str, combined_id: str
    ):
        """Deregister tools for a failed ACP MCP server (no server shutdown needed)."""
        try:
            await service.deregister_server_tools(server_name, agent_name)
        except Exception:
            logger.exception("Error deregistering failed ACP MCP server tools")
        service._acp_server_configs.pop(server_name, None)

    async def cleanup_session_mcp_servers(
        self, state: AcpSessionState, clear_configs: bool = False
    ):
        """Deregister all ACP MCP server tools for a session."""
        if not state.acp_mcp_server_configs:
            return

        mcp_manager = MCPSessionManager.get_instance()
        service = mcp_manager.mcp_service
        for config in list(state.acp_mcp_server_configs):
            try:
                await service.deregister_server_tools(config.name, state.agent_name)
            except Exception:
                logger.exception("Error deregistering ACP MCP server tools")
            service._acp_server_configs.pop(config.name, None)

        state.acp_mcp_server_ids.clear()
        if clear_configs:
            state.acp_mcp_server_configs.clear()
