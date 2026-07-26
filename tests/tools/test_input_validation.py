"""Unit tests for :mod:`AgentCrew.modules.tools.input_validation`."""

from __future__ import annotations

import copy

from AgentCrew.modules.tools.input_validation import (
    ToolInputValidationIssue,
    ToolInputValidationResult,
    extract_tool_input_schema,
    validate_tool_input,
)

# ============================================================================
# Schema extraction
# ============================================================================


class TestExtractToolInputSchema:
    def test_openai_function_format(self):
        """OpenAI-style ``{type: function, function: {name, parameters}}``."""
        definition = {
            "type": "function",
            "function": {
                "name": "run_command",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                },
            },
        }
        schema = extract_tool_input_schema(definition)
        assert schema == {"type": "object", "properties": {"cmd": {"type": "string"}}}

    def test_mcp_input_schema_format(self):
        """Anthropic / MCP-style ``{input_schema: ...}``."""
        definition = {
            "name": "my_tool",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        }
        schema = extract_tool_input_schema(definition)
        assert schema == {"type": "object", "properties": {"x": {"type": "integer"}}}

    def test_parameters_only(self):
        """Bare ``{parameters: ...}``."""
        definition = {
            "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}
        }
        schema = extract_tool_input_schema(definition)
        assert schema == {"type": "object", "properties": {"a": {"type": "string"}}}

    def test_fallback_empty_object(self):
        """When no known key is present, fall back to ``{type: object}``."""
        assert extract_tool_input_schema({}) == {"type": "object"}
        assert extract_tool_input_schema({"name": "foo"}) == {"type": "object"}


# ============================================================================
# Validator — valid inputs
# ============================================================================


class TestValidInput:
    def test_valid_object_returns_no_issues(self):
        schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 5, "maximum": 60},
            },
            "required": ["command"],
        }
        result = validate_tool_input({"command": "ls -la", "timeout": 30}, schema)
        assert result.valid is True
        assert result.issues == []

    def test_valid_input_with_no_required_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        result = validate_tool_input({}, schema)
        assert result.valid is True

    def test_valid_input_with_defaults_only(self):
        schema = {
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean"},
            },
        }
        result = validate_tool_input({}, schema)
        assert result.valid is True

    def test_valid_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "guided_answers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        },
                        "required": ["question", "guided_answers"],
                    },
                },
            },
            "required": ["questions"],
        }
        result = validate_tool_input(
            {
                "questions": [
                    {"question": "Q1?", "guided_answers": ["A", "B"]},
                ],
            },
            schema,
        )
        assert result.valid is True

    def test_valid_enum(self):
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
            "required": ["mode"],
        }
        result = validate_tool_input({"mode": "fast"}, schema)
        assert result.valid is True


# ============================================================================
# Validator — invalid inputs
# ============================================================================


class TestMissingRequiredFields:
    def test_single_missing_required(self):
        schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "working_dir": {"type": "string"},
            },
            "required": ["command", "working_dir"],
        }
        result = validate_tool_input({}, schema)
        assert result.valid is False
        paths = {i.path for i in result.issues}
        assert "$.command" in paths
        assert "$.working_dir" in paths
        for issue in result.issues:
            assert issue.message == "required field is missing"
            assert issue.validator == "required"

    def test_missing_field_pointed_correctly(self):
        """Missing 'timeout' field yields $.timeout, not $."""
        schema = {
            "type": "object",
            "properties": {"timeout": {"type": "integer"}},
            "required": ["timeout"],
        }
        result = validate_tool_input({}, schema)
        assert len(result.issues) >= 1
        assert result.issues[0].path == "$.timeout"

    def test_some_required_present_some_missing(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
                "c": {"type": "boolean"},
            },
            "required": ["a", "b", "c"],
        }
        result = validate_tool_input({"a": "ok"}, schema)
        assert result.valid is False
        paths = {i.path for i in result.issues}
        assert "$.b" in paths
        assert "$.c" in paths
        assert "$.a" not in paths


