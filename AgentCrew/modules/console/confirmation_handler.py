"""
Confirmation handlers for console UI.
Handles tool confirmation requests and MCP prompt confirmations.
"""

from __future__ import annotations

from rich.text import Text
from rich.panel import Panel
from rich.box import HORIZONTALS
from rich.console import Group
import time

from .diff_display import DiffDisplay

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


class ConfirmationHandler:
    """Handles confirmation dialogs for tools and MCP prompts."""

    def __init__(self, console_ui: ConsoleUI):
        """Initialize the confirmation handler."""
        self.console = console_ui.console
        self._ui = console_ui
        self.input_handler = console_ui.input_handler

    def display_tool_confirmation_request(self, tool_info, message_handler):
        """Display tool confirmation request and get user response."""
        tool_use = tool_info.copy()
        confirmation_id = tool_use.pop("confirmation_id")

        # Special handling for 'ask' tool
        if tool_use["name"] == "ask":
            self._handle_ask_tool(tool_use, confirmation_id, message_handler)
            return

        # Special handling for 'write_file' tool with search/replace blocks
        if tool_use["name"] == "write_file":
            file_path = tool_use["input"].get("file_path", "")
            text_or_blocks = tool_use["input"].get("write_blocks", "")

            if DiffDisplay.has_search_replace_blocks(text_or_blocks):
                self._display_write_file_diff(tool_use, file_path, text_or_blocks)
                self._get_and_handle_tool_response(
                    tool_use, confirmation_id, message_handler
                )
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
                box=HORIZONTALS,
                title_align="left",
                border_style=RICH_STYLE_YELLOW,
            )
        )

        self._get_and_handle_tool_response(tool_use, confirmation_id, message_handler)

    def _handle_ask_tool(self, tool_use, confirmation_id, message_handler):
        """Handle the ask tool - display one question at a time with navigation."""
        questions = tool_use["input"].get("questions", [])
        if isinstance(questions, str):
            import json
            try:
                questions = json.loads(questions)
            except (json.JSONDecodeError, TypeError):
                questions = []

        if not questions or not isinstance(questions, list):
            questions = [{"question": "(missing questions)", "guided_answers": ["OK"]}]

        # Normalize each question's guided_answers
        for q in questions:
            answers = q.get("guided_answers", [])
            if isinstance(answers, str):
                q["guided_answers"] = answers.strip("\n ").splitlines()

        total = len(questions)
        current_idx = 0
        answers: dict[str, str] = {}

        self.input_handler._stop_input_thread()

        while current_idx < total:
            q = questions[current_idx]
            question_text = q.get("question", "")
            guided_answers = list(q.get("guided_answers", []))

            # Build navigation prefix
            nav_prefix = f"[{current_idx + 1}/{total}] " if total > 1 else ""

            guided_answers.append("Custom your answer")

            self.console.print(
                Text(
                    f"\n❓ {nav_prefix}Agent is asking for clarification:",
                    style=RICH_STYLE_BLUE_BOLD,
                )
            )

            # Determine choices: answers + navigation + submit
            choices = list(guided_answers)
            if total > 1:
                if current_idx > 0:
                    choices.append("← Back")
                if current_idx < total - 1:
                    choices.append("Next →")
                choices.append("Submit all answers")

            response = self.input_handler.get_choice_input(
                f"{nav_prefix}{question_text}", choices
            )

            if response == "Custom your answer":
                custom_answer = self.input_handler.get_prompt_input(
                    "Input your answer (Alt+Enter or Ctrl+S to submit):"
                )
                if custom_answer:
                    answers[str(current_idx)] = custom_answer
                current_idx += 1

            elif response == "← Back":
                current_idx -= 1

            elif response == "Next →":
                current_idx += 1

            elif response == "Submit all answers":
                break

            elif response:
                answers[str(current_idx)] = response
                current_idx += 1

            else:
                # User cancelled mid-way
                message_handler.resolve_tool_confirmation(
                    confirmation_id,
                    {"action": "answer", "answer": "Cancelled by user"},
                )
                self._ui.start_loading_animation()
                self.input_handler._start_input_thread()
                return

        # Build structured result
        answer_lines = []
        for i, q in enumerate(questions):
            idx_str = str(i)
            ans = answers.get(idx_str, "(skipped)")
            answer_lines.append(f"- {q['question']}: {ans}")

        result = "Answers:\n" + "\n".join(answer_lines)

        message_handler.resolve_tool_confirmation(
            confirmation_id, {"action": "answer", "answer": result}
        )

        self._ui.start_loading_animation()
        self.input_handler._start_input_thread()

    def _display_write_file_diff(self, tool_use, file_path, blocks):
        """Display split diff view for write_or_edit_file tool."""
        header = Text("📝 File Edit ", style=RICH_STYLE_YELLOW)
        header.append(file_path, style=RICH_STYLE_BLUE_BOLD)
        header.append(" - Search/Replace Blocks", style=RICH_STYLE_YELLOW)

        self.console.print(
            Panel(header, box=HORIZONTALS, border_style=RICH_STYLE_YELLOW)
        )

        parsed_blocks = DiffDisplay.parse_search_replace_blocks(blocks)

        if not parsed_blocks:
            self.console.print(
                Text("No valid search/replace blocks found", style=RICH_STYLE_RED)
            )
            return

        for block in parsed_blocks:
            diff_table = DiffDisplay.create_split_diff_table(
                block["search"], block["replace"], max_width=self.console.width - 4
            )
            self.console.print(diff_table)

    def _get_and_handle_tool_response(self, tool_use, confirmation_id, message_handler):
        """Get user response for tool confirmation and handle it."""
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
            deny_reason = self.input_handler.get_prompt_input(
                "Please tell me why you are denying this tool (Alt+Enter or Ctrl+S to submit): "
            )
            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "deny", "reason": deny_reason}
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
            from AgentCrew.modules.config.global_config import GlobalConfig

            GlobalConfig().write_auto_approval_tools(tool_use["name"], add=True)

            message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            saved_text = Text(
                f"✓ Tool '{tool_use['name']}' will be auto-approved forever.",
                style=RICH_STYLE_YELLOW,
            )
            self.console.print(saved_text)

        self._ui.start_loading_animation()
        self.input_handler._start_input_thread()
        time.sleep(0.2)

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
