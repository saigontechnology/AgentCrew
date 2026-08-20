"""Focused tests for the AgentMessageFormatter collaborator.

Pins the exact message structures produced by ``LocalAgent`` message
formatting after extraction into ``AgentMessageFormatter``:

- assistant messages (text, thinking block, tool calls)
- tool results (error/rejected prefixes)
- ``MessageType`` routing (Assistant / ToolResult / FileContent / unknown)
- invalid tool-use filtering (valid retained, malformed dropped + warning)
- LocalAgent delegation boundary (wrappers route to the collaborator)
"""

from __future__ import annotations

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.message_formatter import AgentMessageFormatter


class _StubFileLLM:
    """Stub with only the formatting-relevant surface."""

    def __init__(self):
        self.processed = []

    def process_file_for_message(self, file_uri):
        self.processed.append(file_uri)
        return {"role": "user", "content": f"FILE:{file_uri}"}


def _make_agent(llm=None, name="formatter-agent"):
    return LocalAgent(
        name=name,
        description="desc",
        llm_service=llm,
        services={},
        tools=[],
    )


# ---------------------------------------------------------------------------
# assistant formatting
# ---------------------------------------------------------------------------


class TestFormatAssistantMessage:
    def test_plain_text(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_assistant_message("hello world")
        assert msg == {
            "agent": "formatter-agent",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello world"}],
        }

    def test_thinking_block_with_signature(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_assistant_message(
            "answer", ("think text", "sig123")
        )
        assert msg["content"] == [
            {"type": "thinking", "thinking": "think text", "signature": "sig123"},
            {"type": "text", "text": "answer"},
        ]

    def test_thinking_block_without_signature(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_assistant_message(
            "answer", ("think text", "")
        )
        assert msg["content"] == [
            {"type": "thinking", "thinking": "think text"},
            {"type": "text", "text": "answer"},
        ]

    def test_tool_calls_mapped(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_assistant_message(
            "answer",
            tool_uses=[
                {"id": "call_1", "name": "read_file", "input": {"path": "a.py"}},
                {"id": "call_2", "name": "write_file", "input": {}, "type": "custom"},
            ],
        )
        assert msg["tool_calls"] == [
            {
                "id": "call_1",
                "name": "read_file",
                "arguments": {"path": "a.py"},
                "type": "tool_call",
            },
            {"id": "call_2", "name": "write_file", "arguments": {}, "type": "custom"},
        ]

    def test_invalid_tool_uses_filtered(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_assistant_message(
            "answer", tool_uses=[{"id": "x", "name": "ok", "input": {}}]
        )
        assert msg["tool_calls"] == [
            {"id": "x", "name": "ok", "arguments": {}, "type": "tool_call"}
        ]


# ---------------------------------------------------------------------------
# tool result formatting
# ---------------------------------------------------------------------------


class TestFormatToolResult:
    def test_success(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_tool_result(
            {"id": "c1", "name": "read_file"}, "content here"
        )
        assert msg == {
            "role": "tool",
            "agent": "formatter-agent",
            "tool_call_id": "c1",
            "tool_name": "read_file",
            "content": "content here",
        }

    def test_error_prefix(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_tool_result(
            {"id": "c1", "name": "run_command"}, {"stderr": "boom"}, is_error=True
        )
        assert msg["content"] == "ERROR: {'stderr': 'boom'}"

    def test_rejected_prefix_and_flag(self):
        agent = _make_agent()
        msg = agent._message_formatter.format_tool_result(
            {"id": "c1", "name": "write_file"}, "denied by user", is_rejected=True
        )
        assert msg["content"] == "DENIED: denied by user"
        assert msg["is_rejected"] is True


# ---------------------------------------------------------------------------
# MessageType routing
# ---------------------------------------------------------------------------


class TestFormatMessageRouting:
    def test_assistant_routing(self):
        agent = _make_agent()
        msg = agent.format_message(
            MessageType.Assistant, {"message": "hi", "thinking": ("t", "s")}
        )
        assert msg["role"] == "assistant"
        assert msg["content"][0] == {
            "type": "thinking",
            "thinking": "t",
            "signature": "s",
        }

    def test_tool_result_routing(self):
        agent = _make_agent()
        msg = agent.format_message(
            MessageType.ToolResult,
            {
                "tool_use": {"id": "c", "name": "ask"},
                "tool_result": "42",
                "is_error": True,
            },
        )
        assert msg["content"] == "ERROR: 42"

    def test_file_content_routing_with_llm(self):
        llm = _StubFileLLM()
        agent = _make_agent(llm)
        msg = agent.format_message(MessageType.FileContent, {"file_uri": "/tmp/a.txt"})
        assert msg == {"role": "user", "content": "FILE:/tmp/a.txt"}
        assert llm.processed == ["/tmp/a.txt"]

    def test_file_content_routing_without_llm(self):
        agent = _make_agent(None)
        msg = agent.format_message(MessageType.FileContent, {"file_uri": "/tmp/a.txt"})
        assert msg == {"file_uri": "/tmp/a.txt"}

    def test_unknown_type_returns_none(self):
        agent = _make_agent()
        assert agent.format_message(object(), {}) is None


# ---------------------------------------------------------------------------
# invalid tool-use filtering
# ---------------------------------------------------------------------------


class TestFilterInvalidToolUses:
    def test_valid_names_retained(self):
        agent = _make_agent()
        tool_uses = [{"name": "read_file"}, {"name": "  write_file  "}, {"name": ""}]
        result = agent._message_formatter.filter_invalid_tool_uses(tool_uses)
        assert result == [{"name": "read_file"}, {"name": "  write_file  "}]

    def test_unusable_unnamed_calls_dropped(self, monkeypatch):
        from AgentCrew.modules.agents import message_formatter as mf_module

        warnings = []
        monkeypatch.setattr(
            mf_module.logger, "warning", lambda msg: warnings.append(msg)
        )
        agent = _make_agent()
        tool_uses = [{"id": "c1", "args_json": "{}"}]
        result = agent._message_formatter.filter_invalid_tool_uses(tool_uses)
        assert result == []
        assert warnings and "Dropping malformed parsed tool call" in warnings[0]

    def test_plain_unusable_dropped_without_warning(self, monkeypatch):
        from AgentCrew.modules.agents import message_formatter as mf_module

        warnings = []
        monkeypatch.setattr(
            mf_module.logger, "warning", lambda msg: warnings.append(msg)
        )
        agent = _make_agent()
        result = agent._message_formatter.filter_invalid_tool_uses([{"foo": "bar"}])
        assert result == []
        assert warnings == []


# ---------------------------------------------------------------------------
# delegation boundary
# ---------------------------------------------------------------------------


class TestDelegationBoundary:
    def test_collaborator_wired_in_init(self):
        agent = _make_agent()
        assert isinstance(agent._message_formatter, AgentMessageFormatter)
        assert agent._message_formatter.agent is agent

    def test_wrappers_delegate(self):
        agent = _make_agent()
        tool_use = {"id": "c", "name": "ask"}
        # format_message remains the production-facing wrapper (BaseAgent API).
        msg = agent.format_message(
            MessageType.ToolResult, {"tool_use": tool_use, "tool_result": "x"}
        )
        assert msg == agent._message_formatter.format_tool_result(tool_use, "x")

    def test_collaborator_owns_no_state(self):
        agent = _make_agent()
        coord = agent._message_formatter
        assert not hasattr(coord, "messages")
        assert not hasattr(coord, "name")
        assert not hasattr(coord, "llm")

    def test_removed_wrappers_absent(self):
        agent = _make_agent()
        for name in (
            "_format_tool_result",
            "_format_assistant_message",
            "_filter_invalid_tool_uses",
        ):
            assert not hasattr(agent, name)


# ---------------------------------------------------------------------------
# pure collaborator behavior (no agent needed for formatting core)
# ---------------------------------------------------------------------------


class TestCollaboratorDirect:
    def test_formats_without_touching_agent_state(self):
        agent = _make_agent()
        coord = agent._message_formatter
        msg = coord.format_assistant_message("direct", thinking_data=("t", "s"))
        assert msg["content"][0]["type"] == "thinking"
        assert msg["content"][-1]["text"] == "direct"
