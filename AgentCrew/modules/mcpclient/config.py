import json
import os
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class MCPOAuthOverrideConfig:
    """Normalized optional OAuth override configuration for an MCP server."""

    tokens: OAuthToken | None = None
    client_info: OAuthClientInformationFull | None = None


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: list[str]
    enabledForAgents: list[str]
    env: dict[str, str] | None = None
    streaming_server: bool = False
    url: str = ""
    headers: dict[str, str] | None = None
    includeTools: list[str] | None = None
    oauth: MCPOAuthOverrideConfig | None = None


class MCPConfigManager:
    """Manager for MCP server configurations."""

    def __init__(self, config_path: str | None = None):
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
        self.configs: dict[str, MCPServerConfig] = {}

    def _normalize_include_tools(
        self, include_tools: Any, server_id: str
    ) -> list[str] | None:
        """Normalize the optional MCP includeTools setting."""
        if include_tools is None:
            return None

        if not isinstance(include_tools, list):
            logger.warning(
                f"Invalid includeTools for MCP server '{server_id}': expected a list, got {type(include_tools).__name__}. Ignoring filter."
            )
            return None

        normalized_tools: list[str] = []
        seen_tools = set()
        for tool_name in include_tools:
            normalized_name = str(tool_name).strip()
            if not normalized_name or normalized_name in seen_tools:
                continue
            normalized_tools.append(normalized_name)
            seen_tools.add(normalized_name)

        return normalized_tools or None

    def _normalize_oauth(
        self, oauth_config: Any, server_id: str
    ) -> MCPOAuthOverrideConfig | None:
        """Normalize the optional MCP OAuth override setting."""
        if oauth_config is None:
            return None

        if not isinstance(oauth_config, dict):
            logger.warning(
                f"Invalid oauth override for MCP server '{server_id}': expected an object, got {type(oauth_config).__name__}. Ignoring override."
            )
            return None

        tokens_override: OAuthToken | None = None
        client_info_override: OAuthClientInformationFull | None = None

        raw_tokens = oauth_config.get("tokens")
        if raw_tokens is not None:
            if not isinstance(raw_tokens, dict):
                logger.warning(
                    f"Invalid oauth.tokens for MCP server '{server_id}': expected an object, got {type(raw_tokens).__name__}. Ignoring tokens override."
                )
            else:
                normalized_tokens = dict(raw_tokens)
                expires_at = normalized_tokens.get("expires_at")
                if (
                    expires_at is not None
                    and normalized_tokens.get("expires_in") is None
                ):
                    try:
                        expires_at_value = float(expires_at)
                        now_ms = time.time() * 1000
                        remaining_seconds = max(
                            0, int((expires_at_value - now_ms + 999) // 1000)
                        )
                        normalized_tokens["expires_in"] = remaining_seconds
                    except (TypeError, ValueError):
                        logger.warning(
                            f"Invalid oauth.tokens.expires_at for MCP server '{server_id}'. Ignoring expires_at value."
                        )
                try:
                    tokens_override = OAuthToken.model_validate(normalized_tokens)
                except Exception as exc:
                    logger.warning(
                        f"Invalid oauth.tokens for MCP server '{server_id}'. Ignoring tokens override: {exc}"
                    )

        raw_client_info = oauth_config.get("client_info")
        if raw_client_info is not None:
            if not isinstance(raw_client_info, dict):
                logger.warning(
                    f"Invalid oauth.client_info for MCP server '{server_id}': expected an object, got {type(raw_client_info).__name__}. Ignoring client info override."
                )
            else:
                try:
                    client_info_override = OAuthClientInformationFull.model_validate(
                        raw_client_info
                    )
                except Exception as exc:
                    logger.warning(
                        f"Invalid oauth.client_info for MCP server '{server_id}'. Ignoring client info override: {exc}"
                    )

        if not tokens_override and not client_info_override:
            return None

        return MCPOAuthOverrideConfig(
            tokens=tokens_override,
            client_info=client_info_override,
        )

    def load_config(self) -> dict[str, MCPServerConfig]:
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
                    includeTools=self._normalize_include_tools(
                        config.get("includeTools"), server_id
                    ),
                    oauth=self._normalize_oauth(config.get("oauth"), server_id),
                )

            return self.configs
        except Exception as e:
            logger.error(f"Error loading MCP configuration: {e}")
            return {}

    def get_enabled_servers(
        self, agent_name: str | None = None
    ) -> dict[str, MCPServerConfig]:
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
