"""
Display handlers for console UI components.
Handles rendering of various UI elements like messages, dividers, models, agents, etc.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.box import HORIZONTALS, SIMPLE, SQUARE
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual_image.renderable import Image as TextualImage

from AgentCrew.modules.chat.agent_evaluation import parse_agent_evaluation
from AgentCrew.modules.llm.token_usage import TokenUsage

from .constants import (
    CODE_THEME,
    COMMAND_HELP_MESSAGES,
    RICH_STYLE_BLUE,
    RICH_STYLE_BLUE_BOLD,
    RICH_STYLE_FILE_ACCENT_BOLD,
    RICH_STYLE_GRAY,
    RICH_STYLE_GREEN,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_RED,
    RICH_STYLE_WHITE,
    RICH_STYLE_YELLOW,
    RICH_STYLE_YELLOW_BOLD,
)
from .diff_display import DiffDisplay

if TYPE_CHECKING:
    from .console_ui import ConsoleUI


class DisplayHandlers:
    """Handles all display-related functionality for the console UI."""

    def __init__(self, console_ui: ConsoleUI):
        """Initialize the display handlers with a console instance."""
        self.console = console_ui.console
        self._ui = console_ui
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
        self.console.print(Panel(message, box=SIMPLE))

    def display_user_message(self, message: str):
        header = Text(
            "👤 YOU:",
            style=RICH_STYLE_BLUE_BOLD,
        )
        user_panel = Panel(
            Text(message),
            title=header,
            box=HORIZONTALS,
            title_align="left",
            border_style=RICH_STYLE_BLUE,
        )
        self.console.print(user_panel)

    def display_planning_message(self, message: str):
        planning_panel = Panel(
            Markdown(message, code_theme=CODE_THEME),
            title=Text("🧭 AGENT PLAN", style=RICH_STYLE_GRAY),
            box=HORIZONTALS,
            title_align="left",
            border_style=RICH_STYLE_GRAY,
        )
        self.console.print(planning_panel)

    def display_assistant_message(self, agent_name: str, message: str):
        header = Text(
            f"🤖 {agent_name.upper()}:",
            style=RICH_STYLE_GREEN_BOLD,
        )
        assistant_panel = Panel(
            Markdown(message, code_theme=CODE_THEME),
            title=header,
            box=HORIZONTALS,
            title_align="left",
            border_style=RICH_STYLE_GREEN,
        )
        self.console.print(assistant_panel)

    def display_thinking_message(self, agent_name: str, message: str):
        header = Text(
            f"🤖 {agent_name.upper()} Thought:",
            style=RICH_STYLE_GRAY,
        )
        thinking_panel = Panel(
            Markdown(message, code_theme=CODE_THEME),
            title=header,
            box=HORIZONTALS,
            title_align="left",
            border_style=RICH_STYLE_GRAY,
        )
        self.console.print(thinking_panel)

    def display_divider(self):
        """Display a divider line."""

    def print_divider(self, title="", with_time=False):
        """Display a divider line."""
        title_length = len(title) + 1 if title else 0
        time = ""
        if with_time:
            time = f" {datetime.now().strftime('%H:%M:%S')} ──"  # noqa: DTZ005 - intentional local wall-clock display
        self.console.print(
            " ─"
            + title
            + ("─" * (self.console.width - title_length - len(time) - 3))
            + time
            + " ",
            style=RICH_STYLE_BLUE,
        )

    def display_debug_info(self, debug_info):
        """Display debug information with formatting and truncation.

        Args:
            debug_info: Either a dict with 'type' and 'messages' keys (new format)
                       or a raw list of messages (legacy format)
        """
        if isinstance(debug_info, dict) and debug_info.get("type") == "system":
            self.console.print(Text("\nSystem Prompt:", style=RICH_STYLE_YELLOW))
            self.console.print(debug_info.get("system_prompt") or "")
            return

        if (
            isinstance(debug_info, dict)
            and "type" in debug_info
            and "messages" in debug_info
        ):
            msg_type = debug_info["type"]
            messages = debug_info["messages"]
            title = "Agent Messages" if msg_type == "agent" else "Chat Messages"
        else:
            title = "Messages"
            messages = debug_info

        self.console.print(
            Text(f"\n{title} ({len(messages)} messages):", style=RICH_STYLE_YELLOW)
        )

        formatted = self._format_messages_for_debug(messages)
        try:
            self.console.print(json.dumps(formatted, indent=2))
        except Exception:
            self.console.print(str(formatted))

    def _format_messages_for_debug(
        self, messages, max_content_length: int = 200
    ) -> list:
        """Format messages for debug display with truncated content.

        Args:
            messages: list of message dictionaries
            max_content_length: Maximum length for message content

        Returns:
            list of formatted message dictionaries
        """
        formatted = []

        for i, msg in enumerate(messages):
            formatted_msg = {}

            formatted_msg["#"] = i

            if "role" in msg:
                formatted_msg["role"] = msg["role"]
            if "agent" in msg:
                formatted_msg["agent"] = msg["agent"]

            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "tool" or role == "tool_result":
                if "tool_call_id" in msg:
                    formatted_msg["tool_call_id"] = msg["tool_call_id"]
                if "tool_use_id" in msg:
                    formatted_msg["tool_use_id"] = msg["tool_use_id"]
                if "tool_id" in msg:
                    formatted_msg["tool_id"] = msg["tool_id"]
                if "tool_name" in msg:
                    formatted_msg["tool_name"] = msg["tool_name"]
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )
            elif role == "assistant" and "tool_calls" in msg:
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )
                formatted_msg["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "tool_call"),
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    }
                    for tc in msg["tool_calls"]
                ]
            elif isinstance(content, list):
                formatted_content = []
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type", "unknown")
                        if item_type == "tool_use":
                            formatted_content.append(
                                {
                                    "type": "tool_use",
                                    "id": item.get("id", ""),
                                    "name": item.get("name", "unknown"),
                                    "input": item.get("input", {}),
                                }
                            )
                        elif item_type == "tool_result":
                            formatted_content.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": item.get("tool_use_id", ""),
                                    "content": self._truncate_content(
                                        item.get("content", ""), max_content_length
                                    ),
                                }
                            )
                        elif item_type == "thinking":
                            formatted_content.append(
                                {
                                    "type": item_type,
                                    "thinking": self._truncate_content(
                                        item.get("thinking", item), max_content_length
                                    ),
                                    "signature": self._truncate_content(
                                        item.get("signature", ""), max_content_length
                                    ),
                                }
                            )
                        else:
                            formatted_content.append(
                                {
                                    "type": item_type,
                                    "content": self._truncate_content(
                                        item.get("text", item), max_content_length
                                    ),
                                }
                            )
                    elif isinstance(item, str):
                        formatted_content.append(
                            {
                                "type": "text",
                                "content": self._truncate_content(
                                    item, max_content_length
                                ),
                            }
                        )
                formatted_msg["content"] = formatted_content
            else:
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )

            formatted.append(formatted_msg)

        return formatted

    def _truncate_content(
        self, content, max_length: int = 200
    ) -> str | list[dict[str, Any]]:
        """Truncate content to max_length with ellipsis.

        Args:
            content: Message content (can be string, list, or dict)
            max_length: Maximum length for the output

        Returns:
            Truncated string representation
        """
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Extract text from content blocks
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        item["text"] = self._truncate_content(item.get("text", ""))
                    elif item.get("type") == "thinking":
                        item["thinking"] = self._truncate_content(
                            f"[tool:{item.get('thinking', '')}]"
                        )
                    elif item.get("type") == "image_url":
                        item["image_url"] = self._truncate_content(
                            item.get("image_url", {}).get("url", "")
                        )
            return content
        else:
            text = str(content)

        # Clean up whitespace
        text = " ".join(text.split())

        if len(text) <= max_length:
            return text

        return text[: max_length - 3] + "..."

    def display_models(self, models_by_provider: dict):
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

    def display_agents(self, agents_info: dict):
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

    def display_conversations(
        self,
        conversations: list[dict[str, Any]],
        get_history_callback=None,
        delete_callback=None,
    ):
        """Display available conversations using interactive browser.

        Args:
            conversations: list of conversation metadata
            get_history_callback: Optional callback to fetch full conversation history
            delete_callback: Optional callback to delete conversations by IDs

        Returns:
            Selected conversation ID or None if cancelled
        """
        if not conversations:
            self.console.print(
                Text("No saved conversations found.", style=RICH_STYLE_YELLOW)
            )
            return None

        from .conversation_browser.browser import ConversationBrowser

        browser = ConversationBrowser(
            console=self.console,
            get_conversation_history=get_history_callback,
            on_delete=delete_callback,
        )
        browser.set_conversations(conversations)
        return browser.show()

    def display_consolidation_result(self, result: dict[str, Any]):
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

    def display_evolution_summary(self, proposal: dict[str, Any]):
        """Display a prompt evolution review summary."""
        summary = proposal.get("analysis_summary", {})
        table = Table(show_header=False, box=HORIZONTALS, expand=True)
        table.add_column(style=RICH_STYLE_YELLOW_BOLD, width=28)
        table.add_column(style=RICH_STYLE_WHITE)

        table.add_row("Agent", proposal.get("agent_name", "Unknown"))
        table.add_row(
            "Memories analyzed",
            str(proposal.get("source_memory_count", 0)),
        )

        for label, key in (
            ("Durable traits", "durable_traits"),
            ("Output preferences", "output_preferences"),
            ("Recurring corrections", "recurring_user_corrections"),
            ("Workflow patterns", "workflow_patterns"),
            ("Tool usage preferences", "tool_usage_preferences"),
        ):
            items = summary.get(key, [])
            value = "\n".join(f"• {item.get('item', '')}" for item in items) or "None"
            table.add_row(label, value)

        confidence_notes = summary.get("confidence_notes", [])
        if confidence_notes:
            table.add_row(
                "Confidence notes",
                "\n".join(f"• {note}" for note in confidence_notes),
            )

        editable_summary = proposal.get("user_editable_summary", "")
        panel_group = Group(
            table,
            Text("", style=RICH_STYLE_WHITE),
            Text("Editable approved summary:", style=RICH_STYLE_BLUE_BOLD),
            Markdown(
                editable_summary or "_No summary generated_", code_theme=CODE_THEME
            ),
            Text(
                "Choose Accept, Edit, or Decline in the next prompt.",
                style=RICH_STYLE_BLUE,
            ),
        )
        self.console.print(
            Panel(
                panel_group,
                box=SQUARE,
                title=Text("🧬 Prompt Evolution Review", style=RICH_STYLE_YELLOW_BOLD),
                border_style=RICH_STYLE_YELLOW,
            )
        )

    def display_prompt_evolution_result(
        self, result: dict[str, Any], max_width: int = 60
    ):
        self.console.print(
            DiffDisplay.create_summary_diff_panel(
                result.get("previous_system_prompt", ""),
                result.get("revised_system_prompt", ""),
                title=f"🧬 Prompt Evolution Result · {result.get('agent_name', 'Agent')}",
                max_width=max_width,
            )
        )

    def _display_message_images(self, msg: dict) -> int:
        """Scan message content for image_url blocks and display them.

        Handles both flat list content and nested tool_result content.

        Args:
            msg: The message dict to scan.

        Returns:
            Number of images displayed.
        """
        count = 0
        content = msg.get("content", "")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        self.display_image_from_data_uri(url)
                        count += 1
        return count

    def display_loaded_conversation(self, messages: list, default_agent_name: str):
        """Display all messages from a loaded conversation."""
        last_consolidated_idx = 0
        for i, msg in reversed(list(enumerate(messages))):
            if msg.get("role") == "consolidated":
                last_consolidated_idx = i
                break

        # Display each message in the conversation
        for msg in messages[max(last_consolidated_idx, len(messages) - 50) :]:
            role = msg.get("role")
            if role == "user":
                content = self._extract_message_content(msg)
                if content.startswith("<Transfer_Request>"):
                    transfer_text = Text("Transfered to ", style=RICH_STYLE_YELLOW)
                    transfer_text.append(f"{msg.get('agent', 'unknown')} agent")
                    self.display_message(transfer_text)
                    continue
                elif "<Tag_Action>" in content and "</Tag_Action>" in content:
                    prefix = "the user request: "
                    start = content.find(prefix)
                    end = content.find("</Tag_Action>")
                    if start != -1 and end != -1:
                        content = content[start + len(prefix) : end]
                elif content.startswith("Content of "):
                    # Still show images even if text content is suppressed
                    self._display_message_images(msg)
                    continue
                # Show images from user message first, then text
                has_image = self._display_message_images(msg)
                if content.strip():
                    self.display_user_message(content)
                elif not has_image:
                    # Only show empty message if there's nothing at all
                    self.display_user_message(content)
            elif role == "assistant":
                agent_name = msg.get("agent") or default_agent_name
                thinking = self._extract_thinking_content(msg)
                if thinking:
                    self.display_thinking_message(agent_name, thinking)
                content = self._extract_message_content(msg)
                parsed = parse_agent_evaluation(content)
                if parsed["planning_content"]:
                    self.display_planning_message(parsed["planning_content"])
                if parsed["visible_content"].strip():
                    self.display_assistant_message(
                        agent_name, parsed["visible_content"]
                    )
                if "tool_calls" in msg:
                    for tool_call in msg["tool_calls"]:
                        self._ui.tool_display.display_tool_use(tool_call)
                self.display_divider()
            elif role == "tool":
                # Display tool result messages (e.g. generate_image results)
                tool_name = msg.get("tool_name", "tool")
                has_image = self._display_message_images(msg)
                content = self._extract_message_content(msg)
                if has_image:
                    self.console.print(
                        Text(
                            f"🔧 Tool result [{tool_name}] ── image above",
                            style=RICH_STYLE_GRAY,
                        )
                    )
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

    # ── Image display ────────────────────────────────────────────────────────

    def display_image(self, image_source: str | Path) -> None:
        """Display an image in the console using textual-image renderable.

        Lazy-imports textual_image; falls back to a text message if unavailable.

        Args:
            image_source: File path string or Path to an image file.
        """
        try:
            self.console.print(TextualImage(image_source, width=30, height="auto"))
        except ImportError:
            self.console.print(
                Text(
                    f"[textual-image not installed; cannot display: {image_source}]",
                    style=RICH_STYLE_YELLOW,
                )
            )
        except Exception as e:
            self.console.print(
                Text(f"[Image display error: {e}]", style=RICH_STYLE_RED)
            )

    def display_image_from_data_uri(self, data_uri: str) -> None:
        """Decode a base64 data URI and display the image."""
        pil_image = self._data_uri_to_image(data_uri)
        if pil_image:
            try:
                self.console.print(TextualImage(pil_image, width=30, height="auto"))
            except ImportError:
                self.console.print(
                    Text(
                        "[textual-image not installed; cannot display image data]",
                        style=RICH_STYLE_YELLOW,
                    )
                )
            except Exception as e:
                self.console.print(
                    Text(f"[Image display error: {e}]", style=RICH_STYLE_RED)
                )
        else:
            self.console.print(
                Text("[Failed to decode image data]", style=RICH_STYLE_RED)
            )

    @staticmethod
    def _data_uri_to_image(data_uri: str):
        """Convert a base64 data URI to a Pillow Image."""
        import base64

        match = re.match(r"^data:([^;]+);base64,(.*)$", data_uri, re.DOTALL)
        if not match:
            return None
        try:
            image_bytes = base64.b64decode(match.group(2))
            from PIL import Image as PILImage

            return PILImage.open(BytesIO(image_bytes))
        except Exception:
            return None

    def display_token_usage(
        self,
        token_usage: TokenUsage,
        total_cost: float,
        session_cost: float,
    ):
        """Display token usage and cost information."""
        self.display_divider()
        input_tokens = token_usage.input_tokens
        output_tokens = token_usage.output_tokens
        cached_tokens = token_usage.cached_tokens
        total_token = token_usage.total_tokens
        token_info = Text("📊 Token Usage: ", style=RICH_STYLE_YELLOW)
        token_info.append(
            f"Input: {input_tokens:,} | Output: {output_tokens:,} | ",
            style=RICH_STYLE_YELLOW,
        )
        if cached_tokens > 0:
            token_info.append(
                f"Cached: {cached_tokens:,} | ",
                style=RICH_STYLE_YELLOW,
            )
        token_info.append(
            f"Total: {total_token:,} | Cost: ${total_cost:.4f} | Session: ${session_cost:.4f}",
            style=RICH_STYLE_YELLOW,
        )
        self.console.print(Panel(token_info, box=HORIZONTALS))
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
        welcome_parts = [
            Text("Press Ctrl+C twice to exit.", style=RICH_STYLE_GRAY),
            Text("Type '/exit' or '/quit' to end the session.", style=RICH_STYLE_GRAY),
        ]
        welcome_parts.extend(
            Text(msg, style=RICH_STYLE_YELLOW) for msg in COMMAND_HELP_MESSAGES
        )
        welcome_parts.extend(
            [
                Text(
                    "Tool calls require confirmation before execution.",
                    style=RICH_STYLE_BLUE,
                ),
                Text(
                    "Use 'y' to approve once, 'n' to deny, 'all' to approve future calls to the same tool.",
                    style=RICH_STYLE_BLUE,
                ),
            ]
        )
        welcome_messages = Group(*welcome_parts)

        self.console.print(
            Panel(
                welcome_messages,
                box=SQUARE,
                title=Text(
                    "🎮 Welcome to AgentCrew v" + version + " interactive chat!",
                    style=RICH_STYLE_YELLOW_BOLD,
                ),
            )
        )
        self.display_divider()

    def print_prompt_prefix(
        self,
        agent_name: str,
        model_name: str,
        yolo_mode_enabled: bool,
        reasoning_effort: str | None = None,
    ):
        """Print the prompt prefix with agent and model information."""
        title = Text(f"\n [{agent_name}", style=RICH_STYLE_RED)
        title.append(":")
        title.append(f"{model_name}", style=RICH_STYLE_BLUE)

        if reasoning_effort:
            effort = (
                reasoning_effort.name.lower()
                if hasattr(reasoning_effort, "name")
                else str(reasoning_effort).lower()
            )
            title.append(f":{effort}", style=RICH_STYLE_GREEN)

        title.append("]")

        if yolo_mode_enabled:
            title.append("\n [YOLO mode enabled]", style=RICH_STYLE_YELLOW_BOLD)

        title.append(
            f"\n (Press {'Enter' if not self._ui.input_handler.swap_enter else 'Alt+Enter'} for new line, Ctrl+S/{'Enter' if self._ui.input_handler.swap_enter else 'Alt+Enter'} to Send, Ctrl+V to paste)\n",
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

    def _extract_thinking_content(self, message):
        content = message.get("content", "")
        if isinstance(content, str):
            return None
        thinking_block = next(
            (
                c
                for c in message.get("content", [])
                if c.get("type", "text") == "thinking"
            ),
            None,
        )
        if thinking_block:
            return thinking_block.get("thinking", "")
        else:
            return None

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
                if isinstance(item, dict) and item.get("type") == "text":
                    result.append(item.get("text", ""))
                # Handle other content types if needed
            return "\n".join(result)

        return str(content)
