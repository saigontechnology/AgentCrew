"""Regression tests for the A2A 100ms chunk accumulator.

Covers: serialized concurrent flushes under queue backpressure, flush-task
cleanup across tool recursion / input-required / cancellation / context-length
retry, and final-batch ordering before the last_chunk marker and terminal
COMPLETED status.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from a2a.server.events import EventQueueLegacy
from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from openai import APIError

from AgentCrew.modules.a2a.agent_executor import (
    AgentCrewA2AExecutor,
    AnswerArtifactState,
    ToolCallResult,
    _TurnChunkBuffer,
)
from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.llm.token_usage import TokenUsage


class _Ctx:
    call_context = None
    current_task = None
    task_id = "t1"
    context_id = "c1"
    message = None


class _BlockingQueue:
    """EventQueue stand-in whose first enqueue blocks until released."""

    def __init__(self) -> None:
        self.events: list = []
        self._blocks_first = True
        self._gate: asyncio.Future | None = None

    @property
    def blocked(self) -> bool:
        return self._gate is not None and not self._gate.done()

    async def enqueue_event(self, event) -> None:
        if self._blocks_first:
            self._blocks_first = False
            self._gate = asyncio.get_running_loop().create_future()
            await self._gate
        self.events.append(event)

    def release(self) -> None:
        if self._gate is not None and not self._gate.done():
            self._gate.set_result(None)


class _FakeAgent:
    """Minimal agent with scripted process_messages generators."""

    def __init__(self, generators) -> None:
        self._generators = list(generators)
        self._calls = 0

    async def process_messages(self, history, callback=None, **kwargs):
        gen = self._generators[self._calls](history, callback)
        self._calls += 1
        async for item in gen:
            yield item

    def format_message(self, message_type, data):
        return None

    def _extract_last_user_message_for_memory(self, history):
        return ""

    def store_memory_if_available(self, *args, **kwargs):
        pass

    def calculate_usage_cost(self, *args, **kwargs):
        return 0.0


def _make_executor(agent) -> AgentCrewA2AExecutor:
    store = AsyncMock()
    store.get_history = AsyncMock(return_value=[])
    store.get_pending_tools = AsyncMock(return_value=None)
    store.append_history = AsyncMock()
    return AgentCrewA2AExecutor(agent=agent, session_store=store)


def _track_flush_tasks(monkeypatch) -> list[asyncio.Task]:
    """Wrap flush_loop to record each turn's timer task and assert no overlap."""
    tracked: list[asyncio.Task] = []
    original = _TurnChunkBuffer.flush_loop

    async def tracked_flush_loop(self):
        assert all(t.done() for t in tracked), "prior flush task still alive"
        tracked.append(asyncio.current_task())
        await original(self)

    monkeypatch.setattr(_TurnChunkBuffer, "flush_loop", tracked_flush_loop)
    return tracked


async def _run_and_drain(executor, queue):
    async def drain():
        events = []
        while True:
            try:
                ev = await asyncio.wait_for(queue.dequeue_event(), timeout=0.3)
            except TimeoutError:
                break
            events.append(ev)
            queue.task_done()
        return events

    run_task = asyncio.create_task(
        executor._run_agent_loop(
            _Ctx(), queue, "t1", "c1", [], AnswerArtifactState("answer_t1")
        )
    )
    await asyncio.sleep(0.02)
    events = await drain()
    await asyncio.wait_for(run_task, timeout=5)
    return events


# -- FIX 1: serialized concurrent flushes --------------------------------------


