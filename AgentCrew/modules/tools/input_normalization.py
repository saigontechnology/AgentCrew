"""Conservative, schema-guided repair of stringified tool input containers.

This is not validation: native values remain available for the authoritative
whole-input validator. Only decoded strings must validate before replacement.
Unresolved references and unsupported/over-budget traversal leave input intact.
"""

import ast
import json
import re
from itertools import product
from typing import Any

from jsonschema.validators import validator_for

from AgentCrew.modules.tools.input_validation import validate_tool_input

_MAX_DEPTH = 32
_MAX_WORK = 4096
_MAX_BRANCHES = 64
_COMBINATORS = ("allOf", "anyOf", "oneOf")
_REFERENCES = {"$ref", "$dynamicRef", "$recursiveRef"}
_CONDITIONALS = {"if", "then", "else", "dependentSchemas"}


class _CannotNormalize(Exception):
    """The schema or input exceeds the repairer's supported traversal."""


def normalize_tool_input(value: Any, schema: Any) -> Any:
    """Return a repaired copy without modifying the input or registered schema.

    Container branches take precedence over string branches, but a decoded
    candidate must satisfy both its branch and the complete target node schema.
    Empty/untyped schemas provide no evidence to decode a string. References
    must already be flattened by the tool provider; repair never resolves URLs.
    """
    if not isinstance(schema, dict):
        return value
    try:
        normalizer = _SchemaNormalizer(schema)
        normalizer.check_schema(schema)
        return normalizer.normalize(value, schema, 0)
    except Exception:
        return value


class _SchemaNormalizer:
    """Per-call traversal budget and schema dialect; never shared by streams."""

    def __init__(self, schema: dict[str, Any]):
        validator = (
            validator_for(schema, default=None)
            if "$schema" in schema
            else validator_for(schema)
        )
        if validator is None or "allOf" not in validator.VALIDATORS:
            raise _CannotNormalize
        self.keywords = validator.VALIDATORS
        self.dialect = schema.get("$schema")
        self.remaining = _MAX_WORK

    def step(self, depth: int) -> None:
        self.remaining -= 1
        if depth > _MAX_DEPTH or self.remaining < 0:
            raise _CannotNormalize

    def check_schema(self, schema: dict[str, Any]) -> None:
        def scan(node: Any, depth: int) -> None:
            self.step(depth)
            if isinstance(node, dict):
                if _REFERENCES.intersection(node):
                    raise _CannotNormalize
                for child in node.values():
                    scan(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    scan(child, depth + 1)

        scan(schema, 0)
        result = validate_tool_input(None, schema)
        if any(issue.validator == "schema" for issue in result.issues):
            raise _CannotNormalize

    def combined(self, schemas: list[Any]) -> dict[str, Any]:
        schema = {"allOf": schemas}
        if self.dialect:
            schema["$schema"] = self.dialect
        return schema

    def matches(self, value: Any, schema: Any) -> bool:
        self.step(0)
        return validate_tool_input(value, self.combined([schema])).valid

    def paths(self, schema: Any, depth: int) -> list[list[Any]]:
        self.step(depth)
        if not isinstance(schema, dict):
            return [[schema]]
        if _CONDITIONALS.intersection(schema):
            raise _CannotNormalize
        base = {key: value for key, value in schema.items() if key not in _COMBINATORS}
        paths = [[base]]
        for keyword in _COMBINATORS:
            if keyword not in schema:
                continue
            if keyword == "allOf":
                groups = [self.paths(branch, depth + 1) for branch in schema[keyword]]
            else:
                groups = [
                    [
                        path
                        for branch in schema[keyword]
                        for path in self.paths(branch, depth + 1)
                    ]
                ]
            for group in groups:
                if len(paths) * len(group) > _MAX_BRANCHES:
                    raise _CannotNormalize
                paths = [left + right for left, right in product(paths, group)]
        return paths

    def compatible(self, value: Any, path: list[Any]) -> bool:
        kind = "object" if isinstance(value, dict) else "array"
        evidence = False
        for schema in path:
            if schema is False:
                return False
            if not isinstance(schema, dict):
                continue
            declared = schema.get("type")
            if declared is not None:
                types = [declared] if isinstance(declared, str) else declared
                if kind not in types:
                    return False
                evidence = True
            structural = (
                ("properties", "patternProperties", "additionalProperties")
                if kind == "object"
                else ("items", "prefixItems")
            )
            evidence = evidence or any(
                key in schema and key in self.keywords for key in structural
            )
        return evidence

    def child_schema(self, path: list[Any], key: str | int, is_object: bool) -> Any:
        schemas = []
        for schema in path:
            if not isinstance(schema, dict):
                continue
            if is_object:
                matched = False
                properties = schema.get("properties", {})
                if key in properties:
                    schemas.append(properties[key])
                    matched = True
                for pattern, child in schema.get("patternProperties", {}).items():
                    if re.search(pattern, key):
                        schemas.append(child)
                        matched = True
                if not matched and "additionalProperties" in schema:
                    schemas.append(schema["additionalProperties"])
            else:
                prefix = (
                    schema.get("prefixItems", [])
                    if "prefixItems" in self.keywords
                    else []
                )
                items = schema.get("items")
                if key < len(prefix):
                    schemas.append(prefix[key])
                elif isinstance(items, list):
                    if key < len(items):
                        schemas.append(items[key])
                    elif "additionalItems" in schema:
                        schemas.append(schema["additionalItems"])
                elif isinstance(items, (dict, bool)):
                    schemas.append(items)
        return self.combined(schemas) if schemas else None

    def normalize(self, value: Any, schema: Any, depth: int) -> Any:
        self.step(depth)
        if not isinstance(schema, (dict, bool)):
            return value
        source_string = isinstance(value, str)
        candidate = _try_parse_container_literal(value) if source_string else value
        if not isinstance(candidate, (dict, list)):
            return value
        fallback = value
        found_fallback = False
        paths = self.paths(schema, depth)
        for path in paths:
            if not self.compatible(candidate, path):
                continue
            is_object = isinstance(candidate, dict)
            entries = candidate.items() if is_object else enumerate(candidate)
            children = {}
            for key, child in entries:
                child_schema = self.child_schema(path, key, is_object)
                children[key] = self.normalize(child, child_schema, depth + 1)
            normalized = children if is_object else list(children.values())
            if not source_string and not found_fallback:
                fallback = normalized
                found_fallback = True
            if not source_string and len(paths) == 1:
                return normalized
            if self.matches(normalized, self.combined(path)) and self.matches(
                normalized, schema
            ):
                return normalized
        return fallback


def _try_parse_container_literal(value: str) -> Any:
    stripped = value.strip()
    if not stripped.startswith(("[", "{")):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, (list, dict)) else None
