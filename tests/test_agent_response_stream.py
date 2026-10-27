"""Focused tests for the stream-owned ``AgentResponseStream`` lifecycle.

Covers:

- ``process_messages`` creates and owns provider stream state internally;
  callers finalize assistant messages with the exact captured encrypted
  reasoning after iteration
- no-tool, tool-call, and reasoning-summary paths through the new stream API
- ``aclose()`` cancellation, including partial assistant/thinking persistence
  and encrypted metadata when already captured
- two concurrent streams on the same agent do not mix reasoning items
- a no-state stream (representative of remote agents) uses the same interface
  and adds no ``_metadata``
- chat, A2A, ACP, and agent_runner no longer create/thread stream state or
  import Responses metadata helpers
"""

from __future__ import annotations

import pathlib
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from AgentCrew.modules.agents.agent_response_stream import AgentResponseStream
from AgentCrew.modules.agents.context_manager import AgentContextManager
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.message_metadata import (
    METADATA_FIELD,
    get_responses_provider_state,
)
from AgentCrew.modules.anthropic.service import AnthropicService
from AgentCrew.modules.openai.response_service import OpenAIResponseService

ENCRYPTED = "gAAAAABstream-encrypted-reasoning-blob=="


class _StreamContext:
    """Async context manager yielding prepared stream chunks."""

    def __init__(self, chunks: list[SimpleNamespace]):
        self._chunks = list(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _ResponsesStubLLM:
    """Responses-like LLM service: real chunk parsing, stubbed transport."""

    def __init__(self, chunk_lists: list[list[SimpleNamespace]]):
        self._chunk_lists = [list(chunks) for chunks in chunk_lists]
        self._provider_name = "openai"
        self.provider_name = "openai"
        self.model = "gpt-5.4"
        self._responses = OpenAIResponseService.__new__(OpenAIResponseService)
        self._responses._provider_name = self._provider_name
        self._responses.model = self.model
        self._responses.tools = []
        self._responses.structured_output = None
        self._responses.reasoning_effort = "medium"
        self._responses.system_prompt = ""
        self._responses._extra_headers = None

    def get_system_prompt(self) -> str:
        return ""

    def set_system_prompt(self, system_prompt: str) -> None:
        pass

    def create_stream_state(self) -> dict[str, Any]:
        return self._responses.create_stream_state()

    async def stream_assistant_response(self, messages) -> _StreamContext:
        chunks = self._chunk_lists.pop(0) if self._chunk_lists else []
        return _StreamContext(chunks)

    def process_stream_chunk(
        self, chunk, assistant_response, tool_uses, stream_state=None
    ):
        return self._responses.process_stream_chunk(
            chunk, assistant_response, tool_uses, stream_state
        )


def _event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _reasoning_item(item_id: str = "rs_stream") -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        type="reasoning",
        summary=[SimpleNamespace(type="summary_text", text="step one")],
        content=None,
        encrypted_content=ENCRYPTED,
        status="completed",
    )


def _completed_event(input_tokens=10, cached_tokens=0) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        output_tokens_details=None,
    )
    return _event("response.completed", response=SimpleNamespace(usage=usage))


def _text_events(text: str, output_index: int = 1) -> list[SimpleNamespace]:
    return [
        _event("response.output_text.delta", delta=text, output_index=output_index),
        _completed_event(),
    ]


def _function_call_events() -> list[SimpleNamespace]:
    return [
        _event(
            "response.output_item.added",
            item=SimpleNamespace(
                type="function_call", call_id="call_1", name="web_search"
            ),
            output_index=0,
        ),
        _event("response.function_call_arguments.delta", delta="{}", output_index=0),
        _event(
            "response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="web_search",
                arguments="{}",
            ),
            output_index=0,
        ),
    ]


def _make_agent(chunk_lists: list[list[SimpleNamespace]]) -> LocalAgent:
    return LocalAgent(
        name="stream-agent",
        description="test agent",
        llm_service=_ResponsesStubLLM(chunk_lists),
        services={},
        tools=[],
    )


def _thinking_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part for part in message.get("content", []) if part.get("type") == "thinking"
    ]


async def _drain(stream: AgentResponseStream) -> list[tuple]:
    return [item async for item in stream]


