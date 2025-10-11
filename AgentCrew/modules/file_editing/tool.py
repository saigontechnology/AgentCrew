"""
File editing tool definitions and handlers for AgentCrew.

Provides file_write_or_edit tool for intelligent file editing with search/replace blocks.
"""

from typing import Dict, Any, Callable, Optional
from .service import FileEditingService


def get_file_write_or_edit_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Get tool definition for file editing.

    Args:
        provider: LLM provider name ("claude", "openai", "groq", "google")

    Returns:
        Provider-specific tool definition
    """
    tool_description = """Write/edit files via search/replace blocks or full content.

LOGIC: percentage_to_change >50 = full content | ≤50 = search/replace

SEARCH/REPLACE BLOCK FORMAT:
<<<<<<< SEARCH
[exact content to find]
=======
[replacement content]
>>>>>>> REPLACE

RULES:
1. SEARCH must match exactly (character-perfect)
2. Include changing lines +0-3 context
3. Multiple blocks in one call OK
4. Preserve whitespace/indentation
5. Empty REPLACE = delete

EXAMPLES: Add import (existing+new) | Delete (full→empty) | Modify (signature+changes)

Auto syntax check (30+ langs) with rollback on error
"""

    tool_arguments = {
        "file_path": {
            "type": "string",
            "description": "Path (absolute/relative). Use ~ for home. Ex: './src/main.py'",
        },
        "percentage_to_change": {
            "type": "number",
            "description": "% lines changing (0-100). >50=full, ≤50=blocks",
        },
        "text_or_search_replace_blocks": {
            "type": "string",
            "description": "Full content (>50%) OR search/replace blocks (≤50%)",
        },
    }

    tool_required = [
        "file_path",
        "percentage_to_change",
        "text_or_search_replace_blocks",
    ]

    if provider == "claude":
        return {
            "name": "file_write_or_edit",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider in ["openai", "google", "groq"] or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "file_write_or_edit",
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
    """
    Get the handler function for the file editing tool.

    Args:
        file_editing_service: FileEditingService instance

    Returns:
        Handler function
    """

    def handle_file_write_or_edit(**params) -> str:
        """
        Tool execution handler.

        Args:
            **params: Tool parameters (file_path, percentage_to_change, text_or_search_replace_blocks)

        Returns:
            Success or error message
        """
        file_path = params.get("file_path")
        percentage_to_change = params.get("percentage_to_change")
        text_or_search_replace_blocks = params.get("text_or_search_replace_blocks")

        if not file_path:
            raise ValueError("❌ Error: No file path provided.")

        if percentage_to_change is None:
            raise ValueError("❌ Error: No percentage_to_change provided.")

        if not text_or_search_replace_blocks:
            raise ValueError("❌ Error: No content or search/replace blocks provided.")

        result = file_editing_service.write_or_edit_file(
            file_path=file_path,
            percentage_to_change=float(percentage_to_change),
            text_or_search_replace_blocks=text_or_search_replace_blocks,
        )

        if result["status"] == "success":
            parts = [f"✅ {result['file_path']}"]
            parts.append(f"{result.get('changes_applied', 1)} change(s)")
            if result.get("syntax_check", {}).get("is_valid"):
                parts.append(
                    f"syntax OK ({result['syntax_check'].get('language', '?')})"
                )
            if result.get("backup_created"):
                parts.append("backup OK")
            return " | ".join(parts)

        elif result["status"] == "syntax_error":
            errors = "\n".join(
                [
                    f"L{e['line']}:C{e['column']} {e['message']}"
                    for e in result.get("errors", [])[:5]
                ]
            )
            extra = (
                f"\n+{len(result['errors']) - 5} more"
                if len(result.get("errors", [])) > 5
                else ""
            )
            restore = " | Backup restored" if result.get("backup_restored") else ""
            return (
                f"❌ Syntax ({result.get('language', '?')}):\n{errors}{extra}{restore}"
            )

        elif result["status"] in ["no_match", "ambiguous"]:
            return f"❌ {result['status'].title()}: {result.get('error', '?')} (block {result.get('block_index', '?')})"

        elif result["status"] == "denied":
            return f"❌ Access denied: {result.get('error', 'Permission error')}"

        elif result["status"] == "parse_error":
            return f"❌ Parse: {result.get('error', 'Invalid block format')}"

        else:
            return f"❌ {result.get('error', 'Unknown error')}"

    return handle_file_write_or_edit


def register(service_instance: Optional[FileEditingService] = None, agent=None):
    """
    Register file editing tools with AgentCrew tool registry.

    Args:
        service_instance: Optional FileEditingService instance
        agent: Optional agent to register with directly
    """
    from AgentCrew.modules.tools.registration import register_tool

    if service_instance is None:
        service_instance = FileEditingService()

    register_tool(
        get_file_write_or_edit_tool_definition,
        get_file_write_or_edit_tool_handler,
        service_instance,
        agent,
    )
