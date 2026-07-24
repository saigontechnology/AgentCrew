from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from acp import text_block, tool_content
from acp.schema import PermissionOption, ToolCallUpdate
from loguru import logger

from AgentCrew.modules.acp.tools.context import classify_tool_kind

if TYPE_CHECKING:
    from acp import Client


SENSITIVE_TOOL_NAMES: set[str] = {
    "acp_write_file",
    "write_file",
    "acp_run_command",
    "run_command",
    "browser_navigate",
    "browser_click_element",
    "browser_input_data",
    "browser_keyboard_action",
    "browser_refresh",
    "browser_execute_script",
    "delegate",
}


@dataclass
class AcpPermissionBroker:
    conn: Client | None
    session_id: str
    yolo_mode: bool = False
    always_allowed: set[str] = field(default_factory=set)
    always_denied: set[str] = field(default_factory=set)

    async def request_permission(self, tool_use: dict[str, Any]) -> str:
        tool_name = tool_use.get("name", "")

        if tool_name not in SENSITIVE_TOOL_NAMES:
            return "allow_once"

        if self.yolo_mode or tool_name in self.always_allowed:
            return "allow_once"

        if tool_name in self.always_denied:
            return "reject"

        if self.conn is None:
            return "allow_once"

        try:
            outcome = await self._call_client_permission(tool_use)
            return outcome
        except Exception as exc:
            logger.warning(
                f"ACP permission request failed for '{tool_name}', rejecting: {exc}"
            )
            return "reject"

    async def _call_client_permission(self, tool_use: dict[str, Any]) -> str:
        tool_name = tool_use.get("name", "")
        tool_id = tool_use.get("id", "unknown")
        tool_input = tool_use.get("input", {})

        title = self._build_title(tool_name, tool_input)
        kind = classify_tool_kind(tool_name)
        content = [tool_content(text_block(str(tool_input)))] if tool_input else None

        tool_call_update = ToolCallUpdate(
            tool_call_id=tool_id,
            title=title,
            kind=kind,
            status="pending",
            content=content,
        )

        options = [
            PermissionOption(
                option_id="allow_once",
                kind="allow_once",
                name="Allow once",
            ),
            PermissionOption(
                option_id="allow_always",
                kind="allow_always",
                name="Always allow",
            ),
            PermissionOption(
                option_id="reject_once",
                kind="reject_once",
                name="Deny",
            ),
        ]
        if not self.conn:
            raise ValueError("Client is not connected")

        response = await self.conn.request_permission(
            options=options,
            session_id=self.session_id,
            tool_call=tool_call_update,
        )

        outcome_obj = response.outcome
        if getattr(outcome_obj, "outcome", None) == "selected":
            selected_id = getattr(outcome_obj, "option_id", "")
            if selected_id == "allow_always":
                self.always_allowed.add(tool_name)
                return "allow_once"
            if selected_id in ("allow_once",):
                return "allow_once"
            if selected_id in ("reject_once", "reject_always"):
                if selected_id == "reject_always":
                    self.always_denied.add(tool_name)
                return "reject"
            return "allow_once"

        return "reject"

    @staticmethod
    def _build_title(tool_name: str, tool_input: dict[str, Any]) -> str:
        detail = (
            tool_input.get("file_path", "")
            or tool_input.get("command", "")
            or tool_input.get("url", "")
            or tool_input.get("target_agent", "")
        )
        if detail:
            return f"{tool_name}: {detail}"
        return tool_name
