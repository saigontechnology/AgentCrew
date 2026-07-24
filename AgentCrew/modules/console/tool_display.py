"""
Tool display handlers for console UI.
Handles rendering of tool-related information like tool use, results, errors, and confirmations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.box import HORIZONTALS
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from .constants import (
    RICH_STYLE_BLUE,
    RICH_STYLE_GRAY,
    RICH_STYLE_GREEN,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_RED,
    RICH_STYLE_RED_BOLD,
    RICH_STYLE_WHITE,
    RICH_STYLE_YELLOW,
)
from .diff_display import DiffDisplay

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class ToolDisplayHandlers:
    """Handles display of tool-related information."""

    def __init__(self, console_ui: ConsoleUI):
        """Initialize the tool display handlers with a console instance."""
        self.console = console_ui.console

    def get_tool_icon(self, tool_name: str) -> str:
        """Get the appropriate icon for a tool based on its name."""
        tool_icons = {
            "web_search": "🔍",
            "fetch_webpage": "🌐",
            "transfer": "↗️",
            "delegate": "📋",
            "adapt": "🧠",
            "retrieve_memory": "💭",
            "forget_memory_topic": "🗑️",
            "analyze_repo": "📂",
            "read_file": "📄",
        }
        return tool_icons.get(tool_name, "🔧")

    def display_delegate_started(self, tool_use: dict):
        """Display a compact 'working' line for a delegate tool call."""
        params = tool_use.get("input") or tool_use.get("arguments", {})
        target_agent = (
            params.get("target_agent", "Unknown")
            if isinstance(params, dict)
            else "Unknown"
        )
        task = params.get("task_description", "") if isinstance(params, dict) else ""
        task_preview = task[:60] + "..." if len(task) > 60 else task

        line = Text("📋 Delegating → ", style=RICH_STYLE_YELLOW)
        line.append(target_agent, style="bold yellow")
        line.append(f": {task_preview}", style=RICH_STYLE_GRAY)
        self.console.print(line)

    def display_delegate_completed(self, tool_use: dict):
        """Display a compact 'done' line when a delegate tool finishes."""
        params = tool_use.get("input") or tool_use.get("arguments", {})
        target_agent = (
            params.get("target_agent", "Unknown")
            if isinstance(params, dict)
            else "Unknown"
        )

        line = Text("✅ ", style=RICH_STYLE_GREEN)
        line.append(target_agent, style="bold green")
        line.append(" completed", style=RICH_STYLE_GREEN)
        self.console.print(line)

    def display_tool_use(self, tool_use: dict):
        """Display information about a tool being used."""
        tool_icon = self.get_tool_icon(tool_use["name"])

        tool_texts_group = []

        # Display tool header with better formatting
        header = Text(f"{tool_icon} Tool: ", style=RICH_STYLE_GRAY)
        header.append(tool_use["name"], style=RICH_STYLE_GRAY)

        tool_parameters = tool_use.get("input") or tool_use.get("arguments")

        if tool_use["name"] == "write_file" and isinstance(tool_parameters, dict):
            file_path = tool_parameters.get("file_path", "")
            text_or_blocks = tool_parameters.get("write_blocks", "")

            if DiffDisplay.has_search_replace_blocks(text_or_blocks):
                self._display_write_file_use(tool_use, file_path, text_or_blocks)
                return

        if isinstance(tool_parameters, dict):
            tool_texts_group.append(Text("Parameters:", style=RICH_STYLE_YELLOW))
            for key, value in tool_parameters.items():
                # Format value based on type
                if isinstance(value, (dict, list)):
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
                box=HORIZONTALS,
                title_align="left",
            )
        )

    def _display_write_file_use(self, tool_use: dict, file_path: str, blocks):
        """Display write_or_edit_file tool with split diff view."""
        tool_icon = self.get_tool_icon(tool_use["name"])

        header = Text(f"{tool_icon} Tool: ", style=RICH_STYLE_GRAY)
        header.append("write_file", style=RICH_STYLE_GRAY)
        header.append(f" → {file_path}", style=RICH_STYLE_BLUE)

        self.console.print(Panel(header, box=HORIZONTALS, title_align="left"))

        parsed_blocks = DiffDisplay.parse_search_replace_blocks(blocks)

        if parsed_blocks:
            for block in parsed_blocks:
                diff_table = DiffDisplay.create_split_diff_table(
                    block["search"], block["replace"], max_width=self.console.width - 4
                )
                self.console.print(diff_table)

    def display_tool_result(self, data: dict):
        """Display the result of a tool execution."""
        tool_use = data["tool_use"]
        tool_result = data["tool_result"]
        tool_icon = self.get_tool_icon(tool_use["name"])

        tool_texts_group = []

        header = Text(f"{tool_icon} Tool Result: ", style=RICH_STYLE_GREEN)
        header.append(tool_use["name"], style=RICH_STYLE_GREEN_BOLD)

        result_str = str(tool_result)
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
            Panel(
                Group(*tool_texts_group),
                box=HORIZONTALS,
                title=header,
                title_align="left",
            )
        )

    def display_tool_error(self, data: dict):
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
                box=HORIZONTALS,
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
