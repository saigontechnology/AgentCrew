"""No-network regressions for custom LLM tool argument normalization."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import validate

from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.file_editing.tool import (
    get_file_write_or_edit_tool_definition,
    get_file_write_or_edit_tool_handler,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry


@pytest.fixture
def service(monkeypatch):
    instance = object.__new__(CustomLLMService)
    instance._provider_name = "verification"
    instance.model = "test-model"
    instance._is_thinking = False
    instance.current_input_tokens = 0
    instance.current_output_tokens = 0
    monkeypatch.setattr(ModelRegistry, "get_model_capabilities", lambda *_: [])
    return instance


@pytest.fixture(params=["non_stream", "stream", "inline"])
def parse_tool_call(request, service, monkeypatch):
    def parse(name, arguments):
        tool_uses = []
        if request.param == "stream":
            monkeypatch.setattr(
                ModelRegistry, "get_model_capabilities", lambda *_: ["stream"]
            )
            raw_arguments = json.dumps(arguments)
            midpoint = len(raw_arguments) // 2
            for index, fragment in enumerate(
                [raw_arguments[:midpoint], raw_arguments[midpoint:]]
            ):
                delta = SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call_test" if index == 0 else None,
                            function=SimpleNamespace(
                                name=name if index == 0 else None,
                                arguments=fragment,
                            ),
                        )
                    ],
                )
                chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta)])
                service.process_stream_chunk(chunk, "", tool_uses)
        else:
            if request.param == "inline":
                payload = json.dumps({"name": name, "arguments": arguments})
                message = SimpleNamespace(
                    content=f"<tool_call>{payload}<｜tool▁calls▁end｜>",
                    tool_calls=None,
                )
            else:
                message = SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_test",
                            type="function",
                            function=SimpleNamespace(
                                name=name, arguments=json.dumps(arguments)
                            ),
                        )
                    ],
                )
            service.process_stream_chunk(SimpleNamespace(message=message), "", tool_uses)
        assert len(tool_uses) == 1
        assert tool_uses[0]["name"] == name
        return tool_uses[0]["input"]

    return parse


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        '{\n  "key": "value"\n}',
        ' \t\r\n{\r\n  "key": "value"\r\n}\r\n ',
        '[\n  {"key": "value"}\n]\n',
        "{'key': 'value'}",
        "ordinary text\nwith a trailing newline\n",
        "{not valid JSON",
        "",
    ],
)
async def test_write_file_preserves_string_content_through_handler(
    parse_tool_call, content
):
    arguments = parse_tool_call(
        "write_file", {"file_path": "example.json", "write_blocks": content}
    )
    assert isinstance(arguments["write_blocks"], str)
    assert arguments["write_blocks"] == content
    schema = get_file_write_or_edit_tool_definition()["function"]["parameters"]
    validate(arguments, schema)
    file_service = Mock()
    file_service.write_or_edit_file.return_value = {
        "status": "success",
        "file_path": "example.json",
    }
    handler = get_file_write_or_edit_tool_handler(file_service)
    await handler(**arguments)
    file_service.write_or_edit_file.assert_called_once_with(
        file_path="example.json",
        is_search_replace=False,
        text_or_search_replace_blocks=content,
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"key": "value"}, {"key": "value"}),
        ('{"key": "value"}', {"key": "value"}),
        ("{'key': 'value'}", {"key": "value"}),
        ('["one", "two"]', ["one", "two"]),
        ("not JSON", "not JSON"),
        ("{malformed", "{malformed"),
    ],
)
def test_other_tools_keep_existing_container_coercion(parse_tool_call, value, expected):
    arguments = parse_tool_call("another_tool", {"write_blocks": value})
    assert arguments == {"write_blocks": expected}


def test_write_file_keeps_array_mode_and_nested_strings(parse_tool_call):
    blocks = [{"search": '{"key": "old"}', "replace": '{"key": "new"}'}]
    arguments = parse_tool_call(
        "write_file", {"file_path": "example.json", "write_blocks": blocks}
    )
    assert arguments["write_blocks"] == blocks
    validate(
        arguments,
        get_file_write_or_edit_tool_definition()["function"]["parameters"],
    )


def test_write_file_exclusion_does_not_disable_other_parameter_coercion(parse_tool_call):
    arguments = parse_tool_call(
        "write_file",
        {"file_path": "example.json", "write_blocks": "{}", "other": '{"key": 1}'},
    )
    assert arguments["write_blocks"] == "{}"
    assert arguments["other"] == {"key": 1}


@pytest.mark.parametrize("raw_arguments", [None, "", "{malformed", "[]", "null", 42])
def test_non_stream_malformed_arguments_keep_empty_input(service, raw_arguments):
    tool_uses = []
    assert service._append_non_stream_tool_call(
        tool_uses,
        SimpleNamespace(
            id="call_test",
            type="function",
            function=SimpleNamespace(name="write_file", arguments=raw_arguments),
        ),
    )
    assert tool_uses[0]["input"] == {}


@pytest.mark.parametrize("raw_arguments", ["{malformed", "[]", "null"])
def test_stream_malformed_arguments_keep_empty_input(service, raw_arguments):
    tool_uses = []
    service._merge_stream_tool_call_delta(
        tool_uses,
        SimpleNamespace(
            index=0,
            id="call_test",
            function=SimpleNamespace(name="write_file", arguments=raw_arguments),
        ),
    )
    assert tool_uses[0]["input"] == {}
    assert tool_uses[0]["args_json"] == raw_arguments


def test_inline_malformed_json_keeps_original_response(service):
    content = '<tool_call>{"name": "write_file", broken}<｜tool▁calls▁end｜>'
    chunk = SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))
    result = service.process_stream_chunk(chunk, "", [])
    assert result[0] == content
    assert result[1] == []


@pytest.mark.asyncio
async def test_write_file_object_content_remains_invalid(parse_tool_call):
    arguments = parse_tool_call(
        "write_file", {"file_path": "example.json", "write_blocks": {"key": "value"}}
    )
    handler = get_file_write_or_edit_tool_handler(Mock())
    with pytest.raises(TypeError, match="write_blocks must be a string"):
        await handler(**arguments)
