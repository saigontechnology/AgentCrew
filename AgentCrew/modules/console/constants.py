from rich.style import Style

# Rich styles for console UI
RICH_STYLE_YELLOW = Style(color="yellow", bold=False)
RICH_STYLE_GREEN = Style(color="green", bold=False)
RICH_STYLE_BLUE = Style(color="blue", bold=False)
RICH_STYLE_RED = Style(color="red", bold=False)
RICH_STYLE_GRAY = Style(color="grey66", bold=False)

RICH_STYLE_YELLOW_BOLD = Style(color="yellow", bold=True)
RICH_STYLE_GREEN_BOLD = Style(color="green", bold=True)
RICH_STYLE_BLUE_BOLD = Style(color="blue", bold=True)
RICH_STYLE_RED_BOLD = Style(color="red", bold=True)

RICH_STYLE_FILE_ACCENT_BOLD = Style(color="bright_cyan", bold=True)
RICH_STYLE_WHITE = Style(color="#ffffff", bold=False)

CODE_THEME = "lightbulb"
PROMPT_CHAR = "  "

# Shared command help messages used by both the welcome message
# (display_handlers.py) and the loading animation tips (ui_effects.py).
# Add new commands here once — both surfaces will pick them up.
COMMAND_HELP_MESSAGES = [
    "Use '/voice' to input message with your voice.",
    "Use '/file <file_path>' to include a file in your message.",
    "Use '/clear' to clear the conversation history.",
    "Use '/think <budget|level>' to enable extended reasoning mode.",
    "Use '/think 0' or '/think none' to disable thinking mode.",
    "Use '/model [model_id]' to switch models or list available models.",
    "Use '/usage' to show current provider usage limits.",
    "Use '/debug [agent|chat|system]' to show debug information.",
    "Use '/copy <number>' to copy the nth-latest assistant response to clipboard.",
    "Use '/jump <turn_number>' to rewind the conversation to a previous turn.",
    "Use '/agent [agent_name]' to switch agents or list available agents.",
    "Use '/export_agent <agent_names> <output_file>' to export selected agents to a TOML file (comma-separated names).",
    "Use '/import_agent <file_or_url>' to import/replace agent configuration from a file or URL.",
    "Use '/edit_agent' to open agent configuration file in your default editor.",
    "Use '/edit_mcp' to open MCP configuration file in your default editor.",
    "Use '/edit_config' to open AgentCrew global configuration file in your default editor.",
    "Use '/toggle_transfer' to toggle agent transfer enforcement.",
    "Use '/agent_mode [transfer|delegate|none]' to switch agent interaction mode.",
    "Use '/toggle_session_yolo' to toggle YOLO mode (auto-approval of tool calls) in this session only.",
    "Use '/list_behaviors' to list all adaptive behaviors (global and project-specific).",
    "Use '/update_behavior <scope> <id> <behavior>' to create or update an adaptive behavior (format: 'when..., do...').",
    "Use '/delete_behavior <scope> <id>' to delete an adaptive behavior.",
    "Use '/clean_behaviors <scope>' to normalize and deduplicate adaptive behaviors in 'global' or 'project' scope.",
    "Use '/learn' to extract and store reusable behaviors from the current conversation.",
    "Use '/list' to list saved conversations.",
    "Use '/load <id>' or '/load <number>' to load a conversation.",
    "Use '/consolidate [count]' to summarize older messages (default: 10 recent messages preserved).",
    "Use '/evolve' to analyze current local-agent memory and propose a persisted system prompt evolution.",
    "Review and approve, edit, or decline the proposal in the interactive review UI after '/evolve'.",
    "Use '/unconsolidate' undo last consolidated.",
    "Use '/visual' to view raw message content with vim-like navigation and copy.",
]
