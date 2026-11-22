"""Provider-independent, per-external-turn protection against tool-call loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from AgentCrew.modules.config.global_config import DEFAULT_MAX_TOOL_ROUNDS, GlobalConfig

from .message_metadata import is_tool_loop_recovery, mark_tool_loop_recovery

MAX_CONSECUTIVE_TOOL_EXECUTIONS = 2

REPEAT_WARNING = (
    "Tool loop protection: this exact tool call has already been requested "
    "twice. The repeated call was removed from the assistant message and was not "
    "executed again. "
    "Reuse the previous result, change the tool or arguments, or finish the task "
    "without repeating this call."
)


FINALIZATION_PROMPT_TEMPLATE = (
    "The tool budget of {max_tool_rounds} model tool rounds is exhausted. All tool calls in "
    "the attempted extra round were removed and were not executed. No further "
    "tools are available. Use only the existing conversation and previous tool "
    "results to provide your best-effort final answer now. Clearly disclose "
    "any incomplete work and any uncertainty; do not claim unverified success."
)


def pending_recovery_message(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return recovery context once all retained calls have results.

    The assistant marker survives ask/input-required pauses and persistence.
    A marked user message closes that recovery round without mutating history.
    """
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if not is_tool_loop_recovery(message):
            continue
        if message.get("role") != "assistant":
            return None
        pending_ids = {call["id"] for call in message.get("tool_calls", [])}
        for following in history[index + 1 :]:
            if following.get("role") == "tool":
                pending_ids.discard(following.get("tool_call_id"))
        if pending_ids:
            return None
        recovery = {
            "role": "user",
            "content": [{"type": "text", "text": REPEAT_WARNING}],
        }
        mark_tool_loop_recovery(recovery)
        return recovery
    return None


class ToolLoopError(RuntimeError):
    """The model ignored loop recovery instructions or exceeded the round limit."""


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize(value: Any, ancestors: set[int], depth: int = 0) -> Any:
    """Normalize JSON recursively; conservatively identify unsupported values.

    Type tags avoid collisions between malformed inputs and valid JSON. Opaque
    objects, cycles, and excessively deep inputs use a stable type-only fallback
    rather than invoking arbitrary repr/serialization methods.
    """
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    if value is None or isinstance(value, (str, bool, int, float)):
        return [kind, value]
    if depth >= 64 or id(value) in ancestors:
        return ["unsupported", kind]
    if isinstance(value, bytes):
        return [kind, value.hex()]
    if not isinstance(value, (dict, list, tuple, set, frozenset)):
        return ["unsupported", kind]
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            items = [
                [
                    _normalize(k, ancestors, depth + 1),
                    _normalize(v, ancestors, depth + 1),
                ]
                for k, v in value.items()
            ]
            return ["object", sorted(items, key=lambda item: _encode(item[0]))]
        items = [_normalize(item, ancestors, depth + 1) for item in value]
        if isinstance(value, (set, frozenset)):
            return ["set", sorted(items, key=_encode)]
        return ["array", items]
    finally:
        ancestors.remove(id(value))


