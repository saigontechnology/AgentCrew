"""No-network regressions for custom LLM tool argument normalization."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import validate

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.file_editing.tool import (
    get_file_write_or_edit_tool_definition,
    get_file_write_or_edit_tool_handler,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.tools.input_validation import validate_tool_input

OBJECT_SCHEMA = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
    "additionalProperties": False,
}
ARRAY_SCHEMA = {"type": "array", "items": OBJECT_SCHEMA}


@pytest.fixture
def service(monkeypatch):
    instance = object.__new__(CustomLLMService)
    instance._provider_name = "verification"
    instance.model = "test-model"
    instance._is_thinking = False
    instance.current_input_tokens = 0
    instance.current_output_tokens = 0
    instance.tools = [
        get_file_write_or_edit_tool_definition(),
        {
            "type": "function",
            "function": {
                "name": "another_tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "write_blocks": {"type": ["object", "array"]},
                    },
                },
            },
        },
    ]
    instance.tool_handlers = {}
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
            service.process_stream_chunk(
                SimpleNamespace(message=message), "", tool_uses
            )
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


def test_missing_property_schema_is_not_coerced(parse_tool_call):
    arguments = parse_tool_call(
        "write_file",
        {"file_path": "example.json", "write_blocks": "{}", "other": '{"key": 1}'},
    )
    assert arguments["write_blocks"] == "{}"
    assert arguments["other"] == '{"key": 1}'


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
    agent = object.__new__(LocalAgent)
    agent._tool_registrar = Mock()
    agent._tool_registrar.get_tool_definition.return_value = (
        get_file_write_or_edit_tool_definition()
    )
    error = agent.validate_tool_use({"name": "write_file", "input": arguments})
    assert error is not None
    assert "write_blocks" in error
    handler = get_file_write_or_edit_tool_handler(Mock())
    with pytest.raises(TypeError, match="write_blocks must be a string"):
        await handler(**arguments)


@pytest.fixture
def set_field_schema(service):
    def register(schema):
        definition = {
            "type": "function",
            "function": {
                "name": "schema_tool",
                "parameters": {
                    "type": "object",
                    "properties": {"value": schema},
                    "required": ["value"],
                },
            },
        }
        service.register_tool(definition, Mock())
        return definition["function"]["parameters"]

    return register


@pytest.mark.parametrize(
    "schema,value,expected",
    [
        (OBJECT_SCHEMA, '{"key": "value"}', {"key": "value"}),
        (OBJECT_SCHEMA, "{'key': 'value'}", {"key": "value"}),
        (OBJECT_SCHEMA, ' \r\n{"key": 42}\r\n ', ' \r\n{"key": 42}\r\n '),
        (OBJECT_SCHEMA, '{"wrong": "value"}', '{"wrong": "value"}'),
        (ARRAY_SCHEMA, '[{"key": "value"}]', [{"key": "value"}]),
        (ARRAY_SCHEMA, "[{'key': 'value'}]", [{"key": "value"}]),
        (ARRAY_SCHEMA, "[{'key': 42}]", "[{'key': 42}]"),
        (
            ARRAY_SCHEMA,
            ' \r\n[{"wrong": "value"}]\r\n ',
            ' \r\n[{"wrong": "value"}]\r\n ',
        ),
        ({"type": "string"}, '{"key": "value"}', '{"key": "value"}'),
        ({"type": "string"}, '[{"key": "value"}]', '[{"key": "value"}]'),
        ({"type": "array"}, "(1, 2)", "(1, 2)"),
        ({"type": "array"}, "{1, 2}", "{1, 2}"),
    ],
)
def test_schema_gates_container_coercion(
    parse_tool_call, set_field_schema, schema, value, expected
):
    set_field_schema(schema)
    assert parse_tool_call("schema_tool", {"value": value})["value"] == expected


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
@pytest.mark.parametrize("container", [OBJECT_SCHEMA, ARRAY_SCHEMA])
def test_union_prefers_valid_container_over_string(
    parse_tool_call, set_field_schema, keyword, container
):
    schema = set_field_schema({keyword: [{"type": "string"}, container]})
    value = {"key": "value"} if container is OBJECT_SCHEMA else [{"key": "value"}]
    arguments = parse_tool_call("schema_tool", {"value": json.dumps(value)})
    assert arguments["value"] == value
    assert validate_tool_input(arguments, schema).valid


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_union_invalid_container_falls_back_to_exact_string(
    parse_tool_call, set_field_schema, keyword
):
    set_field_schema({keyword: [{"type": "string"}, ARRAY_SCHEMA]})
    text = ' \t\r\n[{"key": 42}]\r\n '
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


def test_one_of_ambiguous_container_is_not_coerced(parse_tool_call, set_field_schema):
    set_field_schema({"oneOf": [{"type": "string"}, OBJECT_SCHEMA, {"type": "object"}]})
    text = '{"key": "value"}'
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", [json.dumps, repr])
async def test_write_file_encoded_valid_blocks_use_array_mode(parse_tool_call, literal):
    blocks = [{"search": "old", "replace": "new"}]
    arguments = parse_tool_call(
        "write_file", {"file_path": "example.txt", "write_blocks": literal(blocks)}
    )
    assert arguments["write_blocks"] == blocks
    file_service = Mock()
    file_service.write_or_edit_file.return_value = {
        "status": "success",
        "file_path": "example.txt",
    }
    await get_file_write_or_edit_tool_handler(file_service)(**arguments)
    file_service.write_or_edit_file.assert_called_once_with(
        file_path="example.txt",
        is_search_replace=True,
        text_or_search_replace_blocks="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
    )


@pytest.mark.parametrize(
    "schema", [None, {}, {"type": "bogus"}, {"type": "object", "properties": {}}]
)
def test_no_usable_schema_preserves_strings(parse_tool_call, service, schema):
    service.tools = [{"type": "function", "function": {"name": "schema_tool"}}]
    if schema is not None:
        service.tools[0]["function"]["parameters"] = schema
    text = ' \r\n{"key": "value"}\r\n '
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


def test_unknown_tool_does_not_coerce(parse_tool_call):
    text = '{"key": "value"}'
    assert parse_tool_call("unknown_tool", {"value": text})["value"] == text


def test_clear_tools_does_not_leave_stale_schema(
    parse_tool_call, set_field_schema, service
):
    set_field_schema(OBJECT_SCHEMA)
    service.clear_tools()
    text = '{"key": "value"}'
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "integer"}, "42"),
        ({"type": "boolean"}, "true"),
        ({"type": "null"}, "null"),
        ({"type": "number"}, "3.14"),
        ({"type": "string"}, '"hello"'),
    ],
)
def test_scalar_strings_are_not_coerced(
    parse_tool_call, set_field_schema, schema, value
):
    set_field_schema(schema)
    assert parse_tool_call("schema_tool", {"value": value})["value"] == value


@pytest.mark.parametrize("encode_outer", [False, True])
@pytest.mark.parametrize("encode_items", [False, True])
def test_recursive_object_properties_and_array_items(
    parse_tool_call, set_field_schema, encode_outer, encode_items
):
    item_schema = {
        "type": "object",
        "properties": {"child": OBJECT_SCHEMA, "text": {"type": "string"}},
        "required": ["child", "text"],
    }
    schema = set_field_schema(
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": item_schema}},
            "required": ["rows"],
        }
    )
    item = {"child": '{"key": "value"}', "text": '{"keep": "as text"}'}
    rows = [json.dumps(item) if encode_items else item]
    value = {"rows": json.dumps(rows)}
    arguments = parse_tool_call(
        "schema_tool", {"value": json.dumps(value) if encode_outer else value}
    )
    assert arguments == {
        "value": {"rows": [{"child": {"key": "value"}, "text": '{"keep": "as text"}'}]}
    }
    assert validate_tool_input(arguments, schema).valid


def test_native_array_recurses_without_requiring_valid_parent(
    parse_tool_call, set_field_schema
):
    set_field_schema({"type": "array", "minItems": 2, "items": OBJECT_SCHEMA})
    assert parse_tool_call("schema_tool", {"value": ['{"key": "value"}']}) == {
        "value": [{"key": "value"}]
    }


def test_all_of_combines_nested_constraints_without_merging_schemas(
    parse_tool_call, set_field_schema
):
    schema = set_field_schema(
        {
            "allOf": [
                {"type": "object", "properties": {"child": {"type": "object"}}},
                {"properties": {"child": OBJECT_SCHEMA}, "required": ["child"]},
            ]
        }
    )
    arguments = parse_tool_call(
        "schema_tool", {"value": '{"child": "{\\"key\\": \\"value\\"}"}'}
    )
    assert arguments == {"value": {"child": {"key": "value"}}}
    assert validate_tool_input(arguments, schema).valid
    invalid = '{"child": "{\\"wrong\\": \\"value\\"}"}'
    assert parse_tool_call("schema_tool", {"value": invalid})["value"] == invalid


def test_recursive_union_branches(parse_tool_call, set_field_schema):
    set_field_schema(
        {
            "anyOf": [
                {"type": "string"},
                {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {
                                "rows": {"type": "array", "items": OBJECT_SCHEMA}
                            },
                        },
                        {"required": ["rows"]},
                    ]
                },
            ]
        }
    )
    value = json.dumps({"rows": json.dumps(['{"key": "value"}'])})
    assert parse_tool_call("schema_tool", {"value": value}) == {
        "value": {"rows": [{"key": "value"}]}
    }


def test_prefix_items_and_remaining_items(parse_tool_call, set_field_schema):
    set_field_schema(
        {
            "type": "array",
            "prefixItems": [OBJECT_SCHEMA, {"type": "string"}],
            "items": ARRAY_SCHEMA,
        }
    )
    value = ['{"key": "value"}', '{"key": "text"}', '[{"key": "tail"}]']
    assert parse_tool_call("schema_tool", {"value": value})["value"] == [
        {"key": "value"},
        '{"key": "text"}',
        [{"key": "tail"}],
    ]


def test_pattern_and_additional_properties(parse_tool_call, set_field_schema):
    set_field_schema(
        {
            "type": "object",
            "properties": {"plain": {"type": "string"}},
            "patternProperties": {"^obj_": OBJECT_SCHEMA},
            "additionalProperties": ARRAY_SCHEMA,
        }
    )
    value = {"plain": "{}", "obj_a": '{"key": "value"}', "other": '[{"key": "tail"}]'}
    assert parse_tool_call("schema_tool", {"value": value})["value"] == {
        "plain": "{}",
        "obj_a": {"key": "value"},
        "other": [{"key": "tail"}],
    }


def test_property_and_pattern_constraints_both_apply(parse_tool_call, set_field_schema):
    set_field_schema(
        {
            "type": "object",
            "properties": {"obj_a": {"type": "object"}},
            "patternProperties": {"^obj_": OBJECT_SCHEMA},
        }
    )
    value = {"obj_a": ' \r\n{"wrong": "value"}\r\n '}
    assert parse_tool_call("schema_tool", {"value": value})["value"] == value


def test_normalization_does_not_mutate_schema_or_native_input(
    service, set_field_schema
):
    schema = set_field_schema(
        {"type": "object", "properties": {"child": OBJECT_SCHEMA}}
    )
    original_schema = deepcopy(schema)
    value = {"value": {"child": '{"key": "value"}'}}
    original = deepcopy(value)
    normalized = service._normalize_tool_input_values(value, tool_name="schema_tool")
    assert normalized == {"value": {"child": {"key": "value"}}}
    assert value == original
    assert schema == original_schema


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema.json"},
        {
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/child"}},
            "$defs": {"child": OBJECT_SCHEMA},
        },
        {"type": "object", "properties": {"key": {"type": "bogus"}}},
        {"if": {"type": "string"}, "then": {"type": "string"}, "else": OBJECT_SCHEMA},
        True,
        False,
    ],
)
def test_uninterpreted_node_preserves_original(
    parse_tool_call, set_field_schema, schema
):
    set_field_schema(schema)
    text = ' \r\n{"child": "{\\"key\\": \\"value\\"}"}\r\n '
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


def test_root_dialect_preserves_legacy_tuple_items(parse_tool_call, service):
    service.register_tool(
        {
            "type": "function",
            "function": {
                "name": "legacy_tool",
                "parameters": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {
                        "rows": {
                            "type": "array",
                            "items": [OBJECT_SCHEMA, {"type": "string"}],
                            "additionalItems": ARRAY_SCHEMA,
                        }
                    },
                },
            },
        },
        Mock(),
    )
    rows = ['{"key": "value"}', '{"key": "text"}', '[{"key": "tail"}]']
    assert parse_tool_call("legacy_tool", {"rows": json.dumps(rows)})["rows"] == [
        {"key": "value"},
        '{"key": "text"}',
        [{"key": "tail"}],
    ]


@pytest.mark.parametrize(
    "dialect",
    [
        "https://example.invalid/custom-dialect",
        "http://json-schema.org/draft-03/schema#",
    ],
)
def test_unsupported_dialect_does_not_guess(parse_tool_call, service, dialect):
    service.tools = [
        {
            "function": {
                "name": "schema_tool",
                "parameters": {
                    "$schema": dialect,
                    "type": "object",
                    "properties": {"value": OBJECT_SCHEMA},
                },
            }
        }
    ]
    value = '{"key": "value"}'
    assert parse_tool_call("schema_tool", {"value": value})["value"] == value


def test_all_of_closed_branches_remain_constraints(parse_tool_call, set_field_schema):
    set_field_schema(
        {
            "allOf": [
                OBJECT_SCHEMA,
                {
                    "type": "object",
                    "properties": {"other": {"type": "string"}},
                    "additionalProperties": False,
                },
            ]
        }
    )
    text = '{"key": "value", "other": "value"}'
    assert parse_tool_call("schema_tool", {"value": text})["value"] == text


def test_boolean_property_schema_does_not_request_coercion(
    parse_tool_call, set_field_schema
):
    set_field_schema(
        {"type": "object", "properties": {"allowed": True, "denied": False}}
    )
    value = {"allowed": "{}", "denied": "[]"}
    assert parse_tool_call("schema_tool", {"value": value})["value"] == value


def test_limits_and_cycles_leave_original_input_intact():
    from AgentCrew.modules.tools.input_normalization import normalize_tool_input

    schema = {"type": "object", "properties": {"value": OBJECT_SCHEMA}}
    cyclic = {"type": "object"}
    cyclic["properties"] = {"child": cyclic}
    assert normalize_tool_input({"value": "{}"}, cyclic) == {"value": "{}"}
    deep = OBJECT_SCHEMA
    for _ in range(40):
        deep = {"type": "object", "properties": {"child": deep}}
    value = {"child": "{}"}
    assert normalize_tool_input(value, deep) is value
    wide = {"allOf": [{"anyOf": [{"type": "object"}, {"type": "object"}]}] * 8}
    value = '{"key": "value"}'
    assert normalize_tool_input(value, wide) == value
    data = {"value": '{"key": "value"}', "extras": list(range(5000))}
    schema["properties"]["extras"] = {"type": "array", "items": {"type": "integer"}}
    assert normalize_tool_input(data, schema) is data


def test_original_eager_coercion_is_not_applied_to_document_strings(parse_tool_call):
    text = '{\n  "key": "value"\n}'
    assert isinstance(json.loads(text), dict)
    arguments = parse_tool_call(
        "write_file", {"file_path": "example.json", "write_blocks": text}
    )
    assert arguments["write_blocks"] == text


def test_interleaved_stream_calls_keep_schema_and_identity_separate(service):
    tool_uses = []
    raw = [
        json.dumps({"file_path": "example.json", "write_blocks": '{"key": "value"}'}),
        json.dumps({"write_blocks": '{"key": "value"}'}),
    ]
    names = ["write_file", "another_tool"]
    for half in (0, 1):
        for index, name in enumerate(names):
            midpoint = len(raw[index]) // 2
            fragment = raw[index][:midpoint] if half == 0 else raw[index][midpoint:]
            service._merge_stream_tool_call_delta(
                tool_uses,
                SimpleNamespace(
                    index=index if half == 0 else None,
                    id=f"call_{index}",
                    function=SimpleNamespace(
                        name=name if half == 0 else None, arguments=fragment
                    ),
                ),
            )
    assert [tool["id"] for tool in tool_uses] == ["call_0", "call_1"]
    assert [tool["name"] for tool in tool_uses] == names
    assert tool_uses[0]["input"]["write_blocks"] == '{"key": "value"}'
    assert tool_uses[1]["input"]["write_blocks"] == {"key": "value"}
    assert [tool["args_json"] for tool in tool_uses] == raw
    assert all(
        tool["type"] == "function" and tool["response"] == "" for tool in tool_uses
    )


def test_missing_tool_name_cannot_select_an_unnamed_schema(service):
    service.tools = [
        {"parameters": {"type": "object", "properties": {"value": OBJECT_SCHEMA}}}
    ]
    value = {"value": '{"key": "value"}'}
    assert service._normalize_tool_input_values(value) is value
