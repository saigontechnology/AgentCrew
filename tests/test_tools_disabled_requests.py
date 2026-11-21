"""Request-scoped tool omission using stub transports, never live providers."""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from AgentCrew.modules.anthropic.service import AnthropicService
from AgentCrew.modules.custom_llm.copilot_response_service import (
    GithubCopilotResponseService,
)
from AgentCrew.modules.custom_llm.github_copilot_service import GithubCopilotService
from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.google.native_service import GoogleAINativeService
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai.response_service import OpenAIResponseService
from AgentCrew.modules.openai.service import OpenAIService
from AgentCrew.modules.openai_codex.service import OpenAICodexService
from AgentCrew.modules.together.service import TogetherAIService
from AgentCrew.modules.xai.response_service import XAISubscriptionResponseService

PROVIDERS = [
    OpenAIService,
    OpenAIResponseService,
    OpenAICodexService,
    GithubCopilotResponseService,
    CustomLLMService,
    GithubCopilotService,
    AnthropicService,
    GoogleAINativeService,
    TogetherAIService,
    XAISubscriptionResponseService,
]


def make_provider(cls, monkeypatch):
    monkeypatch.setattr(
        ModelRegistry,
        "get_model_capabilities",
        lambda _: ["tool_use", "thinking", "stream", "structured_output"],
    )
    monkeypatch.setattr(ModelRegistry, "get_model_sample_params", lambda _: None)
    service = cls.__new__(cls)
    service.model = "model"
    service._provider_name = "test"
    service.temperature = 0.3
    service.reasoning_effort = "high"
    service.system_prompt = "Keep these system instructions"
    service.structured_output = None
    service.thinking_enabled = True
    service.thinking_budget = 1024
    service.caching_blocks = 1
    service._extra_headers = {"x-test": "header"}
    service.extra_headers = {"x-test": "header"}
    service.tools = [
        {"type": "function", "name": "calc", "parameters": {"type": "object"}}
    ]
    if cls is GoogleAINativeService:
        from google.genai import types

        service.tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="calc", parameters=types.Schema(type="OBJECT")
                    )
                ]
            )
        ]
    service.tool_handlers = {"calc": lambda _: "ok"}
    service._convert_internal_format = MagicMock(
        return_value=[{"role": "user", "content": "work"}]
    )
    create = AsyncMock(return_value=SimpleNamespace())
    if cls is AnthropicService:
        create = MagicMock(return_value=SimpleNamespace())
    service.client = SimpleNamespace(
        api_key="stub",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=SimpleNamespace(create=create),
        messages=SimpleNamespace(stream=create),
        aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=create)),
    )
    if cls is OpenAICodexService:
        service._ensure_valid_token = MagicMock()
        service._ensure_extra_headers = AsyncMock()
        service._codex_service_tier = lambda: "auto"
    if cls in {GithubCopilotResponseService, GithubCopilotService}:
        service._is_github_provider = lambda: False
    if cls is XAISubscriptionResponseService:
        service._client_loop = None
        service._ensure_valid_token = AsyncMock()
        service._model_definition = lambda _: SimpleNamespace(
            capabilities=["tool_use", "thinking"]
        )
    return service, create


def payload(create, cls):
    params = copy.deepcopy(create.call_args.kwargs)
    if cls is GoogleAINativeService:
        params["config"] = params["config"].model_dump(exclude_none=True)
    return params


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize("structured", [False, True])
async def test_tools_disabled_omits_only_request_tools_without_mutation(
    cls, structured, monkeypatch
):
    service, create = make_provider(cls, monkeypatch)
    if structured:
        service.structured_output = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }
    tools = service.tools
    handlers = service.tool_handlers
    original_tools = copy.deepcopy(tools)
    messages = [{"role": "user", "content": [{"type": "text", "text": "work"}]}]
    await service.stream_assistant_response(copy.deepcopy(messages))
    default = payload(create, cls)
    await service.stream_assistant_response(copy.deepcopy(messages), tools_enabled=True)
    enabled = payload(create, cls)
    assert default == enabled
    await service.stream_assistant_response(
        copy.deepcopy(messages), tools_enabled=False
    )
    disabled = payload(create, cls)
    if cls is GoogleAINativeService:
        enabled_tools = enabled["config"].pop("tools", None)
        assert "tools" not in disabled["config"]
        assert bool(enabled_tools) is (not structured)
    else:
        assert enabled.pop("tools")
        assert "tools" not in disabled
    assert disabled == enabled
    assert service.tools is tools and service.tools == original_tools
    assert service.tool_handlers is handlers
    assert handlers["calc"]({}) == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls",
    [
        OpenAIService,
        CustomLLMService,
        AnthropicService,
        GoogleAINativeService,
        XAISubscriptionResponseService,
    ],
)
async def test_concurrent_disabled_and_enabled_requests_are_isolated(cls, monkeypatch):
    service, create = make_provider(cls, monkeypatch)
    messages = [{"role": "user", "content": [{"type": "text", "text": "work"}]}]
    await asyncio.gather(
        service.stream_assistant_response(copy.deepcopy(messages), tools_enabled=False),
        service.stream_assistant_response(copy.deepcopy(messages), tools_enabled=True),
    )
    params = [entry.kwargs for entry in create.call_args_list]
    if cls is GoogleAINativeService:
        assert params[0]["config"].tools is None
        assert params[1]["config"].tools == service.tools
    else:
        assert "tools" not in params[0]
        assert params[1]["tools"]
    assert service.tools and service.tool_handlers


def test_every_stream_override_and_stub_accepts_defaulted_keyword_policy():
    root = Path(__file__).resolve().parents[1]
    found = []
    for directory in [root / "AgentCrew", root / "tests"]:
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "stream_assistant_response"
                ):
                    args = node.args.kwonlyargs
                    defaults = node.args.kw_defaults
                    policy = next(
                        (
                            default
                            for arg, default in zip(args, defaults, strict=True)
                            if arg.arg == "tools_enabled"
                        ),
                        None,
                    )
                    assert isinstance(policy, ast.Constant) and policy.value is True, (
                        path
                    )
                    found.append(path)
    assert len(found) >= len(PROVIDERS) + 1
    for cls in PROVIDERS:
        assert (
            inspect.signature(cls.stream_assistant_response)
            .parameters["tools_enabled"]
            .default
            is True
        )


@pytest.mark.asyncio
async def test_custom_nonstreaming_tools_disabled_and_choice_is_removed(monkeypatch):
    service, create = make_provider(CustomLLMService, monkeypatch)
    create.return_value = SimpleNamespace(usage=None, choices=[])
    service._build_stream_params = lambda: (
        {"model": "model", "tools": service.tools, "tool_choice": "required"},
        False,
    )
    async with await service.stream_assistant_response(
        [], tools_enabled=False
    ) as stream:
        assert [chunk async for chunk in stream] == []
    assert "tools" not in create.call_args.kwargs
    assert "tool_choice" not in create.call_args.kwargs
    assert create.call_args.kwargs["stream"] is False
    assert service.tools and service.tool_handlers