def tool_call_fingerprint(tool_use: dict[str, Any]) -> str:
    """Hash tool name and canonical arguments, never the protocol call ID."""
    if "input" in tool_use:
        arguments = tool_use["input"]
    else:
        arguments = tool_use.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, RecursionError):
                pass
    canonical = _encode(_normalize([tool_use.get("name", ""), arguments], set()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolLoopRound:
    """Ordered pre-dispatch decisions for a complete model tool batch.

    Repeat-terminal batches retain assistant/tool pairing before raising.
    Global-budget overflow discards the complete batch and permits one
    tools-disabled finalization request instead.
    """

    errors: tuple[str | None, ...]
    terminal_error: str | None = None
    needs_recovery: bool = False
    finalization_required: bool = False
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS

    def omit_blocked_calls(
        self, tool_uses: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], ToolLoopRound]:
        """Remove recovery-blocked calls while keeping terminal batches paired."""
        if self.finalization_required:
            return [], self
        retained = [
            (tool, error)
            for tool, error in zip(tool_uses, self.errors, strict=True)
            if error != REPEAT_WARNING
        ]
        return [tool for tool, _ in retained], ToolLoopRound(
            tuple(error for _, error in retained),
            self.terminal_error,
            self.needs_recovery
            or any(error == REPEAT_WARNING for error in self.errors),
            max_tool_rounds=self.max_tool_rounds,
        )

    def recovery_message(self, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Build one internal budget-finalization or repeat-recovery message."""
        if not self.finalization_required:
            return pending_recovery_message(history)
        message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": FINALIZATION_PROMPT_TEMPLATE.format(
                        max_tool_rounds=self.max_tool_rounds
                    ),
                }
            ],
        }
        mark_tool_loop_recovery(message, finalization=True)
        return message

    def mark_assistant_message(self, message: dict[str, Any] | None) -> None:
        """Persist a pending recovery round before its retained tools execute."""
        if self.needs_recovery and message is not None:
            mark_tool_loop_recovery(message)

    def raise_if_terminal(self) -> None:
        if self.terminal_error:
            raise ToolLoopError(self.terminal_error)


class ToolLoopGuard:
    """Track consecutive fingerprints locally to one external turn.

    Duplicate calls within a batch consume the same execution budget but cannot
    terminate before the model has received a warning in an earlier round.
    Absence from a model round resets a fingerprint's streak. Counts represent dispatch
    opportunities, including calls rejected by validation or permission gates.
    """

    def __init__(self, max_tool_rounds: int | None = None) -> None:
        if max_tool_rounds is not None and (
            type(max_tool_rounds) is not int or max_tool_rounds <= 0
        ):
            raise ValueError("max_tool_rounds must be a positive integer")
        self._max_tool_rounds = (
            max_tool_rounds
            if max_tool_rounds is not None
            else GlobalConfig().get_max_tool_rounds()
        )
        self._occurrences: dict[str, int] = {}
        self._rounds = 0
        self._terminal_error: str | None = None
        self._finalization_required = False
        self._finalization_requested = False

    @property
    def max_tool_rounds(self) -> int:
        """The resolved budget snapshot for this external turn."""
        return self._max_tool_rounds

    @property
    def tools_enabled(self) -> bool:
        """Whether this external turn still permits tool-enabled requests."""
        return not self._finalization_required

    def start_request(self) -> bool:
        """Claim the sole tools-disabled request, preventing retry recursion."""
        if self._finalization_requested:
            raise ToolLoopError(
                "Tool loop protection terminated this turn: tools-disabled "
                "finalization was already attempted."
            )
        if self._finalization_required:
            self._finalization_requested = True
        return self.tools_enabled

    def validate_response(self, response: Any, tool_uses: list[dict[str, Any]]) -> None:
        """Fail closed if the finalization request cannot produce a final answer."""
        if self._finalization_required and (
            tool_uses or not isinstance(response, str) or not response.strip()
        ):
            raise ToolLoopError(
                "Tool loop protection terminated this turn: tools-disabled "
                "finalization returned tool calls or an empty/malformed response."
            )

    def check_round(self, tool_uses: list[dict[str, Any]]) -> ToolLoopRound:
        """Decide before confirmation, hooks, or tool dispatch can take place."""
        if not tool_uses:
            self._occurrences = {}
            return ToolLoopRound((), max_tool_rounds=self.max_tool_rounds)
        self._rounds += 1
        errors: list[str | None] = []
        occurrences: dict[str, int] = {}
        for tool_use in tool_uses:
            fingerprint = tool_call_fingerprint(tool_use)
            count = (
                occurrences.get(fingerprint, self._occurrences.get(fingerprint, 0)) + 1
            )
            occurrences[fingerprint] = count
            error = None
            if (
                count > MAX_CONSECUTIVE_TOOL_EXECUTIONS
                and self._occurrences.get(fingerprint, 0)
                <= MAX_CONSECUTIVE_TOOL_EXECUTIONS
            ):
                error = REPEAT_WARNING
                logger.warning(
                    "Tool loop blocked: tool={} occurrence={} digest={}",
                    tool_use.get("name", ""),
                    count,
                    fingerprint,
                )
            elif (
                count > MAX_CONSECUTIVE_TOOL_EXECUTIONS and self._terminal_error is None
            ):
                self._terminal_error = (
                    "Tool loop protection terminated this turn: the model repeated "
                    f"tool {tool_use.get('name', '')!r} after the recovery warning "
                    f"(occurrence={count}, digest={fingerprint}). "
                    "No tools in this round were executed."
                )
                logger.error(self._terminal_error)
            errors.append(error)
        self._occurrences = occurrences
        if self._terminal_error:
            return ToolLoopRound(
                tuple(self._terminal_error for _ in tool_uses),
                self._terminal_error,
                max_tool_rounds=self.max_tool_rounds,
            )
        if self._rounds > self.max_tool_rounds:
            self._finalization_required = True
            return ToolLoopRound(
                (), finalization_required=True, max_tool_rounds=self.max_tool_rounds
            )
        return ToolLoopRound(tuple(errors), max_tool_rounds=self.max_tool_rounds)
