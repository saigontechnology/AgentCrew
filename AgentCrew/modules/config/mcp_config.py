import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerEntry:
    """Type-safe representation of a single MCP server entry in mcp_servers.json."""

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    enabled_for_agents: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    streaming_server: bool = False
    url: str = ""
    headers: dict[str, str] | None = None
    include_tools: list[str] | None = None

    @classmethod
    def from_dict(cls, server_id: str, data: dict[str, Any]) -> "MCPServerEntry":
        return cls(
            name=data.get("name", server_id),
            command=data.get("command", ""),
            args=data.get("args", []),
            enabled_for_agents=data.get("enabledForAgents", []),
            env=data.get("env"),
            streaming_server=data.get("streaming_server", False),
            url=data.get("url", ""),
            headers=data.get("headers"),
            include_tools=data.get("includeTools"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "enabledForAgents": self.enabled_for_agents,
        }
        if self.env is not None:
            result["env"] = self.env
        if self.streaming_server:
            result["streaming_server"] = self.streaming_server
        if self.url:
            result["url"] = self.url
        if self.headers is not None:
            result["headers"] = self.headers
        if self.include_tools is not None:
            result["includeTools"] = self.include_tools
        return result


class MCPConfig:
    """Manages mcp_servers.json — MCP server definitions."""

    @property
    def _path(self) -> str:
        return os.getenv("MCP_CONFIG_PATH", os.path.expanduser("./mcp_servers.json"))

    def read(self) -> dict[str, MCPServerEntry]:
        """Return MCP server config as typed entries, or {} on error."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                server_id: MCPServerEntry.from_dict(server_id, data)
                for server_id, data in raw.items()
            }
        except Exception:
            return {}

    def write(self, config_data: dict[str, MCPServerEntry]) -> None:
        """Persist config_data and trigger agent reload."""
        from AgentCrew.modules.config.agents_config import AgentsConfig

        try:
            raw = {
                server_id: entry.to_dict() for server_id, entry in config_data.items()
            }
            dir_path = os.path.dirname(self._path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
            AgentsConfig().reload()
        except Exception as e:
            raise ValueError(f"Error writing MCP configuration: {e!s}")