# ---------------------------------------------------------------------------
# 1. state ownership + finalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_messages_owns_state_and_finalizes_with_metadata():
    chunks = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_text_events("Final answer"),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    stream = agent.process_messages(history)
    items = await _drain(stream)
    assert items[-1][0] == "Final answer"

    message = stream.format_assistant_message(
        "Final answer", thinking=("step one", None)
    )
    assert message is not None
    assert message["role"] == "assistant"
    state = get_responses_provider_state(message)
    assert state is not None
    assert state["provider"] == "openai"
    assert state["model"] == "gpt-5.4"
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED
    assert message["_metadata"]["context_usage"] == {
        "prompt_tokens": 10,
        "provider": "openai",
        "model": "gpt-5.4",
    }
    assert len(_thinking_blocks(message)) == 1


@pytest.mark.asyncio
async def test_tool_call_finalization_attaches_metadata_and_tool_calls():
    chunks = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_function_call_events(),
        _completed_event(),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    stream = agent.process_messages(history)
    await _drain(stream)

    message = stream.format_assistant_message(
        "calling tool",
        thinking=("step one", None),
        tool_uses=[
            {"id": "call_1", "name": "web_search", "input": {}, "type": "tool_call"}
        ],
    )
    assert message is not None
    assert message["tool_calls"][0]["id"] == "call_1"
    state = get_responses_provider_state(message)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED


@pytest.mark.asyncio
async def test_reasoning_summary_deltas_yield_thinking_chunks():
    chunks = [
        _event("response.reasoning_summary_text.delta", delta="step "),
        _event("response.reasoning_summary_text.delta", delta="one"),
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_text_events("Final answer"),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    stream = agent.process_messages(history)
    thinking = ""
    async for _, _, thinking_chunk in stream:
        if thinking_chunk:
            text, _ = thinking_chunk
            thinking += text

    assert thinking == "step one"
    message = stream.format_assistant_message("Final answer", thinking=(thinking, None))
    assert message is not None
    blocks = _thinking_blocks(message)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(message)
    assert state is not None


# ---------------------------------------------------------------------------
# 2. aclose() cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_preserves_captured_metadata_for_partial_message():
    chunks = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        _event("response.output_text.delta", delta="partial", output_index=1),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    stream = agent.process_messages(history)
    # First item captures the completed reasoning item; second yields partial text.
    await stream.__anext__()
    await stream.__anext__()
    await stream.aclose()

    message = stream.format_assistant_message("partial", thinking=("step one", None))
    assert message is not None
    state = get_responses_provider_state(message)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED
    assert len(_thinking_blocks(message)) == 1


@pytest.mark.asyncio
async def test_aclose_before_first_iteration_is_safe():
    agent = _make_agent([])
    stream = agent.process_messages(
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    )

    await stream.aclose()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()

    message = stream.format_assistant_message("never started")
    assert message is not None
    assert METADATA_FIELD not in message


@pytest.mark.asyncio
async def test_aclose_after_completion_is_safe():
    agent = _make_agent([_text_events("done", output_index=0)])
    stream = agent.process_messages(
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    )
    await _drain(stream)

    await stream.aclose()  # must not raise


# ---------------------------------------------------------------------------
# 3. concurrent streams stay isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_streams_do_not_mix_reasoning_items():
    chunks_a = [
        _event(
            "response.output_item.done",
            item=_reasoning_item(item_id="rs_a"),
            output_index=0,
        ),
        _event("response.output_text.delta", delta="answer a", output_index=1),
        _completed_event(input_tokens=31, cached_tokens=21),
    ]
    chunks_b = [
        _event(
            "response.output_item.done",
            item=_reasoning_item(item_id="rs_b"),
            output_index=0,
        ),
        _event("response.output_text.delta", delta="answer b", output_index=1),
        _completed_event(input_tokens=57, cached_tokens=7),
    ]
    agent = _make_agent([chunks_a, chunks_b])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    stream_a = agent.process_messages(history)
    stream_b = agent.process_messages(history)

    # Interleave iteration so both streams are active at the same time.
    first_a = await stream_a.__anext__()
    first_b = await stream_b.__anext__()
    # First items are the reasoning yields (empty text + thinking fallback).
    assert first_a[2] == ("step one", None)
    assert first_b[2] == ("step one", None)
    items_a = await _drain(stream_a)
    items_b = await _drain(stream_b)
    assert items_a[-1][0] == "answer a"
    assert items_b[-1][0] == "answer b"

    msg_a = stream_a.format_assistant_message("answer a", thinking=("step one", None))
    msg_b = stream_b.format_assistant_message("answer b", thinking=("step one", None))
    assert msg_a is not None and msg_b is not None
    state_a = get_responses_provider_state(msg_a)
    state_b = get_responses_provider_state(msg_b)
    assert state_a is not None and state_b is not None
    assert state_a["output_items"][0]["id"] == "rs_a"
    assert state_b["output_items"][0]["id"] == "rs_b"
    assert msg_a["_metadata"]["context_usage"]["prompt_tokens"] == 31
    assert msg_b["_metadata"]["context_usage"]["prompt_tokens"] == 57


@pytest.mark.asyncio
async def test_anthropic_cache_creation_counts_in_snapshot_and_shrink_gate(monkeypatch):
    chunks = [
        _event(
            "message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=20,
                    cache_read_input_tokens=10,
                    cache_creation_input_tokens=60,
                )
            ),
        ),
        _event("content_block_delta", delta=SimpleNamespace(text="answer")),
        _event("message_delta", usage=SimpleNamespace(output_tokens=5)),
    ]
    agent = _make_agent([chunks])
    agent.llm._provider_name = "anthropic"
    agent.llm.model = "claude-test"
    agent.llm.process_stream_chunk = MethodType(
        AnthropicService.process_stream_chunk, agent.llm
    )
    stream = agent.process_messages(
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    )
    await _drain(stream)
    message = stream.format_assistant_message("answer")
    assert message["_metadata"]["context_usage"] == {
        "prompt_tokens": 90,
        "provider": "anthropic",
        "model": "claude-test",
    }
    assert agent.token_usage.input_tokens == 20
    assert agent.token_usage.cached_tokens == 10
    assert agent.token_usage.cache_creation_tokens == 60
    assert agent.token_usage.total_input_tokens == 90
    assert agent.token_usage.total_tokens == 95

    agent.services["agent_manager"] = SimpleNamespace(
        context_shrink_enabled=True, shrink_excluded_list=[]
    )
    monkeypatch.setenv("AGENTCREW_DEFAULT_MAX_CONTEXT", "100")
    history = [
        {"role": "assistant", "tool_calls": [{"id": "old", "name": "read_file"}]},
        {"role": "tool", "tool_call_id": "old", "content": "old result"},
        *[{"role": "user", "content": "recent"} for _ in range(10)],
    ]
    AgentContextManager(agent).shrink_tool_results(history)
    assert history[1]["role"] == "user"
    assert "was truncated" in history[1]["content"][0]["text"]


