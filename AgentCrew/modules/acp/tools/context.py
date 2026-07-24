from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from acp import Client

ToolKind = Literal["read", "edit", "execute", "search", "fetch", "other"]


@dataclass
class AcpSessionContext:
    conn: Client | None
    session_id: str
    client_capabilities: Any = None
    active_terminals: dict[str, str] = field(default_factory=dict)


def classify_tool_kind(tool_name: str) -> ToolKind:
    if (
        "read" in tool_name
        or "read_file" in tool_name
        or "grep" in tool_name
        or "find" in tool_name
    ):
        return "read"
    if "write" in tool_name or "edit" in tool_name:
        return "edit"
    if "search" in tool_name or "analyze" in tool_name:
        return "search"
    if "command" in tool_name or "run" in tool_name:
        return "execute"
    if "browser" in tool_name or "fetch" in tool_name or "web" in tool_name:
        return "fetch"
    return "other"


_current_acp_session: contextvars.ContextVar[AcpSessionContext | None] = (
    contextvars.ContextVar("current_acp_session", default=None)
)
