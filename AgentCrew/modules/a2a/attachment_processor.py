"""Inbound attachment preprocessing for A2A executor.

This module handles raw file, local/file URI, and HTTP(S) URI preprocessing
with lazy FileHandler access. It preserves the current failure contract:
warn and retain original content items.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.utils.file_handler import FileHandler


class AttachmentProcessor:
    """Processes inbound A2A attachments through FileHandler.

    Handles raw file payloads, file URIs (local and HTTP/HTTPS), and preserves
    original content items on failure with warnings.
    """

    def __init__(self) -> None:
        self._file_handler: FileHandler | None = None

    def _get_file_handler(self) -> FileHandler:
        if self._file_handler is None:
            from AgentCrew.modules.utils.file_handler import FileHandler

            self._file_handler = FileHandler()
        return self._file_handler

    async def process_attachments(
        self, user_message: dict[str, Any]
    ) -> dict[str, Any]:
        """Process any file or file_uri content items through FileHandler.

        Replaces inline file payloads and file URIs with their FileHandler-processed
        equivalents (e.g. Docling markdown, optimized images). Text-only messages pass
        through unchanged. Processing failures preserve the original content item and
        emit a warning, consistent with existing FileHandler contract.
        """

        content = user_message.get("content")
        if not isinstance(content, list) or not content:
            return user_message

        has_attachments = any(
            item.get("type") in ("file", "file_uri")
            for item in content
            if isinstance(item, dict)
        )
        if not has_attachments:
            return user_message

        processed_content: list[dict[str, Any]] = []

        for item in content:
            if not isinstance(item, dict):
                processed_content.append(item)
                continue

            item_type = item.get("type")

            if item_type == "file":
                result = await self._process_raw_file_item(item)
                if result is not None:
                    processed_content.append(result)
                else:
                    processed_content.append(item)

            elif item_type == "file_uri":
                result = await self._process_file_uri_item(item)
                if result is not None:
                    processed_content.append(result)
                else:
                    processed_content.append(item)

            else:
                processed_content.append(item)

        user_message["content"] = processed_content
        return user_message

    async def _process_raw_file_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Process a 'file' content item (raw bytes payload) through FileHandler.

        Writes bytes to a temporary file with an appropriate extension so FileHandler
        can validate and process it normally.
        """
        file_data = item.get("file_data")
        if not file_data:
            return None

        file_name = item.get("file_name") or "attachment"
        file_handler = self._get_file_handler()

        suffix = Path(file_name).suffix or ".bin"
        tmp_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            result = await file_handler.async_process_file(tmp_path)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(
                f"Failed to process file attachment '{file_name}' via FileHandler: {e!s}"
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return None

    async def _process_file_uri_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Process a 'file_uri' content item through FileHandler.

        Resolves the URI to a local file path:
        - file:// URIs are converted to local paths.
        - HTTP/HTTPS URIs are downloaded to a temporary file.
        - Bare paths are used as-is.
        """
        uri = item.get("uri", "")
        if not uri:
            return None

        parsed = urlparse(uri)
        tmp_path: str | None = None
        should_cleanup = False
        file_handler = self._get_file_handler()

        try:
            if parsed.scheme in ("file", ""):
                local_path = (
                    url2pathname(unquote(parsed.path))
                    if parsed.scheme == "file"
                    else uri
                )
                result = await file_handler.async_process_file(local_path)
                return result

            if parsed.scheme in ("http", "https"):
                import httpx

                file_name = item.get("file_name") or "download"
                suffix = Path(file_name).suffix if Path(file_name).suffix else ".bin"

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    should_cleanup = True

                with httpx.Client(follow_redirects=True, timeout=30) as client:
                    resp = client.get(uri)
                    resp.raise_for_status()

                content_bytes = resp.content

                def _write_tmp(path: str, data: bytes) -> None:
                    with open(path, "wb") as f:
                        f.write(data)

                await asyncio.to_thread(_write_tmp, tmp_path, content_bytes)

                result = await file_handler.async_process_file(tmp_path)
                return result

            logger.warning(
                f"Unsupported URI scheme '{parsed.scheme}' for file URI: {uri}"
            )
        except Exception as e:
            logger.warning(f"Failed to process file URI '{uri}' via FileHandler: {e!s}")
        finally:
            if should_cleanup and tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return None
