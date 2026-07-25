from loguru import logger

from .config import MCPConfigManager
from .service import MCPService


class MCPSessionManager:
    """Manager for MCP sessions and server connections (stateless).

    No persistent event loop thread. Tool definitions are discovered once
    during agent activation (brief connect → list → disconnect) and cached.
    Each tool call creates a temporary MCP session, executes, and tears down.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """Get the singleton instance of the session manager."""
        if cls._instance is None:
            cls._instance = MCPSessionManager()
        return cls._instance

    @classmethod
    def force_new_instance(cls):
        cls._instance = MCPSessionManager()
        return cls._instance

    def __init__(self):
        """Initialize the session manager."""
        self.config_manager = MCPConfigManager()
        self.mcp_service = MCPService()
        self.mcp_service._config_manager = self.config_manager
        self.initialized = False

    def initialize(self) -> None:
        """Initialize the MCP session manager (no thread/loop needed)."""
        if self.initialized:
            logger.info("MCPSessionManager: Already initialized.")
            return

        logger.info("MCPSessionManager: Initializing...")
        self.config_manager.load_config()
        self.initialized = True
        logger.info("MCPSessionManager: Initialization complete.")

    async def discover_mcps_for_agent(self, agent_name: str | None = None) -> None:
        """Discover and register MCP tools, resources, and prompts for an agent (stateless).

        Briefly connects to each enabled server, discovers tool schemas,
        resources, and prompts, caches them, and registers lazy tool
        handlers on the agent. Connections are torn down after discovery.
        """
        if not self.initialized:
            logger.error("MCPSessionManager: Has not initialized.")
            return

        self.config_manager.load_config()
        enabled_servers = self.config_manager.get_enabled_servers(agent_name)

        if not enabled_servers:
            logger.info(
                "MCPSessionManager: No enabled MCP servers found in configuration."
            )
            return

        logger.info(
            f"MCPSessionManager: Discovering MCPs from {len(enabled_servers)} "
            f"enabled MCP servers for agent '{agent_name}'..."
        )

        await self.deregister_tools_for_agent(agent_name)

        for config in enabled_servers.values():
            target_agents = self.mcp_service._target_agent_names(agent_name, config)
            for target_agent_name in target_agents:
                try:
                    await self.mcp_service.register_tools_for_agent(
                        config, target_agent_name
                    )
                except Exception as e:
                    logger.error(
                        f"MCPSessionManager: Failed to register tools from "
                        f"'{config.name}' for '{target_agent_name}': {e}"
                    )

        logger.info(
            "MCPSessionManager: Finished discovering MCPs for all enabled servers."
        )

    def discover_mcps_for_agent_background(self, agent_name: str) -> None:
        """Start MCP discovery in a background thread (non-blocking).

        ``register_tools_for_agent`` internally checks ``tools_cache`` and
        skips MCP connections when cached definitions are available, so
        re-activation is fast even though the thread always starts.

        Args:
            agent_name: Name of the agent to discover MCPs for
        """
        if not self.initialized:
            return

        self.config_manager.load_config()
        enabled_servers = self.config_manager.get_enabled_servers(agent_name)

        if not enabled_servers:
            return

        import threading

        def _run_discovery():
            import asyncio

            asyncio.run(self.discover_mcps_for_agent(agent_name))

        thread = threading.Thread(target=_run_discovery, daemon=True)
        thread.start()

    async def deregister_tools_for_agent(self, agent_name: str | None = None) -> None:
        """Deregister MCP tools for an agent (no server shutdown needed)."""
        if not self.initialized:
            return

        if not self.config_manager.configs:
            return

        enabled_servers = self.config_manager.get_enabled_servers(agent_name)

        for config in enabled_servers.values():
            target_agents = self.mcp_service._target_agent_names(agent_name, config)
            for target_agent_name in target_agents:
                try:
                    await self.mcp_service.deregister_server_tools(
                        config.name, target_agent_name
                    )
                except Exception as e:
                    logger.error(f"MCPSessionManager: Error deregistering tools: {e}")

    def cleanup(self):
        """Clean up all resources (no thread/loop to stop)."""
        self.initialized = False
        logger.info("MCPSessionManager: Cleanup complete.")
