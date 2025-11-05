from typing import Dict, Any
import asyncio

from loguru import logger
from AgentCrew.modules.config import ConfigManagement

# from AgentCrew.modules.llm.message import MessageTransformer
from AgentCrew.modules.agents.base import MessageType


class ToolManager:
    """Manages tool execution and confirmation."""

    def __init__(self, message_handler):
        from AgentCrew.modules.chat.message import MessageHandler

        if isinstance(message_handler, MessageHandler):
            self.message_handler = message_handler

        self._auto_approved_tools = self._load_persistent_auto_approved_tools()

        self._pending_confirmations = {}  # Store futures for confirmation requests
        self._next_confirmation_id = 0  # ID counter for confirmation requests
        self.yolo_mode = False  # Enable/disable auto-approval mode
        self.session_overrided_yolo_mode: bool = False

    def get_effective_yolo_mode(self) -> bool:
        """Determine the effective YOLO mode considering session override."""
        return self.session_overrided_yolo_mode or self.yolo_mode

    def _load_persistent_auto_approved_tools(self):
        """Load persistent auto-approved tools from config."""
        config_manager = ConfigManagement()
        return set(config_manager.get_auto_approval_tools())

    async def execute_tool(self, tool_use: Dict[str, Any]):
        """Execute a tool with proper confirmation flow."""
        tool_name = tool_use["name"]
        tool_id = tool_use["id"]

        # Special handling for the transfer tool - always auto-approve
        if tool_name == "transfer":
            self.message_handler._notify("tool_use", tool_use)
            try:
                tool_result = await self.message_handler.agent.execute_tool_call(
                    tool_name, tool_use["input"]
                )
                self._post_tool_transfer(tool_use, tool_result)
            except Exception as e:
                # if transfer failed we should add the tool_call message back for record

                self.message_handler._messages_append(
                    self.message_handler.agent.format_message(
                        MessageType.Assistant,
                        {
                            "message": "",  # No message content for failed transfer
                            "tool_uses": [tool_use],
                        },
                    )
                )
                error_message = self.message_handler.agent.format_message(
                    MessageType.ToolResult,
                    {
                        "tool_use": tool_use,
                        "tool_result": str(e),
                        "is_error": True,
                    },
                )
                self.message_handler._messages_append(error_message)
                self.message_handler._notify(
                    "tool_error",
                    {
                        "tool_use": tool_use,
                        "error": str(e),
                        "message": error_message,
                    },
                )
            return

        elif tool_name == "ask":
            self.message_handler._notify("tool_use", tool_use)
            try:
                # Wait for user response through confirmation flow
                user_response = await self._wait_for_tool_confirmation(tool_use)

                # Format the user's answer as the tool result
                if user_response.get("action") == "answer":
                    answer = user_response.get("answer", "")
                    tool_result = f"User's answer: {answer}"
                else:
                    # User cancelled or error occurred
                    tool_result = "User cancelled the question."

                # Store the tool result in message history
                tool_result_message = self.message_handler.agent.format_message(
                    MessageType.ToolResult,
                    {"tool_use": tool_use, "tool_result": tool_result},
                )
                self.message_handler._messages_append(tool_result_message)
                self.message_handler._notify(
                    "tool_result",
                    {
                        "tool_use": tool_use,
                        "tool_result": tool_result,
                        "message": tool_result_message,
                    },
                )
            except Exception as e:
                error_message = self.message_handler.agent.format_message(
                    MessageType.ToolResult,
                    {
                        "tool_use": tool_use,
                        "tool_result": str(e),
                        "is_error": True,
                    },
                )
                self.message_handler._messages_append(error_message)
                self.message_handler._notify(
                    "tool_error",
                    {
                        "tool_use": tool_use,
                        "error": str(e),
                        "message": error_message,
                    },
                )
            return

        if (
            not self.message_handler.is_non_interactive
            and not self.get_effective_yolo_mode()
            and tool_name not in self._auto_approved_tools
        ):
            # Request confirmation from the user
            confirmation = await self._wait_for_tool_confirmation(tool_use)
            action = confirmation.get("action", "deny")

            if action == "deny":
                reason = confirmation.get("reason", "")
                reason_message = (
                    f"Here is the reason: {reason}. Adjust your actions bases on user's reason. Adapt behavior if it's recurring."
                    if reason
                    else "Immediately STOP any tasks or any tool calls and WAIT for user reason and adjustment, adapt new behavior if needed. "
                )
                tool_result = (
                    f"Tool: {tool_id} call has been rejected by user, {reason_message}"
                )
                error_message = self.message_handler.agent.format_message(
                    MessageType.ToolResult,
                    {
                        "tool_use": tool_use,
                        "tool_result": tool_result,
                        "is_rejected": True,
                        "is_error": True,
                    },
                )
                self.message_handler._messages_append(error_message)
                self.message_handler._notify(
                    "tool_denied",
                    {
                        "tool_use": tool_use,
                        "message": tool_result,
                    },
                )
                return  # Skip to the next tool

            if action == "approve_all":
                # Remember this tool for auto-approval
                self._auto_approved_tools.add(tool_name)

        # Tool is approved, execute it
        self.message_handler._notify("tool_use", tool_use)

        try:
            tool_result = await self.message_handler.agent.execute_tool_call(
                tool_name, tool_use["input"]
            )

            tool_result_message = self.message_handler.agent.format_message(
                MessageType.ToolResult,
                {"tool_use": tool_use, "tool_result": tool_result},
            )
            self.message_handler._messages_append(tool_result_message)
            self.message_handler._notify(
                "tool_result",
                {
                    "tool_use": tool_use,
                    "tool_result": tool_result,
                    "message": tool_result_message,
                },
            )

        except Exception as e:
            error_message = self.message_handler.agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": str(e),
                    "is_error": True,
                },
            )
            self.message_handler._messages_append(error_message)
            self.message_handler._notify(
                "tool_error",
                {
                    "tool_use": tool_use,
                    "error": str(e),
                    "message": error_message,
                },
            )

    async def _wait_for_tool_confirmation(self, tool_use):
        """
        Create a future and wait for tool confirmation from the user.

        Args:
            tool_use: The tool use dictionary

        Returns:
            Dict with confirmation result containing action and any additional data
        """
        confirmation_id = self._next_confirmation_id
        self._next_confirmation_id += 1

        # Create a future that will be resolved when the user responds
        self._pending_confirmations[confirmation_id] = {"approval": "pending"}

        # Notify UI that confirmation is required
        tool_info = {**tool_use, "confirmation_id": confirmation_id}
        self.message_handler._notify("tool_confirmation_required", tool_info)

        try:
            while self._pending_confirmations[confirmation_id]["approval"] == "pending":
                await asyncio.sleep(0.1)  # Wait for the user to respond
            # Wait for the user's response
            result = self._pending_confirmations[confirmation_id]
            logger.info(
                f"Successfully received tool confirmation {confirmation_id} with result: {result}"
            )
            return result
        except Exception as e:
            logger.error(
                f"Error while waiting for tool confirmation {confirmation_id}: {str(e)}"
            )
            return {"action": "deny"}
        finally:
            # Clean up the future
            if confirmation_id in self._pending_confirmations:
                del self._pending_confirmations[confirmation_id]

    def resolve_tool_confirmation(self, confirmation_id, result):
        """
        Resolve a pending tool confirmation future with the user's decision.

        Args:
            confirmation_id: The ID of the confirmation request
            result: Dictionary with the user's decision (action: 'approve', 'approve_all', or 'deny')
        """
        if confirmation_id in self._pending_confirmations:
            self._pending_confirmations[confirmation_id] = {
                "approval": "done",
                **result,
            }

    def _post_tool_transfer(self, tool_use, tool_result):
        """Handle post-transfer operations."""
        if (
            self.message_handler.persistent_service
            and self.message_handler.current_conversation_id
            and self.message_handler.last_assisstant_response_idx >= 0
        ):
            self.message_handler.persistent_service.append_conversation_messages(
                self.message_handler.current_conversation_id,
                self.message_handler.get_recent_agent_responses(),
                # MessageTransformer.standardize_messages(
                #     self.message_handler.agent.history[
                #         self.message_handler.last_assisstant_response_idx :
                #     ],
                #     self.message_handler.agent.get_provider(),
                #     self.message_handler.agent.name,
                # ),
            )

        # Update llm service when transfer agent
        self.message_handler.agent = (
            self.message_handler.agent_manager.get_current_agent()
        )

        self.message_handler._messages_append(
            {
                "role": "user",
                "agent": self.message_handler.agent.name,
                "content": [{"type": "text", "text": tool_result}],
            }
        )
        if (
            self.message_handler.persistent_service
            and self.message_handler.current_conversation_id
        ):
            self.message_handler.persistent_service.append_conversation_messages(
                self.message_handler.current_conversation_id,
                [
                    {
                        "role": "user",
                        "agent": self.message_handler.agent.name,
                        "content": [{"type": "text", "text": tool_result}],
                    }
                ],
            )
        self.message_handler.last_assisstant_response_idx = len(
            self.message_handler.streamline_messages
        )

        self.message_handler._notify(
            "agent_changed_by_transfer",
            {"tool_use": tool_use, "agent_name": self.message_handler.agent.name},
        )

    def reset_approved_tools(self):
        """Reset approved tools for a new conversation."""
        self._auto_approved_tools = self._load_persistent_auto_approved_tools()
