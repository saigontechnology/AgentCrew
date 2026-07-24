"""
File editing tool definitions and handlers for AgentCrew.

Provides file_write_or_edit tool for intelligent file editing with search/replace blocks.
"""

from collections.abc import Callable
from typing import Any

from .service import FileEditingService


def is_full_content_mode(blocks: list[dict[str, str]]) -> bool:
    if len(blocks) == 1:
        block = blocks[0]
        search_text = block.get("search", "")
        return search_text == ""
    return False


def convert_blocks_to_string(blocks: list[dict[str, str]]) -> str:
    result_parts = []
    for block in blocks:
        search_text = block.get("search", "")
        replace_text = block.get("replace", "")
        block_str = (
            f"<<<<<<< SEARCH\n{search_text}\n=======\n{replace_text}\n>>>>>>> REPLACE"
        )
        result_parts.append(block_str)
    return "\n".join(result_parts)


def get_file_write_or_edit_tool_definition() -> dict[str, Any]:
    tool_description = """Write/edit files. Accepts either full file content as a string or search/replace blocks as an array.

TEXT MODE (string): Provide the full content and the entire file is written.

BLOCK MODE (array of {"search": "...", "replace": "..."} objects):
- Non-empty search + replace = search/replace operation
- Non-empty search + empty replace = delete matched content

RULES:
1. SEARCH must match exactly (character-perfect)
2. Include changing lines +0-5 context
3. Preserve whitespace/indentation

Auto syntax check (30+ langs) with rollback on error
"""

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

    tool_arguments = {
        "file_path": {
            "type": "string",
            "description": "Path (absolute/relative). Use ~ for home. Ex: './src/main.py'",
        },
        "write_blocks": {
            "anyOf": [
                {
                    "type": "string",
                    "description": "Full file content. Use this to write an entire file.",
                },
                {
                    "type": "array",
                    "items": block_schema,
                    "description": "Array of search/replace blocks for targeted edits.",
                },
            ],
            "description": 'String for full file write, or array of {"search", "replace"} blocks for targeted edits.',
        },
    }

    tool_required = [
        "file_path",
        "write_blocks",
    ]

    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_file_write_or_edit_tool_handler(
    file_editing_service: FileEditingService,
) -> Callable:
    async def handle_file_write_or_edit(**params) -> str:
        file_path = params.get("file_path")
        blocks = params.get("write_blocks")

        if not file_path:
            raise ValueError("Error: No file_path provided.")

        if blocks is None:
            raise ValueError("Error: No write_blocks provided.")

        # String mode — full file write
        if isinstance(blocks, str):
            if blocks.lstrip().startswith('[{"search":'):
                raise ValueError("Error: Malformed full content format.")
            result = file_editing_service.write_or_edit_file(
                file_path=file_path,
                is_search_replace=False,
                text_or_search_replace_blocks=blocks,
            )
        # Array mode — search/replace blocks
        elif isinstance(blocks, list):
            if is_full_content_mode(blocks):
                content = blocks[0].get("replace", None)
                result = file_editing_service.write_or_edit_file(
                    file_path=file_path,
                    is_search_replace=False,
                    text_or_search_replace_blocks=content,
                )
            else:
                blocks_string = convert_blocks_to_string(blocks)
                result = file_editing_service.write_or_edit_file(
                    file_path=file_path,
                    is_search_replace=True,
                    text_or_search_replace_blocks=blocks_string,
                )
        else:
            raise TypeError(
                "Error: write_blocks must be a string (full file write) "
                "or an array of search/replace objects (targeted edit)."
            )

        if result["status"] == "success":
            parts = [f"{result['file_path']}"]
            parts.append(f"{result.get('changes_applied', 1)} change(s)")
            syntax = result.get("syntax_check", {})
            if syntax.get("is_valid"):
                parts.append(f"syntax OK ({syntax.get('language', '?')})")
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
                    f"syntax WARNING ({syntax.get('language', '?')}):\n{warnings}{extra}"
                )
            if result.get("backup_created"):
                parts.append("backup OK")
            return " | ".join(parts)

        elif result["status"] in ["no_match", "ambiguous"]:
            return f"{result['status'].title()}: {result.get('error', '?')} (block {result.get('block_index', '?')})"

        elif result["status"] == "denied":
            return f"Access denied: {result.get('error', 'Permission error')}"

        elif result["status"] == "parse_error":
            return f"Parse: {result.get('error', 'Invalid block format')}"

        else:
            return f"{result.get('error', 'Unknown error')}"

    return handle_file_write_or_edit


def register(service_instance: FileEditingService | None = None, agent=None):
    from AgentCrew.modules.tools.registration import register_tool

    if service_instance is None:
        service_instance = FileEditingService()

    register_tool(
        get_file_write_or_edit_tool_definition,
        get_file_write_or_edit_tool_handler,
        service_instance,
        agent,
    )
