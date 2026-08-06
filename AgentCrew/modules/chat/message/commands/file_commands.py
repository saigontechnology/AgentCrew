from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING

from AgentCrew.modules.chat.message.commands.base import CommandResult
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.utils.file_handler import FileHandler

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message import MessageHandler


class FileCommands:
    """Handles file-related slash commands."""

    def __init__(self, message_handler: MessageHandler):
        self.message_handler = message_handler

    async def handle_file(self, user_input: str) -> CommandResult:
        """Handle file command with support for multiple files."""
        file_paths_str: str = user_input[6:].strip()
        file_paths: list[str] = [
            os.path.expanduser(path.strip())
            for path in shlex.split(file_paths_str)
            if path.strip()
        ]

        if not file_paths:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message="No file paths provided"
            )
            return CommandResult(handled=True, clear_flag=True)

        processed_files: list[str] = []
        failed_files: list[str] = []
        all_file_contents: list[dict[str, str]] = []

        for file_path in file_paths:
            if self.message_handler.file_handler is None:
                self.message_handler.file_handler = FileHandler()
            file_content = await self.message_handler.file_handler.async_process_file(
                file_path
            )

            if file_content:
                all_file_contents.append(file_content)
                processed_files.append(file_path)
                self.message_handler.bus.emit_sync(
                    AppEvents.FILE_PROCESSED, file_path=file_path, message=file_content
                )
            else:
                failed_files.append(file_path)
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Failed to process file {file_path} Or Model is not supported",
                )

        if all_file_contents:
            self.message_handler._messages_append(
                {
                    "role": "user",
                    "agent": self.message_handler.agent.name,
                    "content": all_file_contents,
                }
            )

            if failed_files:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message=f"Failed to process: {', '.join(failed_files)}",
                )
            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"✅ Successfully processed {len(processed_files)} files: {', '.join(processed_files)}",
            )

        return CommandResult(handled=True, clear_flag=True)

    def handle_drop(self, user_input: str) -> CommandResult:
        """Handle drop command to remove queued files."""
        file_path = user_input[6:].strip()

        if not file_path:
            if not self.message_handler._queued_attached_files:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR,
                    message="No files are currently queued for processing",
                )
                return CommandResult(handled=True, clear_flag=True)

            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message="📋 Queued files:\n"
                + "\n".join(self.message_handler._queued_attached_files)
                + "\nUsage: /drop <file_id>",
            )
            return CommandResult(handled=True, clear_flag=True)

        try:
            try:
                self.message_handler._queued_attached_files.remove(file_path)
            except Exception:
                self.message_handler.bus.emit_sync(
                    AppEvents.ERROR, message=f"Cannot unqueue file: {file_path}"
                )

            self.message_handler.bus.emit_sync(
                AppEvents.SYSTEM_MESSAGE,
                message=f"🗑️ Removed file from queue: {file_path}",
            )

            self.message_handler.bus.emit_sync(
                AppEvents.FILE_DROPPED, file_path=file_path
            )

            return CommandResult(handled=True, clear_flag=True)

        except ValueError as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Invalid file ID format: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)
        except Exception as e:
            self.message_handler.bus.emit_sync(
                AppEvents.ERROR, message=f"Error removing file: {e!s}"
            )
            return CommandResult(handled=True, clear_flag=True)
