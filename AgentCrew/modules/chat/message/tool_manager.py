from __future__ import annotations

import asyncio
import copy
from typing import Any

from loguru import logger

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.events.hooks import CancelOperation
from AgentCrew.modules.tools.parallel_executor import (
    ToolResult,
    execute_tool_tasks_in_parallel,
    is_sequential_tool,
)


class ToolManager:
    """Manages tool execution and confirmation."""

    def __init__(self, message_handler):
        self.message_handler = message_handler
        self.bus = EventBus.get_instance()
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
        return set(GlobalConfig().get_auto_approval_tools())

    async def _execute_approved_tool(
        self,
        tool_use: dict[str, Any],
    ) -> ToolResult:
        requested_name = tool_use["name"]
        requested_input = copy.deepcopy(tool_use["input"])

        try:
            tool_result = await self.message_handler.agent.execute_tool_call(tool_use)
            return ToolResult(
                tool_use=tool_use,
                result=tool_result,
                resolved_name=requested_name,
                resolved_input=requested_input,
            )
        except CancelOperation:
            message = (
                f"Tool: {requested_name} with {tool_use['id']} was cancelled "
                "by a tool.execute hook and was not executed."
            )
            return ToolResult(
                tool_use=tool_use,
                result=message,
                is_error=True,
                is_rejected=True,
                was_executed=False,
                resolved_name=requested_name,
                resolved_input=requested_input,
            )
        except Exception as exc:
            return ToolResult(
                tool_use=tool_use,
                result=str(exc),
                is_error=True,
                resolved_name=requested_name,
                resolved_input=requested_input,
            )

    def _record_tool_result(self, result: ToolResult) -> None:
        tool_use = result.tool_use
        if result.is_rejected:
            result_message = self.message_handler.agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": result.result,
                    "is_rejected": True,
                    "is_error": True,
                },
            )
            self.message_handler._messages_append(result_message)
            self.bus.emit_sync(
                AppEvents.TOOL_DENIED,
                tool_use=tool_use,
                message=result.result,
            )
            return

        if result.is_error:
            result_message = self.message_handler.agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": tool_use,
                    "tool_result": result.result,
                    "is_error": True,
                },
            )
            self.message_handler._messages_append(result_message)
            self.bus.emit_sync(
                AppEvents.TOOL_ERROR,
                tool_use=tool_use,
                error=result.result,
                message=result_message,
            )
            return

        if tool_use["name"] == "transfer":
            self._post_tool_transfer(tool_use, result.result)
            return

        result_message = self.message_handler.agent.format_message(
            MessageType.ToolResult,
            {"tool_use": tool_use, "tool_result": result.result},
        )
        self.message_handler._messages_append(result_message)
        self.bus.emit_sync(
            AppEvents.TOOL_RESULT,
            tool_use=tool_use,
            tool_result=result.result,
            message=result_message,
        )

    def _validate_tool_use(
        self,
        tool_use: dict[str, Any],
    ) -> ToolResult | None:
        """Validate a single *tool_use* against its registered schema.

        Delegates to ``agent.validate_tool_use()`` for shared logic.
        Returns ``None`` when the tool call is valid, or an error
        ``ToolResult`` when validation fails.

        Unknown (unregistered) tools are also rejected here so they
        fail before confirmation.
        """
        tool_name = tool_use.get("name", "")
        tool_input = tool_use.get("input", {})

        error_text = self.message_handler.agent.validate_tool_use(tool_use)
        if error_text is None:
            return None

        return ToolResult(
            tool_use=tool_use,
            result=error_text,
            is_error=True,
            is_rejected=False,
            was_executed=False,
            resolved_name=tool_name,
            resolved_input=tool_input,
        )

    async def execute_tool(self, tool_use: dict[str, Any]):
        """Execute a tool with proper confirmation flow."""
        tool_name = tool_use["name"]
        tool_id = tool_use["id"]

        # --- Pre-confirmation input validation -----------------------------
        validation_result = self._validate_tool_use(tool_use)
        if validation_result is not None:
            self._record_tool_result(validation_result)
            return

        if tool_name == "ask":
            try:
                # Wait for user response through confirmation flow
                user_response = await self._wait_for_tool_confirmation(tool_use)

                # Format the user's answer as the tool result
                if user_response.get("action") == "answer":
                    answer = user_response.get("answer", "")
                    tool_result = answer
                else:
                    # User cancelled or error occurred
                    tool_result = "User cancelled the question."

                # Store the tool result in message history
                tool_result_message = self.message_handler.agent.format_message(
                    MessageType.ToolResult,
                    {"tool_use": tool_use, "tool_result": tool_result},
                )
                self.message_handler._messages_append(tool_result_message)
                await self.bus.emit(
                    AppEvents.TOOL_RESULT,
                    tool_use=tool_use,
                    tool_result=tool_result,
                    message=tool_result_message,
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
                await self.bus.emit(
                    AppEvents.TOOL_ERROR,
                    tool_use=tool_use,
                    error=str(e),
                    message=error_message,
                )
            return

        if (
            not self.get_effective_yolo_mode()
            and tool_name not in self._auto_approved_tools
        ):
            # Request confirmation from the user
            confirmation = await self._wait_for_tool_confirmation(tool_use)
            action = confirmation.get("action", "deny")

            if action == "deny":
                reason = confirmation.get("reason", "")
                reason_message = (
                    f"Rejected reason: {reason}. Adjust your next steps bases on the reason why user rejected. Learn behavior only when the reason has `when <condition>, <action>` format."
                    if reason
                    else "Immediately Pause the response and WAIT for user reason and adjustment."
                )
                tool_result = f"Tool: {tool_name} with {tool_id} has been rejected and nothing has been changed. {reason_message}"
                if tool_name == "transfer":
                    error_message = {
                        "role": "user",
                        "agent": self.message_handler.agent.name,
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You were trying to transfer the request to "
                                    f"{tool_use['input']['target_agent']} with the task: "
                                    f"{tool_use['input']['task_description']}. The transfer "
                                    f"was rejected. {reason_message}"
                                ),
                            }
                        ],
                    }
                else:
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
                await self.bus.emit(
                    AppEvents.TOOL_DENIED,
                    tool_use=tool_use,
                    message=tool_result,
                )
                return  # Skip to the next tool

            if action == "approve_all":
                # Remember this tool for auto-approval
                self._auto_approved_tools.add(tool_name)

        await self.bus.emit(AppEvents.TOOL_USE, **tool_use)
        result = await self._execute_approved_tool(tool_use)
        self._record_tool_result(result)

    async def _wait_for_tool_confirmation(self, tool_use):
        """
        Create a future and wait for tool confirmation from the user.

        Args:
            tool_use: The tool use dictionary

        Returns:
            dict with confirmation result containing action and any additional data
        """
        confirmation_id = self._next_confirmation_id
        self._next_confirmation_id += 1

        # Create a future that will be resolved when the user responds
        self._pending_confirmations[confirmation_id] = {"approval": "pending"}

        # Notify UI that confirmation is required
        await self.bus.emit(
            AppEvents.TOOL_CONFIRMATION_REQ,
            tool_use=tool_use,
            confirmation_id=confirmation_id,
        )

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
                f"Error while waiting for tool confirmation {confirmation_id}: {e!s}"
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

        self.bus.emit_sync(
            AppEvents.AGENT_CHANGED_BY_TRANSFER,
            tool_use=tool_use,
            agent_name=self.message_handler.agent.name,
        )

    async def execute_tools_batch(self, tool_uses: list[dict[str, Any]]):
        parallel_buffer = []

        for tool_use in tool_uses:
            if is_sequential_tool(tool_use["name"]):
                if parallel_buffer:
                    await self._execute_parallel_batch(parallel_buffer)
                    parallel_buffer = []
                await self.execute_tool(tool_use)
            else:
                parallel_buffer.append(tool_use)

        if parallel_buffer:
            await self._execute_parallel_batch(parallel_buffer)

    async def _execute_parallel_batch(self, tool_uses: list[dict[str, Any]]):
        approved = []
        for tool_use in tool_uses:
            # --- Pre-confirmation input validation -------------------------
            validation_result = self._validate_tool_use(tool_use)
            if validation_result is not None:
                self._record_tool_result(validation_result)
                continue

            approval_result = await self._needs_and_gets_approval(tool_use)
            if approval_result == "denied":
                continue
            approved.append(tool_use)

        if not approved:
            return

        for tool_use in approved:
            await self.bus.emit(AppEvents.TOOL_USE, **tool_use)

        results = await execute_tool_tasks_in_parallel(
            approved,
            self._execute_approved_tool,
        )

        for result in results:
            self._record_tool_result(result)

    async def _needs_and_gets_approval(self, tool_use: dict[str, Any]) -> str:
        tool_name = tool_use["name"]
        tool_id = tool_use["id"]

        if self.get_effective_yolo_mode() or tool_name in self._auto_approved_tools:
            return "approved"

        confirmation = await self._wait_for_tool_confirmation(tool_use)
        action = confirmation.get("action", "deny")

        if action == "deny":
            reason = confirmation.get("reason", "")
            reason_message = (
                f"Rejected reason: {reason}. Adjust your next steps bases on the reason why user rejected. Learn behavior only when the reason has `when <condition>, <action>` format."
                if reason
                else "Immediately Pause the response and WAIT for user reason and adjustment."
            )
            tool_result = f"Tool: {tool_name} with {tool_id} has been rejected and nothing has been changed. {reason_message}"
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
            await self.bus.emit(
                AppEvents.TOOL_DENIED,
                tool_use=tool_use,
                message=tool_result,
            )
            return "denied"

        if action == "approve_all":
            self._auto_approved_tools.add(tool_name)

        return "approved"

    def reset_approved_tools(self):
        """Reset approved tools for a new conversation."""
        self._auto_approved_tools = self._load_persistent_auto_approved_tools()
