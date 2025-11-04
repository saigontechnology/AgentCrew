from typing import Optional
from .config import MCPConfigManager
from .service import MCPService
from loguru import logger


class MCPSessionManager:
    """Manager for MCP sessions and server connections."""

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
        self.initialized = False

    def initialize(self) -> None:
        """Synchronous initialization method to start MCPService and initiate server connections."""
        if self.initialized:
            logger.info("MCPSessionManager: Already initialized.")
            return

        logger.info("MCPSessionManager: Initializing...")
        # Start the MCP service's event loop thread
        self.mcp_service.start()

        # Initialize server connections asynchronously on the mcp_service's loop
        # This call will block until initialize_servers_async is scheduled and returns.
        # The actual connections will happen in background tasks within MCPService.
        try:
            # self.mcp_service._run_async(self.initialize_servers_async())
            self.initialized = True
            logger.info("MCPSessionManager: Initialization process started.")
        except Exception as e:
            logger.error(f"MCPSessionManager: Error during async initialization: {e}")
            # Potentially stop mcp_service if its start was successful but async part failed
            self.mcp_service.stop()
            self.initialized = False  # Ensure it's marked as not initialized

    def initialize_for_agent(self, agent_name: Optional[str] = None) -> None:
        if not self.initialized:
            logger.error("MCPSessionManager: Has not initialized.")
            return

        try:
            self.mcp_service._run_async(
                self.mcp_service.shutdown_all_server_connections(agent_name)
            )
            self.mcp_service._run_async(self.initialize_servers_async(agent_name))
            logger.info("MCPSessionManager: Initialization process started.")
        except Exception as e:
            logger.error(f"MCPSessionManager: Error during async initialization: {e}")

    async def initialize_servers_async(self, agent_name: Optional[str] = None) -> None:
        """
        Asynchronously starts the connection management for all enabled MCP servers.
        This method is intended to be run on the MCPService's event loop.
        """
        # Load server configurations
        self.config_manager.load_config()  # Ensure configs are loaded
        enabled_servers = self.config_manager.get_enabled_servers(agent_name)

        if not enabled_servers:
            logger.info(
                "MCPSessionManager: No enabled MCP servers found in configuration."
            )
            return

        logger.info(
            f"MCPSessionManager: Found {len(enabled_servers)} enabled MCP servers. Requesting MCPService to manage connections..."
        )

        for server_id, config in enabled_servers.items():
            logger.info(
                f"MCPSessionManager: Requesting MCPService to manage connection for {server_id}"
            )
            # This is an async call. Since this method itself runs on the MCPService's loop (via _run_async),
            # we can directly await it.
            await self.mcp_service.start_server_connection_management(
                config, agent_name
            )

        logger.info(
            "MCPSessionManager: Finished requesting connection management for all enabled servers."
        )
        # Note: Actual connections are managed by background tasks in MCPService.

    def cleanup_for_agent(self, agent_name: str):
        if not self.initialized:
            logger.error("MCPSessionManager: Has not initialized.")
            return

        try:
            logger.info("MCPSessionManager: Starting cleanup...")
            self.mcp_service._run_async(
                self.mcp_service.shutdown_all_server_connections(agent_name)
            )
        except Exception as e:
            logger.error(
                f"MCPSessionManager: Error during cleanup_for_agent {agent_name}: {e}"
            )
        finally:
            logger.info("MCPSessionManager: Cleanup complete.")

    def cleanup(self):
        """Clean up all resources, including stopping MCP service and connections."""
        if not self.initialized and not (
            hasattr(self.mcp_service, "loop_thread")
            and self.mcp_service.loop_thread.is_alive()
        ):
            logger.info(
                "MCPSessionManager: Cleanup called but not initialized or service not running. Skipping."
            )
            return

        logger.info("MCPSessionManager: Starting cleanup...")
        try:
            # Signal all server connections to shut down and wait for them.
            # This needs to run on the MCPService's event loop.
            if hasattr(self.mcp_service, "loop") and self.mcp_service.loop.is_running():
                logger.info(
                    "MCPSessionManager: Running shutdown_all_server_connections on MCPService loop."
                )
                self.mcp_service._run_async(
                    self.mcp_service.shutdown_all_server_connections()
                )
            elif (
                hasattr(self.mcp_service, "loop_thread")
                and self.mcp_service.loop_thread.is_alive()
            ):
                logger.warning(
                    "MCPSessionManager: MCPService loop not running but thread alive. Attempting async shutdown."
                )
                # This case is tricky, _run_async might fail if loop isn't running.
                # However, shutdown_all_server_connections primarily sets events and gathers tasks
                # that might still be on a non-closed loop.
                try:
                    self.mcp_service._run_async(
                        self.mcp_service.shutdown_all_server_connections()
                    )
                except Exception as e_async_shutdown:
                    logger.error(
                        f"MCPSessionManager: Exception during _run_async for shutdown: {e_async_shutdown}"
                    )
            else:
                logger.warning(
                    "MCPSessionManager: MCPService loop not running/thread not alive, cannot run async shutdown. Resources might not be fully cleaned."
                )

            # Stop the MCPService's event loop and thread
            logger.info("MCPSessionManager: Stopping MCPService.")
            self.mcp_service.stop()  # This will join the thread

        except Exception:
            logger.exception("MCPSessionManager: Error during cleanup")
        finally:
            self.initialized = False
            logger.info("MCPSessionManager: Cleanup complete.")
