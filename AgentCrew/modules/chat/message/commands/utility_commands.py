from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from AgentCrew.modules.chat.message.commands.base import CommandResult
from AgentCrew.modules.chat.message.commands.copy_utils import (
    extract_assistant_text,
    get_copyable_assistants,
)
from AgentCrew.modules.events import AppEvents

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class UtilityCommands:
    """Handles utility slash commands."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    def handle_think(self, user_input: str) -> CommandResult:
        """Handle the /think command to set or show thinking budget.

        Usage:
            /think          - Show current thinking budget
            /think <budget> - Set thinking budget (0 to disable)
        """
        parts = user_input.split()

        if len(parts) == 1:
            current_budget = getattr(
                self.message_handler.agent.llm, "thinking_budget", None
            )
            if current_budget is not None:
                if current_budget == 0:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message="Thinking mode is currently disabled.",
                    )
                else:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message=f"Thinking budget is currently set to {current_budget} tokens.",
                    )
            else:
                reasoning_effort = getattr(
                    self.message_handler.agent.llm, "reasoning_effort", None
                )
                if reasoning_effort:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message=f"Reasoning effort is currently set to: {reasoning_effort}",
                    )
                else:
                    self.message_handler.bus.emit_sync(
                        AppEvents.SYSTEM_MESSAGE,
                        message="Thinking mode is not available for the current model.",
                    )
            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message="Usage: /think <budget> (0 to disable)",
            )
            return CommandResult(handled=True, clear_flag=True)

        try:
            budget = parts[1]
            self.message_handler.agent.configure_think(budget)
            self.message_handler.bus.emit_sync(
                AppEvents.THINK_BUDGET_SET, budget=budget
            )
        except ValueError:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR,
                message="Invalid budget value. Please provide a number.",
            )
        return CommandResult(handled=True, clear_flag=True)

    @staticmethod
    def _format_usage_percent(value: Any) -> str:
        if value is None:
            return "unknown"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "unknown"
        if numeric.is_integer():
            return f"{int(numeric)}%"
        return f"{numeric:.1f}%"

    @staticmethod
    def _format_reset_time(value: Any) -> str | None:
        if value is None:
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return str(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    @classmethod
    def _format_usage_message(cls, usage: dict[str, Any]) -> str:
        provider = usage.get("provider", "unknown")
        model = usage.get("model", "unknown")
        if not usage.get("supported"):
            return usage.get("message") or "Usage not supported for this provider"

        lines = [f"Usage for {provider} / {model}"]
        plan_type = usage.get("plan_type")
        if plan_type:
            lines.append(f"Plan: {plan_type}")

        limits = usage.get("limits") or []
        for limit in limits:
            name = str(limit.get("name") or "unknown")
            used = cls._format_usage_percent(limit.get("used_percent"))
            remaining = cls._format_usage_percent(limit.get("remaining_percent"))
            remaining_raw = limit.get("remaining")
            if (
                remaining_raw is not None
                and limit.get("remaining_percent") is None
                and limit.get("used_percent") is None
            ):
                line = f"{name} limit: {remaining_raw} remaining"
            else:
                line = f"{name} limit: {used} used, {remaining} left"
            if limit.get("window_seconds") == 86400:
                name_lower = name.lower()
                if "hour" not in name_lower:
                    line += " (daily)"
            reset_at = cls._format_reset_time(limit.get("reset_at"))
            if reset_at:
                line += f", resets at {reset_at}"
            elif limit.get("reset_after_seconds") is not None:
                line += f", resets in {limit.get('reset_after_seconds')}s"
            lines.append(line)

        credits = usage.get("credits")
        if isinstance(credits, dict):
            balance = credits.get("balance")
            if balance is not None:
                lines.append(f"Credits balance: {balance}")
            elif credits.get("used") is not None and credits.get("total") is not None:
                lines.append(
                    f"Premium requests: {credits.get('used')} / {credits.get('total')}"
                )

        if len(lines) == 1:
            message = usage.get("message")
            if message:
                lines.append(message)
            else:
                lines.append("Usage data returned, but no limit windows were found.")

        return "\n".join(lines)

    async def handle_usage(self, user_input: str) -> CommandResult:
        try:
            llm = self.message_handler.agent.llm
            if not llm:
                logger.error("LLM of agent is not initialized")
                return CommandResult(handled=True, clear_flag=True)
            usage = await llm.get_usage()
            message = self._format_usage_message(usage)
            await self.message_handler.bus.emit(
                AppEvents.SYSTEM_MESSAGE, message=message
            )
        except Exception as e:
            logger.debug(f"Usage retrieval failed: {e}")
            await self.message_handler.bus.emit(
                AppEvents.ERROR, message=f"Failed to retrieve usage: {e!s}"
            )
        return CommandResult(handled=True, clear_flag=True)

    async def handle_copy(self, user_input: str) -> CommandResult:
        copy_idx = user_input[5:].strip() or 1
        try:
            copy_idx = int(copy_idx)
        except (TypeError, ValueError):
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message="Invalid copy index. Usage: /copy <number> (default: 1)",
            )
            return CommandResult(handled=True, clear_flag=True)

        if copy_idx < 1:
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message="Copy index must be a positive integer.",
            )
            return CommandResult(handled=True, clear_flag=True)

        assistant_messages = get_copyable_assistants(
            self.message_handler.streamline_messages,
            self.message_handler.conversation_turns,
        )

        if not assistant_messages:
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message="No assistant messages available to copy.",
            )
            return CommandResult(handled=True, clear_flag=True)

        if copy_idx > len(assistant_messages):
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message=f"Index {copy_idx} out of range. Available: 1-{len(assistant_messages)}.",
            )
            return CommandResult(handled=True, clear_flag=True)

        selected_msg = assistant_messages[-copy_idx]
        text = extract_assistant_text(selected_msg)

        if not text:
            await self.message_handler.bus.emit(
                AppEvents.ERROR,
                message="Selected assistant message has no text content.",
            )
            return CommandResult(handled=True, clear_flag=True)

        await self.message_handler.bus.emit(AppEvents.COPY_REQUESTED, text=text)
        return CommandResult(handled=True, clear_flag=True)

    async def handle_clean_behaviors(self, user_input: str) -> CommandResult:
        try:
            context_service = self.message_handler.persistent_service
            if not context_service:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR, message="Context persistence service not available"
                )
                return CommandResult(handled=True, clear_flag=True)

            parts = user_input.split(maxsplit=1)
            scope = parts[1].strip().lower() if len(parts) > 1 else "global"
            if scope not in ("global", "project"):
                await self.message_handler.bus.emit(
                    AppEvents.SYSTEM_MESSAGE,
                    message="⚠️  Scope must be 'global' or 'project'. Defaulting to 'global'.",
                )
                scope = "global"

            is_local = scope == "project"
            agent_name = self.message_handler.agent.name
            behaviors = context_service.get_adaptive_behaviors(
                agent_name, is_local=is_local
            )

            if not behaviors:
                await self.message_handler.bus.emit(
                    AppEvents.SYSTEM_MESSAGE,
                    message=f"ℹ️  No {scope} behaviors to clean.",
                )
                return CommandResult(handled=True, clear_flag=True)

            llm_service = self.message_handler.agent.llm
            if not llm_service:
                await self.message_handler.bus.emit(
                    AppEvents.ERROR, message="LLM service not available"
                )
                return CommandResult(handled=True, clear_flag=True)

            await self.message_handler.bus.emit(
                AppEvents.SYSTEM_MESSAGE,
                message=f"🔄 Normalizing {len(behaviors)} {scope} behavior(s)...",
            )
            old_behaviors, normalized = await context_service.clean_adaptive_behaviors(
                agent_name, llm_service, is_local=is_local
            )
            old_ids = set(old_behaviors.keys())
            new_ids = set(normalized.keys())
            removed = old_ids - new_ids
            added = new_ids - old_ids
            message = f"✅ Cleaned {scope} behaviors: {len(old_behaviors)} → {len(normalized)} entries"
            if removed or added:
                message += f" (merged/removed: {len(removed)}, new IDs: {len(added)})"
            await self.message_handler.bus.emit(
                AppEvents.SYSTEM_MESSAGE, message=message
            )
        except Exception as e:
            logger.error(f"clean behaviors error: {e!s}", exc_info=True)
            await self.message_handler.bus.emit(
                AppEvents.ERROR, message=f"Error cleaning behaviors: {e!s}"
            )
        return CommandResult(handled=True, clear_flag=True)

    def handle_debug(self, user_input: str) -> CommandResult:
        """Handle /debug command with optional filtering.

        Usage:
            /debug         - Show both agent and chat messages
            /debug agent   - Show only agent messages
            /debug chat    - Show only chat/streamline messages
            /debug system  - Show the current LLM system prompt
        """
        parts = user_input.lower().split()
        filter_type = parts[1] if len(parts) > 1 else None
        valid_filters = ("agent", "chat", "system")

        if filter_type and filter_type not in valid_filters:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR,
                message=f"Invalid filter '{filter_type}'. Use 'agent', 'chat', or 'system'.",
            )
            return CommandResult(handled=True, clear_flag=True)

        if filter_type is None or filter_type == "agent":
            self.message_handler.bus.emit_sync(
                AppEvents.DEBUG_REQUESTED,
                type="agent",
                messages=self.message_handler.agent.clean_history,
            )

        if filter_type is None or filter_type == "chat":
            self.message_handler.bus.emit_sync(
                AppEvents.DEBUG_REQUESTED,
                type="chat",
                messages=self.message_handler.streamline_messages,
            )

        if filter_type == "system" and self.message_handler.agent.llm:
            self.message_handler.bus.emit_sync(
                AppEvents.DEBUG_REQUESTED,
                type="system",
                system_prompt=self.message_handler.agent.llm.get_system_prompt(),
            )

        return CommandResult(handled=True, clear_flag=True)
