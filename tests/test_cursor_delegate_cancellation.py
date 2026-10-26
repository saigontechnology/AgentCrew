"""Fake-SDK regressions for local-to-Cursor delegation cancellation."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from loguru import logger

from AgentCrew.modules.agents import cursor_agent
from AgentCrew.modules.agents.tools import delegate as delegate_module
from AgentCrew.modules.agents.tools.delegate import get_delegate_tool_handler
from AgentCrew.modules.events import AppEvents, EventBus


def _setup_delegate(tmp_path, monkeypatch, sdk_agent):
    monkeypatch.setattr(
        cursor_agent,
        "_load_cursor_sdk",
        lambda: SimpleNamespace(
            Agent=SimpleNamespace(create=lambda **kwargs: sdk_agent),
            LocalAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setattr(delegate_module, "DELEGATE_CLEANUP_TIMEOUT", 0.03)
    target = cursor_agent.CursorAgent("Cursor", "worker", cwd=str(tmp_path))
    monkeypatch.setattr(target, "_mcp_custom_tools", lambda _sdk: {})
    source = SimpleNamespace(name="Source")
    manager = SimpleNamespace(
        current_agent=source, agents={"Source": source, "Cursor": target}
    )
    bus = EventBus()
    events = []
    finished = asyncio.Event()

    def record(**event):
        events.append(event["kind"])
        if event["kind"] == "finish":
            finished.set()

    bus.on(AppEvents.DELEGATE_PROGRESS, record)
    monkeypatch.setattr(EventBus, "get_instance", lambda: bus)
    return get_delegate_tool_handler(manager), target, manager, events, finished


@pytest.mark.asyncio
async def test_cancellation_during_send_cancels_published_run_before_stream(
    tmp_path, monkeypatch
):
    in_send = threading.Event()
    allow_send = threading.Event()
    closed = threading.Event()

    class Run:
        cancels = 0
        stream_calls = 0

        def cancel(self):
            self.cancels += 1

        def stream(self):
            self.stream_calls += 1
            return iter(())

    run = Run()

    class Agent:
        def send(self, _prompt):
            in_send.set()
            assert allow_send.wait(timeout=3)
            return run

        def close(self):
            closed.set()

    handler, target, manager, events, finished = _setup_delegate(
        tmp_path, monkeypatch, Agent()
    )
    task = asyncio.create_task(handler(target_agent="Cursor", task_description="task"))
    try:
        assert await asyncio.to_thread(in_send.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert target.cwd in cursor_agent._cwd_owners
    finally:
        allow_send.set()
    assert await asyncio.to_thread(closed.wait, 2)
    await asyncio.wait_for(finished.wait(), 1)
    assert run.cancels == 1
    assert run.stream_calls == 0
    assert events == ["start", "finish"]
    assert target.cwd not in cursor_agent._cwd_owners
    assert manager.current_agent.name == "Source"


@pytest.mark.asyncio
async def test_unresponsive_run_bounds_cancel_wait_but_retains_cwd_until_exit(
    tmp_path, monkeypatch
):
    entered_stream = threading.Event()
    unblock_stream = threading.Event()
    closed = threading.Event()

    class Run:
        cancelled = False

        def cancel(self):
            self.cancelled = True

        def stream(self):
            entered_stream.set()
            assert unblock_stream.wait(timeout=3)
            return iter(())

    run = Run()

    class Agent:
        def send(self, _prompt):
            return run

        def close(self):
            closed.set()

    handler, target, manager, events, finished = _setup_delegate(
        tmp_path, monkeypatch, Agent()
    )
    task = asyncio.create_task(handler(target_agent="Cursor", task_description="task"))
    try:
        assert await asyncio.to_thread(entered_stream.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(finished.wait(), 1)
        assert events == ["start", "finish"]
        assert run.cancelled
        assert not closed.is_set()
        assert target.cwd in cursor_agent._cwd_owners
    finally:
        unblock_stream.set()
    assert await asyncio.to_thread(closed.wait, 2)
    assert target.cwd not in cursor_agent._cwd_owners
    assert manager.current_agent.name == "Source"


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_original_cancellation_and_hides_error(
    tmp_path, monkeypatch
):
    entered_stream = threading.Event()
    unblock_stream = threading.Event()
    closed = threading.Event()
    secret = "fake-sensitive-sdk-exception"

    class Run:
        def stream(self):
            entered_stream.set()
            assert unblock_stream.wait(timeout=3)
            return iter(())

    run = Run()

    class Agent:
        def send(self, _prompt):
            return run

        def close(self):
            closed.set()

    handler, target, _, events, finished = _setup_delegate(
        tmp_path, monkeypatch, Agent()
    )

    def fail_cancel(_run):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        cursor_agent.CursorAgent, "_cancel_run", staticmethod(fail_cancel)
    )
    messages = []
    sink = logger.add(
        lambda message: messages.append(message.record["message"]), level="WARNING"
    )
    task = asyncio.create_task(handler(target_agent="Cursor", task_description="task"))
    try:
        assert await asyncio.to_thread(entered_stream.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(finished.wait(), 1)
        assert target.cwd in cursor_agent._cwd_owners
    finally:
        unblock_stream.set()
    try:
        assert await asyncio.to_thread(closed.wait, 2)
        await asyncio.sleep(0)
        assert events == ["start", "finish"]
        assert target.cwd not in cursor_agent._cwd_owners
        assert secret not in "\n".join(messages)
    finally:
        logger.remove(sink)
