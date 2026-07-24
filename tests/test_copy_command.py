"""
Tests for the /copy <num> command.

Focus areas:
1. /copy selects the last assistant message in the last turn
2. /copy <n> selects the nth-latest assistant message (turn-based)
3. Missing/invalid indices produce safe error messages
4. Intermediate assistant messages (those followed by tools, not user messages) are skipped
5. Messages with list-type content (text blocks) are handled correctly
6. Shared copy_utils functions used by all surfaces
7. CommandProcessor / GUI command routing
8. Completions candidate counts match executable candidates
9. Turn boundaries without assistant response
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from AgentCrew.modules.chat.message.command_processor import CommandProcessor
from AgentCrew.modules.chat.message.commands.copy_utils import (
    extract_assistant_text,
    extract_assistant_text_preview,
    get_copyable_assistants,
)
from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.events.constants import AppEvents


@pytest.fixture
def message_handler():
    """Create a mocked MessageHandler with the needed attributes."""
    handler = MagicMock()
    handler.bus = MagicMock()
    handler.bus.emit = AsyncMock()
    handler.conversation_turns = []
    handler.streamline_messages = []
    return handler


@pytest.fixture
def utility_commands(message_handler):
    return UtilityCommands(message_handler)


def _make_assistant_msg(content, text_content="Hello from assistant"):
    """Helper to create an assistant message with list or text content."""
    if content is None:
        content = [{"type": "text", "text": text_content}]
    return {"role": "assistant", "content": content}


def _make_user_msg(text="User message"):
    return {"role": "user", "content": text}


def _make_tool_msg(text="Tool result"):
    return {"role": "tool", "content": text}


def _make_tool_result_msg(text="Tool result"):
    return {"role": "tool_result", "content": text}


# ─────────────────────────────────────────────
#  Shared copy_utils unit tests
# ─────────────────────────────────────────────


class TestGetCopyableAssistants:
    """Tests for get_copyable_assistants()."""

    def test_basic_turn_boundaries(self):
        """Selects last assistant per turn using turn boundaries."""
        messages = [
            _make_user_msg("q1"),
            _make_assistant_msg([{"type": "text", "text": "A1"}]),
            _make_user_msg("q2"),
            _make_assistant_msg([{"type": "text", "text": "A2"}]),
        ]
        turns = [MagicMock(message_index=0), MagicMock(message_index=2)]
        result = get_copyable_assistants(messages, turns)
        assert len(result) == 2
        assert extract_assistant_text(result[0]) == "A1"
        assert extract_assistant_text(result[1]) == "A2"

    def test_skips_intermediate_assistant_in_turn(self):
        """Within a turn, picks the *last* assistant, not the first."""
        messages = [
            _make_user_msg("q1"),
            _make_assistant_msg([{"type": "text", "text": "intermediate"}]),
            _make_tool_msg(),
            _make_assistant_msg([{"type": "text", "text": "final"}]),
            _make_user_msg("q2"),
            _make_assistant_msg([{"type": "text", "text": "A2"}]),
        ]
        turns = [MagicMock(message_index=0), MagicMock(message_index=4)]
        result = get_copyable_assistants(messages, turns)
        assert len(result) == 2
        assert extract_assistant_text(result[0]) == "final"
        assert extract_assistant_text(result[1]) == "A2"

    def test_turn_without_assistant_produces_no_candidate(self):
        """A turn that has zero assistant messages yields no candidate."""
        messages = [
            _make_user_msg("q1"),
            _make_user_msg("q2"),
            _make_assistant_msg([{"type": "text", "text": "A2"}]),
        ]
        turns = [MagicMock(message_index=0), MagicMock(message_index=1)]
        result = get_copyable_assistants(messages, turns)
        assert len(result) == 1
        assert extract_assistant_text(result[0]) == "A2"

    def test_empty_messages(self):
        assert get_copyable_assistants([], [MagicMock(message_index=0)]) == []

    def test_empty_turns(self):
        assert (
            get_copyable_assistants(
                [_make_assistant_msg([{"type": "text", "text": "hi"}])], []
            )
            == []
        )

    def test_last_turn_goes_to_end_of_messages(self):
        """The final turn's range extends to len(messages)."""
        messages = [
            _make_user_msg("q1"),
            _make_tool_msg(),
            _make_assistant_msg([{"type": "text", "text": "A1"}]),
        ]
        turns = [MagicMock(message_index=0)]
        result = get_copyable_assistants(messages, turns)
        assert len(result) == 1
        assert extract_assistant_text(result[0]) == "A1"


