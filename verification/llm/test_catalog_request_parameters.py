"""No-network request-shaping checks for the new Claude and Gemini models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import TextBlock, ThinkingBlock

from AgentCrew.modules.anthropic.service import AnthropicService
from AgentCrew.modules.google.native_service import GoogleAINativeService

CLAUDE_MODELS = [
    "claude-fable-5-2",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
]
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-4-hydrogen",
]


class EmptyStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.fixture
def claude_service():
    service = object.__new__(AnthropicService)
    service.model = "claude-sonnet-5-5"
    service._provider_name = "claude"
    service.system_prompt = "Test prompt"
    service.temperature = 0.7
    service.thinking_enabled = False
    service.thinking_budget = 0
    service.reasoning_effort = "medium"
    service.caching_blocks = 1
    service.tools = []
    service.client = SimpleNamespace(
        messages=SimpleNamespace(
            stream=MagicMock(return_value=EmptyStream()), create=AsyncMock()
        )
    )
    service.calculate_cost = MagicMock(return_value=0.0)
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", CLAUDE_MODELS)
@pytest.mark.parametrize("thinking", [True, False])
async def test_new_claude_requests_use_adaptive_thinking_without_sampling(
    claude_service, model_id, thinking
):
    service = claude_service
    service.model = model_id
    service.thinking_enabled = thinking
    service.thinking_budget = 8192
    await service.stream_assistant_response([{"role": "user", "content": "Hello"}])
    params = service.client.messages.stream.call_args.kwargs
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k"} & params.keys()
    assert "budget_tokens" not in params["thinking"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", CLAUDE_MODELS)
async def test_auxiliary_requests_do_not_disable_always_on_thinking(
    claude_service, model_id
):
    assert await claude_service.process_message("Hello", model_id=model_id) == ""
    params = claude_service.client.messages.stream.call_args.kwargs
    assert params["model"] == model_id
    assert params["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "low"),
        (1024, "low"),
        (8192, "medium"),
        (32768, "high"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "xhigh"),
        ("max", "max"),
    ],
)
def test_new_claude_effort_accepts_selection_strings_and_legacy_budgets(
    claude_service, value, expected
):
    assert claude_service.set_think(value)
    assert claude_service.reasoning_effort == expected
    assert claude_service.thinking_budget == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking", [True, False])
async def test_existing_and_custom_claude_request_semantics_are_unchanged(
    claude_service, thinking
):
    service = claude_service
    service.model = "private/custom-claude"
    service.thinking_enabled = thinking
    service.thinking_budget = 8192
    await service.stream_assistant_response([{"role": "user", "content": "Hello"}])
    params = service.client.messages.stream.call_args.kwargs
    assert "output_config" not in params
    if thinking:
        assert params["thinking"] == {"type": "enabled", "budget_tokens": 8192}
        assert "temperature" not in params
    else:
        assert "thinking" not in params
        assert params["temperature"] == 0.7


@pytest.mark.asyncio
async def test_spec_validation_extracts_text_after_adaptive_thinking(claude_service):
    claude_service.client.messages.create.return_value = SimpleNamespace(
        content=[
            ThinkingBlock(type="thinking", thinking="", signature="test-signature"),
            TextBlock(type="text", text='{"valid":true}'),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    assert await claude_service.validate_spec("Validate") == '{"valid":true}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id", GEMINI_MODELS + ["custom/private-gemini", "gemini-3.6-flash"]
)
async def test_google_auxiliary_path_uses_supported_thinking_parameters(model_id):
    service = object.__new__(GoogleAINativeService)
    service.model = "gemini-3.6-flash"
    service.system_prompt = "Test prompt"
    service.calculate_cost = MagicMock(return_value=0.0)
    service.client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content_stream=AsyncMock(return_value=EmptyStream())
            )
        )
    )
    assert await service.process_message("Hello", model_id=model_id) == ""
    params = service.client.aio.models.generate_content_stream.call_args.kwargs
    assert params["model"] == model_id
    config = params["config"].thinking_config
    if model_id in GEMINI_MODELS:
        assert config.thinking_level == "LOW"
        assert config.thinking_budget is None
    else:
        assert config.thinking_budget == 0
        assert config.thinking_level is None


@pytest.mark.parametrize("value", [-1, "invalid", "ultra", None])
def test_new_claude_rejects_invalid_effort_without_changing_state(
    claude_service, value
):
    assert not claude_service.set_think(value)
    assert claude_service.reasoning_effort == "medium"


@pytest.mark.asyncio
async def test_spec_validation_rejects_response_without_text(claude_service):
    claude_service.client.messages.create.return_value = SimpleNamespace(
        content=[
            ThinkingBlock(type="thinking", thinking="", signature="test-signature")
        ]
    )
    with pytest.raises(TypeError, match="no TextBlock"):
        await claude_service.validate_spec("Validate")
