import asyncio
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from AgentCrew.modules.agents.agent_runner import run_agent_loop
from AgentCrew.modules.console.console_ui import ConsoleUI
from AgentCrew.modules.console.constants import RICH_STYLE_GRAY, RICH_STYLE_WHITE
from AgentCrew.modules.console.ui_effects import UIEffects
from AgentCrew.modules.llm.token_usage import TokenUsage


class FakeStream:
    def __init__(self, events, tools, callback):
        self.events = events
        self.tools = tools
        self.callback = callback

    async def __aiter__(self):
        for event in self.events:
            yield event
        self.callback(self.tools, TokenUsage())

    async def aclose(self):
        pass

    def format_assistant_message(self, response, **kwargs):
        return {"role": "assistant", "content": response}


class FakeAgent:
    def __init__(self):
        self.turn = 0

    def process_messages(self, history, callback, *, tools_enabled: bool = True):
        assert tools_enabled is True
        self.turn += 1
        if self.turn == 1:
            return FakeStream(
                [("", "", ("thinking ", None)), ("", "", None)], [], callback
            )
        if self.turn == 2:
            return FakeStream(
                [("using tool", "using tool", None)], [{"name": "read_file"}], callback
            )
        return FakeStream([("done", "done", None)], [], callback)

    def extract_last_user_message_for_memory(self, history):
        return ""

    def store_memory_if_available(self, *args):
        pass

    def validate_tool_use(self, tool):
        return None

    async def execute_tool_call(self, tool):
        return "ok"

    def format_message(self, *args):
        return {"role": "tool", "content": "ok"}


@pytest.mark.asyncio
async def test_runner_progress_survives_empty_response_and_tool_recursion():
    events = []

    async def progress(kind, text):
        events.append((kind, text))

    agent = FakeAgent()
    response, _ = await run_agent_loop(agent, [], progress_callback=progress)
    assert response == "done"
    assert events == [
        ("thinking", "thinking "),
        ("response", "using tool"),
        ("tool", "read_file"),
        ("response", "done"),
    ]
    assert agent.turn == 3


def test_delegate_panel_previews_parallel_runs_and_retains_final_output():
    output = StringIO()
    console = Console(width=40, force_terminal=False, file=output)
    effects = UIEffects(SimpleNamespace(console=console, message_handler=None))
    effects.update_delegate_progress("unknown", "Other", "finish", "")
    effects.update_delegate_progress(
        "one", "ResearchAgent", "start", "  find\n facts  "
    )
    effects.update_delegate_progress("two", "WriterAgent", "start", "draft answer")
    effects.update_delegate_progress("one", "ResearchAgent", "thinking", "a" * 85)
    effects.update_delegate_progress("two", "WriterAgent", "response", "b" * 85)

    panel = effects._render_delegate_progress()
    assert isinstance(panel, Panel)
    assert panel.title == "Delegating"
    assert isinstance(panel.renderable, Group)
    first, *rows = panel.renderable.renderables
    assert all(isinstance(row, Text) for row in panel.renderable.renderables)
    assert first.plain == "#1 ResearchAgent: find facts"
    assert first.style == "bold yellow"
    assert [row.plain for row in rows[:2]] == ["a" * 36, "a" * 13]
    assert all(row.spans[-1].style == RICH_STYLE_GRAY for row in rows[:2])
    assert rows[2].plain == "#2 WriterAgent: draft answer"
    assert [row.plain for row in rows[3:]] == ["b" * 36, "b" * 13]
    assert all(row.spans[-1].style == RICH_STYLE_WHITE for row in rows[3:])

    effects.update_delegate_progress("one", "ResearchAgent", "tool", "read_file")
    first, thinking, tool, second, *responses = (
        effects._render_delegate_progress().renderable.renderables
    )
    assert first.plain == "#1 ResearchAgent: find facts"
    assert thinking.plain == "a" * 13
    assert thinking.spans[-1].style == RICH_STYLE_GRAY
    assert tool.plain == "Tool: read_file"
    assert tool.spans[-1].style == RICH_STYLE_GRAY
    assert second.plain == "#2 WriterAgent: draft answer"
    assert [row.plain for row in responses] == ["b" * 36, "b" * 13]
    assert all(row.spans[-1].style == RICH_STYLE_WHITE for row in responses)

    effects.update_delegate_progress("one", "ResearchAgent", "finish", "")
    assert list(effects._delegate_agents) == ["one", "two"]
    assert effects._delegate_agents["one"].completed
    assert effects._delegate_live is not None
    first, *others = effects._render_delegate_progress().renderable.renderables
    assert first.plain == "#1 ResearchAgent: find facts ✅ Completed"
    assert any(row.plain == "#2 WriterAgent: draft answer" for row in others)
    effects.update_delegate_progress("one", "ResearchAgent", "finish", "")
    effects.update_delegate_progress("one", "ResearchAgent", "response", "ignored")
    assert effects._delegate_live is not None

    effects.update_delegate_progress("two", "WriterAgent", "finish", "")
    assert not effects._delegate_agents
    assert effects._delegate_live is None
    final = output.getvalue()
    assert final.count("Delegating") == 1
    assert "#1 ResearchAgent: find facts ✅" in final
    assert "#2 WriterAgent: draft answer ✅" in final
    assert final.count("Completed") == 2
    assert "✅ ResearchAgent completed" not in final

    effects.update_delegate_progress("one", "ResearchAgent", "finish", "")
    assert effects._delegate_live is None
    effects.update_delegate_progress("next", "NewAgent", "start", "new task")
    rows = effects._render_delegate_progress().renderable.renderables
    assert [row.plain for row in rows] == ["#1 NewAgent: new task"]
    effects.cleanup()
    effects.cleanup()


