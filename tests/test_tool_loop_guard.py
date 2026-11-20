"""Shared loop decisions and real pre-dispatch orchestration in all four modes."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AgentCrew.modules.agents.tool_loop_guard import (
    MAX_TOOL_ROUNDS,
    REPEAT_WARNING,
    ToolLoopError,
    ToolLoopGuard,
    tool_call_fingerprint,
)
from AgentCrew.modules.llm.token_usage import TokenUsage


def call(name="read_file", arguments=None, call_id="call"):
    return {"id": call_id, "name": name, "input": arguments or {}}


def test_fingerprint_canonicalizes_nested_objects_but_preserves_list_order():
    left = call(arguments={"b": [{"y": None, "x": True}, 2], "a": "text"})
    right = call(arguments={"a": "text", "b": [{"x": True, "y": None}, 2]})
    right["id"] = "different-id"
    assert tool_call_fingerprint(left) == tool_call_fingerprint(right)
    persisted = {
        "name": "read_file",
        "arguments": '{"a":"text","b":[{"x":true,"y":null},2]}',
    }
    assert tool_call_fingerprint(left) == tool_call_fingerprint(persisted)
    assert tool_call_fingerprint(left) != tool_call_fingerprint(
        call("other", left["input"])
    )
    assert tool_call_fingerprint(
        call(arguments={"a": [1, 2]})
    ) != tool_call_fingerprint(call(arguments={"a": [2, 1]}))
    assert tool_call_fingerprint(call(arguments={"a": 1})) != tool_call_fingerprint(
        call(arguments={"a": "1"})
    )


def test_fingerprint_handles_unexpected_values_without_repr_or_recursion():
    class Opaque:
        def __repr__(self):
            raise RuntimeError("must not call repr")

    cycle = []
    cycle.append(cycle)
    value = call(
        arguments={"set": {2, 1}, "opaque": Opaque(), "cycle": cycle, 4: b"bytes"}
    )
    assert tool_call_fingerprint(value) == tool_call_fingerprint(copy.deepcopy(value))
    assert tool_call_fingerprint({"name": "tool", "arguments": "invalid json"})


def test_first_two_execute_third_warns_fourth_terminates():
    guard = ToolLoopGuard()
    assert guard.check_round([call()]).errors == (None,)
    assert guard.check_round([call(call_id="new")]).errors == (None,)
    warning = guard.check_round([call()])
    assert warning.errors == (REPEAT_WARNING,)
    assert warning.terminal_error is None
    terminal = guard.check_round([call(), call("new_tool")])
    assert all(terminal.errors)
    with pytest.raises(ToolLoopError, match="after the recovery warning"):
        terminal.raise_if_terminal()
    assert guard.check_round([call("different")]).terminal_error


def test_changed_arguments_absence_and_new_turn_reset_streaks():
    guard = ToolLoopGuard()
    guard.check_round([call()])
    guard.check_round([call()])
    assert guard.check_round([call(arguments={"new": 1})]).errors == (None,)
    assert guard.check_round([call()]).errors == (None,)
    guard.check_round([call()])
    guard.check_round([call("other")])
    assert guard.check_round([call()]).errors == (None,)
    guard.check_round([call()])
    guard.check_round([])
    assert guard.check_round([call()]).errors == (None,)
    assert ToolLoopGuard().check_round([call()]).errors == (None,)


def test_duplicates_share_budget_and_terminal_batch_is_nonexecuting():
    guard = ToolLoopGuard()
    assert guard.check_round([call(), call(), call()]).errors == (
        None,
        None,
        REPEAT_WARNING,
    )
    assert guard.check_round([call()]).terminal_error
    first_batch = ToolLoopGuard()
    assert first_batch.check_round([call()] * 4).errors == (
        None,
        None,
        REPEAT_WARNING,
        REPEAT_WARNING,
    )
    assert first_batch.check_round([call()]).terminal_error


def test_hard_cap_catches_alternating_tools():
    guard = ToolLoopGuard()
    for index in range(MAX_TOOL_ROUNDS):
        assert guard.check_round([call(str(index % 2))]).errors == (None,)
    terminal = guard.check_round([call("new")])
    with pytest.raises(ToolLoopError, match=f"exceeded {MAX_TOOL_ROUNDS}"):
        terminal.raise_if_terminal()


def test_guard_logs_only_name_count_and_digest():
    from loguru import logger

    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        guard = ToolLoopGuard()
        for _ in range(4):
            guard.check_round([call(arguments={"token": "secret-value"})])
    finally:
        logger.remove(sink)
    assert len(messages) == 2
    assert all(
        "secret-value" not in message and "token" not in message for message in messages
    )
    assert all(
        "digest=" in message and "occurrence=" in message for message in messages
    )


class ModelStream:
    def __init__(self, agent, callback, tools):
        self.agent = agent
        self.callback = callback
        self.tools = tools

    async def __aiter__(self):
        self.callback(self.tools or [], TokenUsage(input_tokens=1, output_tokens=1))
        yield ("working" if self.tools else "Finished", None, None)

    async def aclose(self):
        pass

    def format_assistant_message(self, response, thinking=None, tool_uses=None):
        return self.agent._message_formatter.format_assistant_message(
            response, thinking, tool_uses
        )


class Harness:
    def __init__(self, mode, monkeypatch, batches):
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.events import HookRegistry

        self.mode = mode
        self.batches = batches
        self.requests = 0
        self.hooks = MagicMock()
        self.hooks.run_before = AsyncMock(side_effect=lambda point, **kwargs: kwargs)
        self.hooks.run_after = AsyncMock(
            side_effect=lambda point, result, **kwargs: result
        )
        monkeypatch.setattr(HookRegistry, "get_instance", lambda: self.hooks)
        self.llm = SimpleNamespace(
            execute_tool=AsyncMock(return_value="ok"),
            is_stream=True,
            calculate_cost=MagicMock(return_value=0.0),
        )
        self.agent = LocalAgent("loop-test", "test", self.llm, {}, [])
        self.agent.validate_tool_use = MagicMock(return_value=None)
        self.agent.store_memory_if_available = MagicMock()
        self.agent.extract_last_user_message_for_memory = MagicMock(return_value="user")
        self.agent.process_messages = self.process_messages
        self.history = [{"role": "user", "content": [{"type": "text", "text": "work"}]}]
        self.agent.history = self.history
        self.queue = MagicMock(enqueue_event=AsyncMock())
        self.store = AsyncMock()
        self.approval = None
        self.session = None
        if mode == "chat":
            self._setup_chat()
        elif mode == "acp":
            from AgentCrew.modules.acp.session_state import AcpSessionState
            from AgentCrew.modules.acp.turn_executor import TurnExecutor

            self.state = AcpSessionState(
                cwd=".", agent_name=self.agent.name, history=self.history
            )
            self.state.permission_broker = SimpleNamespace(
                request_permission=AsyncMock(return_value="allow_once")
            )
            self.approval = self.state.permission_broker.request_permission
            self.executor = TurnExecutor(AsyncMock(), AsyncMock())
            self.executor._get_agent = AsyncMock(return_value=self.agent)
            self.executor._send_usage_update = AsyncMock()
        elif mode == "a2a":
            from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor

            self.executor = AgentCrewA2AExecutor(self.agent, self.store)
            self.context = SimpleNamespace(
                call_context=None,
                current_task=None,
                task_id="t",
                context_id="c",
                message=None,
            )

    def process_messages(self, messages=None, callback=None):
        assert self.requests < 60, "stub model exceeded bounded round budget"
        tools = copy.deepcopy(self.batches[min(self.requests, len(self.batches) - 1)])
        self.requests += 1
        for index, tool in enumerate(tools or []):
            tool["id"] = f"r{self.requests}-{index}"
        return ModelStream(self.agent, callback, tools)

    def _setup_chat(self):
        from AgentCrew.modules.chat.message.handler import MessageHandler
        from AgentCrew.modules.chat.message.tool_manager import ToolManager
        from AgentCrew.modules.chat.stream_session import StreamSession

        handler = MessageHandler.__new__(MessageHandler)
        handler.agent = self.agent
        handler.agent_manager = SimpleNamespace(defered_transfer="")
        handler.bus = MagicMock(emit=AsyncMock())
        handler.hooks = self.hooks
        handler.streamline_messages = self.history
        handler.persistent_service = None
        handler.current_conversation_id = None
        handler.voice_service = None
        handler._pending_resource_attachments = []
        handler._messages_append = self.history.append
        handler._record_turn_request_usage = MagicMock()
        handler._finalize_current_turn = MagicMock()
        handler._yolo_mode_check = MagicMock()
        handler._clear_stream_session = MagicMock()

        def create_session():
            self.session = StreamSession(session_id=self.requests + 1)
            return self.session

        handler._create_stream_session = create_session
        with patch.object(
            ToolManager, "_load_persistent_auto_approved_tools", return_value=set()
        ):
            handler.tool_manager = ToolManager(handler)
        handler.tool_manager.bus = handler.bus
        handler.tool_manager._wait_for_tool_confirmation = AsyncMock(
            return_value={"action": "approve"}
        )
        self.approval = handler.tool_manager._wait_for_tool_confirmation
        self.handler = handler

    async def run(self, terminal=False):
        if self.mode == "jobs":
            from AgentCrew.modules.agents.agent_runner import run_agent_loop

            operation = run_agent_loop(self.agent, self.history)
        elif self.mode == "acp":
            operation = self.executor.run_turn("session", self.state, None)
        elif self.mode == "a2a":
            from AgentCrew.modules.a2a.agent_executor import AnswerArtifactState

            operation = self.executor._run_agent_loop(
                self.context,
                self.queue,
                "t",
                "c",
                self.history,
                AnswerArtifactState("answer_t"),
            )
        else:
            operation = self.handler.get_assistant_response()
        if terminal and self.mode in {"jobs", "acp"}:
            with pytest.raises(ToolLoopError, match="Tool loop protection terminated"):
                await asyncio.wait_for(operation, timeout=5)
            return None
        return await asyncio.wait_for(operation, timeout=5)

    def assert_paired(self):
        expected = []
        actual = []
        for message in self.history:
            expected.extend(tool["id"] for tool in message.get("tool_calls", []))
            if message["role"] == "tool":
                actual.append(message["tool_call_id"])
        assert expected == actual


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("recover", [True, False])
async def test_repeating_model_recovers_or_terminates(mode, recover, monkeypatch):
    batches = [[call()]] * 3 + [None] if recover else [[call()]]
    harness = Harness(mode, monkeypatch, batches)
    result = await harness.run(terminal=not recover)
    assert harness.requests == 4
    assert harness.llm.execute_tool.await_count == 2
    assert harness.hooks.run_before.await_count == 2
    assert harness.agent.validate_tool_use.call_count == 2
    if harness.approval:
        assert harness.approval.await_count == 2
    tool_messages = [
        message for message in harness.history if message["role"] == "tool"
    ]
    assert REPEAT_WARNING in tool_messages[2]["content"]
    harness.assert_paired()
    if recover:
        assert harness.history[-1]["content"][-1]["text"] == "Finished"
    else:
        assert "terminated" in tool_messages[-1]["content"]
    if mode == "chat":
        assert harness.session.status == ("completed" if recover else "failed")
        assert harness.session.finished.is_set()
        assert result[1].total_input_tokens == 4
        if not recover:
            from AgentCrew.modules.events import AppEvents

            assert any(
                args.args[0] == AppEvents.ERROR
                for args in harness.handler.bus.emit.await_args_list
            )
    if mode == "a2a":
        from a2a.types.a2a_pb2 import TaskState, TaskStatusUpdateEvent

        states = [
            args.args[0]
            for args in harness.queue.enqueue_event.await_args_list
            if isinstance(args.args[0], TaskStatusUpdateEvent)
        ]
        assert states[-1].status.state == (
            TaskState.TASK_STATE_COMPLETED if recover else TaskState.TASK_STATE_FAILED
        )
        assert states[-1].task_id == "t" and states[-1].context_id == "c"
        if not recover:
            assert (
                "Tool loop protection terminated"
                in states[-1].status.message.parts[0].text
            )
        persisted = [
            args.args[1] for args in harness.store.append_history.await_args_list
        ]
        assert persisted == harness.history[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("name", ["read_file", "browser_click"])
async def test_mixed_batch_keeps_order_and_blocks_before_side_effects(
    mode, name, monkeypatch
):
    blocked = call(name)
    mixed = [
        call("other_parallel"),
        blocked,
        call("browser_other"),
        call("last_parallel"),
    ]
    harness = Harness(mode, monkeypatch, [[blocked], [blocked], mixed, None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 5
    names = [args.args[0] for args in harness.llm.execute_tool.await_args_list]
    assert names.count(name) == 2
    assert harness.hooks.run_before.await_count == 5
    if harness.approval:
        assert harness.approval.await_count == 5
    harness.assert_paired()
    third_round = [
        message
        for message in harness.history
        if message.get("tool_call_id", "").startswith("r3-")
    ]
    assert [message["tool_call_id"] for message in third_round] == [
        f"r3-{index}" for index in range(4)
    ]
    assert REPEAT_WARNING in third_round[1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_fresh_external_turn_uses_fresh_guard(mode, monkeypatch):
    harness = Harness(mode, monkeypatch, [[call()], [call()], [call()], None])
    await harness.run()
    harness.requests = 0
    await harness.run()
    assert harness.llm.execute_tool.await_count == 4
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_terminal_mixed_batch_pairs_every_call_and_dispatches_nothing(
    mode, monkeypatch
):
    batch = [call("new_parallel"), call(), call("ask"), call("browser_new")]
    harness = Harness(mode, monkeypatch, [[call()], [call()], [call()], batch])
    await harness.run(terminal=True)
    assert harness.requests == 4
    assert harness.llm.execute_tool.await_count == 2
    assert harness.hooks.run_before.await_count == 2
    if harness.approval:
        assert harness.approval.await_count == 2
    harness.assert_paired()
    assert all("terminated" in message["content"] for message in harness.history[-4:])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("duplicates", [3, 4])
async def test_duplicate_batch_never_dispatches_more_than_twice(
    mode, duplicates, monkeypatch
):
    harness = Harness(mode, monkeypatch, [[call()] * duplicates, None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 2
    assert harness.hooks.run_before.await_count == 2
    if harness.approval:
        assert harness.approval.await_count == 2
    harness.assert_paired()
    assert REPEAT_WARNING in harness.history[-2]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_hard_cap_bounds_alternating_model_across_paths(mode, monkeypatch):
    batches = [[call(str(index % 2))] for index in range(MAX_TOOL_ROUNDS + 1)]
    harness = Harness(mode, monkeypatch, batches)
    await harness.run(terminal=True)
    assert harness.requests == MAX_TOOL_ROUNDS + 1
    assert harness.llm.execute_tool.await_count == MAX_TOOL_ROUNDS
    harness.assert_paired()
    assert f"exceeded {MAX_TOOL_ROUNDS}" in harness.history[-1]["content"]


@pytest.mark.asyncio
async def test_concurrent_jobs_share_agent_but_not_guard_state(monkeypatch):
    from AgentCrew.modules.agents.agent_runner import run_agent_loop

    harness = Harness("jobs", monkeypatch, [])
    histories = [copy.deepcopy(harness.history), copy.deepcopy(harness.history)]
    rounds = {id(history): 0 for history in histories}

    def process_messages(history, callback):
        rounds[id(history)] += 1
        index = rounds[id(history)]
        tools = [call(call_id=f"{id(history)}-{index}")] if index <= 3 else []
        return ModelStream(harness.agent, callback, tools)

    harness.agent.process_messages = process_messages
    results = await asyncio.wait_for(
        asyncio.gather(
            *(run_agent_loop(harness.agent, history) for history in histories)
        ),
        timeout=5,
    )
    assert [result[0] for result in results] == ["Finished", "Finished"]
    assert harness.llm.execute_tool.await_count == 4
    for history in histories:
        assert REPEAT_WARNING in history[-2]["content"]


@pytest.mark.asyncio
async def test_acp_ask_resume_starts_fresh_external_guard(monkeypatch):
    ask = call("ask", {"questions": [{"question": "Which?", "guided_answers": ["A"]}]})
    harness = Harness("acp", monkeypatch, [[call()], [call()], [ask]])
    await harness.run()
    assert harness.state.pending_ask_tool is not None
    assert harness.llm.execute_tool.await_count == 2
    await harness.executor.resume_pending_ask("session", harness.state, "q0: A")
    assert harness.state.pending_ask_tool is None
    harness.batches = [[call()], [call()], [call()], None]
    harness.requests = 0
    await harness.run()
    assert harness.llm.execute_tool.await_count == 4
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_a2a_input_required_resume_preserves_remaining_calls_and_fresh_guard(
    monkeypatch,
    recover,
):
    from AgentCrew.modules.a2a.adapters import convert_agent_response_to_a2a_message

    ask = call("ask", {"questions": [{"question": "Which?", "guided_answers": ["A"]}]})
    harness = Harness("a2a", monkeypatch, [[call()], [call()], [ask, call()]])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 2
    saved = harness.store.save_pending_tools.await_args.args
    pending = {"ask_tool_use": saved[1], "remaining_tools": saved[2]}
    harness.context.message = convert_agent_response_to_a2a_message("A")
    harness.batches = [[call()], [call()], None] if recover else [[call()]]
    harness.requests = 0
    await asyncio.wait_for(
        harness.executor._resume_from_input_required(
            harness.context, harness.queue, "t", "c", harness.history, pending
        ),
        timeout=5,
    )
    assert harness.llm.execute_tool.await_count == 4
    harness.assert_paired()
    if recover:
        assert REPEAT_WARNING in harness.history[-2]["content"]
    else:
        assert "terminated" in harness.history[-1]["content"]
    harness.store.clear_pending_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_acp_terminal_prompt_refuses_and_clears_running_task(monkeypatch):
    from AgentCrew.modules.acp.agent import AgentCrewAcpAgent

    harness = Harness("acp", monkeypatch, [[call()]])
    client = AgentCrewAcpAgent(MagicMock(), session_store=MagicMock())
    client._sessions["session"] = harness.state
    client._turn_executor = harness.executor
    client._client_comm = harness.executor._client_comm
    client._session_lifecycle = AsyncMock()
    response = await asyncio.wait_for(client.prompt("session", []), timeout=5)
    assert response.stop_reason == "refusal"
    assert harness.state.current_task is None
    assert harness.llm.execute_tool.await_count == 2
    harness.assert_paired()
    last_message = client._client_comm.send_agent_message.await_args.args[1]
    assert "Tool loop protection terminated" in last_message
    client._session_lifecycle._persist_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_terminal_logging_never_dumps_raw_tool_arguments(mode, monkeypatch):
    from loguru import logger

    harness = Harness(
        mode, monkeypatch, [[call(arguments={"api_key": "sensitive-loop-marker"})]]
    )
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        await harness.run(terminal=True)
    finally:
        logger.remove(sink)
    assert messages
    assert all("sensitive-loop-marker" not in message for message in messages)
