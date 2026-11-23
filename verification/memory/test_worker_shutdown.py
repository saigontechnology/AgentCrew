"""Provider-free regression checks for the memory worker's owned event loop."""

from __future__ import annotations

import asyncio
import gc
import queue
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import openai
import pytest

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.events.hooks import HookRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.memory.memory_worker import MemoryWorker


class SSEBytes(httpx2.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"id":"fake","choices":[]}\n\ndata: [DONE]\n\n'

    async def aclose(self):
        pass


@pytest.fixture
def loop_observations(monkeypatch):
    observations = SimpleNamespace(
        errors=[], pending_at_close=[], finalized=[], loops=[]
    )
    original_factory = asyncio.events.new_event_loop

    def loop_factory():
        loop = original_factory()
        loop.set_debug(True)
        loop.set_exception_handler(
            lambda _, context: observations.errors.append(context)
        )
        original_finalizer = loop._asyncgen_finalizer_hook
        original_close = loop.close

        def finalize(generator):
            observations.finalized.append(generator.ag_code.co_qualname)
            original_finalizer(generator)

        def close():
            observations.pending_at_close.extend(
                repr(task) for task in asyncio.all_tasks(loop)
            )
            original_close()

        loop._asyncgen_finalizer_hook = finalize
        loop.close = close
        observations.loops.append(loop)
        return loop

    monkeypatch.setattr(asyncio.events, "new_event_loop", loop_factory)
    monkeypatch.setattr(asyncio, "new_event_loop", loop_factory)
    yield observations
    gc.collect()
    assert observations.loops
    assert all(loop.is_closed() for loop in observations.loops)
    assert observations.pending_at_close == []
    assert observations.errors == []


def prepare_worker(operation, *, stop_mode="sentinel"):
    worker = MemoryWorker()
    worker._store_conversation_internal = operation
    worker._conversation_queue.put({"operation_id": "first"})
    if stop_mode == "sentinel":
        worker._conversation_queue.put({"type": "shutdown"})
    else:
        worker._memory_stop_event.set()
    return worker


@pytest.mark.parametrize("stop_mode", ["sentinel", "stop_event"])
@pytest.mark.parametrize("fail_operation", [False, True])
def test_sdk_done_finalizer_is_drained_before_worker_loop_closes(
    loop_observations, monkeypatch, stop_mode, fail_operation
):
    monkeypatch.setattr(
        "AgentCrew.modules.memory.memory_worker.DEFAULT_QUEUE_TIMEOUT", 0.001
    )
    error = ValueError("operation failed after provider completion")

    async def operation(_):
        client = openai.AsyncOpenAI(api_key="not-a-real-key")
        response = httpx2.Response(
            200,
            stream=SSEBytes(),
            request=httpx2.Request("GET", "https://example.invalid"),
        )
        try:
            async with openai.AsyncStream(
                cast_to=object, response=response, client=client
            ) as stream:
                async for _ in stream:
                    pass
        finally:
            await client.close()
        if fail_operation:
            raise error

    prepare_worker(operation, stop_mode=stop_mode)._worker_loop()
    assert "AsyncStream._iter_events" in loop_observations.finalized


@pytest.mark.parametrize("outcome", ["return", "error", "cancel", "stop"])
def test_partially_consumed_agent_stream_is_closed_and_awaited(
    loop_observations, monkeypatch, outcome
):
    events = []
    retained_streams = []
    started = Event()
    cancellation = asyncio.CancelledError("worker cancelled")
    hooks = SimpleNamespace(
        run_before=AsyncMock(side_effect=lambda _, **payload: payload),
        run_after=AsyncMock(side_effect=lambda _, **payload: payload["result"]),
    )
    monkeypatch.setattr(HookRegistry, "get_instance", lambda: hooks)

    class ProviderStream:
        async def __aenter__(self):
            events.append("opened")
            return self

        async def __aexit__(self, *args):
            events.append("closing")
            loop = asyncio.get_running_loop()
            checkpoint = loop.create_future()
            loop.call_soon(checkpoint.set_result, None)
            await checkpoint
            events.append("closed")

        def __aiter__(self):
            return self

        async def __anext__(self):
            return "partial"

    agent = LocalAgent.__new__(LocalAgent)
    agent.llm = SimpleNamespace(
        _provider_name="fake",
        model="fake-model",
        stream_assistant_response=AsyncMock(
            side_effect=lambda *a, **kw: ProviderStream()
        ),
        process_stream_chunk=lambda chunk, *args: (
            chunk,
            [],
            TokenUsage(),
            chunk,
            None,
        ),
    )
    agent.pre_process_message = AsyncMock(side_effect=lambda messages: messages)

    async def operation(_):
        stream = agent.process_messages([{"role": "user", "content": "hello"}])
        retained_streams.append(stream)
        assert await anext(stream) == ("partial", "partial", None)
        started.set()
        if outcome == "cancel":
            raise cancellation
        if outcome == "error":
            raise ValueError("operation failed with an active stream")

    worker = prepare_worker(operation)
    if outcome == "stop":
        worker._conversation_queue = queue.Queue()
        worker._conversation_queue.put({"operation_id": "first"})
        worker.start()
        try:
            assert started.wait(2)
        finally:
            worker.stop()
        assert not worker._memory_thread.is_alive()
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError) as caught:
            worker._worker_loop()
        assert caught.value is cancellation
    else:
        worker._worker_loop()
    assert events == ["opened", "closing", "closed"]
    assert retained_streams[0]._iterator.ag_frame is None


def test_worker_operations_share_one_loop(loop_observations):
    loops = []

    async def operation(_):
        loops.append(asyncio.get_running_loop())

    worker = prepare_worker(operation)
    worker._conversation_queue = queue.Queue()
    worker._conversation_queue.put({"operation_id": "first"})
    worker._conversation_queue.put({"operation_id": "second"})
    worker._conversation_queue.put({"type": "shutdown"})
    worker._worker_loop()
    assert len(loops) == 2
    assert loops[0] is loops[1]