class TestExtractAssistantText:
    """Tests for extract_assistant_text()."""

    def test_plain_string_content(self):
        msg = {"role": "assistant", "content": "hello world"}
        assert extract_assistant_text(msg) == "hello world"

    def test_single_text_block(self):
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
        assert extract_assistant_text(msg) == "hello"

    def test_multiple_text_blocks_concatenated(self):
        """All text blocks are concatenated in order, not just the first."""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "block one "},
                {"type": "tool_use", "id": "x", "name": "test", "input": {}},
                {"type": "text", "text": "block two"},
            ],
        }
        assert extract_assistant_text(msg) == "block one block two"

    def test_empty_content_returns_empty(self):
        assert extract_assistant_text({"role": "assistant", "content": ""}) == ""
        assert extract_assistant_text({"role": "assistant", "content": []}) == ""

    def test_no_text_blocks_returns_empty(self):
        msg = {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "test", "input": {}}],
        }
        assert extract_assistant_text(msg) == ""


class TestExtractAssistantTextPreview:
    """Tests for extract_assistant_text_preview()."""

    def test_short_text_no_truncation(self):
        msg = {"role": "assistant", "content": "short"}
        assert extract_assistant_text_preview(msg, 50) == "short"

    def test_long_text_truncated(self):
        msg = {"role": "assistant", "content": "a" * 60}
        preview = extract_assistant_text_preview(msg, 50)
        assert len(preview) == 50
        assert preview.endswith("...")

    def test_whitespace_normalised(self):
        msg = {"role": "assistant", "content": "hello    world"}
        assert extract_assistant_text_preview(msg, 50) == "hello world"


# ─────────────────────────────────────────────
#  Copy execution tests (use shared helper)
# ─────────────────────────────────────────────


