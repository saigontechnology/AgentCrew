from __future__ import annotations

from typing import TYPE_CHECKING, Any

from AgentCrew.modules.acp.session_state import AcpSessionState

if TYPE_CHECKING:
    from AgentCrew.modules.agents import AgentManager, LocalAgent


class AcpToolManager:
    def __init__(self, agent_manager: AgentManager):
        self._agent_manager = agent_manager
        self._client_capabilities: Any = None

    def update_capabilities(self, capabilities: Any):
        self._client_capabilities = capabilities

    @property
    def client_capabilities(self) -> Any:
        return self._client_capabilities

    @property
    def client_can_read_text_file(self) -> bool:
        fs = self.client_filesystem_capabilities
        return self._capability_flag(fs, "read_text_file", "readTextFile")

    @property
    def client_can_write_text_file(self) -> bool:
        fs = self.client_filesystem_capabilities
        return self._capability_flag(
            fs, "write_text_file", "writeTextFile", "write_file", "writeFile"
        )

    @property
    def client_filesystem_capabilities(self) -> Any | None:
        caps = self._client_capabilities
        if caps is None:
            return None
        if isinstance(caps, dict):
            return caps.get("fs")
        return getattr(caps, "fs", None)

    def _capability_flag(self, source: Any, *names: str) -> bool:
        if source is None:
            return False
        for name in names:
            if isinstance(source, dict):
                value = source.get(name)
            else:
                value = getattr(source, name, False)
            if value:
                return True
        return False

    @property
    def client_has_terminal(self) -> bool:
        caps = self._client_capabilities
        return self._capability_flag(caps, "terminal")

    def _get_agent(self, agent_name: str) -> LocalAgent:

        agent = self._agent_manager.get_local_agent(agent_name)
        if agent is None:
            raise ValueError(f"Local agent '{agent_name}' not found")
        return agent

    async def ensure_tools_for_session(self, session_id: str, state: AcpSessionState):
        if state.tool_state.acp_tools_configured:
            return
        agent = self._get_agent(state.agent_name)
        if agent is None:
            return

        tools_changed = False
        can_read_files = self.client_can_read_text_file
        can_write_files = self.client_can_write_text_file
        if can_read_files or can_write_files:
            from AgentCrew.modules.acp.tools.filesystem import (
                register as register_acp_fs,
            )

            if can_read_files and "read_file" in agent.tool_definitions:
                state.tool_state.acp_backup_tool_defs["read_file"] = (
                    agent.tool_definitions.pop("read_file", None)
                )
            if can_write_files and "write_file" in agent.tool_definitions:
                state.tool_state.acp_backup_tool_defs["write_file"] = (
                    agent.tool_definitions.pop("write_file", None)
                )
            register_acp_fs(
                agent=agent,
                enable_read=can_read_files,
                enable_write=can_write_files,
            )
            state.tool_state.acp_read_tool_configured = can_read_files
            state.tool_state.acp_write_tool_configured = can_write_files
            tools_changed = True

        if self.client_has_terminal:
            from AgentCrew.modules.acp.tools.terminal import (
                register as register_acp_terminal,
            )

            replaced_terminal = [
                "run_command",
                "check_command_status",
                "terminate_command",
                "list_running_commands",
                "send_command_input",
            ]
            for name in replaced_terminal:
                if name in agent.tool_definitions:
                    state.tool_state.acp_backup_tool_defs[name] = (
                        agent.tool_definitions.pop(name, None)
                    )
            register_acp_terminal(agent=agent)
            tools_changed = True

        if tools_changed:
            agent.resync_tools_to_llm()
        state.tool_state.acp_tools_configured = True

    def restore_builtin_tools(self, state: AcpSessionState):
        if not state.tool_state.acp_tools_configured:
            return
        agent = self._get_agent(state.agent_name)
        if agent is None:
            return

        acp_tool_names = set()
        if state.tool_state.acp_read_tool_configured:
            acp_tool_names.add("acp_read_file")
        if state.tool_state.acp_write_tool_configured:
            acp_tool_names.add("acp_write_file")
        if self.client_has_terminal:
            acp_tool_names.update(
                ["acp_run_command", "acp_check_command_status", "acp_terminate_command"]
            )

        for name in acp_tool_names:
            agent.tool_definitions.pop(name, None)

        for name, tool_def_tuple in state.tool_state.acp_backup_tool_defs.items():
            agent.tool_definitions[name] = tool_def_tuple

        agent.resync_tools_to_llm()
        state.tool_state.acp_tools_configured = False
        state.tool_state.acp_read_tool_configured = False
        state.tool_state.acp_write_tool_configured = False
        state.tool_state.acp_backup_tool_defs.clear()
