from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from loguru import logger

from .context import _current_acp_session


def get_acp_read_file_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "acp_read_file",
            "description": (
                "Reads file content from the client's filesystem via ACP. "
                "Use this to read files, including unsaved editor buffer content. "
                "Supports reading specific line ranges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read. Example: '/home/user/src/main.py'",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional. The starting line number (1-indexed). If provided with end_line, only reads the specified line range.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional. The ending line number (1-indexed, inclusive). Requires start_line.",
                    },
                },
                "required": ["file_path"],
            },
        },
    }


def get_acp_read_file_tool_handler() -> Callable:
    async def handle_acp_read_file(**params) -> str:
        file_path = params.get("file_path", "")
        start_line = params.get("start_line")
        end_line = params.get("end_line")

        if not file_path:
            return "Error: file_path is required"

        ctx = _current_acp_session.get()
        if ctx is not None and ctx.conn is not None:
            try:
                response = await ctx.conn.read_text_file(
                    path=file_path,
                    session_id=ctx.session_id,
                    line=start_line,
                    limit=end_line - start_line + 1
                    if (start_line and end_line)
                    else None,
                )
                return f"`{file_path}`:\n{response.content}"
            except Exception as exc:
                logger.warning(
                    f"ACP read_file failed for '{file_path}', falling back to local: {exc}"
                )

        return await _local_read_file(file_path, start_line, end_line)

    return handle_acp_read_file


def get_acp_write_file_tool_definition() -> dict[str, Any]:
    block_schema = {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "Exact content to find.",
            },
            "replace": {
                "type": "string",
                "description": "Replacement content (empty string to delete)",
            },
        },
        "required": ["search", "replace"],
    }

    return {
        "type": "function",
        "function": {
            "name": "acp_write_file",
            "description": (
                "Write/edit files on the client's filesystem via ACP. "
                "Accepts either full file content as a string or search/replace blocks as an array.\n\n"
                "TEXT MODE (string): Full content string — entire file is written (delegated to ACP client).\n\n"
                'BLOCK MODE (array of {"search", "replace"} objects):\n'
                "- Non-empty search + replace = search/replace operation (processed locally).\n"
                "- Non-empty search + empty replace = delete matched content.\n"
                "Rules: 1) SEARCH must match exactly (character-perfect). "
                "2) Include changing lines +0-3 context. 3) Preserve whitespace/indentation. "
                "Auto syntax check (30+ langs) with rollback on error."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to write. Example: '/home/user/src/main.py'",
                    },
                    "write_blocks": {
                        "anyOf": [
                            {
                                "type": "string",
                                "description": "Full file content. Use this for a full file write.",
                            },
                            {
                                "type": "array",
                                "items": block_schema,
                                "description": "Array of search/replace blocks for targeted edits.",
                            },
                        ],
                        "description": (
                            'String for full file write, or array of {"search", "replace"} blocks for targeted edits.'
                        ),
                    },
                },
                "required": ["file_path", "write_blocks"],
            },
        },
    }


def get_acp_write_file_tool_handler() -> Callable:
    async def handle_acp_write_file(**params) -> str:
        file_path = params.get("file_path", "")
        value = params.get("write_blocks")

        if not file_path:
            return "Error: file_path is required"
        if value is None:
            return "Error: write_blocks is required"

        # String mode — full file write via ACP or local
        if isinstance(value, str):
            ctx = _current_acp_session.get()
            if ctx is not None and ctx.conn is not None:
                try:
                    await ctx.conn.write_text_file(
                        content=value,
                        path=file_path,
                        session_id=ctx.session_id,
                    )
                    return f"File written successfully via ACP: {file_path}"
                except Exception as exc:
                    logger.warning(
                        f"ACP write_file failed for '{file_path}', falling back to local: {exc}"
                    )
            return await _local_write_file(file_path, value)

        # Array mode — search/replace blocks (local only)
        if isinstance(value, list):
            if not value:
                return "Error: write_blocks must be a non-empty array"
            return await _local_write_file(file_path, value)

        return (
            "Error: write_blocks must be a string or an array of search/replace objects"
        )

    return handle_acp_write_file


async def _local_read_file(
    file_path: str, start_line: int | None, end_line: int | None
) -> str:
    from AgentCrew.modules.code_analysis.service import CodeAnalysisService

    if not os.path.isabs(file_path):
        file_path = os.path.abspath(os.path.expanduser(file_path))
    service = CodeAnalysisService()
    path, content = await service.get_file_content(
        file_path, start_line=start_line, end_line=end_line
    )
    if isinstance(content, dict):
        return f"Image file: {path}"
    return f"`{path}`: {content}"


async def _local_write_file(file_path: str, value: str | list[dict[str, str]]) -> str:
    from AgentCrew.modules.file_editing.service import FileEditingService
    from AgentCrew.modules.file_editing.tool import convert_blocks_to_string

    if not os.path.isabs(file_path):
        file_path = os.path.abspath(os.path.expanduser(file_path))

    service = FileEditingService()

    # String mode — full file write
    if isinstance(value, str):
        result = service.write_or_edit_file(
            file_path=file_path,
            is_search_replace=False,
            text_or_search_replace_blocks=value,
        )
    else:
        blocks_string = convert_blocks_to_string(value)
        result = service.write_or_edit_file(
            file_path=file_path,
            is_search_replace=True,
            text_or_search_replace_blocks=blocks_string,
        )

    if result["status"] == "success":
        parts = [f"{result['file_path']}"]
        parts.append(f"{result.get('changes_applied', 1)} change(s)")
        syntax = result.get("syntax_check", {})
        if syntax.get("is_valid"):
            parts.append("Syntax check passed.")
        elif syntax.get("warnings"):
            warnings = "\n".join(
                f"  L{w['line']}:C{w['column']} {w['message']}"
                for w in syntax["warnings"][:5]
            )
            extra = (
                f"\n  +{len(syntax['warnings']) - 5} more"
                if len(syntax["warnings"]) > 5
                else ""
            )
            parts.append(
                f"Syntax WARNING ({syntax.get('language', '?')}):\n{warnings}{extra}"
            )
        return " ".join(parts)
    return f"Error writing file: {result.get('error', 'Unknown error')}"


def register(
    context: Any = None,
    agent: Any = None,
    enable_read: bool = True,
    enable_write: bool = True,
):
    from AgentCrew.modules.tools.registration import register_tool

    if enable_read:
        register_tool(
            get_acp_read_file_tool_definition,
            get_acp_read_file_tool_handler,
            context,
            agent,
        )
    if enable_write:
        register_tool(
            get_acp_write_file_tool_definition,
            get_acp_write_file_tool_handler,
            context,
            agent,
        )
