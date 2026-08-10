"""Cross-mode production-path regression tests — single validation per mode.

Covers:
- LocalAgent.validate_tool_use() (shared contract)
- _safe_execute() (parallel executor — no ToolInputValidationError)
- run_agent_loop() (Job mode — validates before dispatch)
- AgentCrewA2AExecutor._execute_single_tool() (A2A v2 — validates all tools)
- AgentCrewA2AExecutor._flush_parallel() (A2A v2 — validates parallel tools)
- TurnExecutor.execute_tools() (ACP — single gate before start/permission)

Each mode must validate every generated call exactly once, before
confirmation / input-required / permission / start-events / hooks / handler.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AgentCrew.modules.tools.parallel_executor import _safe_execute

pytestmark = pytest.mark.asyncio

# ============================================================================
# Test schemas
# ============================================================================

RUN_COMMAND_DEF = {
    "type": "function",
    "function": {
        "name": "run_command",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "working_dir": {"type": "string", "minLength": 1},
            },
            "required": ["command", "working_dir"],
        },
    },
}

ASK_DEF = {
    "type": "function",
    "function": {
        "name": "ask",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "guided_answers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        },
                        "required": ["question", "guided_answers"],
                    },
                    "minItems": 1,
                },
            },
            "required": ["questions"],
        },
    },
}

TOOL_DEFS: dict[str, Any] = {
    "run_command": RUN_COMMAND_DEF,
    "ask": ASK_DEF,
}


# ============================================================================
# Helpers
# ============================================================================


def _make_tool_use(
    name: str,
    input_: dict[str, Any] | None = None,
    tool_id: str = "call_001",
) -> dict[str, Any]:
    return {"id": tool_id, "name": name, "input": input_ or {}, "type": "tool_call"}


def _make_agent(
    tool_defs: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock agent with real validate_tool_use()."""
    from AgentCrew.modules.tools.input_validation import (
        extract_tool_input_schema,
        format_unknown_tool_error_text,
        format_validation_error_text,
        validate_tool_input,
    )

    td = tool_defs or TOOL_DEFS
    agent = MagicMock()
    agent.name = "test_agent"

    def get_tool_definition(name: str) -> dict[str, Any] | None:
        return td.get(name)

    def validate_tool_use(tool_use: dict[str, Any]) -> str | None:
        tool_name = tool_use.get("name", "")
        tool_def = get_tool_definition(tool_name)
        if tool_def is None:
            return format_unknown_tool_error_text(tool_name)
        schema = extract_tool_input_schema(tool_def)
        inp = tool_use.get("input", {})
        r = validate_tool_input(inp, schema)
        if r.valid:
            return None
        return format_validation_error_text(tool_name, r.issues)

    agent.get_tool_definition = get_tool_definition
    agent.validate_tool_use = validate_tool_use
    agent.format_message = MagicMock(
        side_effect=lambda mtype, data: {
            "role": "tool" if mtype == "ToolResult" else "assistant",
            "tool_name": data.get("tool_use", {}).get("name", ""),
            "content": data.get("tool_result") or data.get("message", ""),
            "is_error": data.get("is_error", False),
            "is_rejected": data.get("is_rejected", False),
        }
    )
    agent.execute_tool_call = AsyncMock(return_value="ok")
    agent.store_memory_if_available = MagicMock()
    agent._extract_last_user_message_for_memory = MagicMock(return_value="")
    return agent


# ============================================================================
# 1. Shared unit: validate_tool_use
# ============================================================================


class TestValidateToolUse:
    """Real ``validate_tool_use()`` — the single shared validation contract."""

    def test_valid_returns_none(self):
        agent = _make_agent()
        assert (
            agent.validate_tool_use(
                _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
            )
            is None
        )

    def test_invalid_returns_error_string(self):
        agent = _make_agent()
        err = agent.validate_tool_use(_make_tool_use("run_command", {}))
        assert err is not None
        assert "run_command" in err
        assert "command" in err
        assert "working_dir" in err

    def test_unknown_tool_mentions_not_registered(self):
        agent = _make_agent()
        err = agent.validate_tool_use(_make_tool_use("phantom", {}))
        assert err is not None
        assert "not registered" in err.lower()


# ============================================================================
# 2. Shared unit: _safe_execute (no ToolInputValidationError)
# ============================================================================


class TestSafeExecute:
    """_safe_execute handles CancelOperation and generic exceptions."""

    async def test_canceloperation_maps_correctly(self):
        from AgentCrew.modules.events.hooks import CancelOperation

        async def ex(tu):
            raise CancelOperation("cancelled")

        r = await _safe_execute(ex, _make_tool_use("run_command", {}))
        assert r.is_error is True
        assert r.is_rejected is True
        assert r.was_executed is False

    async def test_other_exceptions_return_error(self):
        async def ex(tu):
            raise RuntimeError("boom")

        r = await _safe_execute(ex, _make_tool_use("run_command", {}))
        assert r.is_error is True
        assert r.was_executed is True  # default

    async def test_success_returns_clean_result(self):
        async def ex(tu):
            return "success"

        r = await _safe_execute(ex, _make_tool_use("run_command", {}))
        assert r.is_error is False
        assert r.result == "success"


