"""LLM message formatting for ``LocalAgent``.

Extracted from :mod:`AgentCrew.modules.agents.local_agent` so the agent
class focuses on orchestration while a focused collaborator owns message
shaping:

- assistant message formatting (text, thinking block, tool calls)
- tool result formatting (error/rejected prefixes)
- ``MessageType`` routing
- invalid tool-use filtering during response normalization

Follows the ``AgentLLMLifecycle`` / ``AgentMemoryCoordinator`` /
``AgentContextManager`` / ``AgentToolRegistrar`` collaborator pattern:
constructed with a back-reference to the owning agent, owns behavior only,
and never duplicates agent state (messages, tools, token usage, selections,
services).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from AgentCrew.modules.agents.base import MessageType

if TYPE_CHECKING:
    from .local_agent import LocalAgent


class AgentMessageFormatter:
    """Formats assistant/tool/file messages and normalizes tool uses.

    Reads ``agent.name`` and ``agent.llm`` as needed but never stores or
    duplicates agent state.
    """

    def __init__(self, agent: LocalAgent) -> None:
        self._agent = agent

    @property
    def agent(self) -> LocalAgent:
        """Return the owning LocalAgent."""
        return self._agent

    def format_tool_result(
        self,
        tool_use: dict,
        tool_result: Any,
        is_error: bool = False,
        is_rejected: bool = False,
    ) -> dict[str, Any]:
        """
        Format a tool result for OpenAI API.

        Args:
            tool_use: The tool use details
            tool_result: The result from the tool execution
            is_error: Whether the result is an error
            is_rejected: Whether the result was rejected by the user

        Returns:
            A formatted message for tool response
        """
        # OpenAI format for tool responses
        message: dict[str, Any] = {
            "role": "tool",
            "agent": self._agent.name,
            "tool_call_id": tool_use["id"],
            "tool_name": tool_use["name"],
            "content": tool_result,
        }

        # Add error indication if needed
        if is_error:
            message["content"] = f"ERROR: {message['content']!s}"
        if is_rejected:
            message["content"] = f"DENIED: {message['content']!s}"
            message["is_rejected"] = True

        return message

    def format_assistant_message(
        self,
        assistant_response: str,
        thinking_data: tuple[str, str] | None = None,
        tool_uses: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Format the assistant's response into the appropriate message format
        for the LLM provider.

        Args:
            assistant_response (str): The text response from the assistant
            thinking_data: Optional ``(content, signature)`` thinking block
            tool_uses: Optional tool uses to attach as ``tool_calls``

        Returns:
            dict[str, Any]: A properly formatted message to append to the
            messages list
        """
        assistant_message: dict[str, Any] = {
            "agent": self._agent.name,
            "role": "assistant",
            "content": [{"type": "text", "text": assistant_response}],
        }
        if thinking_data:
            thinking_content, thinking_signature = thinking_data
            thinking_block: dict[str, Any] = {
                "type": "thinking",
                "thinking": thinking_content,
            }
            # Add signature if available
            if thinking_signature:
                thinking_block["signature"] = thinking_signature
            assistant_message["content"].insert(0, thinking_block)
        valid_tool_uses = [
            tool_use
            for tool_use in (tool_uses or [])
            if tool_use.get("id") and tool_use.get("name")
        ]
        if valid_tool_uses:
            assistant_message["tool_calls"] = [
                {
                    "id": tool_use["id"],
                    "name": tool_use["name"],
                    "arguments": tool_use["input"],
                    "type": tool_use.get("type", "tool_call"),
                }
                for tool_use in valid_tool_uses
            ]
        return assistant_message

    def format_message(
        self, message_type: MessageType, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if message_type == MessageType.Assistant:
            return self.format_assistant_message(
                message_data.get("message", ""),
                message_data.get("thinking", None),
                message_data.get("tool_uses", None),
            )
        elif message_type == MessageType.ToolResult:
            return self.format_tool_result(
                message_data.get("tool_use", {}),
                message_data.get("tool_result", ""),
                message_data.get("is_error", False),
                message_data.get("is_rejected", False),
            )
        elif message_type == MessageType.FileContent:
            return (
                self._agent.llm.process_file_for_message(
                    message_data.get("file_uri", "")
                )
                if self._agent.llm
                else message_data
            )
        return None

    def filter_invalid_tool_uses(
        self, tool_uses: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop tool calls without a usable name, warning as before.

        Pure response normalization: no lifecycle or streaming state is
        involved.
        """
        filtered_tool_uses = []
        for tool_use in tool_uses:
            if isinstance(tool_use.get("name"), str) and bool(
                tool_use.get("name", "").strip()
            ):
                filtered_tool_uses.append(tool_use)
            elif tool_use.get("id") or tool_use.get("args_json"):
                logger.warning(
                    "Dropping malformed parsed tool call without a usable name"
                )
        return filtered_tool_uses
