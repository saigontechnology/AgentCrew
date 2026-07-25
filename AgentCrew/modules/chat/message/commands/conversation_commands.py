from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.chat.consolidation import ConversationConsolidator
from AgentCrew.modules.chat.message.commands.base import CommandResult
from AgentCrew.modules.events import AppEvents

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class ConversationCommands:
    """Handles conversation-related slash commands."""

    def __init__(
        self,
        message_handler: MessageHandler,
        agent_command_handler: Callable[[str], Awaitable[tuple[bool, str]]],
    ):
        self.message_handler = message_handler
        self._agent_command_handler = agent_command_handler

    async def handle_consolidate(self, user_input: str) -> CommandResult:
        """Handle consolidate command."""
        try:
            parts = user_input.split()
            if len(parts) == 1:
                preserve_count = 10
            else:
                preserve_count = int(parts[1])

            if isinstance(self.message_handler.agent, LocalAgent):
                consolidator = ConversationConsolidator(self.message_handler.agent.llm)

                result = await consolidator.consolidate(
                    self.message_handler.streamline_messages, preserve_count
                )

                if result["success"]:
                    self.message_handler.agent_manager.rebuild_agents_messages(
                        self.message_handler.streamline_messages
                    )

                    await self.message_handler.bus.emit(
                        AppEvents.CONSOLIDATION_COMPLETED, result=result
                    )

                    if (
                        self.message_handler.current_conversation_id
                        and self.message_handler.persistent_service
                    ):
                        try:
                            self.message_handler.persistent_service.append_conversation_messages(
                                self.message_handler.current_conversation_id,
                                self.message_handler.streamline_messages,
                                True,
                            )
                        except Exception as e:
                            await self.message_handler.bus.emit(
                                AppEvents.ERROR,
                                message=f"Failed to save consolidated conversation: {e!s}",
                            )

                    message = (
                        f"Consolidated {result['messages_consolidated']} messages, "
                        f"preserving {result['messages_preserved']} recent messages. "
                        f"Token savings: ~{result['original_token_count'] - result['consolidated_token_count']}"
                    )
                    await self.message_handler.bus.emit(
                        AppEvents.SYSTEM_MESSAGE, message=message
                    )
                else:
                    await self.message_handler.bus.emit(
                        AppEvents.SYSTEM_MESSAGE,
                        message=f"Consolidation skipped: {result['reason']}",
                    )

                return CommandResult(handled=True, clear_flag=True)
            else:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR,
                    message="Consolidation is only supported with LocalAgent.",
                )
                return CommandResult(handled=False, clear_flag=False)
        except ValueError as e:
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message=f"Invalid consolidation parameter: {e!s}. Use /consolidate [number]",
            )
            return CommandResult(handled=True, clear_flag=True)
        except Exception as e:
            await self.message_handler.bus.emit(
                AppEvents.ERROR, message=f"Error during consolidation: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)

    async def handle_unconsolidate(self, user_input: str) -> CommandResult:
        """Handle unconsolidate command to remove last consolidated message."""
        try:
            if isinstance(self.message_handler.agent, LocalAgent):
                consolidator = ConversationConsolidator(self.message_handler.agent.llm)

                result = await consolidator.unconsolidate(
                    self.message_handler.streamline_messages
                )

                if result["success"]:
                    self.message_handler.agent_manager.rebuild_agents_messages(
                        self.message_handler.streamline_messages
                    )

                    await self.message_handler.bus.emit(
                        AppEvents.UNCONSOLIDATION_COMPLETED, result=result
                    )

                    if (
                        self.message_handler.current_conversation_id
                        and self.message_handler.persistent_service
                    ):
                        try:
                            self.message_handler.persistent_service.append_conversation_messages(
                                self.message_handler.current_conversation_id,
                                self.message_handler.streamline_messages,
                                True,
                            )
                        except Exception as e:
                            await self.message_handler.bus.emit(
                                AppEvents.ERROR,
                                message=f"Failed to save unconsolidated conversation: {e!s}",
                            )

                    message = (
                        f"Unconsolidated last consolidated message containing "
                        f"{result['messages_restored']} original messages."
                    )
                    await self.message_handler.bus.emit(
                        AppEvents.SYSTEM_MESSAGE, message=message
                    )
                else:
                    await self.message_handler.bus.emit(
                        AppEvents.SYSTEM_MESSAGE,
                        message=f"Unconsolidation skipped: {result['reason']}",
                    )

                return CommandResult(handled=True, clear_flag=True)
            else:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR,
                    message="Unconsolidation is only supported with LocalAgent.",
                )
                return CommandResult(handled=False, clear_flag=False)
        except Exception as e:
            await self.message_handler.bus.emit(
                AppEvents.ERROR, message=f"Error during unconsolidation: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)

    async def handle_jump(self, command: str) -> bool:
        """Handle the /jump command to rewind conversation to a previous turn.

        Usage:
            /jump          - Show available turns for jumping
            /jump <turn>   - Jump back to the specified turn
        """
        try:
            parts = command.split()

            if len(parts) == 1:
                if not self.message_handler.conversation_turns:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message="No conversation turns available for jumping.",
                    )
                    return True

                turns_info = []
                for i, turn in enumerate(self.message_handler.conversation_turns, 1):
                    preview = turn.get_preview(50)
                    turns_info.append(f"  {i}. {preview}")

                message = (
                    "📋 Available turns for jumping:\n"
                    + "\n".join(turns_info)
                    + "\n\nUsage: /jump <turn_number>"
                )
                self.message_handler.bus.emit_sync(
                    AppEvents.SYSTEM_MESSAGE, message=message
                )
                return True

            turn_number = int(parts[1])

            if turn_number < 1 or turn_number > len(
                self.message_handler.conversation_turns
            ):
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Invalid turn number. Available turns: 1-{len(self.message_handler.conversation_turns)}",
                )
                return False

            selected_turn = self.message_handler.conversation_turns[turn_number - 1]

            selected_message = self.message_handler.streamline_messages[
                selected_turn.message_index
            ]

            selected_message_agent = selected_message.get("agent", "")

            self.message_handler.streamline_messages = (
                self.message_handler.streamline_messages[: selected_turn.message_index]
            )
            if (
                self.message_handler.current_conversation_id
                and self.message_handler.persistent_service
            ):
                self.message_handler.persistent_service.append_conversation_messages(
                    self.message_handler.current_conversation_id,
                    self.message_handler.streamline_messages,
                    True,
                )

            last_message = next(
                (msg for msg in reversed(self.message_handler.streamline_messages)),
                None,
            )
            if last_message and last_message.get("agent", ""):
                await self._agent_command_handler(f"/agent {last_message['agent']}")
            elif selected_message_agent:
                await self._agent_command_handler(f"/agent {selected_message_agent}")

            self.message_handler.agent_manager.rebuild_agents_messages(
                self.message_handler.streamline_messages
            )
            self.message_handler.conversation_turns = (
                self.message_handler.conversation_turns[: turn_number - 1]
            )
            self.message_handler.last_assisstant_response_idx = len(
                self.message_handler.streamline_messages
            )
            if isinstance(selected_message.get("content"), list):
                selected_content = next(
                    (
                        c.get("text", "")
                        for c in selected_message.get("content", [])
                        if c.get("type", "") == "text"
                    ),
                    "",
                )

            else:
                selected_content = selected_message.get("content", "")

            self.message_handler.bus.emit_sync(
                AppEvents.JUMP_PERFORMED,
                turn_number=turn_number,
                preview=selected_turn.get_preview(100),
                message=selected_content,
            )

            return True

        except ValueError:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message="Invalid turn number. Please provide a number."
            )
            return False

    async def handle_fork(self, command: str) -> CommandResult:
        """Handle the /fork command to create a conversation fork at a specific turn.

        Usage:
            /fork          - Show available turns for forking
            /fork <turn>   - Fork at the specified turn and switch to the new conversation
        """
        try:
            parts = command.split()

            if len(parts) == 1:
                if not self.message_handler.conversation_turns:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message="No conversation turns available for forking.",
                    )
                    return CommandResult(handled=True, clear_flag=True)

                turns_info = []
                for i, turn in enumerate(self.message_handler.conversation_turns, 1):
                    preview = turn.get_preview(50)
                    turns_info.append(f"  {i}. {preview}")

                message = (
                    "📋 Available turns for forking:\n"
                    + "\n".join(turns_info)
                    + "\n\nUsage: /fork <turn_number>"
                )
                self.message_handler.bus.emit_sync(
                    AppEvents.SYSTEM_MESSAGE, message=message
                )
                return CommandResult(handled=True, clear_flag=True)

            turn_arg = parts[1]
            turn_number = int(turn_arg)

            if turn_number < 1 or turn_number > len(
                self.message_handler.conversation_turns
            ):
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Invalid turn number. Available turns: 1-{len(self.message_handler.conversation_turns)}",
                )
                return CommandResult(handled=True, clear_flag=True)

            selected_turn = self.message_handler.conversation_turns[turn_number - 1]
            preview = selected_turn.get_preview(100)

            success = (
                await self.message_handler.conversation_manager.fork_and_switch_async(
                    turn_number
                )
            )
            if success:
                self.message_handler.bus.emit_sync(
                    AppEvents.FORK_AND_SWITCH, turn_number=turn_number, preview=preview
                )
            return CommandResult(handled=success, clear_flag=True)

        except ValueError:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message="Invalid turn number. Please provide a number."
            )
            return CommandResult(handled=True, clear_flag=True)
        except Exception as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Failed to fork conversation: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)
