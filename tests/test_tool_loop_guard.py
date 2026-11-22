"""Shared loop decisions and real pre-dispatch orchestration in all four modes."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AgentCrew.modules.agents.message_metadata import (
    is_tool_loop_finalization,
    is_tool_loop_recovery,
)
from AgentCrew.modules.agents.tool_loop_guard import (
    FINALIZATION_PROMPT_TEMPLATE,
    REPEAT_FINALIZATION_PROMPT,
    REPEAT_WARNING,
    ToolLoopError,
    ToolLoopGuard,
    tool_batch_fingerprint,
    tool_call_fingerprint,
)
from AgentCrew.modules.llm.token_usage import TokenUsage

TEST_MAX_TOOL_ROUNDS = 12


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


def test_first_two_execute_third_warns_fourth_finalizes_once():
    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    assert guard.check_round([call()]).errors == (None,)
    assert guard.check_round([call(call_id="new")]).errors == (None,)
    warning = guard.check_round([call()])
    assert warning.needs_recovery and warning.discard_assistant
    assert warning.omit_blocked_calls([call()])[0] == []
    assert warning.terminal_error is None
    assert guard.tools_enabled
    batch = [call()]
    finalization = guard.check_round(batch)
    assert finalization.finalization_required
    assert finalization.finalization_cause == guard.finalization_cause == "repeat"
    assert finalization.terminal_error is None
    assert finalization.omit_blocked_calls(batch)[0] == []
    finalization.raise_if_terminal()
    assert guard.start_request() is False
    guard.validate_response("Finished", [])
    with pytest.raises(ToolLoopError, match="already attempted"):
        guard.start_request()


def test_changed_arguments_absence_and_new_turn_reset_streaks():
    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
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
    assert ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS).check_round(
        [call()]
    ).errors == (None,)


def test_duplicates_count_once_per_complete_batch():
    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    batch = [call()] * 4
    for _ in range(2):
        decision = guard.check_round(batch)
        assert decision.errors == (None,) * 4
        assert not decision.discard_assistant
    assert guard.check_round(batch).needs_recovery
    assert guard.check_round(batch).finalization_cause == "repeat"


def test_hard_cap_catches_alternating_tools():
    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    for index in range(TEST_MAX_TOOL_ROUNDS):
        assert guard.check_round([call(str(index % 2))]).errors == (None,)
    terminal = guard.check_round([call("new")])
    assert terminal.finalization_required
    assert terminal.finalization_cause == guard.finalization_cause == "budget"
    assert terminal.terminal_error is None
    assert terminal.omit_blocked_calls([call("new")])[0] == []
    assert guard.start_request() is False
    with pytest.raises(ToolLoopError, match="already attempted"):
        guard.start_request()


def test_guard_logs_only_name_count_and_digest():
    from loguru import logger

    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
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


def finalization_plan(cause):
    if cause == "repeat":
        return [[call()]] * 4 + [None], 2, {"r3-0", "r4-0"}
    return (
        [[call(str(i % 2))] for i in range(TEST_MAX_TOOL_ROUNDS + 1)] + [None],
        TEST_MAX_TOOL_ROUNDS,
        {f"r{TEST_MAX_TOOL_ROUNDS + 1}-0"},
    )


class ModelStream:
    def __init__(self, agent, callback, tools):
        self.agent = agent
        self.callback = callback
        self.tools = tools
        self.format_calls = 0

    async def __aiter__(self):
        self.callback(
            self.tools or [],
            TokenUsage().merge(TokenUsage(input_tokens=1, output_tokens=1)),
        )
        response = "working" if self.tools else "Finished"
        yield (response, response, None)

    async def aclose(self):
        pass

    def format_assistant_message(self, response, thinking=None, tool_uses=None):
        self.format_calls += 1
        return self.agent._message_formatter.format_assistant_message(
            response, thinking, tool_uses
        )


class Harness:
    def __init__(
        self,
        mode,
        monkeypatch,
        batches,
        *,
        max_tool_rounds=TEST_MAX_TOOL_ROUNDS,
        guard=None,
    ):
        modules = {
            "jobs": "AgentCrew.modules.agents.agent_runner",
            "chat": "AgentCrew.modules.chat.message.handler",
            "acp": "AgentCrew.modules.acp.turn_executor",
            "a2a": "AgentCrew.modules.a2a.agent_executor",
        }
        monkeypatch.setattr(
            f"{modules[mode]}.ToolLoopGuard",
            lambda: (
                guard
                if guard is not None
                else ToolLoopGuard(max_tool_rounds=max_tool_rounds)
            ),
        )
        from AgentCrew.modules.agents.local_agent import LocalAgent
        from AgentCrew.modules.events import HookRegistry

        self.mode = mode
        self.batches = batches
        self.requests = 0
        self.request_histories = []
        self.tool_policies = []
        self.streams = []
        self.progress = AsyncMock()
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

    def process_messages(self, messages=None, callback=None, *, tools_enabled=True):
        self.tool_policies.append(tools_enabled)
        assert self.requests < 60, "stub model exceeded bounded round budget"
        self.request_histories.append(
            copy.deepcopy(messages if messages is not None else self.history)
        )
        batch = self.batches[min(self.requests, len(self.batches) - 1)]
        tools = [copy.deepcopy(tool) for tool in batch] if batch else []
        self.requests += 1
        for index, tool in enumerate(tools or []):
            tool["id"] = f"r{self.requests}-{index}"
        stream = ModelStream(self.agent, callback, tools)
        stream.tools_enabled = tools_enabled
        stream.round_number = self.requests
        self.streams.append(stream)
        return stream

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
        handler.current_user_input = None
        handler.current_user_input_idx = -1
        handler.last_assisstant_response_idx = -1
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

            operation = run_agent_loop(
                self.agent, self.history, progress_callback=self.progress
            )
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

    def request_contexts(self):
        return [
            message
            for history in self.request_histories
            for message in history
            if is_tool_loop_recovery(message)
        ]

    def assert_paired(self):
        assert not any(is_tool_loop_recovery(m) for m in self.history)
        for history in [self.history, *self.request_histories]:
            pending = []
            for message in history:
                if message["role"] != "tool":
                    assert not pending, (
                        "a user/assistant message interrupted tool results"
                    )
                pending.extend(tool["id"] for tool in message.get("tool_calls", []))
                if message["role"] == "tool":
                    assert pending and message["tool_call_id"] == pending.pop(0)
            assert not pending

    def assert_recovery(self, count=1):
        recovery = [
            message
            for message in self.request_contexts()
            if message["role"] == "user"
            and is_tool_loop_recovery(message)
            and not is_tool_loop_finalization(message)
        ]
        assert len(recovery) == count
        assert all(
            message["content"] == [{"type": "text", "text": REPEAT_WARNING}]
            for message in recovery
        )
        assert all(
            REPEAT_WARNING not in message["content"]
            for message in self.history
            if message["role"] == "tool"
        )
        return recovery

    def assert_repeat_finalization(self):
        messages = [m for m in self.request_contexts() if is_tool_loop_finalization(m)]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert is_tool_loop_recovery(messages[0])
        assert messages[0]["content"] == [
            {"type": "text", "text": REPEAT_FINALIZATION_PROMPT}
        ]
        assert "budget" not in REPEAT_FINALIZATION_PROMPT
        assert "exhausted" not in REPEAT_FINALIZATION_PROMPT
        assert "Tools are now disabled" in REPEAT_FINALIZATION_PROMPT
        return messages[0]

    def assert_blocked_has_no_side_effects(self, blocked_ids):
        validated = [
            args.args[0]["id"] for args in self.agent.validate_tool_use.call_args_list
        ]
        assert blocked_ids.isdisjoint(validated)
        for args in self.hooks.run_before.await_args_list:
            assert args.kwargs.get("tool_use", {}).get("id") not in blocked_ids
        if self.approval:
            assert all(
                args.args[0]["id"] not in blocked_ids
                for args in self.approval.await_args_list
            )
        for history in [self.history, *self.request_histories]:
            ids = {
                tool["id"]
                for message in history
                for tool in message.get("tool_calls", [])
            }
            ids.update(message.get("tool_call_id") for message in history)
            assert blocked_ids.isdisjoint(ids)
        if self.mode == "chat":
            from AgentCrew.modules.events import AppEvents

            for args in self.handler.bus.emit.await_args_list:
                if args.args[0] in {
                    AppEvents.TOOL_USE,
                    AppEvents.TOOL_CONFIRMATION_REQ,
                }:
                    tool = args.kwargs.get("tool_use", args.kwargs)
                    assert tool.get("id") not in blocked_ids
        elif self.mode == "acp":
            for args in self.executor._client_comm.send_tool_started.await_args_list:
                assert args.args[1]["id"] not in blocked_ids
        elif self.mode == "a2a":
            from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent

            for args in self.queue.enqueue_event.await_args_list:
                event = args.args[0]
                if isinstance(event, TaskArtifactUpdateEvent):
                    assert all(
                        blocked_id not in str(event.artifact)
                        for blocked_id in blocked_ids
                    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("recover", [True, False])
async def test_repeating_model_recovers_or_finalizes(mode, recover, monkeypatch):
    harness = Harness(mode, monkeypatch, [[call()]] * (3 if recover else 4) + [None])
    result = await harness.run()
    assert harness.requests == (4 if recover else 5)
    assert harness.tool_policies == ([True] * 4 if recover else [True] * 4 + [False])
    assert harness.llm.execute_tool.await_count == 2
    assert harness.hooks.run_before.await_count == 2
    assert harness.agent.validate_tool_use.call_count == 2
    if harness.approval:
        assert harness.approval.await_count == 2
    assert len([m for m in harness.history if m["role"] == "tool"]) == 2
    harness.assert_recovery()
    harness.assert_blocked_has_no_side_effects(
        {"r3-0"} if recover else {"r3-0", "r4-0"}
    )
    if mode == "jobs":
        assert (
            sum(args.args[0] == "tool" for args in harness.progress.await_args_list)
            == 2
        )
    harness.assert_paired()
    assert harness.history[-1]["content"][-1]["text"] == "Finished"
    assert harness.agent.extract_last_user_message_for_memory(harness.history) == "work"
    if recover:
        assert not any(is_tool_loop_finalization(m) for m in harness.history)
    else:
        harness.assert_repeat_finalization()
    if mode in {"chat", "jobs", "a2a"}:
        assert result[0] == "Finished"
        assert result[1].total_input_tokens == (4 if recover else 5)
    if mode == "acp":
        assert harness.state.token_usage.total_input_tokens == (4 if recover else 5)
        assert "Finished" in str(
            harness.executor._client_comm.send_agent_message.await_args_list
        )
    if mode == "chat":
        from AgentCrew.modules.events import AppEvents

        assert harness.session.status == "completed"
        assert harness.session.finished.is_set()
        assert not any(
            args.args[0] == AppEvents.ERROR
            for args in harness.handler.bus.emit.await_args_list
        )
    if mode == "a2a":
        from a2a.types.a2a_pb2 import (
            TaskArtifactUpdateEvent,
            TaskState,
            TaskStatusUpdateEvent,
        )

        states = [
            args.args[0]
            for args in harness.queue.enqueue_event.await_args_list
            if isinstance(args.args[0], TaskStatusUpdateEvent)
        ]
        assert states[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert states[-1].task_id == "t" and states[-1].context_id == "c"
        assert [
            args.args[1] for args in harness.store.append_history.await_args_list
        ] == harness.history[1:]
        artifacts = [
            args.args[0].artifact
            for args in harness.queue.enqueue_event.await_args_list
            if isinstance(args.args[0], TaskArtifactUpdateEvent)
        ]
        assert len([a for a in artifacts if a.artifact_id.startswith("tool_")]) == 2
        assert "Finished" in str(artifacts)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("name", ["read_file", "browser_click"])
async def test_changed_mixed_batch_executes_every_call_in_order(
    mode, name, monkeypatch
):
    repeated = call(name)
    mixed = [
        call("other_parallel"),
        repeated,
        call("browser_other"),
        call("last_parallel"),
    ]
    harness = Harness(mode, monkeypatch, [[repeated], [repeated], mixed, None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 6
    assert harness.agent.validate_tool_use.call_count == 6
    assert harness.hooks.run_before.await_count == 6
    if harness.approval:
        assert harness.approval.await_count == 6
    harness.assert_paired()
    harness.assert_recovery(count=0)
    assert all(harness.tool_policies)
    third = [
        m["tool_call_id"]
        for m in harness.history
        if m.get("tool_call_id", "").startswith("r3-")
    ]
    assert third == [f"r3-{i}" for i in range(4)]


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
async def test_repeat_finalization_omits_entire_mixed_batch(mode, monkeypatch):
    batch = [call("new_parallel"), call(), call("browser_new")]
    harness = Harness(mode, monkeypatch, [batch] * 4 + [None])
    await harness.run()
    assert harness.requests == 5
    assert harness.tool_policies == [True] * 4 + [False]
    assert harness.llm.execute_tool.await_count == 6
    assert harness.agent.validate_tool_use.call_count == 6
    assert harness.hooks.run_before.await_count == 6
    if harness.approval:
        assert harness.approval.await_count == 6
    harness.assert_paired()
    harness.assert_recovery()
    harness.assert_repeat_finalization()
    harness.assert_blocked_has_no_side_effects(
        {f"r{r}-{i}" for r in (3, 4) for i in range(3)}
    )
    assert harness.streams[2].format_calls == harness.streams[3].format_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("duplicates", [3, 4])
async def test_duplicates_in_one_batch_do_not_false_trigger(
    mode, duplicates, monkeypatch
):
    harness = Harness(mode, monkeypatch, [[call()] * duplicates, None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == duplicates
    assert harness.hooks.run_before.await_count == duplicates
    if harness.approval:
        assert harness.approval.await_count == duplicates
    harness.assert_paired()
    harness.assert_recovery(count=0)
    assert harness.tool_policies == [True, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("mixed", [False, True])
async def test_hard_cap_finalizes_once_without_tools(mode, mixed, monkeypatch):
    batches = [[call(str(index % 2))] for index in range(TEST_MAX_TOOL_ROUNDS)]
    attempted = (
        [call("overflow"), call("browser_click"), call("ask")]
        if mixed
        else [call("overflow")]
    )
    harness = Harness(mode, monkeypatch, batches + [attempted, None])
    result = await harness.run()
    assert harness.requests == TEST_MAX_TOOL_ROUNDS + 2
    assert harness.tool_policies == [True] * (TEST_MAX_TOOL_ROUNDS + 1) + [False]
    assert harness.llm.execute_tool.await_count == TEST_MAX_TOOL_ROUNDS
    assert harness.agent.validate_tool_use.call_count == TEST_MAX_TOOL_ROUNDS
    assert harness.hooks.run_before.await_count == TEST_MAX_TOOL_ROUNDS
    if harness.approval:
        assert harness.approval.await_count == TEST_MAX_TOOL_ROUNDS
    harness.assert_paired()
    messages = [
        message
        for message in harness.request_contexts()
        if is_tool_loop_finalization(message)
    ]
    assert len(messages) == 1
    assert messages[0]["content"] == [
        {
            "type": "text",
            "text": FINALIZATION_PROMPT_TEMPLATE.format(
                max_tool_rounds=TEST_MAX_TOOL_ROUNDS
            ),
        }
    ]
    assert harness.history[-1]["content"][0]["text"] == "Finished"
    harness.assert_blocked_has_no_side_effects(
        {f"r{TEST_MAX_TOOL_ROUNDS + 1}-{i}" for i in range(len(attempted))}
    )
    assert harness.agent.extract_last_user_message_for_memory(harness.history) == "work"
    assert is_tool_loop_recovery(messages[0])
    if mode != "chat":
        assert harness.agent.store_memory_if_available.call_args.args[0] == "work"
    if mode in {"chat", "jobs", "a2a"}:
        assert result[1].total_input_tokens == TEST_MAX_TOOL_ROUNDS + 2
    if mode == "chat":
        assert harness.session.status == "completed"
    if mode == "acp":
        assert harness.state.token_usage.total_input_tokens == TEST_MAX_TOOL_ROUNDS + 2
    if mode == "a2a":
        from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent

        assert [
            args.args[1] for args in harness.store.append_history.await_args_list
        ] == harness.history[1:]
        tool_artifacts = [
            args.args[0].artifact
            for args in harness.queue.enqueue_event.await_args_list
            if isinstance(args.args[0], TaskArtifactUpdateEvent)
            and args.args[0].artifact.artifact_id.startswith("tool_")
        ]
        assert len(tool_artifacts) == TEST_MAX_TOOL_ROUNDS
    if mode == "jobs":
        assert (
            sum(args.args[0] == "tool" for args in harness.progress.await_args_list)
            == TEST_MAX_TOOL_ROUNDS
        )


@pytest.mark.asyncio
async def test_concurrent_jobs_share_agent_but_not_guard_state(monkeypatch):
    from AgentCrew.modules.agents.agent_runner import run_agent_loop

    harness = Harness("jobs", monkeypatch, [])
    histories = [
        [{"role": "user", "content": [{"type": "text", "text": str(i)}]}]
        for i in range(2)
    ]
    rounds = {str(i): 0 for i in range(2)}
    contexts = []

    def process_messages(history, callback, *, tools_enabled=True):
        key = history[0]["content"][0]["text"]
        rounds[key] += 1
        index = rounds[key]
        contexts.extend(m for m in history if is_tool_loop_recovery(m))
        assert tools_enabled is (index <= 4)
        tools = [call(call_id=f"{key}-{index}")] if index <= 4 else []
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
    assert len(contexts) == 4
    assert sum(is_tool_loop_finalization(m) for m in contexts) == 2
    for history in histories:
        assert len([m for m in history if m["role"] == "assistant"]) == 3
        assert not any(is_tool_loop_recovery(m) for m in history)


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
    harness.batches = [[call()]] * (2 if recover else 3) + [None]
    harness.requests = 0
    await asyncio.wait_for(
        harness.executor._resume_from_input_required(
            harness.context, harness.queue, "t", "c", harness.history, pending
        ),
        timeout=5,
    )
    assert harness.llm.execute_tool.await_count == 4
    harness.assert_paired()
    harness.assert_recovery()
    assert harness.history[-1]["content"][0]["text"] == "Finished"
    if not recover:
        harness.assert_repeat_finalization()
        assert harness.tool_policies[-1] is False
    harness.store.clear_pending_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_acp_invalid_repeat_finalization_refuses_and_clears_running_task(
    monkeypatch,
):
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
async def test_repeat_finalization_failure_logging_never_dumps_raw_tool_arguments(
    mode, monkeypatch
):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("finalize", [False, True])
async def test_several_repeated_calls_produce_one_context_per_round(
    mode, finalize, monkeypatch
):
    repeated = [call("read_file"), call("browser_click")]
    harness = Harness(mode, monkeypatch, [repeated] * (4 if finalize else 3) + [None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 4
    assert harness.agent.validate_tool_use.call_count == 4
    assert harness.hooks.run_before.await_count == 4
    harness.assert_paired()
    harness.assert_recovery()
    harness.assert_blocked_has_no_side_effects(
        {"r3-0", "r3-1", "r4-0", "r4-1"} if finalize else {"r3-0", "r3-1"}
    )
    if finalize:
        harness.assert_repeat_finalization()
        assert harness.tool_policies == [True] * 4 + [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("change", ["arguments", "tool"])
async def test_recovery_can_change_arguments_or_tool_and_continue(
    mode, change, monkeypatch
):
    harness = Harness(
        mode,
        monkeypatch,
        [[call()]] * 3
        + [
            [
                call(arguments={"new": 1})
                if change == "arguments"
                else call("different")
            ],
            None,
        ],
    )
    await harness.run()
    assert harness.llm.execute_tool.await_count == 3
    harness.assert_paired()
    harness.assert_recovery()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("finalize", [False, True])
async def test_memory_storage_uses_external_user_after_recovery(
    mode, finalize, monkeypatch
):
    harness = Harness(mode, monkeypatch, [[call()]] * (4 if finalize else 3) + [None])
    if mode == "chat":
        from AgentCrew.modules.chat.message.handler import MessageHandler

        handler = harness.handler
        handler._finalize_current_turn = MessageHandler._finalize_current_turn.__get__(
            handler
        )
        handler.current_user_input = harness.history[0]
        handler.current_user_input_idx = 0
        handler.last_assisstant_response_idx = -1
        handler.conversation_manager = MagicMock()
        handler._turn_usage_ledger = {}
        handler._turn_usage_committed = {}
    await harness.run()
    harness.agent.store_memory_if_available.assert_called_once()
    assert harness.agent.store_memory_if_available.call_args.args[0] == "work"
    assert harness.agent.extract_last_user_message_for_memory(harness.history) == "work"
    extracted = harness.agent._memory_coordinator.extract_assistant_messages_for_memory(
        harness.history
    )
    assert extracted == ["working", "working", "Finished"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "a2a"])
async def test_changed_ask_batch_resumes_without_synthetic_context(mode, monkeypatch):
    ask = call("ask", {"questions": [{"question": "Which?", "guided_answers": ["A"]}]})
    mixed = [call(), ask, call("other_parallel")] if mode == "a2a" else [call(), ask]
    harness = Harness(mode, monkeypatch, [[call()], [call()], mixed])
    await harness.run()
    harness.assert_recovery(count=0)
    assert harness.llm.execute_tool.await_count == 3
    harness.batches = [None]
    harness.requests = 0
    if mode == "acp":
        await harness.executor.resume_pending_ask("session", harness.state, "q0: A")
        await harness.run()
    else:
        from AgentCrew.modules.a2a.adapters import convert_agent_response_to_a2a_message

        saved = harness.store.save_pending_tools.await_args.args
        assert [tool["name"] for tool in saved[2]] == ["other_parallel"]
        harness.context.message = convert_agent_response_to_a2a_message("A")
        await harness.executor._resume_from_input_required(
            harness.context,
            harness.queue,
            "t",
            "c",
            harness.history,
            {"ask_tool_use": saved[1], "remaining_tools": saved[2]},
        )
        assert harness.llm.execute_tool.await_count == 4
        assert [
            args.args[1] for args in harness.store.append_history.await_args_list
        ] == harness.history[1:]
    harness.assert_recovery(count=0)
    harness.assert_paired()


def test_request_overlay_is_internal_deep_copied_until_acknowledged():
    from AgentCrew.modules.agents.message_metadata import (
        attach_stream_metadata,
        get_responses_provider_state,
        strip_internal_metadata,
    )

    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    for _ in range(3):
        decision = guard.check_round([call()])
    assert decision.needs_recovery and decision.discard_assistant
    previous = {
        "role": "assistant",
        "content": [{"type": "text", "text": "previous"}],
        "tool_calls": [{"id": "prior", "name": "read_file", "arguments": {}}],
    }
    attach_stream_metadata(
        previous,
        {
            "provider": "openai",
            "model": "test",
            "reasoning_items": [
                {
                    "id": "rs_previous",
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "previous reasoning"}],
                }
            ],
        },
    )
    history = [
        {"role": "user", "content": [{"type": "text", "text": "original"}]},
        previous,
        {"role": "tool", "tool_call_id": "prior", "content": "ok"},
    ]
    original = copy.deepcopy(history)
    messages = guard.request_history(history)
    retry_messages = guard.request_history(history)
    assert messages == retry_messages and messages is not retry_messages
    assert messages[:-1] == history and messages is not history
    assert is_tool_loop_recovery(messages[-1])
    assert get_responses_provider_state(messages[1]) == get_responses_provider_state(
        previous
    )
    provider_messages = strip_internal_metadata(messages)
    assert all("_metadata" not in m for m in provider_messages)
    assert provider_messages[-1]["content"][0]["text"] == REPEAT_WARNING
    assert provider_messages[-2]["tool_call_id"] == "prior"
    messages[0]["content"][0]["text"] = "changed"
    messages[-1]["content"][0]["text"] = "mutated intervention"
    get_responses_provider_state(messages[1])["output_items"][0]["summary"].append(
        {"type": "summary_text", "text": "changed"}
    )
    assert history == original
    assert retry_messages[:-1] == original
    assert retry_messages[-1]["content"][0]["text"] == REPEAT_WARNING
    assert guard.request_history(history) == retry_messages
    guard.acknowledge_response()
    assert guard.request_history(history) is history
    guard.acknowledge_response()
    assert guard.request_history(history) is history


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_textless_blocked_round_still_recovers(mode, monkeypatch):
    harness = Harness(mode, monkeypatch, [[call()]] * 3 + [None])

    async def textless_stream(stream):
        stream.callback(stream.tools or [], TokenUsage(input_tokens=1))
        yield ("" if stream.tools else "Finished", None, None)

    monkeypatch.setattr(ModelStream, "__aiter__", textless_stream)
    await harness.run()
    assert harness.requests == 4
    assert harness.llm.execute_tool.await_count == 2
    harness.assert_recovery()
    harness.assert_paired()


def test_recovery_context_is_not_enhanced_as_a_new_external_task():
    from AgentCrew.modules.agents.context_manager import AgentContextManager
    from AgentCrew.modules.agents.message_metadata import mark_tool_loop_recovery

    recovery = {"role": "user", "content": [{"type": "text", "text": REPEAT_WARNING}]}
    mark_tool_loop_recovery(recovery)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "work"}]},
        recovery,
    ]
    original = copy.deepcopy(messages)
    context = AgentContextManager(MagicMock())
    context.build_adaptive_context = MagicMock()
    context.enhance_messages(messages)
    context.build_adaptive_context.assert_not_called()
    assert messages == original


@pytest.mark.asyncio
async def test_chat_changed_transfer_batch_executes_across_agent_change(monkeypatch):
    from AgentCrew.modules.agents.local_agent import LocalAgent

    harness = Harness(
        "chat", monkeypatch, [[call()], [call()], [call(), call("transfer")], None]
    )
    target = LocalAgent("target", "test", harness.llm, {}, [])
    target.process_messages = harness.process_messages
    target.store_memory_if_available = MagicMock()
    harness.handler.agent_manager.get_current_agent = lambda: target
    harness.handler.last_assisstant_response_idx = -1
    await harness.run()
    assert harness.handler.agent is target
    assert harness.llm.execute_tool.await_count == 4
    harness.assert_recovery(count=0)
    harness.assert_paired()
    assert all(
        tool["name"] != "transfer"
        for message in harness.history
        for tool in message.get("tool_calls", [])
    )


@pytest.mark.asyncio
async def test_chat_repeated_transfer_is_omitted_before_dispatch(monkeypatch):
    harness = Harness("chat", monkeypatch, [[call("transfer")]] * 3 + [None])
    harness.handler.agent_manager.get_current_agent = lambda: harness.agent
    harness.handler.last_assisstant_response_idx = -1
    await harness.run()
    assert harness.llm.execute_tool.await_count == 2
    harness.assert_recovery()
    harness.assert_paired()
    harness.assert_blocked_has_no_side_effects({"r3-0"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize(
    "outcome", ["empty", "malformed", "tools", "exception", "api_error", "cancel"]
)
@pytest.mark.parametrize("cause", ["budget", "repeat"])
async def test_finalization_failures_never_retry_or_dispatch(
    mode, outcome, cause, monkeypatch
):
    original = ModelStream.__aiter__
    closes = []

    async def stream_result(stream):
        if stream.tools_enabled:
            async for item in original(stream):
                yield item
            return
        if outcome == "exception":
            raise RuntimeError("final transport failure")
        if outcome == "api_error":
            import httpx
            from openai import BadRequestError

            raise BadRequestError(
                "context window",
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://stub.invalid")
                ),
                body={"code": "context_length_exceeded"},
            )
        if outcome == "cancel":
            raise asyncio.CancelledError()
        stream.callback(
            [call(call_id="anomaly")] if outcome == "tools" else [],
            TokenUsage(input_tokens=1),
        )
        yield ({"invalid": True} if outcome == "malformed" else "", None, None)

    async def close(stream):
        closes.append(stream)

    monkeypatch.setattr(ModelStream, "__aiter__", stream_result)
    monkeypatch.setattr(ModelStream, "aclose", close)
    batches, executions, blocked = finalization_plan(cause)
    harness = Harness(mode, monkeypatch, batches)
    result = None
    if outcome == "cancel" and mode != "chat":
        with pytest.raises(asyncio.CancelledError):
            await harness.run()
    elif outcome == "cancel":
        result = await harness.run()
        assert harness.session.status == "canceled"
    elif outcome in {"exception", "api_error"} and mode in {"jobs", "acp"}:
        with pytest.raises(Exception, match="final transport failure|context window"):
            await harness.run()
    else:
        result = await harness.run(terminal=mode in {"jobs", "acp"})
    if mode == "chat":
        assert result[1].total_input_tokens >= len(batches) - 1
        assert harness.handler.stream_generator is None
    assert harness.tool_policies[-1] is False
    assert len(harness.tool_policies) == len(batches)
    assert harness.llm.execute_tool.await_count == executions
    assert harness.agent.validate_tool_use.call_count == executions
    assert harness.hooks.run_before.await_count == executions
    if harness.approval:
        assert harness.approval.await_count == executions
    harness.assert_blocked_has_no_side_effects(blocked | {"anomaly"})
    harness.assert_paired()
    assert (
        len([m for m in harness.request_contexts() if is_tool_loop_finalization(m)])
        == 1
    )
    assert harness.streams[-1] in closes


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("cause", ["budget", "repeat"])
async def test_finalization_does_not_leak_to_fresh_turn(mode, cause, monkeypatch):
    batches, executions, blocked = finalization_plan(cause)
    harness = Harness(mode, monkeypatch, batches)
    await harness.run()
    harness.assert_blocked_has_no_side_effects(blocked)
    harness.batches = [[call()], None]
    harness.requests = 0
    await harness.run()
    assert harness.tool_policies[-2:] == [True, True]
    assert harness.llm.execute_tool.await_count == executions + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_final_text_at_configured_limit_does_not_trigger_fallback(
    mode, monkeypatch
):
    harness = Harness(
        mode,
        monkeypatch,
        [[call(str(i % 2))] for i in range(TEST_MAX_TOOL_ROUNDS)] + [None],
    )
    await harness.run()
    assert harness.requests == TEST_MAX_TOOL_ROUNDS + 1
    assert all(harness.tool_policies)
    assert harness.llm.execute_tool.await_count == TEST_MAX_TOOL_ROUNDS
    assert not any(is_tool_loop_finalization(message) for message in harness.history)
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["none", "budget", "repeat"])
@pytest.mark.parametrize("failure", ["runtime", "connection"])
async def test_acp_stream_construction_failure_preserves_original_exception(
    cause, failure, monkeypatch
):
    finalization = cause != "none"
    batches, executions, blocked = (
        finalization_plan(cause) if finalization else ([None], 0, set())
    )
    harness = Harness("acp", monkeypatch, batches)
    error = RuntimeError("ACP stream construction failed")
    if failure == "connection":
        import httpx
        from openai import APIConnectionError

        error = APIConnectionError(
            message="ACP stream construction failed",
            request=httpx.Request("POST", "https://stub.invalid"),
        )
    process_messages = harness.agent.process_messages
    policies = []
    construction_histories = []

    def fail_construction(messages=None, callback=None, *, tools_enabled=True):
        policies.append(tools_enabled)
        construction_histories.append(copy.deepcopy(messages))
        if not finalization or not tools_enabled:
            raise error
        return process_messages(messages, callback, tools_enabled=tools_enabled)

    harness.agent.process_messages = fail_construction
    with pytest.raises(type(error)) as caught:
        await harness.run()
    assert caught.value is error
    assert policies == (
        [True] * (len(batches) - 1) + [False] if finalization else [True]
    )
    assert harness.requests == (len(batches) - 1 if finalization else 0)
    assert harness.llm.execute_tool.await_count == executions
    assert harness.agent.validate_tool_use.call_count == executions
    assert harness.approval.await_count == executions
    assert harness.hooks.run_before.await_count == executions
    harness.agent.store_memory_if_available.assert_not_called()
    harness.assert_paired()
    if finalization:
        harness.assert_blocked_has_no_side_effects(blocked)
        assert (
            sum(
                is_tool_loop_finalization(message)
                for message in construction_histories[-1]
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["budget", "repeat"])
async def test_chat_finalization_clears_deferred_transfer_without_follow_up(
    cause,
    monkeypatch,
):
    from AgentCrew.modules.events import AppEvents

    batches, executions, blocked = finalization_plan(cause)
    harness = Harness("chat", monkeypatch, batches)
    harness.handler.agent_manager.defered_transfer = "Return to the coordinator"
    response, usage = await harness.run()
    assert response == "Finished"
    assert usage.total_input_tokens == len(batches)
    assert harness.tool_policies == [True] * (len(batches) - 1) + [False]
    assert harness.requests == len(batches)
    assert harness.llm.execute_tool.await_count == executions
    assert harness.session.status == "completed"
    assert harness.session.finished.is_set()
    assert harness.handler.agent_manager.defered_transfer == ""
    assert harness.history[-1]["content"][0]["text"] == "Finished"
    assert all(
        "<Post_Transfer_Action_Reminder>" not in str(message["content"])
        for message in harness.history
    )
    assert (
        sum(
            is_tool_loop_finalization(message) for message in harness.request_contexts()
        )
        == 1
    )
    assert not any(
        args.args[0] == AppEvents.ERROR
        for args in harness.handler.bus.emit.await_args_list
    )
    harness.assert_paired()
    harness.assert_blocked_has_no_side_effects(blocked)
    harness.batches = [None]
    harness.requests = 0
    await harness.run()
    assert harness.requests == 1
    assert harness.tool_policies[-1] is True


@pytest.mark.asyncio
async def test_chat_normal_final_response_still_runs_deferred_transfer_follow_up(
    monkeypatch,
):
    harness = Harness("chat", monkeypatch, [None])
    harness.handler.agent_manager.defered_transfer = "Return to the coordinator"
    response, usage = await harness.run()
    assert response == "Finished"
    assert usage.total_input_tokens == 2
    assert harness.requests == 2
    assert harness.tool_policies == [True, True]
    assert harness.session.status == "completed"
    assert harness.handler.agent_manager.defered_transfer == ""
    reminders = [
        message
        for message in harness.history
        if "<Post_Transfer_Action_Reminder>" in str(message["content"])
    ]
    assert len(reminders) == 1
    assert "Return to the coordinator" in reminders[0]["content"][0]["text"]
    assert reminders[0] in harness.request_histories[-1]
    assert not any(is_tool_loop_finalization(message) for message in harness.history)
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("source", ["environment", "config"])
async def test_configured_budgets_apply_to_all_surfaces_and_new_turns(
    mode, source, monkeypatch
):
    from AgentCrew.modules.config.global_config import GlobalConfig

    settings = {"max_tool_rounds": 3}
    monkeypatch.setattr(GlobalConfig, "read", lambda _: {"global_settings": settings})
    monkeypatch.delenv("AGENTCREW_MAX_TOOL_ROUNDS", raising=False)
    limit = 3
    if source == "environment":
        limit = 4
        monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", str(limit))
    harness = Harness(
        mode,
        monkeypatch,
        [[call(str(i % 2))] for i in range(limit + 1)] + [None],
        max_tool_rounds=None,
    )
    await harness.run()
    assert harness.requests == limit + 2
    assert harness.tool_policies == [True] * (limit + 1) + [False]
    assert harness.llm.execute_tool.await_count == limit
    harness.assert_blocked_has_no_side_effects({f"r{limit + 1}-0"})
    finalization = [
        m for m in harness.request_contexts() if is_tool_loop_finalization(m)
    ]
    assert len(finalization) == 1
    assert (
        f"tool budget of {limit} model tool rounds"
        in finalization[0]["content"][0]["text"]
    )
    harness.assert_paired()

    new_limit = limit + 2
    if source == "environment":
        monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", str(new_limit))
    else:
        settings["max_tool_rounds"] = new_limit
    harness.batches = [[call(str(i % 2))] for i in range(new_limit + 1)] + [None]
    harness.requests = 0
    await harness.run()
    assert harness.requests == new_limit + 2
    assert harness.tool_policies[-(new_limit + 2) :] == [True] * (new_limit + 1) + [
        False
    ]
    assert harness.llm.execute_tool.await_count == limit + new_limit
    finalization = [
        m for m in harness.request_contexts() if is_tool_loop_finalization(m)
    ]
    assert len(finalization) == 2
    assert (
        f"tool budget of {new_limit} model tool rounds"
        in finalization[-1]["content"][0]["text"]
    )
    harness.assert_paired()


def test_repeat_finalization_takes_priority_at_budget_boundary():
    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    for index in range(TEST_MAX_TOOL_ROUNDS - 3):
        guard.check_round([call(str(index % 2))])
    for _ in range(3):
        guard.check_round([call("repeat")])
    decision = guard.check_round([call("repeat")])
    assert decision.terminal_error is None
    assert decision.finalization_required
    assert decision.finalization_cause == "repeat"
    assert (
        decision.request_message()["content"][0]["text"] == REPEAT_FINALIZATION_PROMPT
    )
    assert guard.start_request() is False
    with pytest.raises(ToolLoopError, match="already attempted"):
        guard.start_request()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_repeat_at_budget_boundary_finalizes_once_with_repeat_cause(
    mode, monkeypatch
):
    batches = [[call(str(i % 2))] for i in range(TEST_MAX_TOOL_ROUNDS - 3)]
    harness = Harness(mode, monkeypatch, batches + [[call("repeat")]] * 4 + [None])
    await harness.run()
    assert harness.requests == TEST_MAX_TOOL_ROUNDS + 2
    assert harness.tool_policies == [True] * (TEST_MAX_TOOL_ROUNDS + 1) + [False]
    assert harness.llm.execute_tool.await_count == TEST_MAX_TOOL_ROUNDS - 1
    assert harness.agent.validate_tool_use.call_count == TEST_MAX_TOOL_ROUNDS - 1
    assert harness.hooks.run_before.await_count == TEST_MAX_TOOL_ROUNDS - 1
    if harness.approval:
        assert harness.approval.await_count == TEST_MAX_TOOL_ROUNDS - 1
    harness.assert_recovery()
    harness.assert_repeat_finalization()
    harness.assert_paired()
    harness.assert_blocked_has_no_side_effects(
        {f"r{TEST_MAX_TOOL_ROUNDS}-0", f"r{TEST_MAX_TOOL_ROUNDS + 1}-0"}
    )


def test_batch_fingerprint_is_order_independent_and_preserves_multiplicity():
    a = call("A", {"b": 2, "a": [1, 2]}, "first")
    same_a = call("A", {"a": [1, 2], "b": 2}, "other-id")
    b = call("B")
    assert tool_batch_fingerprint([a, b]) == tool_batch_fingerprint([b, same_a])
    assert tool_batch_fingerprint([a, a, b]) == tool_batch_fingerprint([b, same_a, a])
    assert tool_batch_fingerprint([a, a, b]) != tool_batch_fingerprint([a, b])
    assert tool_batch_fingerprint([a, b]) != tool_batch_fingerprint([a])
    assert tool_batch_fingerprint([a, b]) != tool_batch_fingerprint([a, call("C")])
    assert tool_batch_fingerprint([a, b]) != tool_batch_fingerprint(
        [call("A", {"a": [2, 1], "b": 2}), b]
    )


@pytest.mark.parametrize(
    "change", ["added", "removed", "arguments", "tool", "multiplicity"]
)
def test_changed_complete_batch_resets_after_warning(change):
    base = [call("A"), call("B")]
    changed = {
        "added": base + [call("C")],
        "removed": base[:1],
        "arguments": [call("A", {"value": 1}), base[1]],
        "tool": [base[0], call("C")],
        "multiplicity": base + [base[0]],
    }[change]
    guard = ToolLoopGuard(max_tool_rounds=12)
    for _ in range(3):
        warning = guard.check_round(base)
    assert warning.needs_recovery
    guard.request_history([])
    guard.acknowledge_response()
    for _ in range(2):
        assert not guard.check_round(changed).discard_assistant
    decision = guard.check_round(changed)
    assert decision.needs_recovery and not decision.finalization_required
    assert guard.tools_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
async def test_shared_call_with_changing_batch_is_progress(mode, monkeypatch):
    batches = [[call("A"), call(name)] for name in ("B", "C", "D")]
    harness = Harness(mode, monkeypatch, batches + [None])
    await harness.run()
    assert harness.llm.execute_tool.await_count == 6
    assert harness.agent.validate_tool_use.call_count == 6
    assert harness.hooks.run_before.await_count == 6
    assert len(harness.request_contexts()) == 0
    assert all(harness.tool_policies)
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("cause", ["recovery", "repeat", "budget"])
async def test_discarded_round_text_thinking_and_metadata_are_never_formatted_or_persisted(
    mode, cause, monkeypatch
):
    if cause == "recovery":
        batches, executions, discarded = [[call()]] * 3 + [None], 2, [3]
    else:
        batches, executions, _ = finalization_plan(cause)
        discarded = [3, 4] if cause == "repeat" else [TEST_MAX_TOOL_ROUNDS + 1]
    harness = Harness(mode, monkeypatch, batches)

    async def reasoning_stream(stream):
        stream.callback(
            stream.tools or [],
            TokenUsage().merge(TokenUsage(input_tokens=1, output_tokens=1)),
        )
        response = f"assistant-{stream.round_number}"
        yield (
            response,
            response,
            (f"thinking-{stream.round_number}", "test-signature"),
        )

    monkeypatch.setattr(ModelStream, "__aiter__", reasoning_stream)
    await harness.run()
    assert harness.llm.execute_tool.await_count == executions
    assistant_messages = [m for m in harness.history if m["role"] == "assistant"]
    assert len(assistant_messages) == executions + 1
    for number in discarded:
        assert harness.streams[number - 1].format_calls == 0
        assert all(
            block.get("text") != f"assistant-{number}"
            and block.get("thinking") != f"thinking-{number}"
            for m in harness.history
            for block in m.get("content", [])
            if isinstance(block, dict)
        )
    assert not any(is_tool_loop_recovery(m) for m in harness.history)
    assert all(harness.streams[i - 1].format_calls == 0 for i in discarded)
    assert harness.agent.extract_last_user_message_for_memory(harness.history) == "work"
    harness.assert_paired()
    if mode == "a2a":
        assert [
            args.args[1] for args in harness.store.append_history.await_args_list
        ] == harness.history[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("finalize", [False, True])
async def test_reordered_identical_batches_still_intervene(mode, finalize, monkeypatch):
    a, b = call("A"), call("B")
    batches = [[a, b], [b, a], [a, b]] + ([[b, a]] if finalize else []) + [None]
    harness = Harness(mode, monkeypatch, batches)
    await harness.run()
    assert harness.llm.execute_tool.await_count == 4
    assert harness.agent.validate_tool_use.call_count == 4
    assert harness.hooks.run_before.await_count == 4
    harness.assert_recovery()
    assert harness.streams[2].format_calls == 0
    if finalize:
        harness.assert_repeat_finalization()
        assert harness.streams[3].format_calls == 0
    assert harness.tool_policies == ([True] * 4 + [False] if finalize else [True] * 4)
    harness.assert_paired()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("finalize", [False, True])
async def test_ask_in_discarded_batch_does_not_pause_or_request_input(
    mode, finalize, monkeypatch
):
    batch = [
        call("A"),
        call("ask", {"questions": [{"question": "Which?", "guided_answers": ["A"]}]}),
    ]
    guard = ToolLoopGuard(max_tool_rounds=12)
    for _ in range(3 if finalize else 2):
        guard.check_round(batch)
    harness = Harness(mode, monkeypatch, [batch, None], guard=guard)
    await harness.run()
    assert harness.requests == 2
    assert harness.tool_policies == [True, not finalize]
    assert harness.llm.execute_tool.await_count == 0
    assert harness.agent.validate_tool_use.call_count == 0
    assert harness.hooks.run_before.await_count == 0
    if harness.approval:
        assert harness.approval.await_count == 0
    harness.assert_blocked_has_no_side_effects({"r1-0", "r1-1"})
    assert harness.streams[0].format_calls == 0
    harness.assert_paired()
    if mode == "acp":
        assert harness.state.pending_ask_tool is None
        harness.executor._client_comm.send_ask_request.assert_not_awaited()
    if mode == "a2a":
        harness.store.save_pending_tools.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs"])
async def test_empty_response_retry_breaks_batch_streak(mode, monkeypatch):
    harness = Harness(mode, monkeypatch, [[call()]] * 3 + [None, [call()], None])
    original = ModelStream.__aiter__

    async def empty_fourth(stream):
        if stream.round_number == 4:
            stream.callback([], TokenUsage().merge(TokenUsage(input_tokens=1)))
            yield ("", None, None)
        else:
            async for item in original(stream):
                yield item

    monkeypatch.setattr(ModelStream, "__aiter__", empty_fourth)
    await harness.run()
    assert harness.requests == 6
    assert all(harness.tool_policies)
    assert harness.llm.execute_tool.await_count == 3
    harness.assert_recovery()
    harness.assert_paired()
    assert not any(is_tool_loop_finalization(m) for m in harness.request_contexts())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "jobs", "acp", "a2a"])
@pytest.mark.parametrize("cause", ["repeat", "budget"])
async def test_cancelled_finalization_never_restores_discarded_rounds(
    mode, cause, monkeypatch
):
    batches, executions, blocked = finalization_plan(cause)
    harness = Harness(mode, monkeypatch, batches)

    async def cancel_after_intervention(stream):
        if not stream.tools_enabled:
            raise asyncio.CancelledError()
        stream.callback(
            stream.tools or [], TokenUsage().merge(TokenUsage(input_tokens=1))
        )
        yield (
            f"assistant-{stream.round_number}",
            None,
            (f"thinking-{stream.round_number}", "test-signature"),
        )

    monkeypatch.setattr(ModelStream, "__aiter__", cancel_after_intervention)
    if mode == "chat":
        await harness.run()
        assert harness.session.status == "canceled"
    else:
        with pytest.raises(asyncio.CancelledError):
            await harness.run()
    assert sum(m["role"] == "assistant" for m in harness.history) == executions
    discarded = [3, 4] if cause == "repeat" else [TEST_MAX_TOOL_ROUNDS + 1]
    assert all(harness.streams[number - 1].format_calls == 0 for number in discarded)
    assert harness.requests == len(batches)
    assert harness.tool_policies[-1] is False
    harness.assert_blocked_has_no_side_effects(blocked)
    harness.assert_paired()


@pytest.mark.parametrize("cause", ["repeat", "budget"])
def test_acknowledgement_precedes_new_intervention_and_preserves_one_shot(cause):
    guard = ToolLoopGuard(max_tool_rounds=3 if cause == "budget" else 12)
    history = [{"role": "user", "content": [{"type": "text", "text": "original"}]}]
    guard.acknowledge_response()
    for _ in range(3):
        guard.check_round([call()])
    assert guard.start_request() is True
    assert guard.request_history(history)[-1]["content"][0]["text"] == REPEAT_WARNING
    guard.acknowledge_response()
    assert guard.request_history(history) is history
    decision = guard.check_round([call() if cause == "repeat" else call("changed")])
    assert decision.finalization_cause == cause
    assert guard.start_request() is False
    pending = guard.request_history(history)
    assert is_tool_loop_finalization(pending[-1])
    assert guard.request_history(history) == pending
    guard.acknowledge_response()
    guard.validate_response("Finished", [])
    assert guard.request_history(history) is history
    with pytest.raises(ToolLoopError, match="already attempted"):
        guard.start_request()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "a2a", "chat"])
@pytest.mark.parametrize("phase", ["construction", "stream"])
@pytest.mark.parametrize("outcome", ["changed", "finished", "repeat"])
async def test_recovery_overlay_survives_retryable_api_attempt(
    mode, phase, outcome, monkeypatch
):
    import httpx
    from openai import BadRequestError

    from AgentCrew.modules.llm.model_registry import ModelRegistry

    guard = ToolLoopGuard(max_tool_rounds=TEST_MAX_TOOL_ROUNDS)
    tail = (
        [None]
        if outcome == "finished"
        else [[call("changed") if outcome == "changed" else call()], None]
    )
    harness = Harness(mode, monkeypatch, [[call()]] * 3 + tail, guard=guard)
    harness.agent.get_model = MagicMock(return_value="stub/test")
    monkeypatch.setattr(ModelRegistry, "get_model_limit", lambda model: 128)
    error = BadRequestError(
        "context window exceeded",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://stub.invalid")
        ),
        body={"code": "context_length_exceeded"},
    )
    process_messages = harness.agent.process_messages
    original_iter = ModelStream.__aiter__
    attempt_histories, policies, failed_streams, closed = [], [], [], []

    async def iterate(stream):
        if stream in failed_streams:
            yield ("failed-attempt-content", "failed-attempt-content", None)
            raise error
        async for item in original_iter(stream):
            yield item

    async def close(stream):
        closed.append(stream)

    monkeypatch.setattr(ModelStream, "__aiter__", iterate)
    monkeypatch.setattr(ModelStream, "aclose", close)

    def attempt(messages=None, callback=None, *, tools_enabled=True):
        policies.append(tools_enabled)
        attempt_histories.append(copy.deepcopy(messages))
        if len(policies) in (4, 5):
            assert guard.tools_enabled and guard.finalization_cause is None
            assert harness.llm.execute_tool.await_count == 2
        if len(policies) == 4:
            assert tools_enabled is True
            if phase == "construction":
                raise error
            stream = ModelStream(harness.agent, callback, [])
            stream.tools_enabled = tools_enabled
            failed_streams.append(stream)
            return stream
        return process_messages(messages, callback, tools_enabled=tools_enabled)

    harness.agent.process_messages = attempt
    result = await harness.run()
    assert len(policies) == len(harness.batches) + 1
    assert policies == (
        [True] * 5 + [False] if outcome == "repeat" else [True] * len(policies)
    )
    warning_attempts = attempt_histories[3:5]
    assert warning_attempts[0] == warning_attempts[1]
    assert warning_attempts[0] is not warning_attempts[1]
    for history in warning_attempts:
        assert is_tool_loop_recovery(history[-1])
        assert not is_tool_loop_finalization(history[-1])
        assert history[-1]["content"][0]["text"] == REPEAT_WARNING
    assert guard.request_history(harness.history) is harness.history
    assert harness.agent.input_tokens_usage == 128
    assert harness.streams[2].format_calls == 0
    executions = 3 if outcome == "changed" else 2
    assert harness.llm.execute_tool.await_count == executions
    assert harness.agent.validate_tool_use.call_count == executions
    assert harness.hooks.run_before.await_count == executions
    assert harness.approval is None or harness.approval.await_count == executions
    assert sum(m["role"] == "assistant" for m in harness.history) == executions + 1
    assert harness.history[-1]["content"][0]["text"] == "Finished"
    assert all(stream.format_calls == 0 for stream in failed_streams)
    assert all(
        block.get("text") != "failed-attempt-content"
        for message in harness.history
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    harness.assert_blocked_has_no_side_effects(
        {"r3-0", "r4-0"} if outcome == "repeat" else {"r3-0"}
    )
    harness.assert_paired()
    assert harness.agent.extract_last_user_message_for_memory(harness.history) == "work"
    if outcome == "repeat":
        assert guard.finalization_cause == "repeat"
        assert is_tool_loop_finalization(attempt_histories[-1][-1])
        assert harness.streams[3].format_calls == 0
        assert policies.count(False) == 1
    else:
        assert guard.tools_enabled and guard.finalization_cause is None
        assert not any(
            is_tool_loop_finalization(m)
            for history in attempt_histories
            for m in history
        )
    if mode == "acp":
        assert harness.state.token_usage.total_input_tokens == len(harness.batches)
    else:
        assert result[1].total_input_tokens == len(harness.batches)
    if mode in {"acp", "a2a"}:
        assert all(stream in closed for stream in harness.streams + failed_streams)
    if mode == "a2a":
        from a2a.types.a2a_pb2 import TaskArtifactUpdateEvent

        assert [
            args.args[1] for args in harness.store.append_history.await_args_list
        ] == harness.history[1:]
        tool_artifacts = [
            args.args[0].artifact
            for args in harness.queue.enqueue_event.await_args_list
            if isinstance(args.args[0], TaskArtifactUpdateEvent)
            and args.args[0].artifact.artifact_id.startswith("tool_")
        ]
        assert len(tool_artifacts) == executions
        assert not any(
            "_TurnChunkBuffer.flush_loop" in task.get_coro().__qualname__
            for task in asyncio.all_tasks()
            if not task.done()
        )