def test_delegate_start_stops_loading_without_static_output():
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    stopped = []
    ui = SimpleNamespace(
        console=console,
        ui_effects=SimpleNamespace(
            stop_loading_animation=lambda: stopped.append(True),
            register_delegate_call=lambda *args: None,
        ),
    )
    ConsoleUI._on_delegate_started(
        ui,
        tool_use={
            "input": {"target_agent": "ResearchAgent", "task_description": "task"}
        },
    )
    assert stopped == [True]
    assert output.getvalue() == ""


@pytest.mark.asyncio
async def test_delegate_result_does_not_print_standalone_completion():
    from AgentCrew.modules.events import AppEvents, EventBus

    output = StringIO()
    started = []
    ui = ConsoleUI.__new__(ConsoleUI)
    ui.console = Console(file=output, force_terminal=False)
    ui.bus = EventBus()
    ui._stream_cancel_pending = False
    ui.ui_effects = SimpleNamespace(
        start_loading_animation=lambda: started.append(True),
        complete_delegate_call=lambda *args: None,
    )
    ui._register_subscriptions()

    await ui.bus.emit(
        AppEvents.TOOL_RESULT,
        tool_use={"name": "delegate", "input": {"target_agent": "ResearchAgent"}},
        tool_result="done",
    )
    assert output.getvalue() == ""
    assert not started
    await ui.bus.emit(
        AppEvents.TOOL_RESULT, tool_use={"name": "read_file"}, tool_result="done"
    )
    assert started == [True]


def test_large_delegate_chunk_preserves_latest_text_and_cleanup():
    console = Console(width=30, force_terminal=False)
    effects = UIEffects(SimpleNamespace(console=console, message_handler=None))
    effects.update_delegate_progress("run", "Agent", "start", "a" * 100)
    effects.update_delegate_progress("run", "Agent", "response", "a" * 9000 + "tail")
    assert "tail" in effects._delegate_agents["run"].entries[-1][1]
    assert len(effects._delegate_agents["run"].entries[-1][1]) <= 8192
    assert (
        effects._render_delegate_progress()
        .renderable.renderables[0]
        .plain.endswith("...")
    )
    effects.cleanup()
    effects.cleanup()
    assert not effects._delegate_agents
    assert effects._delegate_live is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fails,blocked,cancelled",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
