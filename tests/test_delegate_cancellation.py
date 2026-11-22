import asyncio
from importlib import import_module
from types import SimpleNamespace

import pytest

from AgentCrew.modules.agents.agent_runner import run_agent_loop
from AgentCrew.modules.agents.tools.delegate import get_delegate_tool_handler
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.llm.token_usage import TokenUsage


class Stream:
    def __init__(self, callback, *, tools=None, block_stream=False):
        self.callback = callback
        self.tools = tools or []
        self.block_stream = block_stream
        self.started = asyncio.Event()
        self.closed = 0
        self.turn = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.turn:
            self.callback(self.tools, TokenUsage())
            raise StopAsyncIteration
        self.turn += 1
        if self.block_stream:
            self.started.set()
            await asyncio.Event().wait()
        return "answer", "answer", None

    async def aclose(self):
        self.closed += 1

    def format_assistant_message(self, response, **kwargs):
        return {"role": "assistant", "content": response}


class Agent:
    def __init__(self, *, block_stream=False, nested_tool=False):
        self.block_stream = block_stream
        self.nested_tool = nested_tool
        self.tool_started = asyncio.Event()
        self.streams = []

    def process_messages(self, history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        stream = Stream(
            callback,
            tools=[{"name": "read_file"}]
            if self.nested_tool and not self.streams
            else [],
            block_stream=self.block_stream,
        )
        self.streams.append(stream)
        return stream

    def validate_tool_use(self, tool):
        return None

    def format_message(self, *args):
        return {"role": "tool", "content": "result"}

    async def execute_tool_call(self, tool):
        self.tool_started.set()
        await asyncio.Event().wait()

    def extract_last_user_message_for_memory(self, history):
        return "user"

    def store_memory_if_available(self, *args):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("nested_tool", [False, True])
async def test_runner_closes_stream_and_propagates_cancellation(nested_tool):
    agent = Agent(block_stream=not nested_tool, nested_tool=nested_tool)
    task = asyncio.create_task(run_agent_loop(agent, []))
    await asyncio.wait_for(
        agent.tool_started.wait() if nested_tool else _wait_for_stream(agent), 1
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert [stream.closed for stream in agent.streams] == [1]


async def _wait_for_stream(agent):
    while not agent.streams:
        await asyncio.sleep(0)
    await agent.streams[0].started.wait()


@pytest.mark.asyncio
async def test_runner_closes_stream_on_completion_and_error():
    agent = Agent()
    assert (await run_agent_loop(agent, []))[0] == "answer"
    assert agent.streams[0].closed == 1

    async def fail_progress(kind, text):
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        await run_agent_loop(agent, [], progress_callback=fail_progress)
    assert agent.streams[1].closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("close_failure", ["timeout", "error"])
@pytest.mark.parametrize("primary", ["cancel", "complete", "callback_error"])
async def test_runner_bounded_close_preserves_primary_failure(close_failure, primary):
    agent = Agent(block_stream=primary == "cancel")
    stream = Stream(lambda *_: None, block_stream=primary == "cancel")

    def process_messages(history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        return stream

    agent.process_messages = process_messages

    async def failing_close():
        if close_failure == "error":
            raise RuntimeError("close failed")
        await asyncio.Event().wait()

    stream.aclose = failing_close

    async def fail_progress(kind, text):
        raise ValueError("callback failed")

    task = asyncio.create_task(
        run_agent_loop(
            agent,
            [],
            progress_callback=fail_progress if primary == "callback_error" else None,
        )
    )
    if primary == "cancel":
        await asyncio.wait_for(stream.started.wait(), 1)
        task.cancel()
        expected = asyncio.CancelledError
    elif primary == "callback_error":
        expected = ValueError
    else:
        expected = TimeoutError if close_failure == "timeout" else RuntimeError

    with pytest.raises(expected):
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_runner_limits_empty_responses_and_preserves_usage():
    class EmptyStream(Stream):
        async def __anext__(self):
            if self.turn:
                self.callback([], TokenUsage(input_tokens=1))
                raise StopAsyncIteration
            self.turn += 1
            return "   ", "", None

    agent = Agent()

    def process_messages(history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        stream = EmptyStream(callback)
        agent.streams.append(stream)
        return stream

    agent.process_messages = process_messages
    usage_reports = []
    history = []
    with pytest.raises(RuntimeError, match="empty response after 5 retries"):
        await run_agent_loop(
            agent, history, request_usage_callback=usage_reports.append
        )
    assert len(agent.streams) == 6
    assert [stream.closed for stream in agent.streams] == [1] * 6
    assert len(usage_reports) == 6
    assert len(history) == 6


@pytest.mark.asyncio
async def test_runner_accepts_answer_on_fifth_empty_retry():
    class RetryStream(Stream):
        def __init__(self, callback, *, empty):
            super().__init__(callback)
            self.empty = empty

        async def __anext__(self):
            if self.turn == 0 and self.empty:
                self.turn = 1
                return "", "", None
            return await super().__anext__()

    agent = Agent()

    def process_messages(history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        stream = RetryStream(callback, empty=len(agent.streams) < 5)
        agent.streams.append(stream)
        return stream

    agent.process_messages = process_messages
    response, _ = await run_agent_loop(agent, [])
    assert response == "answer"
    assert [stream.closed for stream in agent.streams] == [1] * 6


@pytest.mark.asyncio
async def test_runner_times_out_first_item_and_closes_stream():
    agent = Agent(block_stream=True)
    with pytest.raises(TimeoutError, match="no first item within 0.01s"):
        await run_agent_loop(agent, [], first_response_timeout=0.01)
    assert [stream.closed for stream in agent.streams] == [1]


@pytest.mark.asyncio
async def test_runner_does_not_time_out_later_items():
    class DelayedSecondStream(Stream):
        def __init__(self, callback):
            super().__init__(callback)
            self.second_started = asyncio.Event()
            self.release_second = asyncio.Event()

        async def __anext__(self):
            if self.turn == 1:
                self.second_started.set()
                await self.release_second.wait()
            return await super().__anext__()

    agent = Agent()
    stream = DelayedSecondStream(lambda *_: None)

    def process_messages(history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        return stream

    agent.process_messages = process_messages
    task = asyncio.create_task(run_agent_loop(agent, [], first_response_timeout=0.01))
    await asyncio.wait_for(stream.second_started.wait(), 1)
    await asyncio.sleep(0.03)
    assert not task.done()
    stream.release_second.set()
    assert (await asyncio.wait_for(task, 1))[0] == "answer"
    assert stream.closed == 1


@pytest.mark.asyncio
async def test_runner_times_out_first_item_after_tool():
    class ToolAgent(Agent):
        async def execute_tool_call(self, tool):
            return "result"

    agent = ToolAgent(nested_tool=True)
    original_process = agent.process_messages

    def process_messages(history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        if agent.streams:
            stream = Stream(callback, block_stream=True)
            agent.streams.append(stream)
            return stream
        return original_process(history, callback, tools_enabled=tools_enabled)

    agent.process_messages = process_messages
    with pytest.raises(TimeoutError, match="no first item within 0.01s"):
        await run_agent_loop(agent, [], first_response_timeout=0.01)
    assert [stream.closed for stream in agent.streams] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("nested_tool", [False, True])
async def test_delegate_cancellation_closes_owned_resources_once(
    monkeypatch, nested_tool
):
    delegate = import_module("AgentCrew.modules.agents.tools.delegate")
    local_module = import_module("AgentCrew.modules.agents.local_agent")
    service_module = import_module("AgentCrew.modules.llm.service_manager")
    registry_module = import_module("AgentCrew.modules.llm.model_registry")
    events = []
    finished = asyncio.Event()
    bus = EventBus()

    def record_progress(**data):
        events.append(data)
        if data["kind"] == "finish":
            finished.set()

    bus.on(AppEvents.DELEGATE_PROGRESS, record_progress)
    monkeypatch.setattr(delegate.EventBus, "get_instance", lambda: bus)

    instances = []

    class Clone(Agent):
        def __init__(self, **kwargs):
            super().__init__(block_stream=not nested_tool, nested_tool=nested_tool)
            self.deactivated = 0
            instances.append(self)

        def set_system_prompt(self, prompt):
            pass

        async def activate_async(self):
            pass

        async def deactivate_async(self):
            self.deactivated += 1

    class FreshLLM:
        model = "model"

        def __init__(self):
            self.closed = 0

        async def close(self):
            self.closed += 1

    fresh = FreshLLM()
    shared = SimpleNamespace(model="model", provider_name="provider", closed=0)
    target = SimpleNamespace(
        name="Target",
        description="test",
        llm=shared,
        services={},
        tools=[],
        temperature=0,
        system_prompt="",
        custom_system_prompt="",
        get_model=lambda: "model",
        record_conversation_usage=lambda usage: None,
    )
    manager = SimpleNamespace(
        agents={"Target": target},
        current_agent=SimpleNamespace(name="Source"),
        get_local_agent=lambda name: target,
    )
    monkeypatch.setattr(local_module, "LocalAgent", Clone)
    monkeypatch.setattr(
        service_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=lambda name: fresh),
    )
    monkeypatch.setattr(
        registry_module.ModelRegistry,
        "get_instance",
        lambda: SimpleNamespace(get_model=lambda name: None),
    )
    task = asyncio.create_task(
        get_delegate_tool_handler(manager)(
            target_agent="Target", task_description="task"
        )
    )

    async def started():
        while not instances:
            await asyncio.sleep(0)
        clone = instances[0]
        await (clone.tool_started.wait() if nested_tool else _wait_for_stream(clone))

    await asyncio.wait_for(started(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    clone = instances[0]
    assert [stream.closed for stream in clone.streams] == [1]
    assert clone.deactivated == fresh.closed == 1
    assert shared.closed == 0
    await asyncio.wait_for(finished.wait(), 1)
    assert [event["kind"] for event in events] == (
        ["start", "response", "tool", "finish"] if nested_tool else ["start", "finish"]
    )
    assert [event["kind"] for event in events].count("finish") == 1
    assert len({event["run_id"] for event in events}) == 1


@pytest.mark.asyncio
async def test_delegate_cleanup_timeout_preserves_original_error_and_finishes(
    monkeypatch, caplog
):
    delegate = import_module("AgentCrew.modules.agents.tools.delegate")
    monkeypatch.setattr(delegate, "DELEGATE_CLEANUP_TIMEOUT", 0.01)
    events = []
    finished = asyncio.Event()
    bus = EventBus()

    def record_progress(**data):
        events.append(data)
        if data["kind"] == "finish":
            finished.set()

    bus.on(AppEvents.DELEGATE_PROGRESS, record_progress)
    monkeypatch.setattr(delegate.EventBus, "get_instance", lambda: bus)
    local_module = import_module("AgentCrew.modules.agents.local_agent")
    service_module = import_module("AgentCrew.modules.llm.service_manager")
    registry_module = import_module("AgentCrew.modules.llm.model_registry")

    class Clone:
        def __init__(self, **kwargs):
            pass

        def set_system_prompt(self, prompt):
            pass

        async def activate_async(self):
            raise RuntimeError("original failure")

        async def deactivate_async(self):
            await asyncio.Event().wait()

    class Fresh:
        model = "model"
        closed = False

        async def close(self):
            self.closed = True

    fresh = Fresh()
    target = SimpleNamespace(
        name="Target",
        description="test",
        llm=SimpleNamespace(model="model", provider_name="p"),
        services={},
        tools=[],
        temperature=0,
        system_prompt="",
        custom_system_prompt="",
        get_model=lambda: "model",
        record_conversation_usage=lambda usage: None,
    )
    monkeypatch.setattr(local_module, "LocalAgent", Clone)
    monkeypatch.setattr(
        service_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=lambda name: fresh),
    )
    monkeypatch.setattr(
        registry_module.ModelRegistry,
        "get_instance",
        lambda: SimpleNamespace(get_model=lambda name: None),
    )
    manager = SimpleNamespace(
        agents={"Target": target},
        current_agent=SimpleNamespace(name="Source"),
        get_local_agent=lambda name: target,
    )
    with pytest.raises(RuntimeError, match="original failure"):
        await asyncio.wait_for(
            get_delegate_tool_handler(manager)(
                target_agent="Target", task_description="task"
            ),
            1,
        )
    assert fresh.closed
    await asyncio.wait_for(finished.wait(), 1)
    assert [event["kind"] for event in events] == ["start", "finish"]
    assert [event["kind"] for event in events].count("finish") == 1
    assert "deactivation cleanup timed out" in caplog.text
