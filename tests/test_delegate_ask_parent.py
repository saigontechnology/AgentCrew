import asyncio
from importlib import import_module
from types import SimpleNamespace
from typing import ClassVar

import pytest

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.manager import AgentMode
from AgentCrew.modules.agents.tools.ask import (
    ask_tool_prompt,
    get_ask_tool_definition,
    get_ask_tool_handler,
)
from AgentCrew.modules.agents.tools.delegate import (
    _ParentClarificationSession,
    get_delegate_tool_handler,
)
from AgentCrew.modules.events import AppEvents, EventBus
from AgentCrew.modules.llm.token_usage import TokenUsage

QUESTIONS = [{"question": "Which format?", "guided_answers": ["JSON", "CSV"]}]


class FakeLLM:
    def __init__(self, answer="Use JSON"):
        self.model = "model"
        self.provider_name = "provider"
        self._provider_name = "provider"
        self.answer = answer
        self.handlers = {}
        self.calls = []
        self.closed = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def clear_tools(self):
        self.handlers.clear()

    def register_tool(self, definition, handler):
        self.handlers[definition["function"]["name"]] = handler
        self.definition = definition

    def set_system_prompt(self, prompt):
        self.system_prompt = prompt

    async def execute_tool(self, name, arguments):
        return await self.handlers[name](**arguments)

    async def process_message(self, messages):
        self.calls.append(messages)
        self.started.set()
        await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def close(self):
        self.closed += 1


class FakeStream:
    def __init__(self, turn, history, callback):
        self.turn = turn
        self.history = history
        self.callback = callback

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.turn:
            self.turn = 1
            return "done", "done", None
        tools = (
            [{"id": "ask-1", "name": "ask", "input": {"questions": QUESTIONS}}]
            if len(self.history) == 1
            else []
        )
        self.callback(tools, TokenUsage(input_tokens=1))
        raise StopAsyncIteration

    async def aclose(self):
        pass

    def format_assistant_message(self, response, **kwargs):
        result = {"role": "assistant", "content": response}
        if kwargs.get("tool_uses"):
            result["tool_calls"] = kwargs["tool_uses"]
        return result