# ============================================================================
# 3. Exactly-once validation: execute_tool_call does NOT validate
# ============================================================================


class TestExecuteToolCallNoValidation:
    """``execute_tool_call()`` no longer validates — mode gates do."""

    async def test_execute_tool_call_receives_invalid_input(self):
        """execute_tool_call must NOT validate — passes invalid input through."""
        agent = _make_agent()
        invalid_use = _make_tool_use("run_command", {})

        # execute_tool_call must call the handler even for invalid input
        result = await agent.execute_tool_call(invalid_use)
        # Default mock returns "ok" — validation must not reject it
        assert result == "ok"
        assert agent.execute_tool_call.called


# ============================================================================
# 4. Exactly-once validation with call counters
# ============================================================================


class TestExactlyOnceValidation:
    """Each mode calls validate_tool_use exactly once per generated call."""

    async def test_run_agent_loop_sequential_validates_once(self):
        """run_agent_loop validates each sequential tool exactly once."""
        from AgentCrew.modules.agents.agent_runner import run_agent_loop
        from AgentCrew.modules.llm.token_usage import TokenUsage

        agent = _make_agent()
        validate_count = 0
        original_validate = agent.validate_tool_use

        def counting_validate(tu):
            nonlocal validate_count
            validate_count += 1
            return original_validate(tu)

        agent.validate_tool_use = counting_validate

        call_count = 0

        async def stream(messages, callback=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                callback(
                    [
                        _make_tool_use(
                            "run_command", {"command": "ls", "working_dir": "."}
                        )
                    ],
                    TokenUsage(),
                )
                yield ("resp", None, None)
            else:
                yield ("done", None, None)

        agent.process_messages = stream
        history: list[dict[str, Any]] = []
        await run_agent_loop(agent, history)
        assert validate_count == 1, "sequential tool validated once"

    async def test_acp_validates_once(self):
        """ACP validates each tool exactly once in execute_tools()."""
        from AgentCrew.modules.acp.turn_executor import TurnExecutor

        agent = _make_agent()
        validate_count = 0
        original_validate = agent.validate_tool_use

        def counting_validate(tu):
            nonlocal validate_count
            validate_count += 1
            return original_validate(tu)

        agent.validate_tool_use = counting_validate

        client_comm = MagicMock()
        client_comm.send_tool_started = AsyncMock()
        client_comm.send_tool_completed = AsyncMock()
        tool_manager = MagicMock()
        executor = TurnExecutor(client_comm, tool_manager)

        state = MagicMock()
        state.cancelled = False
        state.permission_broker = None
        state.history = []
        state.pending_ask_tool = None

        await executor.execute_tools(
            "s1",
            state,
            agent,
            [_make_tool_use("run_command", {"command": "ls", "working_dir": "."})],
        )
        assert validate_count == 1, "ACP validated once"

    async def test_a2a_executor_validates_once(self):
        """AgentCrewA2AExecutor validates each tool once in _execute_single_tool."""
        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor

        agent = _make_agent()
        validate_count = 0
        original_validate = agent.validate_tool_use

        def counting_validate(tu):
            nonlocal validate_count
            validate_count += 1
            return original_validate(tu)

        agent.validate_tool_use = counting_validate

        session_store = MagicMock()
        session_store.append_history = AsyncMock()
        exec_ = AgentCrewA2AExecutor(agent, session_store)
        exec_._get_cancel_event = AsyncMock(
            return_value=MagicMock(is_set=MagicMock(return_value=False))
        )

        await exec_._execute_single_tool(
            agent,
            "t1",
            "c1",
            _make_tool_use("run_command", {"command": "ls", "working_dir": "."}),
            [],
            MagicMock(),
            "default",
        )
        assert validate_count == 1, "A2A Executor validated once"


# ============================================================================
# 5. Job: run_agent_loop
# ============================================================================


class TestRunAgentLoopValidation:
    """Directly invokes ``run_agent_loop()`` with a controlled agent."""

    async def _make_stream(self, first_tool_uses, second_response="done"):
        from AgentCrew.modules.llm.token_usage import TokenUsage

        agent = _make_agent()
        call_count = 0

        async def stream(messages, callback=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                callback(first_tool_uses, TokenUsage())
                yield ("resp", None, None)
            else:
                yield (second_response, None, None)

        agent.process_messages = stream
        return agent

    async def test_sequential_invalid_becomes_error_result(self):
        """Invalid sequential → error tool result, execution skipped."""
        from AgentCrew.modules.agents.agent_runner import run_agent_loop

        agent = await self._make_stream(
            [_make_tool_use("run_command", {})],  # invalid
        )
        exec_called = False

        async def execute(use):
            nonlocal exec_called
            exec_called = True
            return "ok"

        agent.execute_tool_call = execute
        history: list[dict[str, Any]] = []
        resp, _ = await run_agent_loop(agent, history)
        assert resp == "done"
        assert not exec_called, "handler must not be called for invalid input"
        errors = [m for m in history if m.get("is_error")]
        assert len(errors) >= 1
        assert "command" in errors[0]["content"]

    async def test_parallel_invalid_becomes_error_result(self):
        """Invalid parallel → error tool result, execution skipped."""
        from AgentCrew.modules.agents.agent_runner import run_agent_loop

        agent = await self._make_stream(
            [_make_tool_use("run_command", {})],  # parallel tool (run_command)
        )
        exec_called = False

        async def execute(use):
            nonlocal exec_called
            exec_called = True
            return "ok"

        agent.execute_tool_call = execute
        history: list[dict[str, Any]] = []
        resp, _ = await run_agent_loop(agent, history)
        assert resp == "done"
        assert not exec_called
        errors = [m for m in history if m.get("is_error")]
        assert len(errors) >= 1

    async def test_sequential_valid_proceeds(self):
        """Valid sequential → executes normally."""
        from AgentCrew.modules.agents.agent_runner import run_agent_loop

        agent = await self._make_stream(
            [_make_tool_use("run_command", {"command": "ls", "working_dir": "."})],
        )
        exec_called = False

        async def execute(use):
            nonlocal exec_called
            exec_called = True
            return "ok"

        agent.execute_tool_call = execute
        history: list[dict[str, Any]] = []
        await run_agent_loop(agent, history)
        assert exec_called

    async def test_mixed_batch_ordered_correctly(self):
        """[valid, invalid] → valid executed, invalid error, ordered results."""
        from AgentCrew.modules.agents.agent_runner import run_agent_loop
        from AgentCrew.modules.llm.token_usage import TokenUsage

        agent = _make_agent()
        call_count = 0

        async def stream(messages, callback=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                callback(
                    [
                        _make_tool_use(
                            "run_command",
                            {"command": "a", "working_dir": "."},
                            tool_id="valid_1",
                        ),
                        _make_tool_use("run_command", {}, tool_id="invalid_1"),
                    ],
                    TokenUsage(),
                )
                yield ("first", None, None)
            else:
                yield ("done", None, None)

        agent.process_messages = stream
        executed: list[str] = []

        async def execute(use):
            executed.append(use["id"])
            return "ok"

        agent.execute_tool_call = execute
        history: list[dict[str, Any]] = []
        await run_agent_loop(agent, history)
        assert "valid_1" in executed
        assert "invalid_1" not in executed
        errors = [m for m in history if m.get("is_error")]
        assert len(errors) >= 1


# ============================================================================
# 6. A2A AgentCrewA2AExecutor
# ============================================================================


class TestA2AExecutor:
    """Invokes real ``_execute_single_tool()`` — validates ALL tools once."""

    @pytest.fixture
    def executor(self):
        from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor

        agent = _make_agent()
        store = MagicMock()
        store.append_history = AsyncMock()
        ex = AgentCrewA2AExecutor(agent, store)
        ex._get_cancel_event = AsyncMock(
            return_value=MagicMock(is_set=MagicMock(return_value=False))
        )
        return ex

    async def test_invalid_non_ask_rejected_by_validate(self, executor):
        """Invalid non-ask → validate_tool_use rejects, no handler."""
        agent = executor.agent
        tool_use = _make_tool_use("run_command", {})

        from AgentCrew.modules.a2a.agent_executor import ToolCallResult

        result = await executor._execute_single_tool(
            agent, "t1", "c1", tool_use, [], MagicMock(), "default"
        )
        assert result == ToolCallResult.CONTINUE
        assert not agent.execute_tool_call.called
        assert executor.session_store.append_history.called
        msg = executor.session_store.append_history.call_args[0][1]
        assert msg["is_error"] is True

    async def test_invalid_ask_returns_continue_no_pending(self, executor):
        """Invalid ask → CONTINUE, no pending saved."""
        agent = executor.agent
        tool_use = _make_tool_use("ask", {})

        from AgentCrew.modules.a2a.agent_executor import ToolCallResult

        with patch.object(executor, "_handle_ask_tool", AsyncMock()) as mock_handle:
            result = await executor._execute_single_tool(
                agent, "t1", "c1", tool_use, [], MagicMock(), "default"
            )
        assert result == ToolCallResult.CONTINUE
        assert not mock_handle.called
        assert not executor.session_store.save_pending_tools.called

    async def test_valid_ask_calls_handle(self, executor):
        """Valid ask → calls _handle_ask_tool."""
        agent = executor.agent
        tool_use = _make_tool_use(
            "ask", {"questions": [{"question": "Go?", "guided_answers": ["Y", "N"]}]}
        )

        with patch.object(executor, "_handle_ask_tool", AsyncMock()) as mock_handle:
            mock_handle.return_value = "input_required"
            result = await executor._execute_single_tool(
                agent, "t1", "c1", tool_use, [], MagicMock(), "default"
            )
        assert result == "input_required"
        assert mock_handle.called


# ============================================================================
# 7. ACP TurnExecutor
# ============================================================================


class TestAcpTurnExecutor:
    """Invokes real ``execute_tools()`` — single validation gate."""

    @pytest.fixture
    def executor(self):
        from AgentCrew.modules.acp.turn_executor import TurnExecutor

        cc = MagicMock()
        cc.send_tool_started = AsyncMock()
        cc.send_tool_completed = AsyncMock()
        cc.send_ask_request = AsyncMock()
        return TurnExecutor(cc, MagicMock())

    @pytest.fixture
    def state(self):
        s = MagicMock()
        s.cancelled = False
        s.permission_broker = None
        s.history = []
        s.pending_ask_tool = None
        return s

    async def test_invalid_skips_start_permission_handler(self, executor, state):
        """Invalid tool → no tool_started, permission, or handler."""
        agent = _make_agent()
        tool_use = _make_tool_use("run_command", {})
        handler_called = False

        async def execute(use):
            nonlocal handler_called
            handler_called = True
            return "should not reach"

        agent.execute_tool_call = execute
        await executor.execute_tools("s1", state, agent, [tool_use])
        assert not executor._client_comm.send_tool_started.called
        assert not handler_called
        calls = executor._client_comm.send_tool_completed.call_args_list
        assert len(calls) >= 1
        assert calls[0][0][3] is True  # is_error=True

    async def test_invalid_ask_no_ask_request(self, executor, state):
        """Invalid ask → no ask request, no pending state."""
        agent = _make_agent()
        await executor.execute_tools("s1", state, agent, [_make_tool_use("ask", {})])
        assert not executor._client_comm.send_tool_started.called
        assert not executor._client_comm.send_ask_request.called
        assert state.pending_ask_tool is None

    async def test_mixed_batch_result_order(self, executor, state):
        """[valid buffered, invalid] → [valid result, invalid error]."""
        agent = _make_agent()
        valid = _make_tool_use(
            "run_command", {"command": "a", "working_dir": "."}, tool_id="v1"
        )
        invalid = _make_tool_use("run_command", {}, tool_id="i1")
        executed: list[str] = []

        async def execute(use):
            executed.append(use["id"])
            return f"ok:{use['id']}"

        agent.execute_tool_call = execute
        await executor.execute_tools("s1", state, agent, [valid, invalid])
        calls = executor._client_comm.send_tool_completed.call_args_list
        tuples = [(c[0][1]["id"], c[0][3]) for c in calls]
        assert tuples[0] == ("v1", False)
        assert tuples[1] == ("i1", True)

    async def test_valid_tool_preserves_behavior(self, executor, state):
        """Valid tool → start + execution + completion, no error."""
        agent = _make_agent()
        tool_use = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})

        async def execute(use):
            return "ok"

        agent.execute_tool_call = execute
        await executor.execute_tools("s1", state, agent, [tool_use])
        assert executor._client_comm.send_tool_started.called
        calls = executor._client_comm.send_tool_completed.call_args_list
        assert len(calls) >= 1
        assert calls[0][0][3] is not True

    async def test_invalid_ask_after_valid_parallel_ordered(self, executor, state):
        """[valid parallel, invalid ask] → valid executes, invalid ask error."""
        agent = _make_agent()
        valid = _make_tool_use(
            "run_command", {"command": "pwd", "working_dir": "."}, tool_id="v1"
        )
        invalid_ask = _make_tool_use("ask", {}, tool_id="a1")
        executed: list[str] = []

        async def execute(use):
            executed.append(use["id"])
            return f"ok:{use['id']}"

        agent.execute_tool_call = execute
        await executor.execute_tools("s1", state, agent, [valid, invalid_ask])
        assert executed == ["v1"]
        assert not executor._client_comm.send_ask_request.called
        calls = executor._client_comm.send_tool_completed.call_args_list
        tuples = [(c[0][1]["id"], c[0][3]) for c in calls]
        assert tuples[0] == ("v1", False)
        assert tuples[1] == ("a1", True)
