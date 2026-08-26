"""Tests pinning ACP payload construction to ``agent-client-protocol`` 0.12.x models.

Covers:
- representative session response models (new/load/fork/resume/set-config/prompt)
- session mode/config-option models
- ClientCommunication session updates and tool updates
- permission request construction through AcpPermissionBroker
"""

from __future__ import annotations

import asyncio
from typing import cast

from acp import (
    Client,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message_text,
)
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    Cost,
    CurrentModeUpdate,
    DeniedOutcome,
    ForkSessionResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    ResumeSessionResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SessionMode,
    SessionModeState,
    SetSessionConfigOptionResponse,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
)

from AgentCrew.modules.acp.client_communication import ClientCommunication
from AgentCrew.modules.acp.tools.permission_broker import AcpPermissionBroker
from AgentCrew.modules.llm.token_usage import TokenUsage


class TestSessionResponseModels:
    def test_new_session_response(self):
        resp = NewSessionResponse(
            session_id="s1",
            modes=SessionModeState(
                available_modes=[SessionMode(id="a", name="A", description="d")],
                current_mode_id="a",
            ),
            config_options=[
                SessionConfigOptionSelect(
                    id="mode",
                    name="Agent",
                    description="d",
                    category="mode",
                    type="select",
                    current_value="a",
                    options=[SessionConfigSelectOption(value="a", name="A")],
                )
            ],
        )
        dumped = resp.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["sessionId"] == "s1"
        assert dumped["modes"]["currentModeId"] == "a"
        assert dumped["configOptions"][0]["id"] == "mode"

    def test_load_session_response(self):
        resp = LoadSessionResponse(
            modes=SessionModeState(
                available_modes=[SessionMode(id="a", name="A")],
                current_mode_id="a",
            ),
            config_options=[],
        )
        dumped = resp.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["modes"]["currentModeId"] == "a"
        assert dumped["configOptions"] == []

    def test_fork_session_response(self):
        resp = ForkSessionResponse(
            session_id="s2",
            modes=SessionModeState(
                available_modes=[SessionMode(id="a", name="A")],
                current_mode_id="a",
            ),
            config_options=[],
        )
        assert resp.model_dump(mode="json", by_alias=True)["sessionId"] == "s2"

    def test_resume_session_response(self):
        resp = ResumeSessionResponse(modes=None, config_options=[])
        assert resp.model_dump(mode="json", by_alias=True, exclude_none=True) == {
            "configOptions": []
        }

    def test_set_config_option_response(self):
        resp = SetSessionConfigOptionResponse(config_options=[])
        assert resp.model_dump(mode="json", by_alias=True)["configOptions"] == []

    def test_prompt_response_stop_reasons(self):
        for reason in ("end_turn", "cancelled", "refusal"):
            resp = PromptResponse(stop_reason=reason)
            assert resp.model_dump(mode="json", by_alias=True, exclude_none=True) == {
                "stopReason": reason
            }


class TestSessionConfigModels:
    def test_session_mode_state(self):
        state = SessionModeState(
            available_modes=[SessionMode(id="a", name="A", description="d")],
            current_mode_id="a",
        )
        dumped = state.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["availableModes"][0]["id"] == "a"
        assert dumped["currentModeId"] == "a"

    def test_session_config_option_select(self):
        opt = SessionConfigOptionSelect(
            id="model",
            name="Model",
            description="d",
            category="model",
            type="select",
            current_value="m1",
            options=[SessionConfigSelectOption(value="m1", name="Model 1")],
        )
        dumped = opt.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["currentValue"] == "m1"
        assert dumped["options"][0]["value"] == "m1"


class _RecordingConn:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))


def _comm_with(conn):
    comm = ClientCommunication()
    comm.conn = conn
    return comm


