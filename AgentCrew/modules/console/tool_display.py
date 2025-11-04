"""
Tool display handlers for console UI.
Handles rendering of tool-related information like tool use, results, errors, and confirmations.
"""

from __future__ import annotations
import json
from typing import Dict
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from .constants import (
    RICH_STYLE_YELLOW,
    RICH_STYLE_GREEN,
    RICH_STYLE_BLUE,
    RICH_STYLE_RED,
    RICH_STYLE_WHITE,
    RICH_STYLE_YELLOW_BOLD,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_RED_BOLD,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


class ToolDisplayHandlers:
    """Handles display of tool-related information."""

    def __init__(self, console: Console):
        """Initialize the tool display handlers with a console instance."""
        self.console = console

    def get_tool_icon(self, tool_name: str) -> str:
        """Get the appropriate icon for a tool based on its name."""
        tool_icons = {
            "web_search": "🔍",
            "fetch_webpage": "🌐",
            "transfer": "↗️",
            "adapt": "🧠",
            "retrieve_memory": "💭",
            "forget_memory_topic": "🗑️",
            "analyze_repo": "📂",
            "read_file": "📄",
        }
        return tool_icons.get(tool_name, "🔧")

    def display_tool_use(self, tool_use: Dict):
        """Display information about a tool being used."""
        tool_icon = self.get_tool_icon(tool_use["name"])

        if tool_use["name"] == "ask":
            return

        tool_texts_group = []

        # Display tool header with better formatting
        header = Text(f"{tool_icon} Tool: ", style=RICH_STYLE_YELLOW)
        header.append(tool_use["name"], style=RICH_STYLE_YELLOW_BOLD)

        # Format tool input parameters
        if isinstance(tool_use.get("input"), dict):
            tool_texts_group.append(Text("Parameters:", style=RICH_STYLE_YELLOW))
            for key, value in tool_use["input"].items():
                # Format value based on type
                if isinstance(value, dict) or isinstance(value, list):
                    formatted_value = json.dumps(value, indent=2)
                    # Add indentation to all lines after the first
                    formatted_lines = formatted_value.split("\n")
                    param_text = Text("• ", style=RICH_STYLE_YELLOW)
                    param_text.append(key, style=RICH_STYLE_BLUE)
                    param_text.append(": " + formatted_lines[0], style=RICH_STYLE_WHITE)
                    tool_texts_group.append(param_text)

                    for line in formatted_lines[1:]:
                        indent_text = Text("    ", style=RICH_STYLE_YELLOW)
                        indent_text.append(line, style=RICH_STYLE_WHITE)
                        tool_texts_group.append(indent_text)
                else:
                    param_text = Text("• ", style=RICH_STYLE_YELLOW)
                    param_text.append(key, style=RICH_STYLE_BLUE)
                    param_text.append(f": {value}", style=RICH_STYLE_WHITE)
                    tool_texts_group.append(param_text)
        else:
            input_text = Text("Input: ", style=RICH_STYLE_YELLOW)
            input_text.append(str(tool_use.get("input", "")))
            tool_texts_group.append(input_text)

        self.console.print(
            Panel(
                Group(*tool_texts_group),
                title=header,
                title_align="left",
            )
        )

    def display_tool_result(self, data: Dict):
        """Display the result of a tool execution."""
        tool_use = data["tool_use"]
        tool_result = data["tool_result"]
        tool_icon = self.get_tool_icon(tool_use["name"])

        tool_texts_group = []

        # Display tool result with better formatting
        header = Text(f"{tool_icon} Tool Result: ", style=RICH_STYLE_GREEN)
        header.append(tool_use["name"], style=RICH_STYLE_GREEN_BOLD)

        # Format the result based on type
        result_str = str(tool_result)
        # If result is very long, try to format it
        if len(result_str) > 500:
            result_line = Text(result_str[:500] + "...", style=RICH_STYLE_GREEN)
            tool_texts_group.append(result_line)

            truncated_line = Text(
                f"(Output truncated, total length: {len(result_str)} characters)",
            )
            tool_texts_group.append(truncated_line)
        else:
            # Split by lines to add prefixes
            for line in result_str.split("\n"):
                result_line = Text(line, style=RICH_STYLE_GREEN)
                tool_texts_group.append(result_line)

        self.console.print(
            Panel(Group(*tool_texts_group), title=header, title_align="left")
        )

    def display_tool_error(self, data: Dict):
        """Display an error that occurred during tool execution."""
        tool_use = data["tool_use"]
        error = data["error"]
        tool_icon = self.get_tool_icon(tool_use["name"])

        tool_texts_group = []

        # Display tool error with better formatting
        header = Text(f"{tool_icon} Tool Error: ", style=RICH_STYLE_RED)
        header.append(tool_use["name"], style=RICH_STYLE_RED_BOLD)

        error_line = Text(str(error), style=RICH_STYLE_RED)
        tool_texts_group.append(error_line)

        self.console.print(
            Panel(
                Group(*tool_texts_group),
                title=header,
                title_align="left",
                border_style=RICH_STYLE_RED,
            )
        )

    def display_tool_denied(self, data):
        """Display information about a denied tool execution."""
        denied_text = Text("\n⚠️ Tool execution denied: ", style=RICH_STYLE_YELLOW)
        denied_text.append(f"{data['message']}")
        self.console.print(denied_text)