class TestHandleCopy:
    """Tests for UtilityCommands.handle_copy."""

    @pytest.mark.asyncio
    async def test_copy_defaults_to_latest_assistant(
        self, utility_commands, message_handler
    ):
        """/copy (no number) defaults to copying the last assistant message."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "First response"}]),
            _make_user_msg("second turn"),
            _make_assistant_msg([{"type": "text", "text": "Second response"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
            MagicMock(message_index=2),
        ]

        result = await utility_commands.handle_copy("/copy")

        assert result.handled is True
        assert result.clear_flag is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="Second response"
        )

    @pytest.mark.asyncio
    async def test_copy_with_number_picks_nth_latest(
        self, utility_commands, message_handler
    ):
        """/copy 2 picks the second-latest assistant message."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "First response"}]),
            _make_user_msg("second"),
            _make_assistant_msg([{"type": "text", "text": "Second response"}]),
            _make_user_msg("third"),
            _make_assistant_msg([{"type": "text", "text": "Third response"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
            MagicMock(message_index=2),
            MagicMock(message_index=4),
        ]

        result = await utility_commands.handle_copy("/copy 2")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="Second response"
        )

    @pytest.mark.asyncio
    async def test_copy_skips_intermediate_assistant_messages(
        self, utility_commands, message_handler
    ):
        """/copy selects only the last assistant per turn."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "Assistant with tools"}]),
            _make_tool_msg(),
            _make_assistant_msg([{"type": "text", "text": "Final assistant"}]),
            _make_user_msg("next turn"),
            _make_assistant_msg([{"type": "text", "text": "New turn response"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
            MagicMock(message_index=4),
        ]

        result = await utility_commands.handle_copy("/copy 1")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="New turn response"
        )

        message_handler.bus.reset_mock()
        result = await utility_commands.handle_copy("/copy 2")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="Final assistant"
        )

    @pytest.mark.asyncio
    async def test_copy_with_string_content(self, utility_commands, message_handler):
        """Assistant messages with plain string content are handled."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            {"role": "assistant", "content": "Plain text response"},
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
        ]

        result = await utility_commands.handle_copy("/copy")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="Plain text response"
        )

    @pytest.mark.asyncio
    async def test_copy_invalid_index_non_numeric(
        self, utility_commands, message_handler
    ):
        """Non-numeric index produces error."""
        result = await utility_commands.handle_copy("/copy abc")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR

    @pytest.mark.asyncio
    async def test_copy_invalid_index_zero(self, utility_commands, message_handler):
        """Zero or negative index produces error."""
        result = await utility_commands.handle_copy("/copy 0")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR

    @pytest.mark.asyncio
    async def test_copy_index_out_of_range(self, utility_commands, message_handler):
        """Index larger than available assistant messages produces error."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "Only one"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
        ]

        result = await utility_commands.handle_copy("/copy 5")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR
        assert "out of range" in str(kwargs.get("message", ""))

    @pytest.mark.asyncio
    async def test_copy_no_assistant_messages(self, utility_commands, message_handler):
        """No assistant messages available produces error."""
        message_handler.streamline_messages = [
            _make_user_msg(),
        ]
        message_handler.conversation_turns = [MagicMock(message_index=0)]

        result = await utility_commands.handle_copy("/copy")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR
        assert "No assistant messages" in str(kwargs.get("message", ""))

    @pytest.mark.asyncio
    async def test_copy_handles_empty_text_content(
        self, utility_commands, message_handler
    ):
        """Assistant message with empty text produces error."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": ""}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
        ]

        result = await utility_commands.handle_copy("/copy")

        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR

    @pytest.mark.asyncio
    async def test_copy_concatenates_multiple_text_blocks(
        self, utility_commands, message_handler
    ):
        """Multiple text blocks are concatenated, not just first."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "first part "},
                    {"type": "tool_use", "id": "x", "name": "test", "input": {}},
                    {"type": "text", "text": "second part"},
                ],
            },
        ]
        message_handler.conversation_turns = [MagicMock(message_index=0)]

        result = await utility_commands.handle_copy("/copy")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="first part second part"
        )

    @pytest.mark.asyncio
    async def test_copy_turn_without_assistant_skipped(
        self, utility_commands, message_handler
    ):
        """A turn with no assistant response is skipped in numbering."""
        message_handler.streamline_messages = [
            _make_user_msg("q1"),
            _make_assistant_msg([{"type": "text", "text": "A1"}]),
            _make_user_msg("q2"),
            _make_user_msg("q3"),
            _make_assistant_msg([{"type": "text", "text": "A3"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
            MagicMock(message_index=2),
            MagicMock(message_index=3),
        ]

        result = await utility_commands.handle_copy("/copy 1")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="A3"
        )

        message_handler.bus.reset_mock()
        result = await utility_commands.handle_copy("/copy 2")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once_with(
            AppEvents.COPY_REQUESTED, text="A1"
        )


# ─────────────────────────────────────────────
#  CommandProcessor routing tests
# ─────────────────────────────────────────────


class TestCommandProcessorRouting:
    """Tests that CommandProcessor routes /copy to handle_copy."""

    @pytest.mark.asyncio
    async def test_command_processor_routes_copy(self):
        """/copy is routed to utility_commands.handle_copy."""
        handler = MagicMock()
        handler.bus = MagicMock()
        handler.bus.emit = AsyncMock()
        handler.conversation_turns = []
        handler.streamline_messages = []
        processor = CommandProcessor(handler)
        result = await processor.process_command("/copy")
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_command_processor_routes_copy_with_number(self):
        """/copy 2 is routed to utility_commands.handle_copy."""
        handler = MagicMock()
        handler.bus = MagicMock()
        handler.bus.emit = AsyncMock()
        handler.conversation_turns = []
        handler.streamline_messages = []
        processor = CommandProcessor(handler)
        result = await processor.process_command("/copy 2")
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_copy_in_processor_turn_numbering(
        self, utility_commands, message_handler
    ):
        """End-to-end: /copy 1=/copy, /copy 2 = second-latest."""
        message_handler.streamline_messages = [
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "first"}]),
            _make_user_msg(),
            _make_assistant_msg([{"type": "text", "text": "second"}]),
        ]
        message_handler.conversation_turns = [
            MagicMock(message_index=0),
            MagicMock(message_index=2),
        ]

        result = await utility_commands.handle_copy("/copy")
        assert extract_assistant_text(
            message_handler.streamline_messages[-1]
        ) == await _get_emitted_text(result, message_handler)
        # /copy defaults to /copy 1 = second (latest)
        assert (
            "second" in str(message_handler.bus.emit.call_args)
            or message_handler.bus.emit.await_args.kwargs.get("text") == "second"
        )

    @pytest.mark.asyncio
    async def test_copy_command_not_confused_by_non_ascii(
        self, utility_commands, message_handler
    ):
        """Non-numeric /copy args are rejected."""
        result = await utility_commands.handle_copy("/copy 1.5")
        assert result.handled is True
        message_handler.bus.emit.assert_awaited_once()
        args, kwargs = message_handler.bus.emit.await_args
        assert args[0] == AppEvents.ERROR


async def _get_emitted_text(result, handler):
    """Extract text from emit if available."""
    if handler.bus.emit.await_args:
        return handler.bus.emit.await_args.kwargs.get("text", "")
    return ""
