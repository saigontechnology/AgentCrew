"""Provider-independent, per-external-turn protection against tool-call loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

MAX_CONSECUTIVE_TOOL_EXECUTIONS = 2
MAX_TOOL_ROUNDS = 50

REPEAT_WARNING = (
    "Tool loop protection: this exact tool call has already been requested "
    "2 times in consecutive model rounds. It was not executed again. "
    "Reuse the previous result, change the tool or arguments, or finish the task "
    "without repeating this call."
)


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

    A terminal batch is entirely non-executing. Callers must record all errors
    before raising, preserving assistant/tool pairing even when terminating.
    """

    errors: tuple[str | None, ...]
    terminal_error: str | None = None

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

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}
        self._rounds = 0
        self._terminal_error: str | None = None

    def check_round(self, tool_uses: list[dict[str, Any]]) -> ToolLoopRound:
        """Decide before confirmation, hooks, or tool dispatch can take place."""
        if not tool_uses:
            self._occurrences = {}
            return ToolLoopRound(())
        self._rounds += 1
        if self._terminal_error is None and self._rounds > MAX_TOOL_ROUNDS:
            self._terminal_error = (
                f"Tool loop protection terminated this turn: exceeded {MAX_TOOL_ROUNDS} "
                "model tool rounds. No tools in this round were executed."
            )
            logger.error(self._terminal_error)
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
                tuple(self._terminal_error for _ in tool_uses), self._terminal_error
            )
        return ToolLoopRound(tuple(errors))
