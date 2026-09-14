"""Focused tests for reasoning persistence on chat cancellation paths.

Covers:

- the explicit ``session.cancel_requested`` branch persists accumulated
  thinking (not the raw latest chunk tuple) plus ``_metadata``
- the ``asyncio.CancelledError`` path persists accumulated thinking plus
  ``_metadata``
- the generic exception path emits a debug-redacted ``AppEvents.ERROR``
  payload while the persisted history keeps the exact encrypted value
- ``/debug`` (both agent and chat) emits redacted messages while the
  original histories retain the exact encrypted value
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from AgentCrew.modules.agents.agent_response_stream import AgentResponseStream
from AgentCrew.modules.agents.message_metadata import (
    build_responses_metadata,
    get_responses_provider_state,
)
from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.chat.stream_session import StreamSession
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.llm.token_usage import TokenUsage

ENCRYPTED = "gAAAAABcancel-encrypted-reasoning-blob=="


def _reasoning_item_dict(item_id: str = "rs_cancel") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "step one"}],
        "status": "completed",
        "encrypted_content": ENCRYPTED,
    }


class _StubAgent:
    """Minimal agent with scripted process_messages and real formatting."""

    def __init__(
        self,
        chunks,
        history: list[dict[str, Any]],
        tool_uses: list[dict[str, Any]] | None = None,
        token_usage: TokenUsage | None = None,
    ) -> None:
        self.history = history
        self._chunks = chunks
        self._tool_uses = tool_uses or []
        self._token_usage = token_usage or TokenUsage()
        self.name = "stub-agent"

    def create_stream_state(self) -> dict[str, Any]:
        return {
            "reasoning_items": [_reasoning_item_dict()],
            "summary_deltas_received": True,
            "provider": "openai",
            "model": "gpt-5.4",
        }

    def process_messages(self, callback=None) -> AgentResponseStream:
        return AgentResponseStream(
            agent=self,
            state_factory=self.create_stream_state,
            iterator_factory=lambda stream_state: self._stream_impl(callback),
        )

    async def _stream_impl(self, callback=None):
        if isinstance(self._chunks, list):
            for item in self._chunks:
                yield item
        else:
            async for item in self._chunks:
                yield item
        if callback:
            callback(self._tool_uses, self._token_usage)

    def format_message(self, message_type, data) -> dict[str, Any]:
        message = data.get("message", "")
        msg: dict[str, Any] = {
            "role": "assistant",
            "agent": self.name,
            "content": [{"type": "text", "text": message}],
        }
        thinking = data.get("thinking")
        if thinking:
            content, signature = thinking
            block: dict[str, Any] = {"type": "thinking", "thinking": content}
            if signature:
                block["signature"] = signature
            msg["content"].insert(0, block)
        tool_uses = data.get("tool_uses")
        if tool_uses:
            msg["tool_calls"] = [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "arguments": t.get("input", {}),
                }
                for t in tool_uses
            ]
        return msg

    def append_message(self, message: dict[str, Any]) -> None:
        self.history.append(message)

    def is_streaming(self) -> bool:
        return False


def _make_handler(agent: _StubAgent) -> MessageHandler:
    handler = MessageHandler.__new__(MessageHandler)
    handler.agent = agent
    handler.bus = SimpleNamespace(emit=AsyncMock(), emit_sync=MagicMock())
    handler.hooks = SimpleNamespace(run_after=AsyncMock(return_value=None))
    handler.agent_manager = SimpleNamespace(defered_transfer="")
    handler.tool_manager = SimpleNamespace(execute_tools_batch=AsyncMock())
    handler.conversation_manager = SimpleNamespace(store_conversation_turn=MagicMock())
    handler.streamline_messages = []
    handler.stream_generator = None
    handler._stream_session_counter = 0
    handler._active_stream_session = None
    handler._turn_usage_ledger = {}
    handler._turn_usage_committed = {}
    handler.persistent_service = None
    handler.current_conversation_id = None
    handler.current_user_input = None
    handler.current_user_input_idx = -1
    handler.last_assisstant_response_idx = -1
    handler._record_turn_request_usage = MagicMock()
    handler._finalize_current_turn = MagicMock(return_value=[])
    return handler


def _thinking_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part for part in message.get("content", []) if part.get("type") == "thinking"
    ]


@pytest.mark.asyncio
async def test_normal_no_tool_completion_formats_message_with_thinking_and_metadata():
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    session = StreamSession(session_id=1)

    async def gen():
        yield ("", None, ("step ", None))
        yield ("", None, ("one", None))
        yield ("final answer", "final answer", None)

    agent = _StubAgent(gen(), history)
    handler = _make_handler(agent)

    response, _ = await handler._run_stream_response(session)

    assert response == "final answer"
    assert len(history) == 2  # user + exactly one assistant message
    assistant = history[-1]
    assert assistant["role"] == "assistant"
    blocks = _thinking_blocks(assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["provider"] == "openai"
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED
    # external cancellation handle cleared after normal completion
    assert handler.stream_generator is None


@pytest.mark.asyncio
async def test_normal_tool_call_completion_formats_tool_message_before_recursion():
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    session = StreamSession(session_id=1)

    tool_uses = [{"id": "call_1", "name": "read_file", "input": {"path": "x.py"}}]
    agent = _StubAgent(
        [("", "answer text", None)],
        history,
        tool_uses=tool_uses,
        token_usage=TokenUsage(input_tokens=5, output_tokens=3),
    )
    handler = _make_handler(agent)
    handler.get_assistant_response = AsyncMock(
        return_value=("final", TokenUsage(input_tokens=5, output_tokens=3))
    )

    response, _ = await handler._run_stream_response(session)

    assert response == "final"
    assistant = history[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"] == [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "x.py"}}
    ]
    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED
    handler.get_assistant_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_requested_persists_accumulated_thinking_and_metadata():
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    session = StreamSession(session_id=1)

    async def gen():
        yield ("", None, ("step ", None))
        yield ("", None, ("one", None))
        session.cancel_requested = True
        yield ("partial answer", "answer", None)

    agent = _StubAgent(gen(), history)
    handler = _make_handler(agent)

    response, _ = await handler._run_stream_response(session)

    assert response == "partial answer"
    assistant = history[-1]
    assert assistant["role"] == "assistant"
    blocks = _thinking_blocks(assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["provider"] == "openai"
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED


@pytest.mark.asyncio
async def test_cancelled_error_persists_accumulated_thinking_and_metadata():
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    session = StreamSession(session_id=1)

    async def gen():
        yield ("", None, ("step ", None))
        yield ("partial", None, ("one", None))
        raise asyncio.CancelledError()

    agent = _StubAgent(gen(), history)
    handler = _make_handler(agent)

    response, _ = await handler._run_stream_response(session)

    assert response == "partial"
    assistant = history[-1]
    assert assistant["role"] == "assistant"
    blocks = _thinking_blocks(assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED


@pytest.mark.asyncio
async def test_error_event_payload_redacted_history_preserved():
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "prior"}],
            "_metadata": build_responses_metadata(
                "openai", "gpt-5.4", [_reasoning_item_dict("rs_prior")]
            ),
        },
    ]
    session = StreamSession(session_id=1)

    async def gen():
        yield ("", None, None)
        raise RuntimeError("boom")

    agent = _StubAgent(gen(), history)
    handler = _make_handler(agent)

    response, _ = await handler._run_stream_response(session)

    assert response is None
    error_calls = [
        call
        for call in handler.bus.emit.await_args_list
        if call.args and call.args[0] == AppEvents.ERROR
    ]
    assert len(error_calls) == 1
    emitted_messages = error_calls[0].kwargs["messages"]
    assert ENCRYPTED not in json.dumps(emitted_messages)
    # persisted history retains the exact encrypted value
    assert (
        history[1]["_metadata"]["provider_state"]["openai_responses"]["output_items"][
            0
        ]["encrypted_content"]
        == ENCRYPTED
    )


def test_debug_agent_and_chat_payloads_redacted_originals_preserved():
    message: dict[str, Any] = {
        "role": "assistant",
        "content": [{"type": "text", "text": "hi"}],
        "_metadata": build_responses_metadata(
            "openai", "gpt-5.4", [_reasoning_item_dict("rs_debug")]
        ),
    }
    handler = SimpleNamespace(
        bus=SimpleNamespace(emit_sync=MagicMock()),
        agent=SimpleNamespace(clean_history=[copy.deepcopy(message)]),
        streamline_messages=[copy.deepcopy(message)],
    )
    commands = UtilityCommands(handler)  # type: ignore[arg-type]

    commands.handle_debug("/debug")

    debug_calls = [
        call
        for call in handler.bus.emit_sync.call_args_list
        if call.args and call.args[0] == AppEvents.DEBUG_REQUESTED
    ]
    assert len(debug_calls) == 2  # agent + chat
    for call in debug_calls:
        assert ENCRYPTED not in json.dumps(call.kwargs["messages"])
    # originals retain the exact encrypted value
    assert (
        handler.streamline_messages[0]["_metadata"]["provider_state"][
            "openai_responses"
        ]["output_items"][0]["encrypted_content"]
        == ENCRYPTED
    )
    assert (
        handler.agent.clean_history[0]["_metadata"]["provider_state"][
            "openai_responses"
        ]["output_items"][0]["encrypted_content"]
        == ENCRYPTED
    )
