from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from AgentCrew.modules.llm.token_usage import TokenUsage

from .tools.permission_broker import AcpPermissionBroker


@dataclass
class AcpToolState:
    """Tracks ACP-specific tool and terminal state within a session."""

    acp_tools_configured: bool = False
    acp_read_tool_configured: bool = False
    acp_write_tool_configured: bool = False
    acp_active_terminals: dict[str, str] = field(default_factory=dict)
    acp_backup_tool_defs: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcpSessionState:
    """Represents the state of an ACP session."""

    cwd: str
    agent_name: str
    history: list[dict[str, Any]] = field(default_factory=list)
    current_task: asyncio.Task | None = None
    cancelled: bool = False
    acp_mcp_server_configs: list[Any] = field(default_factory=list)
    acp_mcp_server_ids: list[str] = field(default_factory=list)
    title: str | None = None
    updated_at: str | None = None
    model_id: str | None = None
    thought_level: str | None = None
    tool_state: AcpToolState = field(default_factory=AcpToolState)
    permission_broker: AcpPermissionBroker | None = None
    pending_ask_tool: dict[str, Any] | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    # TODO: Remove backward-compat properties in next major version
    @property
    def _acp_tools_configured(self) -> bool:
        return self.tool_state.acp_tools_configured

    @_acp_tools_configured.setter
    def _acp_tools_configured(self, value: bool) -> None:
        self.tool_state.acp_tools_configured = value

    @property
    def _acp_read_tool_configured(self) -> bool:
        return self.tool_state.acp_read_tool_configured

    @_acp_read_tool_configured.setter
    def _acp_read_tool_configured(self, value: bool) -> None:
        self.tool_state.acp_read_tool_configured = value

    @property
    def _acp_write_tool_configured(self) -> bool:
        return self.tool_state.acp_write_tool_configured

    @_acp_write_tool_configured.setter
    def _acp_write_tool_configured(self, value: bool) -> None:
        self.tool_state.acp_write_tool_configured = value

    @property
    def _acp_active_terminals(self) -> dict[str, str]:
        return self.tool_state.acp_active_terminals

    @_acp_active_terminals.setter
    def _acp_active_terminals(self, value: dict[str, str]) -> None:
        self.tool_state.acp_active_terminals = value

    @property
    def _acp_backup_tool_defs(self) -> dict[str, Any]:
        return self.tool_state.acp_backup_tool_defs

    @_acp_backup_tool_defs.setter
    def _acp_backup_tool_defs(self, value: dict[str, Any]) -> None:
        self.tool_state.acp_backup_tool_defs = value
