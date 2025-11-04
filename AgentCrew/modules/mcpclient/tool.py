from AgentCrew.modules.mcpclient import MCPSessionManager
from loguru import logger


def register(
    service_instance=None, agent=None
):  # agent parameter is kept for compatibility but not used for global MCP tools
    """
    Register all MCP tools with the global tool registry.

    Args:
        service_instance: Not used for MCP tools, but included for consistency
        agent: Agent instance to register with directly (optional)

    This function should beCalled during application initialization.
    """
    mcp_manager = MCPSessionManager.get_instance()
    if not mcp_manager.initialized:
        logger.info(
            "MCP Tools: MCPSessionManager not initialized by main flow, initializing now."
        )
        mcp_manager.initialize()

    logger.info("MCP Tools registered.")
