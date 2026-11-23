"""Custom-provider transcript conversion without network requests."""

from copy import deepcopy

import pytest

from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.llm.model_registry import ModelRegistry


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        ModelRegistry,
        "get_model_capabilities",
        lambda _: ["thinking", "tool_use"],
    )
    service = CustomLLMService.__new__(CustomLLMService)
    service._provider_name = "test"
    service.model = "model"
    return service


def assistant_message(text="", thinking="Inspect the file before editing."):
    content = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking})
    content.append({"type": "text", "text": text})
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "name": "read_file",
                "arguments": {"file_path": "example.py"},
            }
        ],
    }


def test_blank_tool_transcript_replays_real_thinking_and_normalizes_calls(service):
    message = assistant_message()
    original = deepcopy(message)

    converted = service._convert_internal_format([message])[0]

    assert converted["content"] == " "
    assert converted["reasoning_content"] == "Inspect the file before editing."
    assert converted["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": '{"file_path": "example.py"}',
            },
        }
    ]
    assert message == original


@pytest.mark.parametrize("thinking", [None, ""])
@pytest.mark.parametrize("reasoning", [None, "", "Previously captured reasoning."])
def test_thinking_tool_transcript_retains_non_null_reasoning_fallback(
    service, thinking, reasoning
):
    message = assistant_message(thinking=thinking)
    if reasoning is not None and thinking is None:
        message["reasoning_content"] = reasoning

    converted = service._convert_internal_format([message])[0]

    assert converted["content"] == " "
    assert converted["reasoning_content"] == (
        reasoning if thinking is None and reasoning else " "
    )


@pytest.mark.parametrize("thinking", [None, "Hidden reasoning."])
@pytest.mark.parametrize("with_tool_calls", [False, True])
def test_non_thinking_model_never_receives_reasoning_field(
    service, monkeypatch, thinking, with_tool_calls
):
    monkeypatch.setattr(ModelRegistry, "get_model_capabilities", lambda _: ["tool_use"])
    message = assistant_message(thinking=thinking)
    message["reasoning_content"] = "Legacy reasoning field."
    if not with_tool_calls:
        message.pop("tool_calls")
    original = deepcopy(message)

    converted = service._convert_internal_format([message])[0]

    assert converted["content"] == " "
    assert "reasoning_content" not in converted
    assert bool(converted.get("tool_calls")) is with_tool_calls
    assert message == original


@pytest.mark.parametrize("thinking_enabled", [False, True])
@pytest.mark.parametrize("as_list", [False, True])
def test_nonblank_assistant_text_is_preserved(
    service, monkeypatch, thinking_enabled, as_list
):
    monkeypatch.setattr(
        ModelRegistry,
        "get_model_capabilities",
        lambda _: ["tool_use", "thinking"] if thinking_enabled else ["tool_use"],
    )
    message = assistant_message(text="I will inspect the file.")
    if not as_list:
        message["content"] = "I will inspect the file."
    original = deepcopy(message)

    converted = service._convert_internal_format([message])[0]

    assert converted["content"] == "I will inspect the file."
    assert converted["tool_calls"][0]["id"] == "call-1"
    assert message == original


@pytest.mark.parametrize("content", [None, "", []])
def test_empty_assistant_wire_content_is_non_null(service, content):
    message = assistant_message(thinking=None)
    message["content"] = content

    converted = service._convert_internal_format([message])[0]

    assert converted["content"] == " "
    assert converted["reasoning_content"] == " "
