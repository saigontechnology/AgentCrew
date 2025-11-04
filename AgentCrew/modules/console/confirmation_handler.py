"""
Confirmation handlers for console UI.
Handles tool confirmation requests and MCP prompt confirmations.
"""

from __future__ import annotations

from rich.text import Text
from rich.panel import Panel
from rich.console import Group
import time

from .constants import (
    RICH_STYLE_BLUE,
    RICH_STYLE_BLUE_BOLD,
    RICH_STYLE_YELLOW,
    RICH_STYLE_GREEN,
    RICH_STYLE_RED,
    RICH_STYLE_GRAY,
    RICH_STYLE_WHITE,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .console_ui import ConsoleUI
    from .input_handler import InputHandler


class ConfirmationHandler:
    """Handles confirmation dialogs for tools and MCP prompts."""

    def __init__(self, console_ui: ConsoleUI, input_handler: InputHandler):
        """Initialize the confirmation handler."""
        self.console = console_ui.console
        self.ui = console_ui
        self.input_handler = input_handler

    def display_tool_confirmation_request(self, tool_info, message_handler):
        """Display tool confirmation request and get user response."""
        tool_use = tool_info.copy()
        confirmation_id = tool_use.pop("confirmation_id")

        # Special handling for 'ask' tool
        if tool_use["name"] == "ask":
            self._handle_ask_tool(tool_use, confirmation_id, message_handler)
            return

        tool_texts_group = []
        header = Text("🔧 Tool ", style=RICH_STYLE_YELLOW)
        header.append(tool_use["name"], style=RICH_STYLE_BLUE_BOLD)
        header.append(" execution requires your permission:", style=RICH_STYLE_YELLOW)

        # Display tool parameters
        if isinstance(tool_use["input"], dict):
            tool_texts_group.append(Text("Parameters:", style=RICH_STYLE_BLUE))
            for key, value in tool_use["input"].items():
                param_text = Text(f"  - {key}: ", style=RICH_STYLE_YELLOW)
                param_text.append(str(value), style=RICH_STYLE_WHITE)
                tool_texts_group.append(param_text)
        else:
            input_text = Text("Input: ", style=RICH_STYLE_YELLOW)
            input_text.append(str(tool_use["input"]))
            tool_texts_group.append(input_text)

        self.console.print(
            Panel(
                Group(*tool_texts_group),
                title=header,
                title_align="left",
                border_style=RICH_STYLE_YELLOW,
            )
        )

        # Get user response
        self.input_handler._stop_input_thread()
        choices = [
            "yes",
            "no",
            "all in this session",
            "forever (this and future sessions)",
        ]
        response = self.input_handler.get_choice_input(
            "Allow this tool to run?", choices
        )
        if not response:
            response = "no"

        if response == choices[0]:
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve"}
            )
        elif response == choices[1]:
            response = self.input_handler.get_prompt_input(
                "Please tell me why you are denying this tool: "
            )
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "deny", "reason": response}
            )
        elif response == choices[2]:
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            approved_text = Text(
                f"✓ Approved all future calls to '{tool_use['name']}' for this session.",
                style=RICH_STYLE_GREEN,
            )
            self.console.print(approved_text)
        elif response == choices[3]:
            from AgentCrew.modules.config import ConfigManagement

            config_manager = ConfigManagement()
            config_manager.write_auto_approval_tools(tool_use["name"], add=True)

            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            saved_text = Text(
                f"✓ Tool '{tool_use['name']}' will be auto-approved forever.",
                style=RICH_STYLE_YELLOW,
            )
            self.console.print(saved_text)
        self.ui.start_loading_animation()
        self.input_handler._start_input_thread()
        time.sleep(0.2)  # Small delay to between tool calls

    def _handle_ask_tool(self, tool_use, confirmation_id, message_handler):
        """Handle the ask tool - display question and guided answers."""
        question = tool_use["input"].get("question", "")
        guided_answers = tool_use["input"].get("guided_answers", [])
        if isinstance(guided_answers, str):
            guided_answers = guided_answers.strip("\n ").splitlines()

        guided_answers.append("Custom your answer")

        self.input_handler._stop_input_thread()
        # Display the question
        self.console.print(
            Text("\n❓ Agent is asking for clarification:", style=RICH_STYLE_BLUE_BOLD)
        )
        response = self.input_handler.get_choice_input(f"{question}", guided_answers)

        if response == "Custom your answer":
            custom_answer = self.input_handler.get_prompt_input("Input your answer:")
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "answer", "answer": custom_answer}
            )
        elif response:
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "answer", "answer": response}
            )

        else:
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "answer", "answer": "Cancelled by user"}
            )

        self.ui.start_loading_animation()

        self.input_handler._start_input_thread()

    def display_mcp_prompt_confirmation(self, prompt_data, input_queue):
        """Display MCP prompt confirmation request and get user response."""
        self.console.print(
            Text("\n🤖 MCP Tool wants to execute a prompt:", style=RICH_STYLE_YELLOW)
        )

        # Display the prompt content
        if isinstance(prompt_data, dict):
            if "name" in prompt_data:
                prompt_name = Text("Prompt: ", style=RICH_STYLE_YELLOW)
                prompt_name.append(prompt_data["name"])
                self.console.print(prompt_name)

            if "content" in prompt_data:
                self.console.print(Text("Content:", style=RICH_STYLE_YELLOW))
                # Display content with proper formatting
                content = str(prompt_data["content"])
                if len(content) > 1000:
                    self.console.print(f"  {content[:1000]}...")
                    self.console.print(
                        Text(
                            f"  (Content truncated, total length: {len(content)} characters)",
                            style=RICH_STYLE_GRAY,
                        )
                    )
                else:
                    self.console.print(f"  {content}")

        # Get user response
        self.input_handler._stop_input_thread()
        while True:
            self.console.print(
                Text(
                    "\nAllow this prompt to be executed? [y]es/[n]o: ",
                    style=RICH_STYLE_YELLOW,
                ),
                end="",
            )
            response = input().lower()

            if response in ["y", "yes"]:
                # User approved, put the prompt data in the input queue
                self.console.print(
                    Text(
                        "✓ MCP prompt approved and queued for execution.",
                        style=RICH_STYLE_GREEN,
                    )
                )
                input_queue.put(prompt_data["content"])
                break
            elif response in ["n", "no"]:
                # User denied, don't queue the prompt
                self.console.print(
                    Text("❌ MCP prompt execution denied.", style=RICH_STYLE_RED)
                )
                break
            else:
                self.console.print(
                    Text(
                        "Please enter 'y' for yes or 'n' for no.",
                        style=RICH_STYLE_YELLOW,
                    )
                )

        self.input_handler._start_input_thread()
