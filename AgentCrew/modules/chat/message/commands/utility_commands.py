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
from AgentCrew.modules.llm.token_usage import ConversationUsage

CONTEXT_BAR_LENGTH = 20

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
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")  # noqa: DTZ006 - display formatting for user-facing timestamps

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

    @staticmethod
    def _format_number(value: Any) -> str:
        """Format an integer token count with thousands separators."""
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def _format_cost(value: Any) -> str:
        """Format a USD cost for display."""
        try:
            return f"${float(value):.4f}"
        except (TypeError, ValueError):
            return "$0.0000"

    @classmethod
    def _format_context_bar(cls, remaining_percent: float) -> str:
        """Render a 20-cell context bar showing how much context is left.

        Filled cells represent the remaining fraction, matching the
        ``[████████░░░░░░░░░░░░] 38% left`` display style.
        """
        filled = round(remaining_percent / 100 * CONTEXT_BAR_LENGTH)
        filled = max(0, min(CONTEXT_BAR_LENGTH, filled))
        bar = "█" * filled + "░" * (CONTEXT_BAR_LENGTH - filled)
        return f"[{bar}] {remaining_percent:.0f}% left"

    @classmethod
    def _context_usage(cls, agent) -> tuple[int | None, int, int | None, float | None]:
        """Resolve an agent's context-window stats.

        Returns ``(context_limit, occupied, remaining, remaining_percent)``.
        ``context_limit``/``remaining`` are ``None`` when the model limit is
        unknown. ``occupied`` is the agent's current prompt input usage
        (context occupancy, not cumulative session consumption) and is clamped
        to zero from below.
        """
        import os

        from AgentCrew.modules.llm.model_registry import ModelRegistry

        model = ModelRegistry.get_instance().get_model(agent.get_model())
        limit = int(
            os.getenv(
                "AGENTCREW_DEFAULT_MAX_CONTEXT",
                model.max_context_token if model else 0,
            )
        )
        occupied = max(0, int(getattr(agent.token_usage, "total_input_tokens", 0) or 0))
        if limit == 0:
            return None, occupied, None, None
        remaining = max(0, limit - occupied)
        return limit, occupied, remaining, (remaining / limit) * 100

    @classmethod
    def _format_agent_stats(cls, agent, is_current: bool) -> str:
        """Format one local agent's cumulative stats block."""
        usage = getattr(agent, "conversation_usage", ConversationUsage())
        name = f"* {agent.name} (current)" if is_current else f"  {agent.name}"
        limit, occupied, _, percent = cls._context_usage(agent)
        if limit is None:
            context_line = (
                f"Context: unknown limit | occupied: {cls._format_number(occupied)}"
            )
        else:
            context_line = (
                f"Context: {cls._format_context_bar(percent or 0)} "
                f"({cls._format_number(limit)} limit | "
                f"{cls._format_number(occupied)} occupied)"
            )
        return "\n".join(
            [
                name,
                f"  Model: {agent.get_model() or 'unknown'}",
                (
                    "  Tokens: "
                    f"{cls._format_number(usage.input_tokens)} in | "
                    f"{cls._format_number(usage.output_tokens)} out | "
                    f"{cls._format_number(usage.cached_tokens)} cached | "
                    f"{cls._format_number(usage.cache_creation_tokens)} cache-write | "
                    f"{cls._format_number(usage.total_tokens)} total"
                ),
                f"  Cost: {cls._format_cost(usage.cost)}",
                f"  {context_line}",
            ]
        )

    @classmethod
    def _format_stats_message(cls, agents: dict, current_agent_name: str) -> str:
        """Render the /stats report for the current conversation.

        Only local agents that recorded usage in this conversation are listed;
        unused or remote agents are omitted. The final block aggregates the
        per-agent cumulative token categories and cost. Context-window
        capacities are not summed because each agent owns an independent
        context window.
        """
        from AgentCrew.modules.agents import LocalAgent

        involved = [
            agent
            for agent in agents.values()
            if isinstance(agent, LocalAgent)
            and agent.conversation_usage.total_tokens > 0
        ]
        lines = ["Token usage for the current conversation (per agent):"]
        totals = ConversationUsage()
        for agent in involved:
            lines.append("")
            usage = agent.conversation_usage
            totals.add(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                cache_creation_tokens=usage.cache_creation_tokens,
                total_input_tokens=usage.total_input_tokens,
                cost=usage.cost,
            )
            lines.append(
                cls._format_agent_stats(agent, agent.name == current_agent_name)
            )
        lines.append("")
        lines.append("Current conversation total:")
        lines.append(
            "  Tokens: "
            f"{cls._format_number(totals.input_tokens)} in | "
            f"{cls._format_number(totals.output_tokens)} out | "
            f"{cls._format_number(totals.cached_tokens)} cached | "
            f"{cls._format_number(totals.cache_creation_tokens)} cache-write | "
            f"{cls._format_number(totals.total_tokens)} total"
        )
        lines.append(f"  Cost: {cls._format_cost(totals.cost)}")
        return "\n".join(lines)

    def handle_stats(self, user_input: str) -> CommandResult:
        """Handle the /stats command: per-agent token usage and cost.

        Reports cumulative per-agent token/cost usage for the active
        conversation, per-local-agent context-window occupancy/remaining, and
        the aggregate current-conversation total.
        """
        agent_manager = self.message_handler.agent_manager
        agents = getattr(agent_manager, "agents", None) or {}
        current_agent_name = getattr(self.message_handler.agent, "name", "")
        message = self._format_stats_message(agents, current_agent_name)
        self.message_handler.bus.emit_sync(AppEvents.SYSTEM_MESSAGE, message=message)
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
