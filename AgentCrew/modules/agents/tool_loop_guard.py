"""Provider-independent, per-external-turn protection against tool-call loops."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger

from AgentCrew.modules.config.global_config import DEFAULT_MAX_TOOL_ROUNDS, GlobalConfig

from .message_metadata import mark_tool_loop_recovery

MAX_CONSECUTIVE_TOOL_BATCHES = 2

REPEAT_WARNING = (
    "<Tool_Loop_Recovery>\n"
    "This exact complete tool-call batch has already been requested twice. The "
    "entire repeated assistant round, including its text, thinking, and tool "
    "calls, was discarded and no calls in that round were executed. "
    "Reuse the previous results, change the tool-call batch or arguments, or "
    "finish the task without repeating this batch.\n"
    "</Tool_Loop_Recovery>"
)

FinalizationCause = Literal["repeat", "budget"]

_FINALIZATION_INSTRUCTIONS = (
    "<Final_Response_Requirements>"
    "Use only the existing conversation and previous tool results to provide your "
    "best-effort final answer now. Clearly disclose any incomplete work and any "
    "uncertainty; do not claim unverified success."
    "</Final_Response_Requirements>\n</Tool_Loop_Finalization>"
)

FINALIZATION_PROMPT_TEMPLATE = (
    "<Tool_Loop_Finalization>\n<Cause>"
    "The tool budget of {max_tool_rounds} model tool rounds is exhausted. The "
    "entire attempted extra assistant round was discarded and no calls in it "
    "were executed. No further tools are available."
    "</Cause>\n" + _FINALIZATION_INSTRUCTIONS
)

REPEAT_FINALIZATION_PROMPT = (
    "<Tool_Loop_Finalization>\n<Cause>"
    "The same complete tool-call batch continued after the tool-loop recovery "
    "warning. The entire assistant round was discarded and no calls in it were "
    "executed. Tools are now disabled."
    "</Cause>\n" + _FINALIZATION_INSTRUCTIONS
)


class ToolLoopError(RuntimeError):
    """The guarded turn cannot safely continue or finalize."""


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


def tool_batch_fingerprint(tool_uses: list[dict[str, Any]]) -> str:
    """Hash the order-independent multiset of canonical call fingerprints."""
    fingerprints = sorted(tool_call_fingerprint(tool) for tool in tool_uses)
    return hashlib.sha256(_encode(fingerprints).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolLoopRound:
    """Pre-dispatch decision for an entire model tool-call batch."""

    errors: tuple[str | None, ...]
    terminal_error: str | None = None
    needs_recovery: bool = False
    finalization_required: bool = False
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    finalization_cause: FinalizationCause | None = None

    @property
    def discard_assistant(self) -> bool:
        """Whether the entire assistant round must stay out of history."""
        return self.needs_recovery or self.finalization_required

    def omit_blocked_calls(
        self, tool_uses: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], ToolLoopRound]:
        """Cancel the whole batch on intervention; never partially filter calls."""
        return ([] if self.discard_assistant else tool_uses), self

    def request_message(self) -> dict[str, Any] | None:
        """Build marked context for one request, never for canonical history."""
        if not self.discard_assistant:
            return None
        if not self.finalization_required:
            prompt = REPEAT_WARNING
        elif self.finalization_cause == "repeat":
            prompt = REPEAT_FINALIZATION_PROMPT
        else:
            prompt = FINALIZATION_PROMPT_TEMPLATE.format(
                max_tool_rounds=self.max_tool_rounds
            )
        message = {"role": "user", "content": [{"type": "text", "text": prompt}]}
        mark_tool_loop_recovery(message, finalization=self.finalization_required)
        return message

    def raise_if_terminal(self) -> None:
        if self.terminal_error:
            raise ToolLoopError(self.terminal_error)


class ToolLoopGuard:
    """Track identical consecutive tool batches within one external turn.

    Call order and generated IDs do not matter; duplicate multiplicity does.
    Any change in the complete batch, or an empty round, resets its streak.
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
        self._last_batch: str | None = None
        self._batch_occurrences = 0
        self._pending_request_round: ToolLoopRound | None = None
        self._rounds = 0
        self._finalization_cause: FinalizationCause | None = None
        self._finalization_requested = False

    @property
    def max_tool_rounds(self) -> int:
        """The resolved budget snapshot for this external turn."""
        return self._max_tool_rounds

    @property
    def tools_enabled(self) -> bool:
        """Whether this external turn still permits tool-enabled requests."""
        return self._finalization_cause is None

    @property
    def finalization_cause(self) -> FinalizationCause | None:
        """The snapshotted trigger, with repeat taking precedence at a boundary."""
        return self._finalization_cause

    def start_request(self) -> bool:
        """Claim the sole tools-disabled request, preventing retry recursion."""
        if self._finalization_requested:
            raise ToolLoopError(
                "Tool loop protection terminated this turn: tools-disabled "
                "finalization was already attempted."
            )
        if not self.tools_enabled:
            self._finalization_requested = True
        return self.tools_enabled

    def request_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply one request-only intervention to a deep copy of canonical history.

        Normal requests retain the existing history semantics. Intervention
        context survives failed attempts until a completed response is acknowledged;
        it never enters persistence or replay state.
        """
        decision = self._pending_request_round
        if decision is None:
            return history
        messages = copy.deepcopy(history)
        if message := decision.request_message():
            messages.append(message)
        return messages

    def acknowledge_response(self) -> None:
        """Clear delivered context after stream completion, before checking its round."""
        self._pending_request_round = None

    def validate_response(self, response: Any, tool_uses: list[dict[str, Any]]) -> None:
        """Fail closed if the finalization request cannot produce a final answer."""
        if not self.tools_enabled and (
            tool_uses or not isinstance(response, str) or not response.strip()
        ):
            raise ToolLoopError(
                "Tool loop protection terminated this turn: tools-disabled "
                "finalization returned tool calls or an empty/malformed response."
            )

    def check_round(self, tool_uses: list[dict[str, Any]]) -> ToolLoopRound:
        """Decide on the whole batch before formatting, events, or dispatch."""
        if not tool_uses:
            self._last_batch = None
            self._batch_occurrences = 0
            return ToolLoopRound((), max_tool_rounds=self.max_tool_rounds)
        self._rounds += 1
        fingerprint = tool_batch_fingerprint(tool_uses)
        self._batch_occurrences = (
            self._batch_occurrences + 1 if fingerprint == self._last_batch else 1
        )
        self._last_batch = fingerprint
        if self._finalization_cause is None:
            if self._batch_occurrences > MAX_CONSECUTIVE_TOOL_BATCHES + 1:
                self._finalization_cause = "repeat"
            elif self._rounds > self.max_tool_rounds:
                self._finalization_cause = "budget"
        decision = ToolLoopRound(
            tuple(None for _ in tool_uses),
            needs_recovery=(
                self._batch_occurrences == MAX_CONSECUTIVE_TOOL_BATCHES + 1
                and self._finalization_cause is None
            ),
            finalization_required=self._finalization_cause is not None,
            max_tool_rounds=self.max_tool_rounds,
            finalization_cause=self._finalization_cause,
        )
        if decision.discard_assistant:
            self._pending_request_round = decision
            logger.warning(
                "Tool loop intervention: calls={} occurrence={} digest={}",
                len(tool_uses),
                self._batch_occurrences,
                fingerprint,
            )
        return decision
