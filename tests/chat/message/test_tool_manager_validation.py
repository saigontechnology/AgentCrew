"""Integration tests for tool input validation in :class:`ToolManager`."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AgentCrew.modules.chat.message.tool_manager import ToolManager
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.tools.parallel_executor import ToolResult

pytestmark = pytest.mark.asyncio


# ============================================================================
# Helpers
# ============================================================================


def _make_tool_use(
    name: str,
    input_: dict[str, Any] | None = None,
    tool_id: str = "call_001",
) -> dict[str, Any]:
    return {
        "id": tool_id,
        "name": name,
        "input": input_ or {},
        "type": "tool_call",
    }


def _run_command_schema() -> dict[str, Any]:
    """Simplified run_command tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds",
                        "minimum": 5,
                        "maximum": 60,
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory",
                    },
                },
                "required": ["command", "working_dir"],
            },
        },
    }


def _ask_schema() -> dict[str, Any]:
    """Simplified ask tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "ask",
            "description": "Ask the user a question",
            "parameters": {
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
                        "minItems": 1,
                        "maxItems": 10,
                    },
                },
                "required": ["questions"],
            },
        },
    }


def create_mock_agent(tool_definitions: dict[str, Any] | None = None):
    """Create a mock agent with tool definitions."""
    from AgentCrew.modules.tools.input_validation import (
        extract_tool_input_schema,
        format_unknown_tool_error_text,
        format_validation_error_text,
        validate_tool_input,
    )

    agent = MagicMock()
    agent.name = "test_agent"

    def get_tool_definition(name: str) -> dict[str, Any] | None:
        if tool_definitions and name in tool_definitions:
            return tool_definitions[name]
        return None

    def validate_tool_use(tool_use: dict[str, Any]) -> str | None:
        tool_name = tool_use.get("name", "")
        tool_def = get_tool_definition(tool_name)
        if tool_def is None:
            return format_unknown_tool_error_text(tool_name)
        input_schema = extract_tool_input_schema(tool_def)
        tool_input = tool_use.get("input", {})
        result = validate_tool_input(tool_input, input_schema)
        if result.valid:
            return None
        return format_validation_error_text(tool_name, result.issues)

    agent.get_tool_definition = get_tool_definition
    agent.validate_tool_use = validate_tool_use
    agent.format_message = MagicMock(return_value={"role": "tool", "content": "mocked"})
    agent.execute_tool_call = AsyncMock(return_value="executed")
    return agent


def create_mock_message_handler(agent=None):
    """Create a mock message handler."""
    handler = MagicMock()
    handler.agent = agent or create_mock_agent(
        {"run_command": _run_command_schema(), "ask": _ask_schema()}
    )
    handler._messages_append = MagicMock()
    handler.persistent_service = None
    return handler


@pytest.fixture
def tool_manager():
    """Create a clean ToolManager with a mock handler and agent."""
    agent = create_mock_agent(
        {"run_command": _run_command_schema(), "ask": _ask_schema()}
    )
    handler = create_mock_message_handler(agent)
    tm = ToolManager(handler)
    # Prevent waiting for user input by not running actual confirmation
    tm._wait_for_tool_confirmation = AsyncMock(return_value={"action": "approve"})
    return tm


@pytest.fixture
def tool_manager_no_confirm():
    """ToolManager in YOLO mode so no confirmation is needed."""
    agent = create_mock_agent(
        {"run_command": _run_command_schema(), "ask": _ask_schema()}
    )
    handler = create_mock_message_handler(agent)
    tm = ToolManager(handler)
    tm.yolo_mode = True
    return tm


# ============================================================================
# Sequential tool validation
# ============================================================================


class TestSequentialValidation:
    """Invalid sequential tools must NOT request confirmation, emit TOOL_USE,
    or call the handler."""

    async def test_valid_sequential_proceeds_to_confirmation(self, tool_manager):
        """A valid tool call should still reach the confirmation flow."""
        tool_use = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
        await tool_manager.execute_tool(tool_use)
        # Should have called _wait_for_tool_confirmation
        assert tool_manager._wait_for_tool_confirmation.called

    async def test_invalid_sequential_skips_confirmation(self, tool_manager):
        """An invalid tool call must NOT request confirmation."""
        tool_use = _make_tool_use(
            "run_command", {"command": "ls"}
        )  # missing working_dir
        await tool_manager.execute_tool(tool_use)
        assert not tool_manager._wait_for_tool_confirmation.called

    async def test_invalid_sequential_records_error_result(self, tool_manager):
        """An invalid tool call must record an error ``ToolResult`` via ``_record_tool_result``."""
        tool_use = _make_tool_use("run_command", {"command": "ls"})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True
            assert result.was_executed is False
            assert result.is_rejected is False

    async def test_invalid_sequential_error_mentions_tool_name(self, tool_manager):
        """The error text must mention the tool name."""
        tool_use = _make_tool_use("run_command", {})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            result: ToolResult = mock_record.call_args[0][0]
            assert "run_command" in result.result.lower()

    async def test_invalid_sequential_error_lists_all_failures(self, tool_manager):
        """The error text must list all validation failures."""
        tool_use = _make_tool_use(
            "run_command", {}
        )  # both command and working_dir missing
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            result: ToolResult = mock_record.call_args[0][0]
            assert "command" in result.result
            assert "working_dir" in result.result

    async def test_invalid_sequential_not_executed(self, tool_manager):
        """Handler and TOOL_USE must not be invoked for invalid calls."""
        tool_use = _make_tool_use("run_command", {})
        bus = tool_manager.bus
        with patch.object(bus, "emit") as mock_emit:
            await tool_manager.execute_tool(tool_use)
            # TOOL_USE should NOT be emitted
            tool_use_calls = [
                c for c in mock_emit.mock_calls if AppEvents.TOOL_USE.value in str(c)
            ]
            assert len(tool_use_calls) == 0

    async def test_invalid_ask_does_not_display_questions(self, tool_manager):
        """An invalid ``ask`` call must not prompt the user."""
        tool_use = _make_tool_use("ask", {})  # missing questions
        await tool_manager.execute_tool(tool_use)
        assert not tool_manager._wait_for_tool_confirmation.called

    async def test_invalid_sequential_yolo_still_validates(
        self, tool_manager_no_confirm
    ):
        """Even in YOLO mode, invalid calls must be rejected."""
        tool_use = _make_tool_use("run_command", {})
        with patch.object(
            tool_manager_no_confirm, "_record_tool_result"
        ) as mock_record:
            await tool_manager_no_confirm.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True
            assert result.was_executed is False

    async def test_invalid_auto_approved_still_validates(self, tool_manager):
        """Persistently auto-approved tools still validate."""
        tool_manager._auto_approved_tools.add("run_command")
        tool_use = _make_tool_use("run_command", {})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True


# ============================================================================
# Parallel batch validation
# ============================================================================


class TestParallelBatchValidation:
    async def test_invalid_parallel_skips_confirmation(self, tool_manager):
        """An invalid tool in a parallel batch must not be confirmed."""
        tool_uses = [
            _make_tool_use("run_command", {"command": "ls", "working_dir": "."}),
            _make_tool_use("run_command", {}),  # invalid
        ]
        await tool_manager._execute_parallel_batch(tool_uses)
        # Only the valid tool should have been approved
        # The mock _wait_for_tool_confirmation returns approve
        # But the invalid one should never reach _needs_and_gets_approval
        # which internally calls _wait_for_tool_confirmation
        # Since we mock it, let's check it was called exactly once
        assert tool_manager._wait_for_tool_confirmation.call_count == 1

    async def test_invalid_call_does_not_block_valid_calls(self, tool_manager):
        """An invalid call in a batch must not prevent valid calls from executing."""
        valid = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
        invalid = _make_tool_use("run_command", {})
        with patch.object(
            tool_manager, "_execute_approved_tool", AsyncMock()
        ) as mock_exec:
            await tool_manager._execute_parallel_batch([valid, invalid])
            # Only the valid tool should have been executed
            assert mock_exec.call_count == 1
            executed_tool = mock_exec.call_args[0][0]
            assert executed_tool["name"] == "run_command"

    async def test_all_invalid_batch_returns_early(self, tool_manager):
        """If all tools are invalid, the batch should return without execution."""
        tool_uses = [
            _make_tool_use("run_command", {}),
            _make_tool_use("run_command", {"command": "ls"}),  # missing working_dir
        ]
        with (
            patch.object(
                tool_manager, "_execute_approved_tool", AsyncMock()
            ) as mock_exec,
            patch.object(tool_manager, "_needs_and_gets_approval") as mock_approval,
        ):
            await tool_manager._execute_parallel_batch(tool_uses)
            assert mock_exec.call_count == 0
            assert mock_approval.call_count == 0

    async def test_invalid_parallel_records_error_result(self, tool_manager):
        """Invalid parallel tools must record error results."""
        tool_uses = [
            _make_tool_use("run_command", {}),
        ]
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager._execute_parallel_batch(tool_uses)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True
            assert result.was_executed is False

    async def test_valid_parallel_preserves_behavior(self, tool_manager):
        """Valid parallel tools still execute normally."""
        valid1 = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
        valid2 = _make_tool_use(
            "run_command", {"command": "pwd", "working_dir": "/tmp"}
        )
        with patch.object(
            tool_manager, "_execute_approved_tool", AsyncMock()
        ) as mock_exec:
            await tool_manager._execute_parallel_batch([valid1, valid2])
            assert mock_exec.call_count == 2


# ============================================================================
# Unknown / unregistered tools
# ============================================================================


class TestUnknownTools:
    async def test_unknown_tool_fails_before_confirmation(self, tool_manager):
        """An unknown (unregistered) tool must fail before confirmation."""
        tool_use = _make_tool_use("nonexistent_tool", {"arg": "value"})
        await tool_manager.execute_tool(tool_use)
        assert not tool_manager._wait_for_tool_confirmation.called

    async def test_unknown_tool_error_message(self, tool_manager):
        """Error for unknown tool must mention it's not registered."""
        tool_use = _make_tool_use("made_up_tool", {})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert "not registered" in result.result.lower()
            assert "made_up_tool" in result.result

    async def test_unknown_tool_in_parallel(self, tool_manager):
        """Unknown tools in parallel batches must also be rejected."""
        valid = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
        unknown = _make_tool_use("phantom_tool", {})
        with (
            patch.object(
                tool_manager, "_execute_approved_tool", AsyncMock()
            ) as mock_exec,
            patch.object(tool_manager, "_record_tool_result") as mock_record,
        ):
            await tool_manager._execute_parallel_batch([valid, unknown])
            assert mock_exec.call_count == 1  # valid still executes
            # The unknown tool should have been recorded as error
            error_results = [
                c[0][0]
                for c in mock_record.call_args_list
                if c[0][0].is_error and not c[0][0].was_executed
            ]
            assert len(error_results) >= 1
            assert any("phantom_tool" in r.result for r in error_results)


