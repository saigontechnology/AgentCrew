"""Conversation-memory parsing and storage for ``LocalAgent``.

Extracted from :mod:`AgentCrew.modules.agents.local_agent` so the agent
class focuses on orchestration while a focused collaborator owns:

- last non-empty user message extraction for memory
- assistant message extraction for memory (with ask-tool pairing)
- memory storage via ``services["memory"]`` when available

Follows the ``AgentLLMLifecycle`` / ``AgentContextManager`` /
``AgentToolRegistrar`` collaborator pattern: constructed with a back-reference
to the owning agent, owns behavior only, and never duplicates agent state.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from .local_agent import LocalAgent


class AgentMemoryCoordinator:
    """Parses and stores conversation memory for a ``LocalAgent``.

    Reads ``agent.name`` and ``agent.services`` as needed but never owns or
    duplicates those values.
    """

    def __init__(self, agent: LocalAgent) -> None:
        self._agent = agent

    @property
    def agent(self) -> LocalAgent:
        """Return the owning LocalAgent."""
        return self._agent

    def extract_last_user_message_for_memory(self, messages: list[dict]) -> str:
        """Return the last non-empty user text from ``messages``.

        Supports string content, list string parts, and
        ``{type: "text", text: ...}`` parts, mirroring the former
        ``LocalAgent._extract_last_user_message_for_memory`` behavior.
        """
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", [])
            if isinstance(content, str):
                normalized = content.strip()
                if normalized:
                    return normalized
                continue
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    normalized = part.strip()
                    if normalized:
                        text_parts.append(normalized)
                elif isinstance(part, dict) and part.get("type") == "text":
                    normalized = str(part.get("text", "")).strip()
                    if normalized:
                        text_parts.append(normalized)
            if text_parts:
                return " ".join(text_parts)
        return ""

    def extract_assistant_messages_for_memory(
        self, messages: list[dict], current_response: str = ""
    ) -> list[str]:
        """Return assistant text for memory starting after the last user message.

        Pair ask-tool questions with their answers by ``tool_call_id``
        (including JSON-string arguments and malformed-JSON degradation) and
        preserve tool rejection feedback. ``current_response`` is appended
        only when non-empty and not already the final extracted message.
        """
        assistant_messages: list[str] = []
        last_user_idx = -1
        # Map tool_call_id -> ask question for pairing across parallel calls
        ask_questions_by_id: dict[str, str] = {}

        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                last_user_idx = index

        for message in messages[last_user_idx + 1 :]:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant":
                content = message.get("content", "")
                if isinstance(content, str):
                    normalized = content.strip()
                    if normalized:
                        assistant_messages.append(normalized)

                # Index ask tool questions by their tool call id
                for tc in message.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name") == "ask":
                        tc_id = tc.get("id")
                        if not tc_id:
                            continue
                        arguments = tc.get("arguments", {})
                        if isinstance(arguments, dict):
                            q = arguments.get("question", "")
                        elif isinstance(arguments, str):
                            try:
                                q = json.loads(arguments).get("question", "")
                            except json.JSONDecodeError:
                                q = ""
                        else:
                            q = ""
                        if q:
                            ask_questions_by_id[tc_id] = q

            elif role == "tool":
                # Capture tool rejection reasons (user feedback)
                if message.get("is_rejected"):
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        assistant_messages.append(
                            f"[Tool rejected: {message.get('tool_name', 'unknown')}] "
                            f"{content.strip()}"
                        )
                # Capture ask tool answers paired with the question by tool_call_id
                elif message.get("tool_name") == "ask":
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        tc_id = message.get("tool_call_id")
                        question = (
                            ask_questions_by_id.pop(tc_id, None) if tc_id else None
                        )
                        if question:
                            assistant_messages.append(
                                f"[User answered: {content.strip()} | Question was: {question}]"
                            )
                        else:
                            assistant_messages.append(
                                f"[User answered: {content.strip()}]"
                            )

        normalized_current_response = current_response.strip()
        if normalized_current_response and (
            not assistant_messages
            or assistant_messages[-1] != normalized_current_response
        ):
            assistant_messages.append(normalized_current_response)
        return assistant_messages

    def store_memory_if_available(
        self,
        user_message: str,
        messages: list[dict],
        current_response: str,
        session_id: str | None = None,
    ) -> None:
        from AgentCrew.modules.memory.base_service import BaseMemoryService

        memory_service = self.agent.services.get("memory")
        if not memory_service or not isinstance(memory_service, BaseMemoryService):
            return
        assistant_messages = self.extract_assistant_messages_for_memory(
            messages, current_response
        )
        try:
            memory_service.store_conversation(
                user_message,
                assistant_messages,
                self.agent.name,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"Failed to store conversation in memory: {e}")