class TestWrongTypes:
    def test_string_received_integer(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = validate_tool_input({"name": 42}, schema)
        assert result.valid is False
        assert any("42" in i.message or "integer" in i.message for i in result.issues)

    def test_integer_received_string(self):
        """No silent coercion: ``\"10\"`` is rejected for an integer field."""
        schema = {
            "type": "object",
            "properties": {"timeout": {"type": "integer"}},
            "required": ["timeout"],
        }
        result = validate_tool_input({"timeout": "10"}, schema)
        assert result.valid is False

    def test_object_received_string(self):
        schema = {
            "type": "object",
            "properties": {"env": {"type": "object"}},
        }
        result = validate_tool_input({"env": "not-an-object"}, schema)
        assert result.valid is False

    def test_array_received_string(self):
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array"}},
        }
        result = validate_tool_input({"items": "not-an-array"}, schema)
        assert result.valid is False

    def test_boolean_received_integer(self):
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
        }
        result = validate_tool_input({"flag": 1}, schema)
        assert result.valid is False

    def test_nested_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                    "required": ["limit"],
                },
            },
            "required": ["options"],
        }
        result = validate_tool_input({"options": {"limit": "not-a-number"}}, schema)
        assert result.valid is False
        assert any("$.options.limit" in i.path for i in result.issues)


class TestRangeConstraints:
    def test_minimum_violation(self):
        schema = {
            "type": "object",
            "properties": {"timeout": {"type": "integer", "minimum": 5}},
        }
        result = validate_tool_input({"timeout": 3}, schema)
        assert result.valid is False

    def test_maximum_violation(self):
        schema = {
            "type": "object",
            "properties": {"timeout": {"type": "integer", "maximum": 60}},
        }
        result = validate_tool_input({"timeout": 120}, schema)
        assert result.valid is False
        assert any("120" in i.message for i in result.issues)

    def test_minimum_and_maximum_both_violated(self):
        schema = {
            "type": "object",
            "properties": {"level": {"type": "number", "minimum": 1, "maximum": 10}},
            "required": ["level"],
        }
        result = validate_tool_input({"level": 0}, schema)
        assert result.valid is False
        result2 = validate_tool_input({"level": 11}, schema)
        assert result2.valid is False

    def test_number_constraints(self):
        schema = {
            "type": "object",
            "properties": {"price": {"type": "number", "minimum": 0.0}},
        }
        result = validate_tool_input({"price": -1.0}, schema)
        assert result.valid is False


class TestStringConstraints:
    def test_min_length(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 2}},
        }
        result = validate_tool_input({"name": "a"}, schema)
        assert result.valid is False

    def test_max_length(self):
        schema = {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 5}},
        }
        result = validate_tool_input({"code": "too-long-code"}, schema)
        assert result.valid is False

    def test_pattern(self):
        schema = {
            "type": "object",
            "properties": {
                "email": {"type": "string", "pattern": "^[a-z]+@example\\.com$"}
            },
        }
        result = validate_tool_input({"email": "not-an-email"}, schema)
        assert result.valid is False


class TestEnumConstraints:
    def test_enum_violation(self):
        schema = {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["auto", "manual"]}},
            "required": ["mode"],
        }
        result = validate_tool_input({"mode": "turbo"}, schema)
        assert result.valid is False

    def test_enum_inside_nested(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": "string", "enum": ["px", "em", "rem"]},
                    },
                    "required": ["unit"],
                },
            },
            "required": ["config"],
        }
        result = validate_tool_input({"config": {"unit": "cm"}}, schema)
        assert result.valid is False
        assert any("$.config.unit" in i.path for i in result.issues)


class TestArrayConstraints:
    def test_minItems(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["tags"],
        }
        result = validate_tool_input({"tags": []}, schema)
        assert result.valid is False

    def test_maxItems(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
            },
        }
        result = validate_tool_input({"items": [1, 2, 3, 4]}, schema)
        assert result.valid is False

    def test_array_item_type_wrong(self):
        schema = {
            "type": "object",
            "properties": {
                "nums": {"type": "array", "items": {"type": "number"}},
            },
        }
        result = validate_tool_input({"nums": [1, "two", 3]}, schema)
        assert result.valid is False


class TestNestedPaths:
    def test_nested_object_path(self):
        schema = {
            "type": "object",
            "properties": {
                "server": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                    },
                    "required": ["host", "port"],
                },
            },
            "required": ["server"],
        }
        result = validate_tool_input({"server": {"host": "localhost"}}, schema)
        assert result.valid is False
        assert any("$.server.port" in i.path for i in result.issues)
        assert "$.server.host" not in {i.path for i in result.issues}

    def test_array_index_in_path(self):
        schema = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "guided_answers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                        },
                        "required": ["question", "guided_answers"],
                    },
                },
            },
            "required": ["questions"],
        }
        result = validate_tool_input(
            {
                "questions": [
                    {
                        "question": "Q1?",
                        "guided_answers": [],
                    },  # guided_answers has minItems: 1
                    {"question": "Q2?"},  # missing guided_answers
                ],
            },
            schema,
        )
        assert result.valid is False
        paths = {i.path for i in result.issues}
        assert "$.questions[0].guided_answers" in paths
        assert "$.questions[1].guided_answers" in paths


