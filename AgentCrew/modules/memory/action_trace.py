"""Bounded runtime evidence; never an authorization source or model reasoning log."""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, fields, replace
from typing import Any

MAX_TRACE_EVENTS = 256
MAX_TRACE_SUMMARY = 1024
MAX_TRACE_EPISODES = 32
MAX_HYDRATED_EVENTS = 1024
MAX_ACTION_JSON_BYTES = 16384
MAX_TRACE_METADATA_JSON_BYTES = 4096
_REFERENCE = re.compile(r"[\w .:/@+-]{1,192}\Z", re.ASCII)
_RESOURCE = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_IDENTIFIER = re.compile(r"[\w .:/@+-]{1,160}\Z", re.ASCII)


def identifier(value: object) -> str:
    """Accept bounded protocol identifiers, never stringify arbitrary objects."""
    return value if type(value) is str and _IDENTIFIER.fullmatch(value) else "unknown"


def agent_identity(value: object) -> str:
    """Preserve bounded configured names, including Unicode and punctuation."""
    if (
        type(value) is str
        and 0 < len(value) <= 160
        and all(
            ord(character) >= 32 and not 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        return value
    return "unknown"


@dataclass(frozen=True)
class ActionEvent:
    action_id: str
    event_index: int
    batch: int
    ordinal: int
    agent_name: str
    kind: str
    tool_name: str
    tool_call_id: str
    status: str
    started_at: float | None
    completed_at: float | None
    duration_ms: float | None
    provenance: str = "runtime_observed"
    request_reference: str | None = None
    result_reference: str | None = None
    result_summary: str | None = None
    summary_provenance: str | None = None
    summary_availability: str = "not_requested"
    summary_truncated: bool = False
    parent_action_id: str | None = None


@dataclass(frozen=True)
class ActionTrace:
    turn_id: str
    inference_scope: str
    agent_name: str
    started_at: float
    completed_at: float
    events: tuple[ActionEvent, ...]
    observed_count: int
    stored_count: int
    incomplete_reasons: tuple[str, ...] = ()
    resource_references: tuple[str, ...] = ()
    coverage: str = "registered_runtime_lifecycle_only"


_ACTION_FIELDS = frozenset(field.name for field in fields(ActionEvent))


class ActionTraceValidationError(ValueError):
    """Malformed evidence must not be persisted or returned as runtime fact."""


def _text(value, limit=160, *, optional=False, empty=False, pattern=None):
    if optional and value is None:
        return
    if type(value) is not str or not (0 if empty else 1) <= len(value) <= limit:
        raise ActionTraceValidationError("Invalid bounded trace text")
    if pattern is not None:
        valid = pattern.fullmatch(value) is not None
    else:
        valid = not any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
        )
    if not valid:
        raise ActionTraceValidationError("Invalid bounded trace text")


def _integer(value, maximum=2**31 - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ActionTraceValidationError("Invalid trace integer")


def _number(value, *, optional=False):
    if optional and value is None:
        return
    try:
        valid = (
            type(value) in (int, float)
            and math.isfinite(value)
            and 0 <= value <= 253402300799000
        )
    except OverflowError:
        valid = False
    if not valid:
        raise ActionTraceValidationError("Invalid trace timestamp/duration")


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise ActionTraceValidationError("Invalid trace enum")


def validate_action_event(event: ActionEvent, trace: ActionTrace) -> None:
    """Background-only validation shared by storage and decoded hydration."""
    if type(event) is not ActionEvent or event.agent_name != trace.agent_name:
        raise ActionTraceValidationError("Invalid trace event ownership")
    _text(event.agent_name)
    _text(event.action_id, pattern=_IDENTIFIER)
    _text(event.parent_action_id, optional=True, pattern=_IDENTIFIER)
    if event.parent_action_id == event.action_id:
        raise ActionTraceValidationError("Self-parented action")
    for value in (event.event_index, event.batch, event.ordinal):
        _integer(value)
    if event.event_index >= trace.stored_count:
        raise ActionTraceValidationError("Event index exceeds stored count")
    _choice(event.kind, {"tool", "response"})
    _choice(
        event.status,
        {"started", "succeeded", "failed", "rejected", "canceled", "not_performed"},
    )
    _choice(event.provenance, {"runtime_observed"})
    _choice(
        event.summary_availability,
        {"not_requested", "ready", "unavailable", "lookup_failed"},
    )
    _text(event.result_summary, MAX_TRACE_SUMMARY, optional=True, empty=True)
    if type(event.summary_truncated) is not bool:
        raise ActionTraceValidationError("Invalid summary truncation flag")
    if event.result_summary is None:
        if (
            event.summary_provenance is not None
            or event.summary_availability == "ready"
            or event.summary_truncated
        ):
            raise ActionTraceValidationError("Invalid missing summary metadata")
    elif (
        event.summary_provenance != "model_reported"
        or event.summary_availability != "ready"
    ):
        raise ActionTraceValidationError("Invalid summary provenance/availability")
    for value in (event.request_reference, event.result_reference):
        _text(value, 192, optional=True, pattern=_REFERENCE)
    if event.kind == "tool":
        _text(event.tool_name, pattern=_IDENTIFIER)
        _text(event.tool_call_id, pattern=_IDENTIFIER)
        if event.request_reference not in (
            None,
            f"tool_call:{event.tool_call_id}",
        ) or event.result_reference not in (None, f"tool_result:{event.tool_call_id}"):
            raise ActionTraceValidationError("Invalid tool reference")
    elif (
        event.tool_name != ""
        or event.tool_call_id != ""
        or event.request_reference is not None
        or event.result_reference not in (None, "assistant_response")
        or event.status not in {"succeeded", "failed", "canceled"}
        or event.started_at is not None
        or event.duration_ms is not None
    ):
        raise ActionTraceValidationError("Invalid response event")
    for value in (event.started_at, event.completed_at, event.duration_ms):
        _number(value, optional=True)
    if event.status == "started":
        if (
            event.started_at is None
            or event.completed_at is not None
            or event.duration_ms is not None
        ):
            raise ActionTraceValidationError("Invalid started action timing")
    elif event.completed_at is None:
        raise ActionTraceValidationError("Terminal action has no completion time")
    elif (event.started_at is None) != (event.duration_ms is None):
        raise ActionTraceValidationError("Invalid terminal action duration")
    for value in (event.started_at, event.completed_at):
        if value is not None and not trace.started_at <= value <= trace.completed_at:
            raise ActionTraceValidationError("Action outside episode time bounds")
    if (
        event.started_at is not None
        and event.completed_at is not None
        and event.started_at > event.completed_at
    ):
        raise ActionTraceValidationError("Action completed before start")


def validate_action_trace(
    trace: ActionTrace, owner: str, *, partial=False, session_id=None
) -> None:
    """Validate an immutable episode outside hooks; partial is hydration-cap only."""
    if type(trace) is not ActionTrace or trace.agent_name != owner:
        raise ActionTraceValidationError("Invalid trace ownership")
    _text(trace.agent_name)
    _text(trace.turn_id, pattern=_IDENTIFIER)
    _text(trace.inference_scope, pattern=_IDENTIFIER)
    _text(session_id, optional=True)
    _choice(trace.coverage, {"registered_runtime_lifecycle_only"})
    _number(trace.started_at)
    _number(trace.completed_at)
    if trace.started_at > trace.completed_at:
        raise ActionTraceValidationError("Episode completed before start")
    _integer(trace.observed_count, 2**63 - 1)
    _integer(trace.stored_count, MAX_TRACE_EVENTS)
    if trace.observed_count < trace.stored_count or type(trace.events) is not tuple:
        raise ActionTraceValidationError("Invalid trace counts/events")
    if len(trace.events) > trace.stored_count or (
        not partial and len(trace.events) != trace.stored_count
    ):
        raise ActionTraceValidationError("Invalid trace stored count")
    validate_incomplete_reasons(trace.incomplete_reasons)
    if (
        type(trace.resource_references) is not tuple
        or len(trace.resource_references) > 32
    ):
        raise ActionTraceValidationError("Invalid resource references")
    for reference in trace.resource_references:
        _text(reference, 64, pattern=_RESOURCE)
    if len(set(trace.resource_references)) != len(trace.resource_references):
        raise ActionTraceValidationError("Duplicate resource reference")
    for event in trace.events:
        validate_action_event(event, trace)
    validate_action_sequence(trace.events, trace.stored_count, partial=partial)


def validate_incomplete_reasons(reasons: tuple[str, ...]) -> None:
    """Validate merged legacy/incoming metadata without rechecking event fields."""
    if type(reasons) is not tuple or len(reasons) > 16:
        raise ActionTraceValidationError("Invalid incomplete reasons")
    for reason in reasons:
        _text(reason, 80, pattern=_REASON)
    if len(set(reasons)) != len(reasons):
        raise ActionTraceValidationError("Duplicate incomplete reason")


def validate_action_sequence(
    events: tuple[ActionEvent, ...], stored_count: int, *, partial=False
) -> None:
    """Check count and index order after individual event-field validation."""
    _integer(stored_count, MAX_TRACE_EVENTS)
    if (
        type(events) is not tuple
        or len(events) > stored_count
        or (not partial and len(events) != stored_count)
    ):
        raise ActionTraceValidationError("Invalid trace stored count")
    indices = []
    for event in events:
        if type(event) is not ActionEvent:
            raise ActionTraceValidationError("Invalid trace event type")
        _integer(event.event_index)
        if event.event_index >= stored_count:
            raise ActionTraceValidationError("Event index exceeds stored count")
        indices.append(event.event_index)
    if len(set(indices)) != len(indices) or (
        not partial and indices != list(range(stored_count))
    ):
        raise ActionTraceValidationError("Noncontiguous trace event indices")


def _json_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ActionTraceValidationError("Duplicate trace JSON key")
    return value


def decode_trace_json(value, *, limit):
    """Bound before decoding, rejecting nonstandard constants and duplicate keys."""
    if type(value) is bytes:
        if len(value) > limit:
            raise ActionTraceValidationError("Invalid trace JSON size/type")
        try:
            value = value.decode("utf-8")
        except UnicodeError as error:
            raise ActionTraceValidationError("Invalid trace JSON encoding") from error
    if (
        type(value) is not str
        or len(value) > limit
        or len(value.encode("utf-8")) > limit
    ):
        raise ActionTraceValidationError("Invalid trace JSON size/type")

    def invalid_constant(_value):
        raise ActionTraceValidationError("Nonfinite trace JSON constant")

    try:
        return json.loads(
            value, object_pairs_hook=_json_object, parse_constant=invalid_constant
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise ActionTraceValidationError("Invalid trace JSON") from error


def decode_action_event(value, trace: ActionTrace) -> ActionEvent:
    payload = decode_trace_json(value, limit=MAX_ACTION_JSON_BYTES)
    if type(payload) is not dict or set(payload) != _ACTION_FIELDS:
        raise ActionTraceValidationError("Invalid action JSON schema")
    event = ActionEvent(**payload)
    validate_action_event(event, trace)
    return event


def decode_trace_list(value) -> tuple[str, ...]:
    payload = decode_trace_json(value, limit=MAX_TRACE_METADATA_JSON_BYTES)
    if type(payload) is not list:
        raise ActionTraceValidationError("Invalid trace metadata list")
    return tuple(payload)


@dataclass
class _Turn:
    turn_id: str
    scope: str
    started_at: float = field(default_factory=time.time)
    events: list[ActionEvent] = field(default_factory=list)
    pending: dict[tuple[str, str], tuple[ActionEvent, float]] = field(
        default_factory=dict
    )
    terminal: dict[tuple[str, str], ActionEvent] = field(default_factory=dict)
    completed_agents: set[str] = field(default_factory=set)
    observed: dict[str, int] = field(default_factory=dict)
    batch: int = 0
    capped: bool = False
    anomalies: set[str] = field(default_factory=set)


class ActionTraceCollector:
    """One application-owned collaborator with per-request, inherited task state.

    Hooks do fixed-size identifier extraction and bounded append/dict bookkeeping.
    They never read raw arguments/result bodies, summaries, resources, SQLite or an LLM.
    State mutations contain no awaits; sibling asyncio tasks share the turn but
    carry their own batch position. No execution lock serializes tool calls.
    """

    def __init__(self, hooks: Any, max_events: int = MAX_TRACE_EVENTS) -> None:
        if not 1 <= max_events <= MAX_TRACE_EVENTS:
            raise ValueError("Trace event limit must be within 1..256")
        self.max_events = max_events
        self._turn: ContextVar[_Turn | None] = ContextVar(
            "action_trace_turn", default=None
        )
        self._position: ContextVar[tuple[int, int] | None] = ContextVar(
            "action_trace_position", default=None
        )
        self._hooks = hooks
        self._owner = f"action_trace:{uuid.uuid4()}"

    def register(self) -> None:
        from AgentCrew.modules.events.hooks import Hook, HookPhase, HookPoints

        self._hooks.register(
            Hook(
                HookPoints.TOOL_EXECUTE,
                HookPhase.BEFORE,
                self.before_tool,
                priority=20000,
            ),
            owner=self._owner,
        )
        self._hooks.register(
            Hook(
                HookPoints.TOOL_EXECUTE,
                HookPhase.AFTER,
                self.after_tool,
                priority=20000,
            ),
            owner=self._owner,
        )
        self._hooks.register(
            Hook(
                HookPoints.RESPONSE_COMPLETE,
                HookPhase.AFTER,
                self.after_response,
                priority=20000,
            ),
            owner=self._owner,
        )

    def close(self) -> None:
        self._hooks.unregister_owner(self._owner)

    def bind(self, scope: str) -> Token:
        return self._turn.set(_Turn(str(uuid.uuid4()), identifier(scope)))

    def reset(self, token: Token) -> None:
        self._turn.reset(token)

    def _append(self, turn: _Turn, event: ActionEvent) -> None:
        agent = event.agent_name
        if agent in turn.observed or len(turn.observed) < self.max_events:
            turn.observed[agent] = turn.observed.get(agent, 0) + 1
        if len(turn.events) >= self.max_events:
            turn.capped = True
            return
        turn.events.append(replace(event, event_index=len(turn.events)))

    def next_batch(self) -> int | None:
        turn = self._turn.get()
        if turn is None:
            return None
        turn.batch += 1
        return turn.batch

    async def execute_in_batch(
        self, executor: Any, tool_use: dict, batch: int | None, ordinal: int
    ) -> Any:
        token = self._position.set((batch, ordinal) if batch is not None else None)
        try:
            return await executor(tool_use)
        finally:
            self._position.reset(token)

    def before_tool(self, context: dict) -> dict:
        turn = self._turn.get()
        if turn is None:
            return context
        agent, call = (
            agent_identity(context.get("agent_name")),
            identifier(context.get("tool_id")),
        )
        key = (agent, call)
        if key in turn.pending or key in turn.terminal:
            turn.anomalies.add("duplicate_tool_call_id")
            turn.terminal.pop(key, None)
        if "unknown" in key or identifier(context.get("tool_name")) == "unknown":
            turn.anomalies.add("invalid_identifiers")
        position = self._position.get()
        if position is not None:
            self._position.set(None)
        if position is None:
            turn.batch += 1
            position = (turn.batch, 0)
        now = time.time()
        event = ActionEvent(
            str(uuid.uuid4()),
            0,
            *position,
            agent,
            "tool",
            identifier(context.get("tool_name")),
            call,
            "started",
            now,
            None,
            None,
            request_reference=f"tool_call:{call}",
        )
        if len(turn.events) < self.max_events:
            turn.pending[(agent, call)] = (event, time.monotonic())
        self._append(turn, event)
        return context

    def after_tool(self, context: dict, result: Any) -> Any:
        status = context.get("execution_status", "succeeded")
        if type(result) is dict and result.get("is_error") is True:
            status = "failed"
        if status not in {
            "succeeded",
            "failed",
            "rejected",
            "canceled",
            "not_performed",
        }:
            status = "failed"
        self.observe_result(context, status)
        return result

    def observe_result(self, context: dict, status: str) -> None:
        turn = self._turn.get()
        if turn is None:
            return
        agent, call = (
            agent_identity(context.get("agent_name")),
            identifier(context.get("tool_id")),
        )
        key = (agent, call)
        previous = turn.terminal.get(key)
        if previous is not None:
            if status == "failed" and previous.status == "succeeded":
                self._append(turn, replace(previous, status="failed"))
                turn.terminal[key] = replace(previous, status="failed")
            return
        pending = turn.pending.pop(key, None)
        now = time.time()
        if pending is not None:
            event, clock = pending
            event = replace(
                event,
                status=status,
                tool_name=identifier(context.get("tool_name")),
                completed_at=now,
                duration_ms=max(0, (time.monotonic() - clock) * 1000),
                result_reference=f"tool_result:{call}",
            )
        else:
            position = self._position.get()
            if position is None:
                turn.batch += 1
                position = (turn.batch, 0)
            event = ActionEvent(
                str(uuid.uuid4()),
                0,
                *position,
                agent,
                "tool",
                identifier(context.get("tool_name")),
                call,
                status,
                None,
                now,
                None,
            )
        if len(turn.terminal) < self.max_events:
            turn.terminal[key] = event
        self._append(turn, event)

    def observe_formatted_result(self, agent_name: str, data: dict) -> None:
        tool = data.get("tool_use")
        if type(tool) is not dict:
            return
        turn = self._turn.get()
        if turn is None:
            return
        key = (agent_identity(agent_name), identifier(tool.get("id")))
        if data.get("is_rejected"):
            status = "rejected"
        elif data.get("is_error"):
            status = (
                "failed"
                if key in turn.terminal or key in turn.pending
                else "not_performed"
            )
        else:
            return
        self.observe_result(
            {
                "agent_name": agent_name,
                "tool_id": tool.get("id"),
                "tool_name": tool.get("name"),
            },
            status,
        )

    def complete(self, agent_name: str, status: str = "succeeded") -> None:
        turn = self._turn.get()
        agent = agent_identity(agent_name)
        if turn is None or agent in turn.completed_agents:
            return
        if len(turn.completed_agents) < self.max_events:
            turn.completed_agents.add(agent)
        turn.batch += 1
        self._append(
            turn,
            ActionEvent(
                str(uuid.uuid4()),
                0,
                turn.batch,
                0,
                agent,
                "response",
                "",
                "",
                status,
                None,
                time.time(),
                None,
                result_reference="assistant_response",
            ),
        )

    def after_response(self, context: dict, result: Any) -> Any:
        agent = context.get("agent_name")
        if type(agent) is str:
            self.complete(agent, "canceled" if context.get("canceled") else "succeeded")
        return result

    def snapshot(
        self,
        agent_name: str,
        summary_service: Any = None,
        summary_scope: str | None = None,
    ) -> ActionTrace | None:
        from .resources import resource_id, snapshot_resources

        turn = self._turn.get()
        if turn is None:
            return None
        agent = agent_identity(agent_name)
        if agent == "unknown" and agent_name != "unknown":
            return None
        try:
            _text(agent)
        except ActionTraceValidationError:
            return None
        events = []
        for event in turn.events:
            if event.agent_name != agent:
                continue
            if (
                event.kind == "tool"
                and event.status != "started"
                and summary_service is not None
            ):
                event = replace(event, summary_availability="unavailable")
                try:
                    summary = summary_service.get_ready_summary(
                        summary_scope, event.tool_call_id, event.tool_name
                    )
                    if type(summary) is str:
                        from AgentCrew.modules.agents.tool_result_summary import (
                            ToolResultSummaryService,
                        )

                        redacted = ToolResultSummaryService._redact_text(
                            summary[:MAX_TRACE_SUMMARY]
                        )
                        redacted = "".join(
                            " "
                            if unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                            else character
                            for character in redacted
                        )
                        event = replace(
                            event,
                            result_summary=redacted[:MAX_TRACE_SUMMARY],
                            summary_truncated=len(summary) > MAX_TRACE_SUMMARY
                            or len(redacted) > MAX_TRACE_SUMMARY,
                            summary_provenance="model_reported",
                            summary_availability="ready",
                        )
                except Exception:
                    event = replace(event, summary_availability="lookup_failed")
            if event.event_index != len(events):
                event = replace(event, event_index=len(events))
            events.append(event)
        reasons = sorted(turn.anomalies)
        if turn.capped:
            reasons.append("event_cap")
        if any(key[0] == agent for key in turn.pending):
            reasons.append("unfinished_actions")
        if agent not in turn.completed_agents:
            reasons.append("response_not_completed")
        resources = snapshot_resources()
        if len(resources) >= 32:
            reasons.append("resource_reference_cap_reached")
        return ActionTrace(
            turn.turn_id,
            turn.scope,
            agent,
            turn.started_at,
            time.time(),
            tuple(events),
            turn.observed.get(agent, 0),
            len(events),
            tuple(reasons),
            tuple(resource_id(resource.uri) for resource in resources[:32]),
        )
