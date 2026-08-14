from __future__ import annotations

from typing import TYPE_CHECKING

from AgentCrew.modules.chat.message.commands.base import CommandResult
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.events import AppEvents

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class AgentCommands:
    """Handles agent-related slash commands."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    async def handle_agent(self, command: str) -> tuple[bool, str]:
        """
        Handle the /agent command to switch agents or list available agents.

        Returns:
            Tuple of (success, message)
        """
        parts = command.split()

        if len(parts) == 1:
            agents_info = {"current": self.message_handler.agent.name, "available": {}}

            for agent_name, agent in self.message_handler.agent_manager.agents.items():
                agents_info["available"][agent_name] = {
                    "description": agent.description,
                    "current": (
                        self.message_handler.agent
                        and self.message_handler.agent.name == agent_name
                    ),
                }

            self.message_handler.bus.emit_sync(
                AppEvents.AGENTS_LISTED, agents=agents_info
            )
            return True, "Listed available agents"

        agent_name = parts[1]
        old_agent_name = self.message_handler.agent_manager.get_current_agent().name
        if old_agent_name == agent_name:
            return (False, f"Already using {agent_name} agent")
        if await self.message_handler.agent_manager.select_agent_async(agent_name):
            self.message_handler.agent = (
                self.message_handler.agent_manager.get_current_agent()
            )
            # prevents reset agent history already involved in conversation
            if len(self.message_handler.agent.history) == 0:
                old_agent = self.message_handler.agent_manager.get_agent(old_agent_name)
                if old_agent:
                    self.message_handler.agent.history = old_agent.history[:]
                    old_agent.history = []

            try:
                GlobalConfig().set_last_used_agent(agent_name)
            except Exception as e:
                print(f"Warning: Failed to save last used agent: {e}")

            self.message_handler.bus.emit_sync(
                AppEvents.AGENT_CHANGED, agent_name=agent_name
            )
            return True, f"Switched to {agent_name} agent"
        else:
            available_agents = ", ".join(
                self.message_handler.agent_manager.agents.keys()
            )
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR,
                message=f"Unknown agent: {agent_name}. Available agents: {available_agents}",
            )
            return (
                False,
                f"Unknown agent: {agent_name}. Available agents: {available_agents}",
            )

    async def handle_toggle_transfer(self, user_input: str) -> CommandResult:
        """Handle /toggle_transfer command — backward-compat alias toggling between transfer and none."""
        try:
            from AgentCrew.modules.agents.manager import AgentMode

            current_mode = self.message_handler.agent_manager.agent_mode
            new_mode = (
                AgentMode.NONE
                if current_mode == AgentMode.TRANSFER
                else AgentMode.TRANSFER
            )
            self.message_handler.agent_manager.agent_mode = new_mode

            await self.message_handler.agent.deactivate_async()
            await self.message_handler.agent.activate_async()

            status = "enabled" if new_mode == AgentMode.TRANSFER else "disabled"
            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"🔄 Transfer enforcement is now {status}.",
            )
            self.message_handler.bus.emit_sync(
                AppEvents.TRANSFER_ENFORCE_TOGGLE, status=status
            )

            return CommandResult(handled=True, clear_flag=True)

        except Exception as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR,
                message=f"Failed to toggle transfer enforcement: {e!s}",
            )
            return CommandResult(handled=True, clear_flag=True)

    async def handle_agent_mode(self, user_input: str) -> CommandResult:
        """Handle /agent_mode command to view or switch agent interaction mode."""
        try:
            from AgentCrew.modules.agents.manager import AgentMode

            parts = user_input.strip().split(maxsplit=1)

            if len(parts) == 1:
                current = self.message_handler.agent_manager.agent_mode.value
                self.message_handler.bus.emit_sync(
                    AppEvents.SYSTEM_MESSAGE,
                    message=f"🤖 Current agent mode: **{current}**\n"
                    f"Options: transfer, delegate, none",
                )
                return CommandResult(handled=True, clear_flag=True)

            mode_str = parts[1].lower()
            try:
                new_mode = AgentMode(mode_str)
            except ValueError:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Invalid mode '{mode_str}'. Options: transfer, delegate, none",
                )
                return CommandResult(handled=True, clear_flag=True)

            self.message_handler.agent_manager.agent_mode = new_mode

            await self.message_handler.agent.deactivate_async()
            await self.message_handler.agent.activate_async()

            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"🔄 Agent mode switched to: **{new_mode.value}**",
            )

            return CommandResult(handled=True, clear_flag=True)

        except Exception as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Failed to switch agent mode: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)
