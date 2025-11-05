"""
Display handlers for console UI components.
Handles rendering of various UI elements like messages, dividers, models, agents, etc.
"""

import json
import re
from datetime import datetime
from typing import Dict, Any, List
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.text import Text
from rich.panel import Panel

from .constants import (
    RICH_STYLE_YELLOW,
    RICH_STYLE_BLUE,
    RICH_STYLE_RED,
    RICH_STYLE_GREEN,
    RICH_STYLE_GRAY,
    RICH_STYLE_YELLOW_BOLD,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_FILE_ACCENT_BOLD,
    RICH_STYLE_WHITE,
    CODE_THEME,
)


class DisplayHandlers:
    """Handles all display-related functionality for the console UI."""

    def __init__(self, console: Console):
        """Initialize the display handlers with a console instance."""
        self.console = console
        self._added_files = []

    def display_thinking_started(self, agent_name: str):
        """Display the start of the thinking process."""
        self.console.print(
            Text(
                f"\n💭 {agent_name.upper()}'s thinking process:",
                style=RICH_STYLE_YELLOW,
            )
        )

    def display_thinking_chunk(self, chunk: str):
        """Display a chunk of the thinking process."""
        self.console.print(Text(chunk, style=RICH_STYLE_GRAY), end="", soft_wrap=True)

    def display_error(self, error):
        """Display an error message."""
        if isinstance(error, dict):
            error_text = Text("\n❌ Error: ", style=RICH_STYLE_RED)
            error_text.append(error["message"])
            self.console.print(error_text)
            if "traceback" in error:
                self.console.print(Text(error["traceback"], style=RICH_STYLE_GRAY))
        else:
            error_text = Text("\n❌ Error: ", style=RICH_STYLE_RED)
            error_text.append(str(error))
            self.console.print(error_text)

    def display_message(self, message: Text):
        """Display a generic message."""
        self.console.print(message)

    def display_user_message(self, message: str):
        header = Text(
            "👤 YOU:",
            style=RICH_STYLE_GREEN_BOLD,
        )
        user_panel = Panel(
            Text(message),
            title=header,
            title_align="left",
            border_style=RICH_STYLE_BLUE,
        )
        self.console.print(user_panel)

    def display_assistant_message(self, agent_name: str, message: str):
        header = Text(
            f"🤖 {agent_name.upper()}:",
            style=RICH_STYLE_GREEN_BOLD,
        )
        assistant_panel = Panel(
            Markdown(message, code_theme=CODE_THEME),
            title=header,
            title_align="left",
            border_style=RICH_STYLE_GREEN,
        )
        self.console.print(assistant_panel)

    def display_divider(self):
        """Display a divider line."""
        pass

    def display_debug_info(self, debug_info):
        """Display debug information."""
        self.console.print(Text("Current messages:", style=RICH_STYLE_YELLOW))
        try:
            self.console.print(json.dumps(debug_info, indent=2))
        except Exception:
            self.console.print(debug_info)

    def display_models(self, models_by_provider: Dict):
        """Display available models grouped by provider."""
        self.console.print(Text("Available models:", style=RICH_STYLE_YELLOW))
        for provider, models in models_by_provider.items():
            self.console.print(
                Text(f"\n{provider.capitalize()} models:", style=RICH_STYLE_YELLOW)
            )
            for model in models:
                current = " (current)" if model["current"] else ""
                self.console.print(f"  - {model['id']}: {model['name']}{current}")
                self.console.print(f"    {model['description']}")
                self.console.print(
                    f"    Capabilities: {', '.join(model['capabilities'])}"
                )

    def display_agents(self, agents_info: Dict):
        """Display available agents."""
        self.console.print(
            Text(f"Current agent: {agents_info['current']}", style=RICH_STYLE_YELLOW)
        )
        self.console.print(Text("Available agents:", style=RICH_STYLE_YELLOW))

        for agent_name, agent_data in agents_info["available"].items():
            current = " (current)" if agent_data["current"] else ""
            self.console.print(
                f"  - {agent_name}{current}: {agent_data['description']}"
            )

    def display_conversations(self, conversations: List[Dict[str, Any]]):
        """Display available conversations."""
        if not conversations:
            self.console.print(
                Text("No saved conversations found.", style=RICH_STYLE_YELLOW)
            )
            return

        self.console.print(Text("Available conversations:", style=RICH_STYLE_YELLOW))
        for i, convo in enumerate(conversations[:30], 1):
            # Format timestamp for better readability
            timestamp = convo.get("timestamp", "Unknown")
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(timestamp).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            title = convo.get("title", "Untitled")
            convo_id = convo.get("id", "unknown")

            # Display conversation with index for easy selection
            self.console.print(f"  {i}. {title} [{convo_id}]")
            self.console.print(f"     Created: {timestamp}")

            # Show a preview if available
            if "preview" in convo:
                self.console.print(f"     Preview: {convo['preview']}")
            self.console.print("")

    def display_consolidation_result(self, result: Dict[str, Any]):
        """Display information about a consolidation operation."""
        self.console.print(
            Text("\n🔄 Conversation Consolidated:", style=RICH_STYLE_YELLOW)
        )
        self.console.print(f"  • {result['messages_consolidated']} messages summarized")
        self.console.print(
            f"  • {result['messages_preserved']} recent messages preserved"
        )
        self.console.print(
            f"  • ~{result['original_token_count'] - result['consolidated_token_count']} tokens saved"
        )

    def display_loaded_conversation(self, messages, message_handler):
        """Display all messages from a loaded conversation."""
        self.console.print(
            Text("\nDisplaying conversation history:", style=RICH_STYLE_YELLOW)
        )
        self.display_divider()

        last_consolidated_idx = 0
        for i, msg in reversed(list(enumerate(messages))):
            if msg.get("role") == "consolidated":
                last_consolidated_idx = i
                break

        # Display each message in the conversation
        for msg in messages[last_consolidated_idx:]:
            role = msg.get("role")
            if role == "user":
                content = self._extract_message_content(msg)
                self.display_user_message(content)
            elif role == "assistant":
                agent_name = msg.get("agent") or message_handler.agent.name
                content = self._extract_message_content(msg)
                # Format as markdown for better display
                self.display_assistant_message(agent_name, content)
                if "tool_calls" in msg:
                    from .tool_display import ToolDisplayHandlers

                    tool_display = ToolDisplayHandlers(self.console)
                    for tool_call in msg["tool_calls"]:
                        tool_display.display_tool_use(tool_call)
                self.display_divider()
            elif role == "consolidated":
                self.console.print(
                    Text("\n📝 CONVERSATION SUMMARY:", style=RICH_STYLE_YELLOW)
                )
                content = self._extract_message_content(msg)

                # Display metadata if available
                metadata = msg.get("metadata", {})
                if metadata:
                    consolidated_count = metadata.get(
                        "messages_consolidated", "unknown"
                    )
                    token_savings = metadata.get(
                        "original_token_count", 0
                    ) - metadata.get("consolidated_token_count", 0)
                    self.console.print(
                        Text(
                            f"({consolidated_count} messages consolidated, ~{token_savings} tokens saved)",
                            style=RICH_STYLE_YELLOW,
                        )
                    )

                # Format the summary with markdown
                self.console.print(Markdown(content, code_theme=CODE_THEME))
                self.display_divider()

        self.console.print(
            Text("End of conversation history\n", style=RICH_STYLE_YELLOW)
        )

    def display_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        total_cost: float,
        session_cost: float,
    ):
        """Display token usage and cost information."""
        self.display_divider()
        token_info = Text("📊 Token Usage: ", style=RICH_STYLE_YELLOW)
        token_info.append(
            f"Input: {input_tokens:,} | Output: {output_tokens:,} | ",
            style=RICH_STYLE_YELLOW,
        )
        token_info.append(
            f"Total: {input_tokens + output_tokens:,} | Cost: ${total_cost:.4f} | Total: {session_cost:.4f}",
            style=RICH_STYLE_YELLOW,
        )
        self.console.print(Panel(token_info))
        self.display_divider()

    def display_added_files(self):
        """Display added files with special styling just above the user input."""
        if not self._added_files:
            return

        file_display = Text("📎 Added files: ", style=RICH_STYLE_FILE_ACCENT_BOLD)
        file_display.append(f"{', '.join(self._added_files)}", style=RICH_STYLE_WHITE)
        self.console.print(file_display)

    def print_welcome_message(self, version: str):
        """Print the welcome message for the chat."""
        welcome_messages = Group(
            Text(
                "🎮 Welcome to AgentCrew v" + version + " interactive chat!",
                style=RICH_STYLE_YELLOW_BOLD,
            ),
            Text("Press Ctrl+C twice to exit.", style=RICH_STYLE_GRAY),
            Text("Type '/exit' or '/quit' to end the session.", style=RICH_STYLE_GRAY),
            Text(
                "Use '/voice' to input message with your voice.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/file <file_path>' to include a file in your message.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/clear' to clear the conversation history.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/think <budget>' to enable Claude's thinking mode (min 1024 tokens).",
                style=RICH_STYLE_YELLOW,
            ),
            Text("Use '/think 0' to disable thinking mode.", style=RICH_STYLE_YELLOW),
            Text(
                "Use '/model [model_id]' to switch models or list available models.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/jump <turn_number>' to rewind the conversation to a previous turn.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/copy' to copy the latest assistant response to clipboard.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/agent [agent_name]' to switch agents or list available agents.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/export_agent <agent_names> <output_file>' to export selected agents to a TOML file (comma-separated names).",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/import_agent <file_or_url>' to import/replace agent configuration from a file or URL.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/edit_agent' to open agent configuration file in your default editor.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/edit_mcp' to open MCP configuration file in your default editor.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/edit_config' to open AgentCrew global configuration file in your default editor.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/toggle_transfer' to toggle agent transfer enforcement.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/toggle_session_yolo' to toggle YOLO mode (auto-approval of tool calls) in this session only.",
                style=RICH_STYLE_YELLOW,
            ),
            Text("Use '/list' to list saved conversations.", style=RICH_STYLE_YELLOW),
            Text(
                "Use '/load <id>' or '/load <number>' to load a conversation.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/consolidate [count]' to summarize older messages (default: 10 recent messages preserved).",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Use '/unconsolidate' undo last consolidated.",
                style=RICH_STYLE_YELLOW,
            ),
            Text(
                "Tool calls require confirmation before execution.",
                style=RICH_STYLE_BLUE,
            ),
            Text(
                "Use 'y' to approve once, 'n' to deny, 'all' to approve future calls to the same tool.",
                style=RICH_STYLE_BLUE,
            ),
        )

        self.console.print(Panel(welcome_messages))
        self.display_divider()

    def print_prompt_prefix(
        self, agent_name: str, model_name: str, yolo_mode_enabled: bool
    ):
        """Print the prompt prefix with agent and model information."""
        title = Text(f"\n[{agent_name}", style=RICH_STYLE_RED)
        title.append(":")
        title.append(f"{model_name}]", style=RICH_STYLE_BLUE)

        title.append(
            f"      [{datetime.now().strftime('%H:%M:%S')}]",
            style=RICH_STYLE_GRAY,
        )

        if yolo_mode_enabled:
            title.append("\n[YOLO mode enabled]", style=RICH_STYLE_YELLOW_BOLD)

        title.append(
            "\n(Press Enter for new line, Ctrl+S/Alt+Enter to Send, Ctrl+V to paste)\n",
            style=RICH_STYLE_YELLOW,
        )
        self.console.print(title)
        self.display_added_files()

    def add_file(self, file_path: str):
        """Add a file to the added files list."""
        self._added_files.append(file_path)

    def clear_files(self):
        """Clear the added files list."""
        self._added_files = []

    def _extract_message_content(self, message):
        """Extract the content from a message, handling different formats."""
        content = message.get("content", "")

        # Handle different content structures
        if isinstance(content, str):
            pass
        elif isinstance(content, list) and content:
            # For content in the format of a list of content parts
            result = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        result.append(item.get("text", ""))
                    # Handle other content types if needed
            return "\n".join(result)

        content = re.sub(
            r"(?:```(?:json)?)?\s*<user_context_summary>.*?</user_context_summary>\s*(?:```)?",
            "",
            str(content),
            flags=re.DOTALL | re.IGNORECASE,
        )
        return str(content)
