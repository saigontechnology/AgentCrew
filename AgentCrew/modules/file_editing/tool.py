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
    tool_description = """Write or edit files using search/replace blocks or full content.

DECISION LOGIC:
- If percentage_to_change > 50: Provide full file content
- If percentage_to_change <= 50: Use search/replace blocks

SEARCH/REPLACE BLOCK FORMAT:
<<<<<<< SEARCH
[exact content to find]
=======
[replacement content]
>>>>>>> REPLACE

CRITICAL RULES:
1. SEARCH sections must EXACTLY MATCH existing content (character-perfect)
2. Include only changing lines + 0-3 context lines for uniqueness
3. Multiple blocks supported in single call (faster than separate calls)
4. Preserve exact whitespace, indentation, comments
5. Apply blocks top-to-bottom
6. Empty REPLACE section deletes the SEARCH content

EXAMPLES:
• Add import: Include existing imports in SEARCH, add new one in REPLACE
• Delete function: Full function in SEARCH, empty REPLACE
• Modify logic: Function signature in SEARCH, modified body in REPLACE

SYNTAX CHECKING:
- Automatic syntax validation using tree-sitter for 30+ languages
- Automatic rollback if syntax errors detected
- Clear error messages for correction
"""

    tool_arguments = {
        "file_path": {
            "type": "string",
            "description": "Absolute or relative path to file. Use ~ for home directory. Examples: './src/main.py', '~/project/file.js'",
        },
        "percentage_to_change": {
            "type": "number",
            "description": "Estimated percentage of lines changing (0-100). Determines whether to use full write (>50) or incremental edit (<=50).",
        },
        "text_or_search_replace_blocks": {
            "type": "string",
            "description": "Full file content (if percentage > 50) OR search/replace blocks (if percentage <= 50)",
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
            return "❌ Error: No file path provided."

        if percentage_to_change is None:
            return "❌ Error: No percentage_to_change provided."

        if not text_or_search_replace_blocks:
            return "❌ Error: No content or search/replace blocks provided."

        result = file_editing_service.write_or_edit_file(
            file_path=file_path,
            percentage_to_change=float(percentage_to_change),
            text_or_search_replace_blocks=text_or_search_replace_blocks,
        )

        if result["status"] == "success":
            syntax_info = ""
            if result.get("syntax_check", {}).get("is_valid"):
                lang = result["syntax_check"].get("language", "unknown")
                syntax_info = f"\n✓ Syntax valid ({lang})"

            backup_info = ""
            if result.get("backup_created"):
                backup_info = "\n✓ Backup created"

            changes_info = f"\n✓ {result.get('changes_applied', 1)} change(s) applied"

            return (
                f"✅ Successfully edited {result['file_path']}"
                f"{changes_info}"
                f"{syntax_info}"
                f"{backup_info}"
            )

        elif result["status"] == "syntax_error":
            errors_list = "\n".join(
                [
                    f"  Line {err['line']}, Col {err['column']}: {err['message']}"
                    for err in result.get("errors", [])[:5]  # Show first 5 errors
                ]
            )

            more_errors = ""
            if len(result.get("errors", [])) > 5:
                more_errors = f"\n  ... and {len(result['errors']) - 5} more errors"

            return (
                f"❌ Syntax errors detected in {result.get('language', 'file')}:\n"
                f"{errors_list}"
                f"{more_errors}\n\n"
                f"Suggestion: {result.get('suggestion', 'Fix syntax errors and retry')}\n"
                f"{'✓ Backup restored' if result.get('backup_restored') else ''}"
            )

        elif result["status"] in ["no_match", "ambiguous"]:
            return (
                f"❌ {result['status'].replace('_', ' ').title()}\n\n"
                f"{result.get('error', 'Unknown error')}\n\n"
                f"Block index: {result.get('block_index', 'N/A')}"
            )

        elif result["status"] == "denied":
            return (
                f"❌ Access denied\n\n"
                f"{result.get('error', 'Permission denied')}\n\n"
                f"Suggestion: {result.get('suggestion', 'Check permissions')}"
            )

        elif result["status"] == "parse_error":
            return (
                f"❌ Parse error\n\n"
                f"{result.get('error', 'Invalid block format')}\n\n"
                f"Suggestion: {result.get('suggestion', 'Check search/replace block format')}"
            )

        else:  # Generic error
            return (
                f"❌ Error: {result.get('error', 'Unknown error')}\n\n"
                f"Suggestion: {result.get('suggestion', 'Check parameters and try again')}"
            )

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
