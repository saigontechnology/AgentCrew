"""Tests for A2A inbound file preprocessing in AgentCrewA2AExecutor.

Covers: text-only bypass, direct file processing, file-URI processing,
multiple attachments, and FileHandler failure behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AgentCrew.modules.a2a.agent_executor import AgentCrewA2AExecutor

# -- Helpers -------------------------------------------------------------------


def _make_executor() -> AgentCrewA2AExecutor:
    """Create an executor with mocked agent and session store."""
    agent = MagicMock()
    session_store = AsyncMock()
    session_store.get_history = AsyncMock(return_value=[])
    session_store.get_pending_tools = AsyncMock(return_value=None)
    session_store.append_history = AsyncMock()
    return AgentCrewA2AExecutor(agent=agent, session_store=session_store)


# -- Text-only bypass ----------------------------------------------------------


class TestTextOnlyBypass:
    """Text-only messages should pass through without FileHandler processing."""

    @pytest.mark.anyio
    async def test_text_only_message_unchanged(self):
        executor = _make_executor()
        msg: dict[str, Any] = {
            "role": "user",
            "content": [{"type": "text", "text": "Hello, world!"}],
        }
        result = await executor._process_attachments(msg)
        assert result["content"] == [{"type": "text", "text": "Hello, world!"}]

    @pytest.mark.anyio
    async def test_mixed_text_and_data_unchanged(self):
        executor = _make_executor()
        msg: dict[str, Any] = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this"},
                {"type": "data", "data": {"key": "value"}},
            ],
        }
        result = await executor._process_attachments(msg)
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "data"

    @pytest.mark.anyio
    async def test_empty_content_list_passthrough(self):
        executor = _make_executor()
        msg: dict[str, Any] = {"role": "user", "content": []}
        result = await executor._process_attachments(msg)
        assert result["content"] == []

    @pytest.mark.anyio
    async def test_no_content_key_passthrough(self):
        executor = _make_executor()
        msg: dict[str, Any] = {"role": "user"}
        result = await executor._process_attachments(msg)
        assert "content" not in result


# -- Direct file processing ----------------------------------------------------


class TestDirectFileProcessing:
    """File content items (raw bytes) should be processed through FileHandler."""

    @pytest.mark.anyio
    async def test_raw_file_processed(self):
        executor = _make_executor()
        processed_result = {"type": "text", "text": "Processed content"}

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=processed_result)
            MockFH.return_value = mock_handler

            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this file"},
                    {
                        "type": "file",
                        "file_data": b"binary content",
                        "file_name": "test.pdf",
                        "mime_type": "application/pdf",
                    },
                ],
            }
            result = await executor._process_attachments(msg)

            assert len(result["content"]) == 2
            assert result["content"][0] == {"type": "text", "text": "Look at this file"}
            assert result["content"][1] == processed_result
            mock_handler.async_process_file.assert_called_once()

    @pytest.mark.anyio
    async def test_raw_file_with_empty_data_skipped(self):
        executor = _make_executor()
        msg: dict[str, Any] = {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "file_data": b"",
                    "file_name": "empty.txt",
                    "mime_type": "text/plain",
                },
            ],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert len(result["content"]) == 1
            assert result["content"][0]["type"] == "file"
            mock_handler.async_process_file.assert_not_called()

    @pytest.mark.anyio
    async def test_raw_file_handler_failure_preserves_original(self):
        executor = _make_executor()
        original_item = {
            "type": "file",
            "file_data": b"binary content",
            "file_name": "bad.pdf",
            "mime_type": "application/pdf",
        }
        msg: dict[str, Any] = {
            "role": "user",
            "content": [original_item],
        }

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=None)
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [original_item]


# -- File URI processing -------------------------------------------------------


class TestFileUriProcessing:
    """File URI content items should be resolved and processed through FileHandler."""

    @pytest.mark.anyio
    async def test_local_file_uri_processed(self, tmp_path: Path):
        executor = _make_executor()
        local_file = tmp_path / "test.txt"
        local_file.write_text("hello world")

        processed_result = {"type": "text", "text": "hello world"}
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=processed_result)
            MockFH.return_value = mock_handler

            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "file_uri",
                        "uri": str(local_file),
                        "file_name": "test.txt",
                        "mime_type": "text/plain",
                    },
                ],
            }
            result = await executor._process_attachments(msg)
            assert result["content"] == [processed_result]
            call_arg = mock_handler.async_process_file.call_args[0][0]
            assert str(local_file) in call_arg

    @pytest.mark.anyio
    async def test_file_scheme_uri_processed(self, tmp_path: Path):
        executor = _make_executor()
        local_file = tmp_path / "test.txt"
        local_file.write_text("hello world")

        processed_result = {"type": "text", "text": "hello world"}
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=processed_result)
            MockFH.return_value = mock_handler

            file_uri = local_file.as_uri()
            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "file_uri",
                        "uri": file_uri,
                        "file_name": "test.txt",
                        "mime_type": "text/plain",
                    },
                ],
            }
            result = await executor._process_attachments(msg)
            assert result["content"] == [processed_result]

    @pytest.mark.anyio
    async def test_empty_uri_skipped(self):
        executor = _make_executor()
        msg: dict[str, Any] = {
            "role": "user",
            "content": [
                {
                    "type": "file_uri",
                    "uri": "",
                    "file_name": "empty.txt",
                },
            ],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [
                {"type": "file_uri", "uri": "", "file_name": "empty.txt"}
            ]
            mock_handler.async_process_file.assert_not_called()

    @pytest.mark.anyio
    async def test_unsupported_scheme_preserves_original(self):
        executor = _make_executor()
        original_item = {
            "type": "file_uri",
            "uri": "ftp://example.com/file.pdf",
            "file_name": "file.pdf",
        }
        msg: dict[str, Any] = {
            "role": "user",
            "content": [original_item],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [original_item]

    @pytest.mark.anyio
    async def test_uri_handler_failure_preserves_original(self):
        executor = _make_executor()
        original_item = {
            "type": "file_uri",
            "uri": "/nonexistent/file.pdf",
            "file_name": "file.pdf",
        }
        msg: dict[str, Any] = {
            "role": "user",
            "content": [original_item],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=None)
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [original_item]


# -- Multiple attachments ------------------------------------------------------


class TestMultipleAttachments:
    """Messages with multiple attachments should process each independently."""

    @pytest.mark.anyio
    async def test_multiple_files_all_processed(self):
        executor = _make_executor()
        processed_a = {"type": "text", "text": "File A processed"}
        processed_b = {"type": "text", "text": "File B processed"}
        call_count = 0

        async def fake_process(path: str) -> dict[str, Any] | None:
            nonlocal call_count
            call_count += 1
            return processed_a if call_count == 1 else processed_b

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(side_effect=fake_process)
            MockFH.return_value = mock_handler

            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file_data": b"data a",
                        "file_name": "a.txt",
                    },
                    {
                        "type": "file",
                        "file_data": b"data b",
                        "file_name": "b.txt",
                    },
                ],
            }
            result = await executor._process_attachments(msg)
            assert len(result["content"]) == 2
            assert result["content"][0] == processed_a
            assert result["content"][1] == processed_b
            assert call_count == 2

    @pytest.mark.anyio
    async def test_mixed_text_and_files(self):
        executor = _make_executor()
        processed_file = {"type": "text", "text": "processed"}

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=processed_file)
            MockFH.return_value = mock_handler

            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please analyze this"},
                    {
                        "type": "file",
                        "file_data": b"file bytes",
                        "file_name": "doc.pdf",
                        "mime_type": "application/pdf",
                    },
                    {"type": "text", "text": "and summarize"},
                ],
            }
            result = await executor._process_attachments(msg)
            assert len(result["content"]) == 3
            assert result["content"][0] == {
                "type": "text",
                "text": "Please analyze this",
            }
            assert result["content"][1] == processed_file
            assert result["content"][2] == {"type": "text", "text": "and summarize"}

    @pytest.mark.anyio
    async def test_file_and_file_uri_mixed(self, tmp_path: Path):
        executor = _make_executor()
        local_file = tmp_path / "test.txt"
        local_file.write_text("uri content")

        processed_raw = {"type": "text", "text": "raw processed"}
        processed_uri = {"type": "text", "text": "uri processed"}
        call_count = 0

        async def fake_process(path: str) -> dict[str, Any] | None:
            nonlocal call_count
            call_count += 1
            return processed_raw if call_count == 1 else processed_uri

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(side_effect=fake_process)
            MockFH.return_value = mock_handler

            msg: dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file_data": b"raw bytes",
                        "file_name": "raw.bin",
                    },
                    {
                        "type": "file_uri",
                        "uri": str(local_file),
                        "file_name": "test.txt",
                    },
                ],
            }
            result = await executor._process_attachments(msg)
            assert result["content"][0] == processed_raw
            assert result["content"][1] == processed_uri


# -- FileHandler failure resilience -------------------------------------------


class TestFileHandlerFailure:
    """Processing failures should preserve original content items."""

    @pytest.mark.anyio
    async def test_one_failure_does_not_block_others(self):
        executor = _make_executor()
        processed_b = {"type": "text", "text": "B processed"}
        call_count = 0

        async def fake_process(path: str) -> dict[str, Any] | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return processed_b

        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(side_effect=fake_process)
            MockFH.return_value = mock_handler

            item_a = {
                "type": "file",
                "file_data": b"a",
                "file_name": "a.pdf",
            }
            item_b = {
                "type": "file",
                "file_data": b"b",
                "file_name": "b.txt",
            }
            msg: dict[str, Any] = {
                "role": "user",
                "content": [item_a, item_b],
            }
            result = await executor._process_attachments(msg)
            assert result["content"][0] == item_a
            assert result["content"][1] == processed_b

    @pytest.mark.anyio
    async def test_handler_exception_preserves_original(self):
        executor = _make_executor()
        original_item = {
            "type": "file",
            "file_data": b"data",
            "file_name": "crash.pdf",
        }
        msg: dict[str, Any] = {
            "role": "user",
            "content": [original_item],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(
                side_effect=RuntimeError("boom")
            )
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [original_item]

    @pytest.mark.anyio
    async def test_handler_returns_none_preserves_original(self):
        executor = _make_executor()
        original_item = {
            "type": "file_uri",
            "uri": "/bad/path.bin",
            "file_name": "bad.bin",
        }
        msg: dict[str, Any] = {
            "role": "user",
            "content": [original_item],
        }
        with patch("AgentCrew.modules.utils.file_handler.FileHandler") as MockFH:
            mock_handler = MagicMock()
            mock_handler.async_process_file = AsyncMock(return_value=None)
            MockFH.return_value = mock_handler

            result = await executor._process_attachments(msg)
            assert result["content"] == [original_item]


# -- Adapter binary data classification ----------------------------------------


class TestAdapterBinaryClassification:
    """Validate that binary raw bytes are classified as type:file, not garbled text."""

    def test_binary_bytes_classified_as_file(self):
        from a2a.types.a2a_pb2 import Message, Part, Role

        from AgentCrew.modules.a2a.adapters import convert_a2a_message_to_agent

        binary_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
        msg = Message(
            message_id="msg_bin",
            role=Role.ROLE_USER,
            parts=[Part(raw=binary_data, filename="image.png", media_type="image/png")],
        )
        result = convert_a2a_message_to_agent(msg)
        assert len(result["content"]) == 1
        item = result["content"][0]
        assert item["type"] == "file"
        assert item["file_data"] == binary_data
        assert item["file_name"] == "image.png"
        assert item["mime_type"] == "image/png"

    def test_valid_utf8_bytes_classified_as_text(self):
        from a2a.types.a2a_pb2 import Message, Part, Role

        from AgentCrew.modules.a2a.adapters import convert_a2a_message_to_agent

        text_data = b"Hello, world!"
        msg = Message(
            message_id="msg_txt",
            role=Role.ROLE_USER,
            parts=[Part(raw=text_data)],
        )
        result = convert_a2a_message_to_agent(msg)
        assert len(result["content"]) == 1
        item = result["content"][0]
        assert item["type"] == "text"
        assert item["text"] == "Hello, world!"

    def test_pdf_bytes_classified_as_file(self):
        from a2a.types.a2a_pb2 import Message, Part, Role

        from AgentCrew.modules.a2a.adapters import convert_a2a_message_to_agent

        pdf_header = b"%PDF-1.4\n\x80\x81\xff\xfe\x00\x01\x02"
        msg = Message(
            message_id="msg_pdf",
            role=Role.ROLE_USER,
            parts=[
                Part(raw=pdf_header, filename="doc.pdf", media_type="application/pdf")
            ],
        )
        result = convert_a2a_message_to_agent(msg)
        assert len(result["content"]) == 1
        item = result["content"][0]
        assert item["type"] == "file"
        assert item["file_data"] == pdf_header