class TestAnyOf:
    def test_anyof_flattened(self):
        """anyOf with string vs array produces a clear failure."""
        schema = {
            "type": "object",
            "properties": {
                "write_blocks": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "search": {"type": "string"},
                                    "replace": {"type": "string"},
                                },
                                "required": ["search", "replace"],
                            },
                        },
                    ],
                },
            },
            "required": ["write_blocks"],
        }
        # Passing an integer should fail both alternatives
        result = validate_tool_input({"write_blocks": 42}, schema)
        assert result.valid is False
        messages = " ".join(i.message for i in result.issues)
        assert "anyOf" in messages.lower() or "any" in messages.lower()
        assert "42" in messages


class TestMultipleErrorsAggregation:
    def test_multiple_unrelated_errors_all_returned(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer", "minimum": 0, "maximum": 150},
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["name", "age", "tags"],
        }
        result = validate_tool_input(
            {"name": 42, "age": 200, "tags": []},
            schema,
        )
        assert result.valid is False
        assert len(result.issues) >= 3

    def test_error_ordering_deterministic(self):
        schema = {
            "type": "object",
            "properties": {
                "c": {"type": "string"},
                "a": {"type": "integer"},
                "b": {"type": "boolean"},
            },
            "required": ["a", "b", "c"],
        }
        result1 = validate_tool_input({}, schema)
        result2 = validate_tool_input({}, schema)
        paths1 = [i.path for i in result1.issues]
        paths2 = [i.path for i in result2.issues]
        assert paths1 == paths2


class TestNoMutation:
    def test_input_not_mutated(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
        original = {"x": "not-an-integer"}
        before = copy.deepcopy(original)
        validate_tool_input(original, schema)
        assert original == before

    def test_schema_not_mutated(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        }
        before = copy.deepcopy(schema)
        validate_tool_input({"x": "string"}, schema)
        assert schema == before


class TestInvalidSchema:
    def test_malformed_schema_fails_closed(self):
        """An invalid schema should still produce a validation result, not crash."""
        schema = {
            "type": "object",
            "properties": None,
        }  # properties should be dict or absent
        result = validate_tool_input({"x": 1}, schema)
        assert result.valid is False
        assert any("schema" in i.message.lower() for i in result.issues)

    def test_invalid_schema_has_expected_path(self):
        schema = {"type": 123}  # type must be a string
        result = validate_tool_input({}, schema)
        assert result.valid is False
        paths = {i.path for i in result.issues}
        assert "$" in paths


class TestEdgeCases:
    def test_non_dict_input(self):
        schema = {
            "type": "object",
            "properties": {"key": {"type": "string"}},
        }
        result = validate_tool_input("not-a-dict", schema)
        assert result.valid is False

    def test_none_input(self):
        schema = {
            "type": "object",
            "properties": {"key": {"type": "string"}},
        }
        result = validate_tool_input(None, schema)
        assert result.valid is False

    def test_empty_schema(self):
        schema = {"type": "object", "properties": {}}
        result = validate_tool_input({"anything": "goes"}, schema)
        assert result.valid is True

    def test_no_type_in_schema(self):
        schema = {"properties": {"x": {"type": "string"}}}
        result = validate_tool_input({"x": "hello"}, schema)
        # jsonschema treats it as untyped — should pass by default
        assert result.valid is True


# ============================================================================
# Dataclass contracts
# ============================================================================


class TestDataclassContracts:
    def test_issue_dataclass(self):
        issue = ToolInputValidationIssue(path="$.x", message="bad", validator="type")
        assert issue.path == "$.x"
        assert issue.message == "bad"
        assert issue.validator == "type"

    def test_result_dataclass_valid(self):
        result = ToolInputValidationResult(valid=True)
        assert result.valid is True
        assert result.issues == []

    def test_result_dataclass_invalid(self):
        issues = [ToolInputValidationIssue(path="$.x", message="bad", validator="type")]
        result = ToolInputValidationResult(valid=False, issues=issues)
        assert result.valid is False
        assert len(result.issues) == 1
