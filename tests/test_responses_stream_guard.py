from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from AgentCrew.modules.custom_llm.copilot_response_service import (
    GithubCopilotResponseService,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai.response_service import OpenAIResponseService
from AgentCrew.modules.openai.response_stream import ResponsesStreamGuard
from AgentCrew.modules.openai_codex.service import OpenAICodexService


class EventStream:
    def __init__(self, events):
        self.events = iter(events)
        self.closes = 0
        self.exits = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = next(self.events, None)
        if event is None:
            raise StopAsyncIteration
        if isinstance(event, Exception):
            raise event
        return SimpleNamespace(type=event)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exits.append(args)
        await self.close()

    async def close(self):
        self.closes += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("events", [["response.completed"], []])
async def test_natural_end(events):
    raw = EventStream(events)
    guard = ResponsesStreamGuard(raw, "provider")
    if events:
        async with guard:
            assert [event.type async for event in guard] == events
    else:
        with pytest.raises(RuntimeError, match="provider.*without completion"):
            async with guard:
                _ = [event async for event in guard]
    await guard.aclose()
    assert raw.closes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["response.failed", "response.incomplete", "error"])
async def test_terminal_failure(event):
    raw = EventStream([event])
    with pytest.raises(RuntimeError, match=f"provider.*{event}"):
        async with ResponsesStreamGuard(raw, "provider") as guard:
            await anext(guard)
    assert raw.closes == 1


@pytest.mark.asyncio
async def test_early_close_is_idempotent():
    raw = EventStream(["response.created"])
    async with ResponsesStreamGuard(raw, "provider") as guard:
        await anext(guard)
        await guard.aclose()
        await guard.aclose()
    assert raw.closes == 1
    assert len(raw.exits) == 1


@pytest.mark.asyncio
async def test_underlying_exception_propagates():
    error = ValueError("transport")
    raw = EventStream([error])
    with pytest.raises(ValueError, match="transport"):
        async with ResponsesStreamGuard(raw, "provider") as guard:
            await anext(guard)
    assert raw.closes == 1
    assert raw.exits[0][1] is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_type",
    [OpenAIResponseService, OpenAICodexService, GithubCopilotResponseService],
)
async def test_openers_wrap_without_changing_request(service_type, monkeypatch):
    monkeypatch.setattr(ModelRegistry, "get_model_capabilities", lambda _: [])
    monkeypatch.setattr(ModelRegistry, "get_model_sample_params", lambda _: None)
    service = service_type.__new__(service_type)
    service._provider_name = (
        "openai_codex" if service_type is OpenAICodexService else "openai"
    )
    service.model = "model"
    service.system_prompt = "instructions"
    service.tools = []
    service.structured_output = None
    service.reasoning_effort = None
    service._extra_headers = None
    service._convert_internal_format = lambda messages: messages
    raw = EventStream(["response.completed"])
    create = AsyncMock(return_value=raw)
    service.client = SimpleNamespace(
        api_key="token", responses=SimpleNamespace(create=create)
    )
    if service_type is OpenAICodexService:
        service._oauth = SimpleNamespace(
            get_valid_access_token=lambda: "token", account_id=None
        )
    if service_type is GithubCopilotResponseService:
        service._is_github_provider = lambda: False
    messages = [{"role": "user", "content": "hello"}]
    guard = await service.stream_assistant_response(messages)
    assert isinstance(guard, ResponsesStreamGuard)
    expected = {
        "model": "model",
        "input": messages,
        "stream": True,
        "instructions": "instructions",
        "include": ["reasoning.encrypted_content"],
    }
    if service_type is OpenAICodexService:
        expected.update(store=False, service_tier=service._codex_service_tier())
    create.assert_awaited_once_with(**expected)
    async with guard:
        assert [event.type async for event in guard] == ["response.completed"]
    assert raw.closes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("service_type", [OpenAIResponseService, OpenAICodexService])
async def test_process_message_uses_guard_and_closes(service_type, monkeypatch):
    monkeypatch.setattr(ModelRegistry, "get_model_capabilities", lambda _: [])
    service = service_type.__new__(service_type)
    service._provider_name = "codex" if service_type is OpenAICodexService else "openai"
    service.model = "model"
    service.system_prompt = "instructions"
    service._extra_headers = None
    service.reasoning_effort = None
    service.calculate_cost = lambda *args: 0
    raw = EventStream(["response.created"])
    service.client = SimpleNamespace(
        api_key="token", responses=SimpleNamespace(create=AsyncMock(return_value=raw))
    )
    if service_type is OpenAICodexService:
        service._oauth = SimpleNamespace(get_valid_access_token=lambda: "token")
    with pytest.raises(RuntimeError, match="without completion"):
        await service.process_message("hello")
    assert raw.closes == 1
    params = service.client.responses.create.call_args.kwargs
    assert params["model"] == "model"
    assert params["stream"] is True
    assert params["instructions"] == "instructions"


@pytest.mark.asyncio
async def test_xai_opener_uses_guard(monkeypatch):
    from AgentCrew.modules.xai.response_service import XAISubscriptionResponseService

    service = XAISubscriptionResponseService.__new__(XAISubscriptionResponseService)
    service._model = "grok"
    service._provider_name = "xai"
    service._client_loop = None
    service._model_definition = lambda _: SimpleNamespace(capabilities=[])
    service._ensure_valid_token = AsyncMock()
    service._convert_internal_format = lambda messages, model_id=None: messages
    service.system_prompt = "instructions"
    service.tools = []
    service.reasoning_effort = None
    raw = EventStream(["response.completed"])
    create = AsyncMock(return_value=raw)
    service.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    messages = [{"role": "user", "content": "hello"}]
    guard = await service.stream_assistant_response(messages)
    assert isinstance(guard, ResponsesStreamGuard)
    create.assert_awaited_once_with(
        model="grok",
        input=messages,
        instructions="instructions",
        stream=True,
        store=False,
    )
    async with guard:
        assert [event.type async for event in guard] == ["response.completed"]
    assert raw.closes == 1


@pytest.mark.asyncio
async def test_copilot_headers_are_preserved(monkeypatch):
    monkeypatch.setattr(ModelRegistry, "get_model_capabilities", lambda _: [])
    monkeypatch.setattr(ModelRegistry, "get_model_sample_params", lambda _: None)
    service = GithubCopilotResponseService.__new__(GithubCopilotResponseService)
    service._provider_name = "github_copilot"
    service.model = "model"
    service.system_prompt = ""
    service.tools = []
    service.structured_output = None
    service.reasoning_effort = None
    service._extra_headers = {"existing": "header"}
    service._is_github_provider = lambda: True
    service._github_copilot_token_to_open_ai_key = lambda _: None
    service.base_url = "https://example.invalid/"
    service.api_key = "token"
    service._convert_internal_format = lambda messages: messages
    raw = EventStream(["response.completed"])
    create = AsyncMock(return_value=raw)
    service.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    guard = await service.stream_assistant_response(
        [{"role": "user", "content": "hello"}]
    )
    assert isinstance(guard, ResponsesStreamGuard)
    headers = create.call_args.kwargs["extra_headers"]
    assert headers["existing"] == "header"
    assert headers["X-Initiator"] == "user"
    assert headers["X-Request-Id"]
    async with guard:
        assert [event.type async for event in guard] == ["response.completed"]