class TestClientCommunication:
    def test_send_agent_message(self):
        conn = _RecordingConn()
        asyncio.run(_comm_with(conn).send_agent_message("s1", "hello"))
        sid, update = conn.updates[0]
        assert sid == "s1"
        assert isinstance(update, AgentMessageChunk)
        assert update.content.type == "text"
        assert update.content.text == "hello"

    def test_send_thought_chunk(self):
        conn = _RecordingConn()
        asyncio.run(_comm_with(conn).send_thought_chunk("s1", "thinking..."))
        sid, update = conn.updates[0]
        assert sid == "s1"
        assert isinstance(update, AgentThoughtChunk)
        assert update.content.text == "thinking..."

    def test_send_empty_thought_chunk_is_skipped(self):
        conn = _RecordingConn()
        asyncio.run(_comm_with(conn).send_thought_chunk("s1", "  "))
        assert conn.updates == []

    def test_send_current_mode_update(self):
        conn = _RecordingConn()
        asyncio.run(
            _comm_with(conn).send_current_mode_update(
                "s1", type("S", (), {"agent_name": "a"})()
            )
        )
        _, update = conn.updates[0]
        assert isinstance(update, CurrentModeUpdate)
        assert update.current_mode_id == "a"

    def test_send_session_info_update(self):
        conn = _RecordingConn()
        asyncio.run(
            _comm_with(conn).send_session_info_update(
                "s1", type("S", (), {"title": "T"})()
            )
        )
        _, update = conn.updates[0]
        assert isinstance(update, SessionInfoUpdate)
        assert update.title == "T"

    def test_send_tool_started(self):
        conn = _RecordingConn()
        asyncio.run(
            _comm_with(conn).send_tool_started(
                "s1", {"id": "t1", "name": "read_file", "input": {"path": "/x"}}
            )
        )
        _, update = conn.updates[0]
        assert isinstance(update, ToolCallStart)
        assert update.tool_call_id == "t1"
        assert update.status == "in_progress"
        assert update.kind == "read"

    def test_send_tool_completed(self):
        conn = _RecordingConn()
        asyncio.run(
            _comm_with(conn).send_tool_completed(
                "s1", {"id": "t1", "name": "read_file"}, "result", False
            )
        )
        _, update = conn.updates[0]
        assert isinstance(update, ToolCallProgress)
        assert update.tool_call_id == "t1"
        assert update.status == "completed"
        dumped = update.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["content"][0]["content"]["text"] == "result"

    def test_send_usage_update(self):
        conn = _RecordingConn()
        usage = TokenUsage(input_tokens=10, output_tokens=5, cached_tokens=2)
        asyncio.run(_comm_with(conn).send_usage_update("s1", usage, 0.01, 1000, 15))
        _, update = conn.updates[0]
        assert isinstance(update, UsageUpdate)
        assert isinstance(update.cost, Cost)
        assert update.size == 1000
        assert update.used == 15


class TestPermissionBroker:
    def test_permission_request_construction(self):
        class _PermissionConn:
            def __init__(self):
                self.calls = []

            async def request_permission(
                self, session_id, tool_call, options, **kwargs
            ):
                self.calls.append((session_id, tool_call, options))
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(option_id="allow_once", outcome="selected")
                )

        mock_conn = _PermissionConn()
        broker = AcpPermissionBroker(conn=cast(Client, mock_conn), session_id="s1")
        outcome = asyncio.run(
            broker.request_permission(
                {"name": "acp_write_file", "id": "t1", "input": {"file_path": "/tmp/x"}}
            )
        )
        assert outcome == "allow_once"
        sid, tool_call, options = mock_conn.calls[0]
        assert sid == "s1"
        assert isinstance(tool_call, ToolCallUpdate)
        assert tool_call.tool_call_id == "t1"
        assert tool_call.kind == "edit"
        assert all(isinstance(o, PermissionOption) for o in options)
        assert [o.option_id for o in options] == [
            "allow_once",
            "allow_always",
            "reject_once",
        ]

    def test_permission_reject_outcome(self):
        class _RejectConn:
            async def request_permission(
                self, session_id, tool_call, options, **kwargs
            ):
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled")
                )

        broker = AcpPermissionBroker(conn=cast(Client, _RejectConn()), session_id="s1")
        outcome = asyncio.run(
            broker.request_permission({"name": "run_command", "id": "t2", "input": {}})
        )
        assert outcome == "reject"

    def test_non_sensitive_tool_does_not_call_client(self):
        class _NeverConn:
            async def request_permission(
                self, session_id, tool_call, options, **kwargs
            ):
                raise AssertionError("should not be called")

        broker = AcpPermissionBroker(conn=cast(Client, _NeverConn()), session_id="s1")
        outcome = asyncio.run(
            broker.request_permission({"name": "grep_text", "id": "t3", "input": {}})
        )
        assert outcome == "allow_once"


def test_update_agent_message_text_returns_valid_chunk():
    update = update_agent_message_text("hi")
    assert isinstance(update, AgentMessageChunk)
    assert update.model_dump(mode="json", by_alias=True)["content"]["text"] == "hi"


def test_start_tool_call_and_tool_content_compose():
    tc = start_tool_call(
        "t1", "Title", kind="execute", status="in_progress", raw_input={"command": "ls"}
    )
    assert isinstance(tc, ToolCallStart)
    content = tool_content(text_block("out"))
    assert content.content.text == "out"
    assert isinstance(
        UserMessageChunk(content=text_block("u"), session_update="user_message_chunk"),
        UserMessageChunk,
    )