async def test_delegate_handler_finishes_run_on_success_error_and_cancellation(
    monkeypatch, fails, blocked, cancelled
):
    from importlib import import_module

    from AgentCrew.modules.agents.tools.delegate import get_delegate_tool_handler
    from AgentCrew.modules.events import AppEvents, EventBus

    module = import_module("AgentCrew.modules.agents.tools.delegate")
    local_agent_module = import_module("AgentCrew.modules.agents.local_agent")
    llm_module = import_module("AgentCrew.modules.llm.service_manager")
    registry_module = import_module("AgentCrew.modules.llm.model_registry")
    events = []
    finished = asyncio.Event()
    started = asyncio.Event()
    loop_entered = asyncio.Event()
    cleaned = asyncio.Event()
    bus = EventBus()

    async def subscriber(**data):
        if blocked and data["kind"] == "start":
            started.set()
            await asyncio.Event().wait()
        events.append(data)
        if data["kind"] == "finish":
            finished.set()

    bus.on(AppEvents.DELEGATE_PROGRESS, subscriber)
    monkeypatch.setattr(module.EventBus, "get_instance", lambda: bus)
    if blocked:
        monkeypatch.setattr(module, "DELEGATE_PROGRESS_TIMEOUT", 0.2)

    clone_arguments = []
    loop_arguments = []

    class Clone:
        def __init__(self, **kwargs):
            clone_arguments.append(kwargs)

        def set_system_prompt(self, prompt):
            pass

        async def activate_async(self):
            if fails:
                raise RuntimeError("activation failed")

        async def deactivate_async(self):
            cleaned.set()

    class FreshLLM:
        model = "model"

        async def close(self):
            pass

    llm = FreshLLM()
    services = {"agent_manager": object()}
    manager = SimpleNamespace(
        agents={"Target": object()},
        current_agent=SimpleNamespace(name="Source"),
        get_local_agent=lambda name: SimpleNamespace(
            name=name,
            description="test",
            llm=SimpleNamespace(model="model", provider_name="provider"),
            services=services,
            tools=[],
            temperature=0,
            system_prompt="",
            custom_system_prompt="",
            get_model=lambda: "model",
            record_conversation_usage=lambda usage: None,
        ),
    )
    monkeypatch.setattr(local_agent_module, "LocalAgent", Clone)
    monkeypatch.setattr(
        llm_module.ServiceManager,
        "get_instance",
        lambda: SimpleNamespace(initialize_standalone_service=lambda name: llm),
    )
    monkeypatch.setattr(
        registry_module.ModelRegistry,
        "get_instance",
        lambda: SimpleNamespace(get_model=lambda name: None),
    )

    async def fake_run_agent_loop(**kwargs):
        loop_arguments.append(kwargs)
        if blocked:
            await asyncio.wait_for(started.wait(), 1)
        if cancelled:
            loop_entered.set()
            await asyncio.Event().wait()
        await kwargs["progress_callback"]("response", "answer")
        return "answer", TokenUsage()

    monkeypatch.setattr(module, "run_agent_loop", fake_run_agent_loop)
    handler = get_delegate_tool_handler(manager)
    description = "  test\n details  "
    if cancelled:
        task = asyncio.create_task(
            handler(target_agent="Target", task_description=description)
        )
        await asyncio.wait_for(loop_entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(cleaned.wait(), 1)
    elif fails:
        with pytest.raises(RuntimeError, match="activation failed"):
            await asyncio.wait_for(
                handler(target_agent="Target", task_description=description), 1
            )
    else:
        assert "answer" in await asyncio.wait_for(
            handler(target_agent="Target", task_description=description),
            0.1 if blocked else 1,
        )
    await asyncio.wait_for(finished.wait(), 1)
    assert clone_arguments[0]["excluded_tools"] == {"delegate", "transfer"}
    assert clone_arguments[0]["services"] is services
    assert "agent_manager" in services
    if fails:
        assert not loop_arguments
    else:
        assert len(loop_arguments) == 1
        assert "tool_filter" not in loop_arguments[0]
    assert [e["kind"] for e in events] == (
        ["response", "finish"]
        if blocked
        else ["start", "finish"]
        if fails or cancelled
        else ["start", "response", "finish"]
    )
    assert len({e["run_id"] for e in events}) == 1
    if not blocked:
        assert events[0]["text"] == description


def delegate_effects():
    output = StringIO()
    console = Console(width=60, force_terminal=False, file=output)
    return UIEffects(SimpleNamespace(console=console, message_handler=None)), output


def test_actual_delegate_completion_reconciles_missing_finish():
    effects, output = delegate_effects()
    effects.register_delegate_call("a", "Agent", "task a")
    effects.register_delegate_call("b", "Agent", "task b")
    effects.update_delegate_progress("run-a", "Agent", "start", "task a")
    effects.update_delegate_progress("run-b", "Agent", "start", "task b")
    effects.update_delegate_progress("run-a", "Agent", "response", "ongoing")
    effects.complete_delegate_call("a")
    assert effects._delegate_agents["run-a"].completed
    assert not effects._delegate_agents["run-b"].completed
    effects.complete_delegate_call("b")
    assert not effects._delegate_agents
    assert effects._delegate_live is None
    assert "#1 Agent: task a ✅ Completed" in output.getvalue()
    assert "#2 Agent: task b ✅ Completed" in output.getvalue()
    assert not effects._delegate_calls


def test_delegate_completion_before_start_does_not_leave_stale_run():
    effects, output = delegate_effects()
    effects.register_delegate_call("a", "Agent", "task")
    effects.complete_delegate_call("a")
    effects.update_delegate_progress("late", "Agent", "start", "task")
    effects.update_delegate_progress("late", "Agent", "response", "late chunk")
    assert not effects._delegate_agents
    assert not effects._delegate_calls
    assert effects._delegate_live is None
    assert output.getvalue() == ""


def test_completed_identical_delegate_consumes_its_delayed_start_in_order():
    effects, _ = delegate_effects()
    effects.register_delegate_call("first", "Agent", "same")
    effects.register_delegate_call("second", "Agent", "same")
    effects.complete_delegate_call("first")
    effects.update_delegate_progress("late-first", "Agent", "start", "same")
    effects.update_delegate_progress("run-second", "Agent", "start", "same")
    assert "late-first" not in effects._delegate_agents
    assert effects._delegate_calls["second"][2] == "run-second"
    effects.complete_delegate_call("second")
    assert not effects._delegate_agents
    assert not effects._delegate_calls


def test_parallel_identical_delegates_correlate_by_registration_order():
    effects, _ = delegate_effects()
    effects.register_delegate_call("first", "Agent", "same")
    effects.register_delegate_call("second", "Agent", "same")
    effects.update_delegate_progress("run-1", "Agent", "start", "same")
    effects.update_delegate_progress("run-2", "Agent", "start", "same")
    assert effects._delegate_calls["first"][2] == "run-1"
    assert effects._delegate_calls["second"][2] == "run-2"
    effects.complete_delegate_call("second")
    assert effects._delegate_agents["run-2"].completed
    assert not effects._delegate_agents["run-1"].completed
    effects.complete_delegate_call("first")
    assert not effects._delegate_agents


def test_normal_delegate_finish_then_actual_completion_is_idempotent():
    effects, output = delegate_effects()
    effects.register_delegate_call("id", "Agent", "task")
    effects.update_delegate_progress("run", "Agent", "start", "task")
    effects.update_delegate_progress("run", "Agent", "finish", "")
    final = output.getvalue()
    effects.complete_delegate_call("id")
    effects.complete_delegate_call("id")
    effects.update_delegate_progress("run", "Agent", "finish", "")
    assert output.getvalue() == final
    assert not effects._delegate_calls
    assert effects._delegate_live is None


def test_delegate_correlation_cleanup_and_bounded_completed_registrations():
    effects, _ = delegate_effects()
    for index in range(300):
        tool_id = str(index)
        effects.register_delegate_call(tool_id, "Agent", str(index))
        effects.complete_delegate_call(tool_id)
    assert len(effects._delegate_calls) <= 256
    effects.update_delegate_progress("run", "Agent", "start", "299")
    effects.register_delegate_call("active", "Other", "task")
    effects.cleanup()
    assert not effects._delegate_calls
    assert not effects._delegate_agents
    assert effects._delegate_live is None


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["result", "error"])
async def test_delegate_completion_subscription_schedules_without_blocking_emit_sync(
    event,
):
    from AgentCrew.modules.events import AppEvents, EventBus

    ui = ConsoleUI.__new__(ConsoleUI)
    ui.console = Console(file=StringIO(), force_terminal=False)
    ui.bus = EventBus()
    ui._stream_cancel_pending = False
    ui.ui_effects = UIEffects(SimpleNamespace(console=ui.console, message_handler=None))
    errors = []
    ui.tool_display = SimpleNamespace(
        display_tool_error=lambda data: errors.append(data)
    )
    ui._register_subscriptions()
    tool_use = {
        "id": "id",
        "name": "delegate",
        "input": {"target_agent": "Agent", "task_description": "task"},
    }
    await ui.bus.emit(AppEvents.TOOL_USE, **tool_use)
    await ui.bus.emit(
        AppEvents.DELEGATE_PROGRESS,
        run_id="run",
        agent_name="Agent",
        kind="start",
        text="task",
    )
    completed = asyncio.Event()
    original = ui.ui_effects.complete_delegate_call

    def reconcile(tool_id):
        original(tool_id)
        completed.set()

    ui.ui_effects.complete_delegate_call = reconcile
    payload = {"tool_use": tool_use, "message": {}}
    payload.update(tool_result="done" if event == "result" else None)
    if event == "error":
        payload.pop("tool_result")
        payload["error"] = "failed"
    ui.bus.emit_sync(
        AppEvents.TOOL_RESULT if event == "result" else AppEvents.TOOL_ERROR, **payload
    )
    assert not completed.is_set()
    await asyncio.wait_for(completed.wait(), 1)
    assert not ui.ui_effects._delegate_agents
    assert not ui.ui_effects._delegate_calls
    assert len(errors) == (1 if event == "error" else 0)
    ui.ui_effects.cleanup()