# ---------------------------------------------------------------------------
# 4. no-state stream (remote-agent representative)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_state_stream_uses_same_interface_without_metadata():
    class _NoStateAgent:
        name = "remote-like"

        def format_message(self, message_type, data):
            return {
                "role": "assistant",
                "agent": self.name,
                "content": [{"type": "text", "text": data.get("message", "")}],
            }

    async def gen():
        yield ("remote answer", "remote answer", None)

    stream = AgentResponseStream(
        agent=_NoStateAgent(),
        state_factory=lambda: None,
        iterator_factory=lambda state: gen(),
    )

    items = await _drain(stream)
    assert items == [("remote answer", "remote answer", None)]

    message = stream.format_assistant_message("remote answer")
    assert message is not None
    assert message["role"] == "assistant"
    assert METADATA_FIELD not in message


# ---------------------------------------------------------------------------
# 5. consumers no longer create/thread stream state
# ---------------------------------------------------------------------------


def test_consumers_have_no_stream_state_plumbing():
    root = pathlib.Path(__file__).resolve().parents[1] / "AgentCrew" / "modules"
    files = [
        root / "chat" / "message" / "handler.py",
        root / "a2a" / "agent_executor.py",
        root / "acp" / "turn_executor.py",
        root / "agents" / "agent_runner.py",
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "create_stream_state" not in source, f"{path} still creates stream state"
        assert "attach_responses_metadata" not in source, (
            f"{path} still imports attach_responses_metadata"
        )
        assert "attach_stream_metadata" not in source, (
            f"{path} still imports attach_stream_metadata"
        )
        assert "stream_state=" not in source, f"{path} still threads stream_state"
