import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: List[str]
    enabledForAgents: List[str]
    env: Optional[Dict[str, str]] = None
    streaming_server: bool = False
    url: str = ""
    headers: Optional[Dict[str, str]] = None


class MCPConfigManager:
    """Manager for MCP server configurations."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the configuration manager.

        Args:
            config_path: Path to the configuration file. If None, uses the default path.
        """
        self.config_path = config_path or os.environ.get(
            "MCP_CONFIG_PATH",
            os.path.join(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                ),
                "mcp_servers.json",
            ),
        )
        self.configs: Dict[str, MCPServerConfig] = {}

    def load_config(self) -> Dict[str, MCPServerConfig]:
        """
        Load server configurations from the config file.

        Returns:
            Dictionary of server configurations keyed by server ID.
        """
        try:
            if not os.path.exists(self.config_path):
                # Create default config directory if it doesn't exist
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
                # Create empty config file
                with open(self.config_path, "w") as f:
                    json.dump({}, f)
                return {}

            with open(self.config_path, "r") as f:
                config_data = json.load(f)

            self.configs = {}
            for server_id, config in config_data.items():
                self.configs[server_id] = MCPServerConfig(
                    name=config.get("name", server_id),
                    command=config.get("command", ""),
                    args=config.get("args", []),
                    env=config.get("env"),
                    enabledForAgents=config.get("enabledForAgents", []),
                    streaming_server=config.get("streaming_server", False),
                    url=config.get("url", ""),
                    headers=config.get("headers"),
                )

            return self.configs
        except Exception as e:
            logger.error(f"Error loading MCP configuration: {e}")
            return {}

    def get_enabled_servers(
        self, agent_name: Optional[str] = None
    ) -> Dict[str, MCPServerConfig]:
        """
        Get all enabled server configurations.

        Returns:
            Dictionary of enabled server configurations.
        """
        if agent_name:
            return {
                server_id: config
                for server_id, config in self.configs.items()
                if agent_name in config.enabledForAgents
            }

        return {
            server_id: config
            for server_id, config in self.configs.items()
            if len(config.enabledForAgents) > 0
        }
