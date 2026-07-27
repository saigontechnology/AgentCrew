"""Centralized pre-confirmation validation of LLM-generated tool-call inputs.

Validates tool input arguments against the registered JSON Schema for each
tool. Collects all errors via ``jsonschema`` ``iter_errors()`` rather than
stopping at the first one, so the LLM receives a complete picture of what
needs fixing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for


@dataclass(frozen=True)
class ToolInputValidationIssue:
    """A single validation issue found in a tool-call input."""

    path: str
    """JSONPath-like path to the field with the issue, e.g. ``$.timeout``."""

    message: str
    """Human-readable description of the issue."""

    validator: str
    """The JSON Schema keyword that triggered the failure (e.g. ``required``,
    ``type``, ``minimum``)."""


@dataclass(frozen=True)
class ToolInputValidationResult:
    """Aggregated result of validating a tool call's input."""

    valid: bool
    issues: list[ToolInputValidationIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema extraction helpers
# ---------------------------------------------------------------------------

SUPPORTED_DEFINITION_KEYS = frozenset({"function", "input_schema", "parameters"})


def extract_tool_input_schema(tool_definition: dict[str, Any]) -> dict[str, Any]:
    """Extract the JSON Schema ``parameters`` / ``input_schema`` sub-dict from a
    tool definition, regardless of the provider-specific wrapper shape.

    Supported shapes:

    * ``{"type": "function", "function": {"name": ..., "parameters": {...}}}``
      — OpenAI / AgentCrew native.
    * ``{"input_schema": {...}}`` — Anthropic / MCP message-based tools.
    * ``{"parameters": {...}}`` — bare schema.
    """
    if "function" in tool_definition:
        return tool_definition["function"].get(
            "parameters",
            {"type": "object"},
        )
    if "input_schema" in tool_definition:
        return tool_definition["input_schema"]
    return tool_definition.get("parameters", {"type": "object"})


# ---------------------------------------------------------------------------
# Shared error-text formatting
# ---------------------------------------------------------------------------


BUILTIN_ERROR_TPL = (
    "{header}\n"
    "{issues}\n"
    "\n"
    "The tool was not shown for confirmation and was not executed.\n"
    "Correct the arguments and call the tool again."
)


def format_validation_error_text(
    tool_name: str,
    issues: list[ToolInputValidationIssue],
) -> str:
    """Build the standard LLM-readable error text for a validation failure.

    Parameters
    ----------
    tool_name:
        Name of the tool that failed validation.
    issues:
        All validation issues discovered.

    Returns
    -------
    str
        Formatted error text ready to be used as a tool result.
    """
    error_count = len(issues)
    plural = "s" if error_count != 1 else ""
    header = f"Tool input validation failed for `{tool_name}` with {error_count} error{plural}:"
    issue_lines = "\n".join(f"- {i.path}: {i.message}" for i in issues)
    return BUILTIN_ERROR_TPL.format(header=header, issues=issue_lines)


def format_unknown_tool_error_text(tool_name: str) -> str:
    """Build the standard LLM-readable error text for an unknown tool."""
    return (
        f"Tool input validation failed for `{tool_name}`:\n"
        f"- $: tool is not registered for the current agent\n\n"
        f"The tool was not shown for confirmation and was not executed.\n"
        f"Correct the arguments and call the tool again."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_tool_input(
    tool_input: Any,
    input_schema: dict[str, Any],
) -> ToolInputValidationResult:
    """Validate *tool_input* against the JSON Schema *input_schema*.

    Collects **all** validation failures (not just the first one), sorts them
    deterministically, and deduplicates identical path/message pairs.  The
    input is never mutated or coerced.

    Returns
    -------
    ToolInputValidationResult
        ``.valid`` is ``True`` when no issues were found.
    """
    validator_cls = validator_for(input_schema)

    # --- Schema validity check (fail closed) --------------------------------
    try:
        validator_cls.check_schema(input_schema)
    except SchemaError as exc:
        return ToolInputValidationResult(
            valid=False,
            issues=[
                ToolInputValidationIssue(
                    path="$",
                    message=f"Registered tool schema is invalid: {exc.message}",
                    validator="schema",
                )
            ],
        )

    # --- Validate input -----------------------------------------------------
    validator = validator_cls(input_schema)
    raw_errors: list[ValidationError] = sorted(
        validator.iter_errors(tool_input),
        key=_error_sort_key,
    )

    issues: list[ToolInputValidationIssue] = []
    seen: set[tuple[str, str]] = set()

    for error in raw_errors:
        converted = _validation_error_to_issues(error)
        for issue in converted:
            key = (issue.path, issue.message)
            if key not in seen:
                seen.add(key)
                issues.append(issue)

    return ToolInputValidationResult(valid=not bool(issues), issues=issues)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _error_sort_key(
    error: ValidationError,
) -> tuple[tuple[str | int, ...], tuple[str | int, ...]]:
    """Deterministic sort key: instance path first, then schema path."""
    return (
        tuple(str(p) for p in error.absolute_path),
        tuple(str(p) for p in error.absolute_schema_path),
    )


def _json_path(error: ValidationError) -> str:
    """Render a ``ValidationError``'s absolute instance path as a
    JSONPath-like string.

    Examples
    --------
    * ``$``  (root)
    * ``$.timeout``
    * ``$.questions[0].guided_answers``
    """
    parts = ["$"]
    for segment in error.absolute_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}")
    return "".join(parts)


def _validation_error_to_issues(
    error: ValidationError,
) -> list[ToolInputValidationIssue]:
    """Convert a single ``ValidationError`` into one or more concise issues.

    Most errors produce one issue.  ``anyOf`` / ``oneOf`` combinator errors
    may produce multiple flattened leaf issues.
    """
    path = _json_path(error)
    validator = error.validator or "unknown"

    # --- Improve ``required`` errors to identify the missing field ----------
    if validator == "required" and isinstance(error.message, str):
        # jsonschema message: "'working_dir' is a required property"
        # We want:  $.working_dir: required field is missing
        missing_field = _extract_required_field(error.message)
        if missing_field:
            return [
                ToolInputValidationIssue(
                    path=f"{path}.{missing_field}",
                    message="required field is missing",
                    validator="required",
                )
            ]
        # Fallback: just clean up the message
        return [
            ToolInputValidationIssue(
                path=path,
                message=error.message,
                validator="required",
            )
        ]

    # --- Flatten ``anyOf`` / ``oneOf`` combinator errors --------------------
    if validator in ("anyOf", "oneOf"):
        issues: list[ToolInputValidationIssue] = []

        # Top-level combinator failure
        if validator == "anyOf":
            issues.append(
                ToolInputValidationIssue(
                    path=path,
                    message="value does not match any allowed schema",
                    validator="anyOf",
                )
            )
        else:
            issues.append(
                ToolInputValidationIssue(
                    path=path,
                    message="value does not match exactly one allowed schema",
                    validator="oneOf",
                )
            )

        # Flatten relevant leaf context for each sub-schema that failed
        for ctx in getattr(error, "context", []):
            ctx_issues = _flatten_context_error(ctx, max_depth=1)
            issues.extend(ctx_issues)

        return issues

    # --- Default: a single issue with the original message ------------------
    return [
        ToolInputValidationIssue(
            path=path,
            message=error.message,
            validator=validator,
        )
    ]


def _extract_required_field(message: str) -> str | None:
    """Try to extract the missing field name from a jsonschema ``required``
    error message.

    Handles messages like::

        ``'working_dir' is a required property``
    """
    for quote_char in ("'", '"'):
        if message.startswith(quote_char):
            end = message.find(quote_char, 1)
            if end > 0:
                return message[1:end]
    return None


def _flatten_context_error(
    ctx_error: ValidationError,
    max_depth: int = 1,
) -> list[ToolInputValidationIssue]:
    """Flatten a nested ``ValidationError`` (from e.g. ``anyOf`` context) into
    a list of concise issues, limiting recursion depth to *max_depth*.
    """
    if max_depth <= 0:
        return []

    path = _json_path(ctx_error)
    validator = ctx_error.validator or "unknown"

    issues: list[ToolInputValidationIssue] = []

    # Improve ``required`` inside context too
    if validator == "required":
        missing_field = _extract_required_field(ctx_error.message)
        if missing_field:
            issues.append(
                ToolInputValidationIssue(
                    path=f"{path}.{missing_field}",
                    message="required field is missing",
                    validator="required",
                )
            )
            return issues

    issues.append(
        ToolInputValidationIssue(
            path=path,
            message=ctx_error.message,
            validator=validator,
        )
    )

    # Add nested context for combinators within this context
    for child in getattr(ctx_error, "context", []):
        child_issues = _flatten_context_error(child, max_depth - 1)
        issues.extend(child_issues)

    return issues
