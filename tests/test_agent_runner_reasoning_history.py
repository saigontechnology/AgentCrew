"""End-to-end tests for reasoning history through ``run_agent_loop``.

Uses a real ``LocalAgent`` plus a stubbed Responses-like LLM service that
delegates chunk parsing to the real ``OpenAIResponseService`` so the whole
pipeline is exercised:

- thinking persists on no-tool completion and filtered-tool completion
- ``_metadata`` provider state is attached to persisted assistant messages
- the next Responses request replays the reasoning item exactly once
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, cast

import pytest

from AgentCrew.modules.agents.agent_runner import run_agent_loop
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.message_metadata import (
    get_responses_provider_state,
)
from AgentCrew.modules.openai.response_service import OpenAIResponseService

ENCRYPTED = "gAAAAABloop-encrypted-reasoning-blob=="


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


def _reasoning_item() -> SimpleNamespace:
    return SimpleNamespace(
        id="rs_loop",
        type="reasoning",
        summary=[SimpleNamespace(type="summary_text", text="step one")],
        content=None,
        encrypted_content=ENCRYPTED,
        status="completed",
    )


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


def _make_agent(chunks: list[list[SimpleNamespace]]) -> LocalAgent:
    return LocalAgent(
        name="reasoning-agent",
        description="test agent",
        llm_service=_ResponsesStubLLM(chunks),
        services={},
        tools=[],
    )


# ---------------------------------------------------------------------------
# G. thinking persists on every completion branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_completion_persists_thinking_and_metadata():
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

    response, _ = await run_agent_loop(agent, history)

    assert response == "Final answer"
    assistant = history[-1]
    assert assistant["role"] == "assistant"

    thinking_blocks = [
        part for part in assistant["content"] if part.get("type") == "thinking"
    ]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["thinking"] == "step one"

    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["provider"] == "openai"
    assert state["model"] == "gpt-5.4"
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED

    converted = cast(Any, agent.llm)._responses._convert_internal_format(
        copy.deepcopy(history)
    )
    replayed = [m for m in converted if m.get("type") == "reasoning"]
    assert len(replayed) == 1
    assert replayed[0]["encrypted_content"] == ENCRYPTED
    assert all(part.get("type") != "thinking" for part in converted[-1]["content"])


@pytest.mark.asyncio
async def test_filtered_tool_completion_persists_thinking_and_metadata():
    chunks = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_function_call_events(),
        _event("response.output_text.delta", delta="Answer", output_index=1),
        _completed_event(),
    ]
    agent = _make_agent([chunks])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    response, _ = await run_agent_loop(agent, history, tool_filter=lambda t: False)

    assert response == "Answer"
    assistant = history[-1]
    assert assistant["role"] == "assistant"

    thinking_blocks = [
        part for part in assistant["content"] if part.get("type") == "thinking"
    ]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["thinking"] == "step one"

    state = get_responses_provider_state(assistant)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED


@pytest.mark.asyncio
async def test_tool_call_completion_keeps_thinking_and_metadata():
    tool_turn = [
        _event("response.output_item.done", item=_reasoning_item(), output_index=0),
        *_function_call_events(),
        _completed_event(),
    ]
    final_item = SimpleNamespace(
        id="rs_final",
        type="reasoning",
        summary=[SimpleNamespace(type="summary_text", text="final step")],
        content=None,
        encrypted_content="gAAAAABfinal-encrypted-blob==",
        status="completed",
    )
    final_turn = [
        _event("response.output_item.done", item=final_item, output_index=0),
        *_text_events("All done", output_index=1),
    ]
    agent = _make_agent([tool_turn, final_turn])
    history: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]

    response, _ = await run_agent_loop(agent, history)

    assert response == "All done"

    tool_assistant = history[1]
    assert tool_assistant["role"] == "assistant"
    assert tool_assistant["tool_calls"][0]["id"] == "call_1"
    thinking_blocks = [
        part for part in tool_assistant["content"] if part.get("type") == "thinking"
    ]
    assert len(thinking_blocks) == 1
    assert get_responses_provider_state(tool_assistant) is not None
    state = get_responses_provider_state(tool_assistant)
    assert state is not None
    assert state["output_items"][0]["encrypted_content"] == ENCRYPTED

    final_assistant = history[-1]
    assert final_assistant["role"] == "assistant"
    final_state = get_responses_provider_state(final_assistant)
    assert final_state is not None
    assert final_state is not None
    assert final_state["output_items"][0]["id"] == "rs_final"

    converted = cast(Any, agent.llm)._responses._convert_internal_format(
        copy.deepcopy(history)
    )
    replayed_ids = [m["id"] for m in converted if m.get("type") == "reasoning"]
    # each turn's reasoning item replayed exactly once, in turn order
    assert replayed_ids == ["rs_loop", "rs_final"]