class Clone(LocalAgent):
    instances: ClassVar[list] = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.turn_histories = []
        self.__class__.instances.append(self)

    async def activate_async(self):
        self.register_tools()
        self._tool_registrar.sync_to_llm()
        return True

    async def deactivate_async(self):
        return True

    def process_messages(self, history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        self.turn_histories.append(history.copy())
        return FakeStream(0, history, callback)

    def extract_last_user_message_for_memory(self, history):
        return ""

    def store_memory_if_available(self, *args):
        pass


@pytest.fixture
def delegate_setup(monkeypatch):
    Clone.instances = []
    monkeypatch.setattr(
        import_module("AgentCrew.modules.agents.local_agent"), "LocalAgent", Clone
    )
    monkeypatch.setattr(
        import_module("AgentCrew.modules.llm.model_registry").ModelRegistry,
        "get_instance",
        lambda: SimpleNamespace(get_model=lambda name: None),
    )
    llms = []

    def new_service(name):
        llm = FakeLLM()
        llms.append(llm)
        return llm

    monkeypatch.setattr(
        import_module("AgentCrew.modules.llm.service_manager").ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=new_service),
    )
    target = SimpleNamespace(
        name="Target",
        description="worker",
        llm=FakeLLM(),
        services={},
        tools=[],
        temperature=0,
        system_prompt="worker prompt",
        custom_system_prompt=None,
        get_model=lambda: "model",
        record_conversation_usage=lambda usage: None,
    )
    source = SimpleNamespace(
        name="Source",
        description="coordinator",
        llm=FakeLLM(),
        temperature=0.2,
        custom_system_prompt="Source custom instructions",
        history=[
            {"role": "user", "content": "Please use JSON for the report"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Delegating"}],
                "tool_calls": [
                    {"id": "delegate-1", "name": "delegate", "input": {}},
                    {"id": "delegate-2", "name": "delegate", "input": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "delegate-1", "content": "other result"},
        ],
        get_model=lambda: "model",
        get_system_prompt=lambda: "Source base instructions",
    )
    manager = SimpleNamespace(
        agent_mode=AgentMode.DELEGATE,
        one_turn_process=False,
        agents={"Source": source, "Target": target},
        current_agent=source,
        get_local_agent=lambda name: target,
        get_agents_list_prompt=lambda: "agents-list",
    )
    target.services = {"agent_manager": manager}
    return manager, llms, source, target, monkeypatch


@pytest.mark.asyncio
async def test_delegated_ask_answers_into_same_loop_without_user_confirmation(
    delegate_setup,
):
    manager, llms, source, target, monkeypatch = delegate_setup
    confirmation_events = []
    bus = EventBus()
    bus.on(
        AppEvents.TOOL_CONFIRMATION_REQ,
        lambda **event: confirmation_events.append(event),
    )
    monkeypatch.setattr(EventBus, "get_instance", lambda: bus)
    result = await get_delegate_tool_handler(manager)(
        target_agent="Target",
        task_description="Write report",
        context="Only JSON is allowed",
    )
    clone = Clone.instances[0]
    parent = llms[1]
    assert result == "## Result from Target:\n\ndone"
    assert clone.excluded_tools == {"delegate", "transfer"}
    assert set(clone.llm.handlers) == {"ask"}
    assert "source/main agent" in clone.llm.definition["function"]["description"]
    assert "source/main agent" in "\n".join(clone.tool_prompts)
    assert clone.turn_histories[-1][-1]["content"] == "Use JSON"
    assert clone.turn_histories[0][0]["role"] == "user"
    assert len(clone.turn_histories) == 2
    assert parent.handlers == {}
    assert "Source base instructions" in parent.system_prompt
    assert "Source custom instructions" in parent.system_prompt
    assert parent.calls[0][0] == source.history[0]
    assert len(parent.calls[0]) == 2
    assert "Write report" in parent.calls[0][-1]["content"][0]["text"]
    assert "Only JSON is allowed" in parent.calls[0][-1]["content"][0]["text"]
    assert "Which format?" in parent.calls[0][-1]["content"][0]["text"]
    assert [llm.closed for llm in llms] == [1, 1]
    assert target.services == {"agent_manager": manager}
    assert confirmation_events == []


@pytest.mark.asyncio
async def test_normal_ask_keeps_user_wording_and_validation():
    assert "user" in get_ask_tool_definition()["function"]["description"]
    assert "Interview the user" in ask_tool_prompt()
    assert await get_ask_tool_handler()(questions=QUESTIONS) == "Asking user 1 question"
    invoked = []

    async def responder(questions):
        invoked.append(questions)
        return "answer"

    delegated = get_ask_tool_handler(responder)
    with pytest.raises(ValueError, match="at least 2 guided answers"):
        await delegated(questions=[{"question": "Why?", "guided_answers": ["one"]}])
    assert invoked == []
    assert await delegated(questions=QUESTIONS) == "answer"
    assert invoked == [QUESTIONS]


@pytest.mark.asyncio
async def test_parent_session_reuses_answers_and_isolates_parallel_state(
    delegate_setup,
):
    _, llms, source, _, _ = delegate_setup
    left = _ParentClarificationSession(source, "Left task")
    right = _ParentClarificationSession(source, "Right task")
    assert await asyncio.gather(left.answer(QUESTIONS), right.answer(QUESTIONS)) == [
        "Use JSON",
        "Use JSON",
    ]
    assert await left.answer(QUESTIONS) == "Use JSON"
    assert len(llms) == 2
    assert left.llm is not right.llm
    assert len(left.llm.calls[1]) == 4
    assert len(right.llm.calls[0]) == 2
    assert source.history[-1]["role"] == "tool"
    assert "Left task" not in str(right.llm.calls)
    assert "Right task" not in str(left.llm.calls)
    await left.close()
    await right.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [RuntimeError("parent failed"), TimeoutError("parent timed out"), ""]
)
async def test_parent_failure_returns_error_to_delegate_and_closes_resources(
    delegate_setup, outcome
):
    manager, llms, _, _, monkeypatch = delegate_setup
    handler = get_delegate_tool_handler(manager)
    original = llms.append

    def fail_parent(name):
        llm = FakeLLM(outcome if llms else "ignored")
        original(llm)
        return llm

    service_module = import_module("AgentCrew.modules.llm.service_manager")
    monkeypatch.setattr(
        service_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=fail_parent),
    )
    result = await handler(target_agent="Target", task_description="task")
    assert result.startswith("## Result from Target")
    tool_result = Clone.instances[0].turn_histories[-1][-1]["content"]
    assert tool_result.startswith("ERROR: ")
    assert any(
        text in tool_result
        for text in (
            "parent failed",
            "parent timed out",
            "Source agent returned no clarification",
        )
    )
    assert [llm.closed for llm in llms] == [1, 1]


@pytest.mark.asyncio
async def test_cleanup_failures_keep_primary_error_and_close_both_llms(delegate_setup):
    manager, llms, _, _, monkeypatch = delegate_setup
    delegate = import_module("AgentCrew.modules.agents.tools.delegate")

    async def fail_after_ask(agent, **kwargs):
        assert (
            await agent.llm.execute_tool("ask", {"questions": QUESTIONS}) == "Use JSON"
        )
        raise RuntimeError("original failure")

    def failing_deactivation(self):
        raise RuntimeError("deactivation failed")

    async def failing_close():
        llms[1].closed += 1
        raise RuntimeError("parent close failed")

    def new_service(name):
        llm = FakeLLM()
        llms.append(llm)
        if len(llms) == 2:
            llm.close = failing_close
        return llm

    service_module = import_module("AgentCrew.modules.llm.service_manager")
    monkeypatch.setattr(delegate, "run_agent_loop", fail_after_ask)
    monkeypatch.setattr(Clone, "deactivate_async", failing_deactivation)
    monkeypatch.setattr(
        service_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=new_service),
    )
    with pytest.raises(RuntimeError, match="original failure"):
        await get_delegate_tool_handler(manager)(
            target_agent="Target", task_description="task"
        )
    assert [llm.closed for llm in llms] == [1, 1]


@pytest.mark.asyncio
async def test_cancellation_during_parent_answer_closes_both_llms(delegate_setup):
    manager, llms, _, _, monkeypatch = delegate_setup
    service_module = import_module("AgentCrew.modules.llm.service_manager")

    def blocking_parent(name):
        llm = FakeLLM()
        if llms:
            llm.release.clear()
        llms.append(llm)
        return llm

    monkeypatch.setattr(
        service_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=blocking_parent),
    )
    task = asyncio.create_task(
        get_delegate_tool_handler(manager)(
            target_agent="Target", task_description="task"
        )
    )
    while len(llms) < 2:
        await asyncio.sleep(0)
    await llms[1].started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [llm.closed for llm in llms] == [1, 1]
