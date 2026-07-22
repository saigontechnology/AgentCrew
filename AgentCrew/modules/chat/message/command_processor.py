from __future__ import annotations
from AgentCrew.modules.events import AppEvents
from typing import TYPE_CHECKING
from AgentCrew.modules.chat.message.commands import (
    AgentCommands,
    CommandResult,
    ConversationCommands,
    FileCommands,
    MCPCommands,
    ModelCommands,
    UtilityCommands,
    VoiceCommands,
)

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class CommandProcessor:
    """Handles command processing for the message handler."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler
        self.agent_commands = AgentCommands(message_handler)
        self.conversation_commands = ConversationCommands(
            message_handler, self.agent_commands.handle_agent
        )
        self.file_commands = FileCommands(message_handler)
        self.mcp_commands = MCPCommands(message_handler)
        self.model_commands = ModelCommands(message_handler)
        self.utility_commands = UtilityCommands(message_handler)
        self.voice_commands = VoiceCommands(message_handler)

    async def process_command(self, user_input: str) -> CommandResult:
        """Process a command and return the result."""
        if self._is_exit_command(user_input):
            await self.message_handler.bus.emit(AppEvents.EXIT_REQUESTED)
            return CommandResult(handled=True, exit_flag=True)
        elif user_input.lower() == "/clear":
            self.message_handler.start_new_conversation()
            return CommandResult(handled=True, clear_flag=True)
        elif user_input.lower().startswith("/copy"):
            return await self.utility_commands.handle_copy(user_input)
        elif user_input.lower().startswith("/debug"):
            return self.utility_commands.handle_debug(user_input)
        elif user_input.lower().startswith("/think"):
            return self.utility_commands.handle_think(user_input)
        elif user_input.lower().startswith("/usage"):
            return await self.utility_commands.handle_usage(user_input)
        elif user_input.lower().startswith("/clean_behaviors"):
            return await self.utility_commands.handle_clean_behaviors(user_input)
        elif user_input.lower().startswith("/learn"):
            await self.message_handler.start_learn_review()
            return CommandResult(handled=True, clear_flag=True)
        elif user_input.lower().startswith("/consolidate"):
            return await self.conversation_commands.handle_consolidate(user_input)
        elif user_input.lower().startswith("/evolve"):
            return await self._handle_evolve_command(user_input)
        elif user_input.lower().startswith("/unconsolidate"):
            return await self.conversation_commands.handle_unconsolidate(user_input)
        elif user_input.lower().startswith("/jump"):
            result = self.conversation_commands.handle_jump(user_input)
            return CommandResult(handled=result, clear_flag=True)
        elif user_input.lower().startswith("/fork"):
            return self.conversation_commands.handle_fork(user_input)
        elif user_input.lower().startswith("/agent_mode"):
            return self.agent_commands.handle_agent_mode(user_input)
        elif user_input.lower().startswith("/agent"):
            success, message = self.agent_commands.handle_agent(user_input)
            await self.message_handler.bus.emit(
                AppEvents.AGENT_COMMAND_RESULT, success=success, message=message
            )
            return CommandResult(handled=True, clear_flag=True)
        elif user_input.lower().startswith("/model"):
            exit_flag, clear_flag = self.model_commands.handle_model(user_input)
            return CommandResult(
                handled=True, exit_flag=exit_flag, clear_flag=clear_flag
            )
        elif user_input.lower().startswith("/mcp"):
            exit_flag, clear_flag = await self.mcp_commands.handle_mcp(user_input)
            return CommandResult(
                handled=True, exit_flag=exit_flag, clear_flag=clear_flag
            )
        elif user_input.startswith("/file"):
            return self.file_commands.handle_file(user_input)
        elif user_input.startswith("/drop"):
            return self.file_commands.handle_drop(user_input)
        elif user_input.lower() == "/voice":
            return await self.voice_commands.handle_voice(user_input)
        elif user_input.lower() == "/end_voice":
            return await self.voice_commands.handle_end_voice(user_input)
        elif user_input.lower() == "/toggle_transfer":
            return self.agent_commands.handle_toggle_transfer(user_input)

        # Catch-all: any unrecognised /command should not fall through to the LLM
        if user_input.startswith("/"):
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message="Invalid command: type /help to view all available commands",
            )
            return CommandResult(handled=True, clear_flag=True)

        return CommandResult(handled=False)

    def _is_exit_command(self, user_input: str) -> bool:
        return user_input.lower() in ["/exit", "/quit"]

    async def _handle_evolve_command(self, user_input: str) -> CommandResult:
        await self.message_handler.start_evolution_review()
        return CommandResult(handled=True, clear_flag=True)