@pytest.mark.asyncio
async def test_concurrent_flushes_serialized_append_state():
    queue = _BlockingQueue()
    state = AnswerArtifactState(artifact_id="answer_t")
    buffer = _TurnChunkBuffer(queue, "t", "c", state)

    buffer.add_answer("AAA")
    first_flush = asyncio.create_task(buffer.flush())
    for _ in range(1000):
        if queue.blocked:
            break
        await asyncio.sleep(0.001)
    assert queue.blocked, "first flush should be blocked inside enqueue"

    buffer.add_answer("BBB")
    second_flush = asyncio.create_task(buffer.flush())
    await asyncio.sleep(0.02)  # let the second flush contend on the lock
    queue.release()
    await asyncio.gather(first_flush, second_flush)

    assert len(queue.events) == 2
    first, second = queue.events
    assert first.append is False
    assert second.append is True
    assert "".join(p.text for p in first.artifact.parts) == "AAA"
    assert "".join(p.text for p in second.artifact.parts) == "BBB"
    combined = "".join(p.text for e in queue.events for p in e.artifact.parts)
    assert combined == "AAABBB"
    assert state.emitted is True
    assert state.accumulated_text == "AAABBB"


# -- FIX 2: flush task stopped at the end of each turn --------------------------


@pytest.mark.asyncio
async def test_tool_recursion_stops_prior_turn_flush_tasks(monkeypatch):
    tracked = _track_flush_tasks(monkeypatch)

    async def tool_turn(history, callback):
        yield "partial", "one ", None
        await asyncio.sleep(0)  # let the timer flush task run, as with real streams
        yield "partial", "two ", None
        await asyncio.sleep(0)
        yield "done", None, None
        callback([{"name": "demo_tool", "id": "1", "input": {}}], TokenUsage())

    async def final_turn(history, callback):
        yield "partial", "three ", None
        await asyncio.sleep(0)
        yield "partial", "four", None
        await asyncio.sleep(0)
        yield "done", None, None

    agent = _FakeAgent([tool_turn, final_turn])
    executor = _make_executor(agent)
    executor._execute_tool_calls = AsyncMock(return_value=ToolCallResult.CONTINUE)

    events = await _run_and_drain(executor, EventQueueLegacy(max_queue_size=1024))

    assert len(tracked) == 2, "one timer per turn"
    assert all(t.done() for t in tracked), "no prior-turn timer may stay alive"

    artifact_ids = [
        e.artifact.artifact_id for e in events if isinstance(e, TaskArtifactUpdateEvent)
    ]
    assert "answer_t1" in artifact_ids
    assert "answer_t1_turn_1" in artifact_ids

    last_chunk_pos = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, TaskArtifactUpdateEvent) and e.last_chunk
    )
    text_before_last = "".join(
        p.text
        for e in events[:last_chunk_pos]
        if isinstance(e, TaskArtifactUpdateEvent)
        for p in e.artifact.parts
        if p.text
    )
    assert text_before_last == "one two three four"
    completed_pos = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, TaskStatusUpdateEvent)
        and e.status.state == TaskState.TASK_STATE_COMPLETED
    )
    assert last_chunk_pos < completed_pos, "last_chunk must precede COMPLETED"


@pytest.mark.asyncio
async def test_input_required_stops_flush_task(monkeypatch):
    tracked = _track_flush_tasks(monkeypatch)

    async def ask_turn(history, callback):
        yield "partial", "ask ", None
        await asyncio.sleep(0)
        yield "partial", "me", None
        await asyncio.sleep(0)
        yield "done", None, None
        callback([{"name": "demo_tool", "id": "1", "input": {}}], TokenUsage())

    agent = _FakeAgent([ask_turn])
    executor = _make_executor(agent)
    executor._execute_tool_calls = AsyncMock(return_value=ToolCallResult.INPUT_REQUIRED)

    events = await _run_and_drain(executor, EventQueueLegacy(max_queue_size=1024))

    assert len(tracked) == 1
    assert tracked[0].done(), "timer must stop before the INPUT_REQUIRED return"
    completed = [
        e
        for e in events
        if isinstance(e, TaskStatusUpdateEvent)
        and e.status.state == TaskState.TASK_STATE_COMPLETED
    ]
    assert not completed


