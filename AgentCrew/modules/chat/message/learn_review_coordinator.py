from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Callable

from loguru import logger

from AgentCrew.modules.events import AppEvents, EventBus


class LearnReviewCoordinator:
    """Coordinates the /learn command flow: extract behaviors from conversation
    history, present each for user confirmation, and store confirmed behaviors.

    Follows the PromptEvolutionCoordinator pattern for async confirmation flow.
    """

    def __init__(
        self,
        agent_getter: Callable[[], Any],
        bus: EventBus,
        persistence_service=None,
    ):
        self._agent_getter = agent_getter
        self._bus = bus
        self._persistence_service = persistence_service
        self._pending_confirmations: dict[int, dict] = {}
        self._next_confirmation_id = 0

    async def start_review(self, streamline_messages: list[dict[str, Any]]) -> bool:
        """Start the learn review process.

        Args:
            streamline_messages: The conversation messages to analyze.
        """
        if not self._persistence_service:
            self._bus.emit_sync(
                AppEvents.ERROR, message="Context persistence service not available"
            )
            return True

        agent = self._agent_getter()
        if not agent or not getattr(agent, "llm", None):
            self._bus.emit_sync(AppEvents.ERROR, message="LLM service not available")
            return True

        compact_history = self._build_compact_conversation_history(streamline_messages)
        if not compact_history.strip():
            self._bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE, message="ℹ️  No conversation to learn from."
            )
            return True

        self._bus.emit_sync(
            AppEvents.SYSTEM_MESSAGE,
            message="🔄 Analyzing conversation for behaviors...",
        )

        agent_name = agent.name
        existing_global = self._persistence_service.get_adaptive_behaviors(
            agent_name, is_local=False
        )
        existing_project = self._persistence_service.get_adaptive_behaviors(
            agent_name, is_local=True
        )

        try:
            prompt = self._create_behavior_extraction_prompt(
                compact_history, existing_global, existing_project
            )
            response = await agent.llm.process_message(prompt, temperature=0)
            behaviors = self._parse_behaviors_response(response)
        except Exception as e:
            logger.error(f"Learn behavior extraction failed: {e}", exc_info=True)
            self._bus.emit_sync(
                AppEvents.ERROR, message=f"Failed to extract behaviors: {str(e)}"
            )
            return True

        if not behaviors:
            self._bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message="ℹ️  No behaviors found in the conversation.",
            )
            return True

        self._bus.emit_sync(
            AppEvents.SYSTEM_MESSAGE,
            message=f"Found {len(behaviors)} behavior(s). Please confirm each one.",
        )

        stored_count = 0
        skipped_count = 0

        for behavior_data in behaviors:
            result = await self._wait_for_confirmation(behavior_data)
            if result.get("action") == "confirm":
                scope = result.get("scope", "global")
                is_local = scope == "project"
                try:
                    success = self._persistence_service.store_adaptive_behavior(
                        agent_name,
                        behavior_data["id"],
                        behavior_data["behavior"],
                        is_local=is_local,
                    )
                    if success:
                        stored_count += 1
                        self._bus.emit_sync(
                            AppEvents.SYSTEM_MESSAGE,
                            message=f"✅ Stored behavior '{behavior_data['id']}' ({scope} scope)",
                        )
                    else:
                        self._bus.emit_sync(
                            AppEvents.ERROR,
                            message=f"❌ Failed to store behavior '{behavior_data['id']}'",
                        )
                except ValueError as e:
                    self._bus.emit_sync(
                        AppEvents.ERROR,
                        message=f"❌ Invalid behavior format for '{behavior_data['id']}': {str(e)}",
                    )
            else:
                skipped_count += 1

        self._bus.emit_sync(
            AppEvents.SYSTEM_MESSAGE,
            message=f"✅ Learn complete: {stored_count} behavior(s) stored, {skipped_count} skipped.",
        )
        return True

    def resolve_confirmation(self, confirmation_id: int, result: dict) -> None:
        """Resolve a pending learn behavior confirmation.

        Args:
            confirmation_id: The ID returned from the notification.
            result: Dict with "action" ("confirm" or "skip") and optional "scope".
        """
        if confirmation_id in self._pending_confirmations:
            self._pending_confirmations[confirmation_id].update(
                {"resolved": True, **result}
            )

    async def _wait_for_confirmation(self, behavior_data: dict) -> dict:
        """Emit a confirmation event and wait for the user's response."""
        confirmation_id = self._next_confirmation_id
        self._next_confirmation_id += 1
        self._pending_confirmations[confirmation_id] = {"resolved": False}

        self._bus.emit_sync(
            AppEvents.LEARN_CONFIRMATION,
            confirmation_id=confirmation_id,
            **behavior_data,
        )

        try:
            while not self._pending_confirmations[confirmation_id]["resolved"]:
                await asyncio.sleep(0.1)
            return self._pending_confirmations[confirmation_id]
        finally:
            if confirmation_id in self._pending_confirmations:
                del self._pending_confirmations[confirmation_id]

    @staticmethod
    def _extract_text_from_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return " ".join(parts)
        return str(content)

    @staticmethod
    def _truncate(text: str, max_length: int = 500) -> str:
        text = text.strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    def _build_compact_conversation_history(
        self, messages: list[dict[str, Any]]
    ) -> str:
        """Build a compact conversation history string from streamline messages.

        Format:
            user: <user message>
            agent: <agent message>
            agent called <tool_name> with <truncated_tool_arguments> but rejected by user with reason: <reason>
        """
        if not messages:
            return ""

        tool_call_map: dict[str, dict] = {}
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id", "")
                    if tc_id:
                        tool_call_map[tc_id] = tc

        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                text = self._extract_text_from_content(content)
                if text.strip():
                    lines.append(f"user: {self._truncate(text)}")
            elif role == "assistant":
                text = self._extract_text_from_content(content)
                if text.strip():
                    lines.append(f"agent: {self._truncate(text)}")
            elif role == "tool" and msg.get("is_rejected"):
                tool_name = msg.get("tool_name", "unknown")
                tool_call_id = msg.get("tool_call_id", "")
                tool_call = tool_call_map.get(tool_call_id, {})
                args = tool_call.get("arguments", {})
                truncated_args = self._truncate(str(args), max_length=200)

                content_str = str(content)
                reason = ""
                if "Rejected reason:" in content_str:
                    after_reason = content_str.split("Rejected reason:", 1)[1]
                    reason = after_reason.split(".", 1)[0].strip()
                elif "Immediately Pause" in content_str:
                    reason = "User did not provide a reason"
                else:
                    reason = "Unknown reason"

                lines.append(
                    f"agent called {tool_name} with {truncated_args} but rejected by user with reason: {reason}"
                )

        return "\n".join(lines)

    @staticmethod
    def _create_behavior_extraction_prompt(
        conversation: str,
        existing_global: dict[str, str] | None = None,
        existing_project: dict[str, str] | None = None,
    ) -> str:
        existing_section = ""
        if existing_global or existing_project:
            lines = []
            if existing_global:
                lines.append("--- Global Behaviors ---")
                for bid, btext in existing_global.items():
                    lines.append(f"  {bid}: {btext}")
            if existing_project:
                lines.append("--- Project Behaviors ---")
                for bid, btext in existing_project.items():
                    lines.append(f"  {bid}: {btext}")
            existing_section = "\n\nEXISTING BEHAVIORS:\n" + "\n".join(lines)

        return f"""You are a behavior extraction assistant. Analyze the following conversation and extract reusable behavioral patterns that the agent should adopt for future interactions.{existing_section}

CONVERSATION:
{conversation}

Extract behaviors that follow the "when [condition], do [action steps]" format. Focus on:
- User preferences for communication style, task execution, or workflow
- Effective approaches that led to successful outcomes
- Patterns from tool rejections (what the user wanted differently)
- Specific instructions or corrections from the user that should be applied going forward

Return ONLY a JSON object:
{{"behaviors": [{{"id": "<category_context>", "behavior": "when..., do..."}}, ...]}}

Rules:
- Each behavior MUST start with "when"
- Use descriptive IDs in "category_context" format (e.g., "communication_style_technical", "task_execution_code_review")
- Only extract genuinely reusable patterns, not one-time instructions
- Keep behaviors concise and actionable
- Do NOT duplicate existing behaviors — if a new behavior overlaps with an existing one, reuse the existing behavior's ID and provide the updated/merged behavior text
- If the conversation reveals a refinement or correction to an existing behavior, include it with the same ID so it overwrites the old one
- If no reusable behaviors are found, return: {{"behaviors": []}}"""

    @staticmethod
    def _parse_behaviors_response(response: str) -> list[dict[str, str]]:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group())
            behaviors = data.get("behaviors", [])
            result = []
            for entry in behaviors:
                behavior_id = entry.get("id", "").strip()
                behavior = entry.get("behavior", "").strip()
                if behavior and behavior.lower().startswith("when"):
                    if not behavior_id:
                        behavior_id = (
                            f"learned_{hashlib.md5(behavior.encode()).hexdigest()[:8]}"
                        )
                    result.append({"id": behavior_id, "behavior": behavior})
            return result
        except (json.JSONDecodeError, AttributeError):
            return []