# ============================================================================
# TOOL_ERROR / TOOL_USE event behavior
# ============================================================================


class TestEvents:
    async def test_invalid_emits_tool_error(self, tool_manager):
        """Invalid input must emit ``TOOL_ERROR`` via ``_record_tool_result``."""
        tool_use = _make_tool_use("run_command", {})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True


# ============================================================================
# execute_tools_batch entry point
# ============================================================================


class TestExecuteToolsBatch:
    async def test_sequential_tool_validated_in_batch(self, tool_manager):
        """``execute_tools_batch`` must validate sequential tools before execution."""
        tool_uses = [
            _make_tool_use("run_command", {}),  # invalid sequential
        ]
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tools_batch(tool_uses)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True
            assert result.was_executed is False

    async def test_parallel_tool_validated_in_batch(self, tool_manager):
        """``execute_tools_batch`` must validate parallel tools before execution."""
        tool_uses = [
            _make_tool_use("run_command", {}),  # invalid parallel
        ]
        tool_manager._needs_and_gets_approval = AsyncMock(return_value="approved")
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tools_batch(tool_uses)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True

    async def test_mixed_valid_invalid_in_batch(self, tool_manager):
        """Valid and invalid tools in the same batch must be handled independently."""
        valid = _make_tool_use("run_command", {"command": "ls", "working_dir": "."})
        invalid = _make_tool_use("run_command", {})
        with (
            patch.object(
                tool_manager, "_execute_approved_tool", AsyncMock()
            ) as mock_exec,
            patch.object(tool_manager, "_record_tool_result") as mock_record,
        ):
            await tool_manager.execute_tools_batch([valid, invalid])
            # Valid should be executed
            assert mock_exec.call_count == 1
            # Invalid should be recorded as error
            error_records = [
                c[0][0] for c in mock_record.call_args_list if c[0][0].is_error
            ]
            assert len(error_records) >= 1


# ============================================================================
# Schema error handling
# ============================================================================


class TestSchemaErrorHandling:
    async def test_invalid_registered_schema_fails_closed(self, tool_manager):
        """A malformed registered schema must produce an error, not a crash."""
        # Override validate_tool_use to simulate a schema-validation error
        from AgentCrew.modules.tools.input_validation import (
            ToolInputValidationIssue,
            format_validation_error_text,
        )

        schema_issues = [
            ToolInputValidationIssue(
                path="$",
                message="Registered tool schema is invalid: 'invalid-type-value' is not valid under any of the given schemas",
                validator="schema",
            )
        ]

        def bad_validation(tool_use):
            return format_validation_error_text(tool_use.get("name", ""), schema_issues)

        tool_manager.message_handler.agent.validate_tool_use = bad_validation
        tool_use = _make_tool_use("bad_tool", {"arg": "val"})
        with patch.object(tool_manager, "_record_tool_result") as mock_record:
            await tool_manager.execute_tool(tool_use)
            assert mock_record.called
            result: ToolResult = mock_record.call_args[0][0]
            assert result.is_error is True
            assert result.was_executed is False
            assert "schema" in result.result.lower()