@pytest.mark.asyncio
async def test_cancellation_stops_flush_task(monkeypatch):
    tracked = _track_flush_tasks(monkeypatch)

    async def endless(history, callback):
        for _ in range(1000):
            yield "resp", "x", None
            await asyncio.sleep(0.005)

    agent = _FakeAgent([endless])
    executor = _make_executor(agent)
    queue = EventQueueLegacy(max_queue_size=1024)

    run_task = asyncio.create_task(
        executor._run_agent_loop(
            _Ctx(), queue, "t1", "c1", [], AnswerArtifactState("answer_t1")
        )
    )
    await asyncio.sleep(0.15)
    cancel_event = await executor._get_cancel_event("t1")
    cancel_event.set()
    await asyncio.wait_for(run_task, timeout=5)
    await queue.close(immediate=True)

    assert len(tracked) == 1
    assert tracked[0].done(), "timer must be cleaned up on cancellation"


class _FakeLocalAgent(LocalAgent):
    """Real LocalAgent subclass so the context-length retry isinstance check passes."""

    def __init__(self) -> None:
        object.__setattr__(self, "token_usage", TokenUsage())
        self.name = "fake"
        self._calls = 0

    async def process_messages(self, history, callback=None, **kwargs):
        self._calls += 1
        if self._calls == 1:
            yield "pre", "before ", None
            await asyncio.sleep(0)
            raise APIError("context window exceeded", request=None, body=None)
        yield "after", "retry ", None
        await asyncio.sleep(0)
        yield "done", None, None

    def get_model(self):
        return "fake-model"

    def format_message(self, message_type, data):
        return None

    def calculate_usage_cost(self, *args, **kwargs):
        return 0.0

    def _extract_last_user_message_for_memory(self, history):
        return ""

    def store_memory_if_available(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_context_length_retry_no_overlapping_timers(monkeypatch):
    tracked = _track_flush_tasks(monkeypatch)

    executor = _make_executor(_FakeLocalAgent())
    events = await _run_and_drain(executor, EventQueueLegacy(max_queue_size=1024))

    assert len(tracked) == 2, "one timer for the failed attempt, one for the retry"
    assert all(t.done() for t in tracked), "timers must not overlap"
    text = "".join(
        p.text
        for e in events
        if isinstance(e, TaskArtifactUpdateEvent)
        for p in e.artifact.parts
        if p.text
    )
    assert text == "before retry ", "pre-retry text must be flushed before the retry"


class _FailingArtifactQueue:
    """Accepts status events but raises on TaskArtifactUpdateEvent enqueue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    async def enqueue_event(self, event) -> None:
        if isinstance(event, TaskArtifactUpdateEvent):
            raise RuntimeError("artifact queue failure")  # noqa: TRY004 - simulates a broken queue, not an invalid type
        await self._queue.put(event)

    async def dequeue_event(self):
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


@pytest.mark.asyncio
async def test_failed_timer_flush_fails_request_not_completes(monkeypatch):
    """A failed background timer flush must FAIL the request, not COMPLETE it."""
    tracked = _track_flush_tasks(monkeypatch)

    async def burst_then_idle(history, callback):
        for i in range(5):
            yield "resp", f"tok{i} ", None
            await asyncio.sleep(0.001)
        # stay alive past the 100ms timer so the timer flush fires and fails
        for _ in range(30):
            yield "resp", None, None
            await asyncio.sleep(0.01)
        yield "done", None, None

    agent = _FakeAgent([burst_then_idle])
    executor = _make_executor(agent)
    queue = _FailingArtifactQueue()
    events = await _run_and_drain(executor, queue)

    states = [e.status.state for e in events if isinstance(e, TaskStatusUpdateEvent)]
    assert TaskState.TASK_STATE_FAILED in states
    assert TaskState.TASK_STATE_COMPLETED not in states
    assert len(tracked) == 1
    assert tracked[0].done(), "timer task must be cleaned up"
    assert isinstance(tracked[0].exception(), RuntimeError), (
        "the timer exception must be observed, not silently discarded"
    )
