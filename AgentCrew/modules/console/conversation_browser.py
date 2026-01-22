"""Conversation browser with split-panel interface.
Provides Rich-based UI for listing and loading conversations with preview.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional, Callable, Tuple, Tuple
from datetime import datetime

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.rule import Rule
from rich.box import ROUNDED

from loguru import logger

from .constants import (
    RICH_STYLE_YELLOW,
    RICH_STYLE_YELLOW_BOLD,
    RICH_STYLE_BLUE,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_GREEN,
    RICH_STYLE_GRAY,
    RICH_STYLE_WHITE,
)


class ConversationBrowser:
    """Interactive conversation browser with split-panel layout."""

    def __init__(
        self,
        console: Console,
        get_conversation_history: Optional[
            Callable[[str], List[Dict[str, Any]]]
        ] = None,
    ):
        """Initialize the conversation browser.

        Args:
            console: Rich console for rendering
            get_conversation_history: Optional callback to fetch full conversation history
        """
        self.console = console
        self.conversations: List[Dict[str, Any]] = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.max_list_items = 50
        self._running = False
        self._get_conversation_history = get_conversation_history
        self._preview_cache: Dict[str, Tuple[List[Dict[str, Any]], int]] = {}
        self._g_pressed = False

    def set_conversations(self, conversations: List[Dict[str, Any]]):
        """Set the conversations list to browse."""
        self.conversations = conversations
        self.selected_index = 0
        self.scroll_offset = 0
        self._preview_cache.clear()

    def _format_timestamp(self, timestamp) -> str:
        """Format timestamp for display."""
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        if isinstance(timestamp, str):
            try:
                dt = datetime.fromisoformat(timestamp)
                return dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                return timestamp
        return str(timestamp) if timestamp else "Unknown"

    def _create_header(self) -> Panel:
        """Create the header panel with title and info."""
        header_table = Table(
            show_header=False,
            show_edge=False,
            expand=True,
            box=None,
            padding=0,
        )
        header_table.add_column("left", justify="left", ratio=1)
        header_table.add_column("center", justify="center", ratio=2)
        header_table.add_column("right", justify="right", ratio=1)

        left_text = Text()
        left_text.append("📚 ", style="bold")
        left_text.append(f"{len(self.conversations)} ", style=RICH_STYLE_GREEN_BOLD)
        left_text.append("conversations", style=RICH_STYLE_GRAY)

        center_text = Text()
        center_text.append("Conversation History", style=RICH_STYLE_YELLOW_BOLD)

        right_text = Text()
        if self.conversations:
            right_text.append(f"{self.selected_index + 1}", style=RICH_STYLE_GREEN_BOLD)
            right_text.append(f"/{len(self.conversations)}", style=RICH_STYLE_GRAY)

        header_table.add_row(left_text, center_text, right_text)

        return Panel(
            header_table,
            border_style="cyan",
            box=ROUNDED,
            padding=(0, 1),
        )

    def _create_list_panel(self, panel_height: Optional[int] = None) -> Panel:
        """Create the left panel with conversation list."""
        if not self.conversations:
            empty_content = Group(
                Text(""),
                Text("  No conversations found", style=RICH_STYLE_GRAY),
                Text(""),
                Text("  Start chatting to create one!", style=RICH_STYLE_YELLOW),
            )
            return Panel(
                empty_content,
                title=Text("Conversations ", style=RICH_STYLE_YELLOW_BOLD),
                border_style="blue",
                box=ROUNDED,
            )

        table = Table(
            show_header=True,
            show_footer=False,
            expand=True,
            box=None,
            padding=(0, 1),
            header_style=RICH_STYLE_YELLOW_BOLD,
        )
        table.add_column("#", width=5, justify="right", no_wrap=True)
        table.add_column("Title", no_wrap=True, overflow="ellipsis")
        table.add_column("Date", width=16, justify="right", no_wrap=True)

        visible_count = min(
            self.max_list_items, len(self.conversations) - self.scroll_offset
        )

        for i in range(visible_count):
            idx = self.scroll_offset + i
            convo = self.conversations[idx]
            is_selected = idx == self.selected_index

            index_text = f"{idx + 1}"
            title = convo.get("title", "Untitled")
            timestamp = self._format_timestamp(convo.get("timestamp"))

            if is_selected:
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GREEN_BOLD),
                    Text(f"▸ {title}", style=RICH_STYLE_GREEN_BOLD),
                    Text(timestamp, style=RICH_STYLE_GREEN),
                )
            else:
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GRAY),
                    Text(f"  {title}", style=RICH_STYLE_BLUE),
                    Text(timestamp, style=RICH_STYLE_GRAY),
                )

        scroll_parts = []
        if self.scroll_offset > 0:
            scroll_parts.append(f"↑{self.scroll_offset}")
        remaining = len(self.conversations) - self.scroll_offset - visible_count
        if remaining > 0:
            scroll_parts.append(f"↓{remaining}")

        subtitle = None
        if scroll_parts:
            subtitle = Text(" ".join(scroll_parts), style=RICH_STYLE_GRAY)

        return Panel(
            table,
            title=Text("Conversations ", style=RICH_STYLE_YELLOW_BOLD),
            subtitle=subtitle,
            border_style="blue",
            box=ROUNDED,
        )

    def _get_conversation_preview_messages(
        self, convo_id: str
    ) -> tuple[List[Dict[str, Any]], int]:
        """Get first 4 user-assistant exchanges for preview.

        Returns:
            Tuple of (preview_messages, total_filtered_messages)
        """
        if convo_id in self._preview_cache:
            return self._preview_cache[convo_id]

        if not self._get_conversation_history:
            return [], 0

        try:
            history = self._get_conversation_history(convo_id)
            if not history:
                return [], 0

            all_messages = []
            for msg in history:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role in ["user", "assistant"]:
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        if content.startswith("Memories related to the user request:"):
                            continue
                        if content.startswith("Content of "):
                            continue
                        all_messages.append({"role": role, "content": content})
                    elif isinstance(content, list):
                        text_content = ""
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_content = block.get("text", "")
                                break
                        if text_content.strip():
                            if text_content.startswith(
                                "Memories related to the user request:"
                            ):
                                continue
                            if text_content.startswith("Content of "):
                                continue
                            all_messages.append({"role": role, "content": text_content})

            preview_messages = []
            exchanges = 0
            max_exchanges = 4

            for msg in all_messages:
                preview_messages.append(msg)
                if msg.get("role") == "assistant":
                    exchanges += 1
                    if exchanges >= max_exchanges:
                        break

            total = len(all_messages)
            result = (preview_messages, total)
            self._preview_cache[convo_id] = result
            return result

        except Exception as e:
            logger.warning(f"Error fetching conversation preview: {e}")
            return [], 0

    def _create_preview_panel(self, panel_height: Optional[int] = None) -> Panel:
        """Create the right panel with conversation preview."""
        if not self.conversations or self.selected_index >= len(self.conversations):
            empty_content = Group(
                Text(""),
                Text("  Select a conversation to preview", style=RICH_STYLE_GRAY),
            )
            return Panel(
                empty_content,
                title=Text("Preview ", style=RICH_STYLE_YELLOW_BOLD),
                border_style="green",
                box=ROUNDED,
            )

        convo = self.conversations[self.selected_index]
        preview_lines = []

        title = convo.get("title", "Untitled")
        preview_lines.append(Text(f"📌 {title}", style=RICH_STYLE_YELLOW_BOLD))

        convo_id = convo.get("id", "unknown")
        timestamp = self._format_timestamp(convo.get("timestamp"))

        meta_table = Table(show_header=False, box=None, padding=0, expand=True)
        meta_table.add_column("key", style=RICH_STYLE_GRAY)
        meta_table.add_column("value", style=RICH_STYLE_WHITE)

        display_id = convo_id[:24] + "…" if len(convo_id) > 24 else convo_id
        meta_table.add_row("ID:", display_id)
        meta_table.add_row("Created:", timestamp)

        preview_lines.append(Text(""))
        preview_lines.append(meta_table)
        preview_lines.append(Text(""))
        preview_lines.append(Rule(title="Messages", style=RICH_STYLE_GRAY))

        messages, total_messages = self._get_conversation_preview_messages(convo_id)

        if messages:
            exchange_count = 0
            i = 0
            while i < len(messages) and exchange_count < 4:
                msg = messages[i]
                role = msg.get("role", "unknown")
                content = msg.get("content", "")

                max_content_len = 120
                content_display = content.replace("\n", " ").strip()
                if len(content_display) > max_content_len:
                    content_display = content_display[:max_content_len] + "…"

                preview_lines.append(Text(""))

                if role == "user":
                    user_header = Text()
                    user_header.append("👤 ", style="bold")
                    user_header.append("User", style=RICH_STYLE_BLUE)
                    preview_lines.append(user_header)
                    preview_lines.append(
                        Text(f"   {content_display}", style=RICH_STYLE_WHITE)
                    )
                else:
                    assistant_header = Text()
                    assistant_header.append("🤖 ", style="bold")
                    assistant_header.append("Assistant", style=RICH_STYLE_GREEN)
                    preview_lines.append(assistant_header)
                    preview_lines.append(
                        Text(f"   {content_display}", style=RICH_STYLE_WHITE)
                    )
                    exchange_count += 1

                i += 1

            remaining = total_messages - len(messages)
            if remaining > 0:
                preview_lines.append(Text(""))
                preview_lines.append(Rule(style=RICH_STYLE_GRAY))
                preview_lines.append(
                    Text(f"  … and {remaining} more messages", style=RICH_STYLE_GRAY)
                )
        else:
            basic_preview = convo.get("preview", "No preview available")
            preview_lines.append(Text(""))
            preview_lines.append(Text(f"  {basic_preview}", style=RICH_STYLE_WHITE))

        return Panel(
            Group(*preview_lines),
            title=Text("Preview ", style=RICH_STYLE_YELLOW_BOLD),
            border_style="green",
            box=ROUNDED,
        )

    def _create_help_panel(self) -> Panel:
        """Create the help panel with keyboard shortcuts."""
        help_table = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        help_table.add_column("section1", justify="left", ratio=1)
        help_table.add_column("section2", justify="center", ratio=1)
        help_table.add_column("section3", justify="right", ratio=1)

        nav_text = Text()
        nav_text.append("↑/k ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Up  ", style=RICH_STYLE_GRAY)
        nav_text.append("↓/j ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Down  ", style=RICH_STYLE_GRAY)
        nav_text.append("gg ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Top  ", style=RICH_STYLE_GRAY)
        nav_text.append("G ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("End", style=RICH_STYLE_GRAY)

        action_text = Text()
        action_text.append("Enter/l ", style=RICH_STYLE_GREEN_BOLD)
        action_text.append("Load  ", style=RICH_STYLE_GRAY)
        action_text.append("Esc/q ", style=RICH_STYLE_GREEN_BOLD)
        action_text.append("Exit", style=RICH_STYLE_GRAY)

        page_text = Text()
        page_text.append("PgUp/Ctrl+U ", style=RICH_STYLE_GREEN_BOLD)
        page_text.append("Page Up  ", style=RICH_STYLE_GRAY)
        page_text.append("PgDn/Ctrl+D ", style=RICH_STYLE_GREEN_BOLD)
        page_text.append("Page Down", style=RICH_STYLE_GRAY)

        help_table.add_row(nav_text, action_text, page_text)

        return Panel(
            help_table,
            border_style="yellow",
            box=ROUNDED,
        )

    def _render(self):
        """Render the split-panel interface."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="help", size=3),
        )

        layout["main"].split_row(
            Layout(name="list", ratio=1, minimum_size=40),
            Layout(name="preview", ratio=1, minimum_size=40),
        )

        layout["header"].update(self._create_header())
        layout["list"].update(self._create_list_panel())
        layout["preview"].update(self._create_preview_panel())
        layout["help"].update(self._create_help_panel())

        self.console.clear()
        self.console.print(layout)

    def _handle_navigation(self, direction: str) -> bool:
        """Handle navigation (up/down/top/bottom). Returns True if selection changed."""
        if not self.conversations:
            return False

        old_index = self.selected_index

        if direction == "up" and self.selected_index > 0:
            self.selected_index -= 1
        elif direction == "down" and self.selected_index < len(self.conversations) - 1:
            self.selected_index += 1
        elif direction == "top":
            self.selected_index = 0
        elif direction == "bottom":
            self.selected_index = len(self.conversations) - 1
        elif direction == "page_up":
            self.selected_index = max(0, self.selected_index - self.max_list_items)
        elif direction == "page_down":
            self.selected_index = min(
                len(self.conversations) - 1, self.selected_index + self.max_list_items
            )

        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + self.max_list_items:
            self.scroll_offset = self.selected_index - self.max_list_items + 1

        return self.selected_index != old_index

    def get_selected_conversation_id(self) -> Optional[str]:
        """Get the ID of the currently selected conversation."""
        if 0 <= self.selected_index < len(self.conversations):
            return self.conversations[self.selected_index].get("id")
        return None

    def get_selected_conversation_index(self) -> int:
        """Get the 1-based index of the currently selected conversation."""
        return self.selected_index + 1

    def show(self) -> Optional[str]:
        """Show the interactive conversation browser.

        Returns:
            The ID of the selected conversation, or None if cancelled.
        """
        if not self.conversations:
            self.console.print(
                Text("No conversations available.", style=RICH_STYLE_YELLOW)
            )
            return None

        self._running = True
        self._g_pressed = False
        selected_id = None

        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys

        self._render()

        kb = KeyBindings()

        @kb.add(Keys.Up)
        @kb.add("k")
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("up"):
                self._render()

        @kb.add(Keys.Down)
        @kb.add("j")
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("down"):
                self._render()

        @kb.add(Keys.ControlP)
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("up"):
                self._render()

        @kb.add(Keys.ControlN)
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("down"):
                self._render()

        @kb.add("g")
        def _(event):
            if self._g_pressed:
                self._g_pressed = False
                if self._handle_navigation("top"):
                    self._render()
            else:
                self._g_pressed = True

        @kb.add("G")
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("bottom"):
                self._render()

        @kb.add(Keys.ControlU)
        @kb.add(Keys.PageUp)
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("page_up"):
                self._render()

        @kb.add(Keys.ControlD)
        @kb.add(Keys.PageDown)
        def _(event):
            self._g_pressed = False
            if self._handle_navigation("page_down"):
                self._render()

        @kb.add(Keys.Enter)
        @kb.add("l")
        def _(event):
            nonlocal selected_id
            self._g_pressed = False
            selected_id = self.get_selected_conversation_id()
            event.app.exit()

        @kb.add(Keys.Escape)
        @kb.add("q")
        def _(event):
            self._g_pressed = False
            event.app.exit()

        @kb.add(Keys.ControlC)
        def _(event):
            self._g_pressed = False
            event.app.exit()

        @kb.add(Keys.Any)
        def _(event):
            self._g_pressed = False

        try:
            session = PromptSession(key_bindings=kb)
            session.prompt("")
        except (KeyboardInterrupt, EOFError):
            pass
        except Exception as e:
            logger.error(f"Error in conversation browser: {e}")

        self._running = False
        return selected_id
