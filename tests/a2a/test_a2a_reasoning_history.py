"""Focused tests for A2A reasoning-summary persistence as thinking content.

Verifies that OpenAI reasoning summary deltas (and the completed-item summary
fallback) become exactly one ``type: thinking`` block in persisted assistant
messages for both tool-call and no-tool turns, while ``_metadata`` provider
state is attached alongside.

Uses a real ``LocalAgent`` plus a stubbed Responses-like LLM service that
delegates chunk parsing to the real ``OpenAIResponseService`` so the whole
pipeline is exercised.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from a2a.server.events import EventQueueLegacy

from AgentCrew.modules.a2a.agent_executor import (
    AgentCrewA2AExecutor,
    AnswerArtifactState,
    ToolCallResult,
)
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.message_metadata import get_responses_provider_state
from AgentCrew.modules.openai.response_service import OpenAIResponseService

ENCRYPTED = "gAAAAABa2a-encrypted-reasoning-blob=="


class _Ctx:
    call_context = None
    current_task = None
    task_id = "t1"
    context_id = "c1"
    message = None


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

    def calculate_cost(self, *args, **kwargs) -> float:
        return 0.0

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


def _reasoning_item() -> SimpleNamespace:
    return SimpleNamespace(
        id="rs_a2a",
        type="reasoning",
        summary=[SimpleNamespace(type="summary_text", text="step one")],
        content=None,
        encrypted_content=ENCRYPTED,
        status="completed",
    )


def _completed_event() -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
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


def _make_agent(chunks: list[list[SimpleNamespace]]) -> LocalAgent:
    return LocalAgent(
        name="a2a-reasoning-agent",
        description="test agent",
        llm_service=_ResponsesStubLLM(chunks),
        services={},
        tools=[],
    )


def _make_executor(agent: LocalAgent) -> AgentCrewA2AExecutor:
    store = AsyncMock()
    store.get_history = AsyncMock(return_value=[])
    store.get_pending_tools = AsyncMock(return_value=None)
    store.append_history = AsyncMock()
    return AgentCrewA2AExecutor(agent=agent, session_store=store)


def _thinking_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part for part in message.get("content", []) if part.get("type") == "thinking"
    ]


async def _run_turn(agent: LocalAgent, history: list[dict[str, Any]]):
    executor = _make_executor(agent)
    queue = EventQueueLegacy(max_queue_size=1024)
    await executor._run_agent_loop(
        _Ctx(), queue, "t1", "c1", history, AnswerArtifactState("answer_t1")
    )
    await queue.close(immediate=True)
    return executor


@pytest.mark.asyncio
async def test_no_tool_turn_persists_thinking_and_metadata():
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

    await _run_turn(agent, history)

    assistant = history[-1]
    assert assistant["role"] == "assistant"
    blocks = _thinking_blocks(assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["provider"] == "openai"
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED

    # replay path remains intact for the next request
    converted = cast(Any, agent.llm)._responses._convert_internal_format(
        copy.deepcopy(history)
    )
    replayed = [m for m in converted if m.get("type") == "reasoning"]
    assert len(replayed) == 1
    assert replayed[0]["encrypted_content"] == ENCRYPTED


@pytest.mark.asyncio
async def test_no_tool_turn_summary_fallback_without_deltas():
    chunks = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_text_events("Final answer"),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    await _run_turn(agent, history)

    assistant = history[-1]
    blocks = _thinking_blocks(assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    assert get_responses_provider_state(assistant) is not None


@pytest.mark.asyncio
async def test_tool_call_turn_persists_thinking_and_metadata():
    tool_turn = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_function_call_events(),
        _completed_event(),
    ]
    # Second (post-tool) turn yields nothing so the loop finalizes cleanly.
    agent = _make_agent([tool_turn, []])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    executor = _make_executor(agent)
    executor._execute_tool_calls = AsyncMock(return_value=ToolCallResult.CONTINUE)

    queue = EventQueueLegacy(max_queue_size=1024)
    await executor._run_agent_loop(
        _Ctx(), queue, "t1", "c1", history, AnswerArtifactState("answer_t1")
    )
    await queue.close(immediate=True)

    tool_assistant = history[1]
    assert tool_assistant["role"] == "assistant"
    assert tool_assistant["tool_calls"][0]["id"] == "call_1"
    blocks = _thinking_blocks(tool_assistant)
    assert len(blocks) == 1
    assert blocks[0]["thinking"] == "step one"
    state = get_responses_provider_state(tool_assistant)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED
