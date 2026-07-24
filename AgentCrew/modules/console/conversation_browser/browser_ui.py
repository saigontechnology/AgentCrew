"""Conversation browser UI rendering components."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from AgentCrew.modules.chat.fork_utils import format_fork_title

from ..constants import (
    RICH_STYLE_BLUE,
    RICH_STYLE_GRAY,
    RICH_STYLE_GREEN,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_WHITE,
    RICH_STYLE_YELLOW,
    RICH_STYLE_YELLOW_BOLD,
)


class ConversationBrowserUI:
    """Handles UI rendering for the conversation browser."""

    def __init__(
        self,
        console: Console,
        get_conversation_history: Callable[[str], list[dict[str, Any]]] | None = None,
    ):
        self.console = console
        self.conversations: list[dict[str, Any]] = []
        self._all_conversations: list[dict[str, Any]] = []
        self.selected_index = 0
        self.scroll_offset = 0
        self._get_conversation_history = get_conversation_history
        self._preview_cache: dict[str, tuple[list[dict[str, Any]], int]] = {}
        self.selected_items: set[int] = set()
        self._live: Live | None = None
        self._layout: Layout | None = None
        self._search_query: str = ""
        self._search_mode: bool = False

    @property
    def max_list_items(self) -> int:
        return self.console.height - 9

    def set_conversations(self, conversations: list[dict[str, Any]]):
        """Set the conversations list to browse."""
        self._all_conversations = conversations
        self.conversations = conversations
        self.selected_index = 0
        self.scroll_offset = 0
        self._preview_cache.clear()
        self.selected_items.clear()
        self._search_query = ""
        self._search_mode = False

    @property
    def search_mode(self) -> bool:
        return self._search_mode

    @property
    def search_query(self) -> str:
        return self._search_query

    def start_search_mode(self):
        """Enter search mode, preserving previous search query."""
        self._search_mode = True

    def exit_search_mode(self, clear_filter: bool = False):
        """Exit search mode."""
        self._search_mode = False
        if clear_filter:
            self._search_query = ""
            self.conversations = self._all_conversations
            self.selected_index = 0
            self.scroll_offset = 0
            self.selected_items.clear()

    def update_search_query(self, query: str):
        """Update search query and filter conversations."""
        self._search_query = query
        self._filter_conversations()

    def append_search_char(self, char: str):
        """Append a character to search query."""
        self._search_query += char
        self._filter_conversations()

    def backspace_search(self):
        """Remove last character from search query."""
        if self._search_query:
            self._search_query = self._search_query[:-1]
        self._filter_conversations()

    def _filter_conversations(self):
        """Filter conversations based on search query."""
        if not self._search_query:
            self.conversations = self._all_conversations
        else:
            query_lower = self._search_query.lower()
            self.conversations = [
                c
                for c in self._all_conversations
                if query_lower in c.get("title", "").lower()
            ]
        self.selected_index = 0
        self.scroll_offset = 0
        self.selected_items.clear()

    def toggle_selection(self, index: int | None = None) -> bool:
        """Toggle selection state of an item. Returns True if state changed."""
        idx = index if index is not None else self.selected_index
        if idx < 0 or idx >= len(self.conversations):
            return False
        if idx in self.selected_items:
            self.selected_items.discard(idx)
        else:
            self.selected_items.add(idx)
        return True

    def clear_selections(self):
        """Clear all selected items."""
        self.selected_items.clear()

    def get_selected_conversation_ids(self) -> list[str]:
        """Get IDs of all selected conversations."""
        ids = []
        for idx in sorted(self.selected_items):
            if 0 <= idx < len(self.conversations):
                convo_id = self.conversations[idx].get("id")
                if convo_id:
                    ids.append(convo_id)
        return ids

    def remove_conversations(self, indices: list[int]):
        """Remove conversations at specified indices and update UI state."""
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.conversations):
                convo_id = self.conversations[idx].get("id")
                if convo_id:
                    self._preview_cache.pop(convo_id, None)
                del self.conversations[idx]
        self.selected_items.clear()
        if self.selected_index >= len(self.conversations):
            self.selected_index = max(0, len(self.conversations) - 1)
        if self.scroll_offset > 0 and self.scroll_offset >= len(self.conversations):
            self.scroll_offset = max(0, len(self.conversations) - self.max_list_items)

    def _format_timestamp(self, timestamp) -> str:
        """Format timestamp for display."""
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")  # noqa: DTZ006 - display formatting for user-facing timestamps
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
        left_text.append("\U0001f4da ", style="bold")
        left_text.append(f"{len(self.conversations)} ", style=RICH_STYLE_GREEN_BOLD)
        if self._search_query:
            left_text.append(
                f"/ {len(self._all_conversations)} ", style=RICH_STYLE_GRAY
            )
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

    def _create_list_panel(self, panel_height: int | None = None) -> Panel:
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
        table.add_column("Date", width=10, justify="right", no_wrap=True)

        visible_count = min(
            self.max_list_items, len(self.conversations) - self.scroll_offset
        )

        for i in range(visible_count):
            idx = self.scroll_offset + i
            convo = self.conversations[idx]
            is_cursor = idx == self.selected_index
            is_marked = idx in self.selected_items

            index_text = f"{idx + 1}"
            title = convo.get("title", "Untitled").replace("\n", " ")
            timestamp = self._format_timestamp(convo.get("timestamp"))

            is_fork = convo.get("is_fork", False)
            fork_children = convo.get("fork_children", [])
            has_children = len(fork_children) > 0

            title_display = format_fork_title(title, convo)

            mark_indicator = "\u25cf " if is_marked else "  "
            cursor_indicator = "\u25b8" if is_cursor else " "

            if is_cursor and is_marked:
                table.add_row(
                    Text(index_text, style="bold magenta"),
                    Text(
                        f"{mark_indicator}{cursor_indicator}{title_display}",
                        style="bold magenta",
                    ),
                    Text(timestamp, style="magenta"),
                )
            elif is_cursor:
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GREEN_BOLD),
                    Text(
                        f"{mark_indicator}{cursor_indicator}{title_display}",
                        style=RICH_STYLE_GREEN_BOLD,
                    ),
                    Text(timestamp, style=RICH_STYLE_GREEN),
                )
            elif is_marked:
                table.add_row(
                    Text(index_text, style="magenta"),
                    Text(f"{mark_indicator} {title_display}", style="magenta"),
                    Text(timestamp, style="magenta"),
                )
            elif is_fork:
                # Fork conversations shown in cyan
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GRAY),
                    Text(f"{mark_indicator} {title_display}", style="cyan"),
                    Text(timestamp, style=RICH_STYLE_GRAY),
                )
            elif has_children:
                # Parent with children shown in yellow
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GRAY),
                    Text(f"{mark_indicator} {title_display}", style=RICH_STYLE_YELLOW),
                    Text(timestamp, style=RICH_STYLE_GRAY),
                )
            else:
                table.add_row(
                    Text(index_text, style=RICH_STYLE_GRAY),
                    Text(f"{mark_indicator} {title_display}", style=RICH_STYLE_BLUE),
                    Text(timestamp, style=RICH_STYLE_GRAY),
                )

        scroll_parts = []
        if self.scroll_offset > 0:
            scroll_parts.append(f"\u2191{self.scroll_offset}")
        remaining = len(self.conversations) - self.scroll_offset - visible_count
        if remaining > 0:
            scroll_parts.append(f"\u2193{remaining}")

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
    ) -> tuple[list[dict[str, Any]], int]:
        """Get the most recent user-assistant exchanges for preview.

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

            assistant_indexes = [
                index
                for index, msg in enumerate(all_messages)
                if msg.get("role") == "assistant"
            ]
            max_exchanges = 4

            if len(assistant_indexes) <= max_exchanges:
                preview_messages = all_messages
            else:
                start_index = assistant_indexes[-max_exchanges]
                if (
                    start_index > 0
                    and all_messages[start_index - 1].get("role") == "user"
                ):
                    start_index -= 1
                preview_messages = all_messages[start_index:]

            total = len(all_messages)
            result = (preview_messages, total)
            self._preview_cache[convo_id] = result
            return result

        except Exception as e:
            logger.warning(f"Error fetching conversation preview: {e}")
            return [], 0

    def _create_preview_panel(self, panel_height: int | None = None) -> Panel:
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
        preview_lines.append(Text(f"\U0001f4cc {title}", style=RICH_STYLE_YELLOW_BOLD))

        convo_id = convo.get("id", "unknown")
        timestamp = self._format_timestamp(convo.get("timestamp"))

        meta_table = Table(show_header=False, box=None, padding=0, expand=True)
        meta_table.add_column("key", style=RICH_STYLE_GRAY)
        meta_table.add_column("value", style=RICH_STYLE_WHITE)

        display_id = convo_id[:24] + "\u2026" if len(convo_id) > 24 else convo_id
        meta_table.add_row("ID:", display_id)
        meta_table.add_row("Created:", timestamp)

        preview_lines.append(Text(""))
        preview_lines.append(meta_table)
        preview_lines.append(Text(""))
        preview_lines.append(Rule(title="Recent Messages", style=RICH_STYLE_GRAY))

        messages, total_messages = self._get_conversation_preview_messages(convo_id)

        if messages:
            remaining = total_messages - len(messages)
            if remaining > 0:
                preview_lines.append(Text(""))
                preview_lines.append(
                    Text(
                        f"  \u2026 and {remaining} earlier messages",
                        style=RICH_STYLE_GRAY,
                    )
                )

            exchange_count = 0
            i = 0
            while i < len(messages) and exchange_count < 4:
                msg = messages[i]
                role = msg.get("role", "unknown")
                content = msg.get("content", "")

                max_content_len = 120
                content_display = content.replace("\n", " ").strip()
                if len(content_display) > max_content_len:
                    content_display = content_display[:max_content_len] + "\u2026"

                preview_lines.append(Text(""))

                if role == "user":
                    user_header = Text()
                    user_header.append("\U0001f464 ", style="bold")
                    user_header.append("User", style=RICH_STYLE_BLUE)
                    preview_lines.append(user_header)
                    preview_lines.append(
                        Text(f"   {content_display}", style=RICH_STYLE_WHITE)
                    )
                else:
                    assistant_header = Text()
                    assistant_header.append("\U0001f916 ", style="bold")
                    assistant_header.append("Assistant", style=RICH_STYLE_GREEN)
                    preview_lines.append(assistant_header)
                    preview_lines.append(
                        Text(f"   {content_display}", style=RICH_STYLE_WHITE)
                    )
                    exchange_count += 1

                i += 1
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
        if self._search_mode:
            return self._create_search_bar()

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
        nav_text.append("\u2191/k ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Up  ", style=RICH_STYLE_GRAY)
        nav_text.append("\u2193/j ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Down  ", style=RICH_STYLE_GRAY)
        nav_text.append("gg ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("Top  ", style=RICH_STYLE_GRAY)
        nav_text.append("G ", style=RICH_STYLE_GREEN_BOLD)
        nav_text.append("End", style=RICH_STYLE_GRAY)

        action_text = Text()
        action_text.append("Enter/l ", style=RICH_STYLE_GREEN_BOLD)
        action_text.append("Load  ", style=RICH_STYLE_GRAY)
        action_text.append("v ", style=RICH_STYLE_GREEN_BOLD)
        action_text.append("Select  ", style=RICH_STYLE_GRAY)
        action_text.append("dd ", style=RICH_STYLE_GREEN_BOLD)
        action_text.append("Delete", style=RICH_STYLE_GRAY)

        page_text = Text()
        page_text.append("/ ", style=RICH_STYLE_GREEN_BOLD)
        page_text.append("Search  ", style=RICH_STYLE_GRAY)
        page_text.append("Esc/q ", style=RICH_STYLE_GREEN_BOLD)
        page_text.append("Exit", style=RICH_STYLE_GRAY)
        if self.selected_items:
            page_text.append(
                f"  ({len(self.selected_items)} selected)", style="magenta"
            )

        help_table.add_row(nav_text, action_text, page_text)

        return Panel(
            help_table,
            border_style="yellow",
            box=ROUNDED,
        )

    def _create_search_bar(self) -> Panel:
        """Create the search bar panel."""
        search_text = Text()
        search_text.append("/ ", style=RICH_STYLE_GREEN_BOLD)
        search_text.append(self._search_query, style=RICH_STYLE_WHITE)
        search_text.append("\u2588", style="blink bold cyan")

        help_text = Text()
        help_text.append("  Enter ", style=RICH_STYLE_GREEN_BOLD)
        help_text.append("Confirm  ", style=RICH_STYLE_GRAY)
        help_text.append("Esc ", style=RICH_STYLE_GREEN_BOLD)
        help_text.append("Cancel", style=RICH_STYLE_GRAY)

        search_table = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        search_table.add_column("search", justify="left", ratio=2)
        search_table.add_column("help", justify="right", ratio=1)
        search_table.add_row(search_text, help_text)

        return Panel(
            search_table,
            border_style="cyan",
            box=ROUNDED,
            title=Text("Search ", style=RICH_STYLE_YELLOW_BOLD),
        )

    def _create_layout(self) -> Layout:
        """Create the layout structure."""
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
        return layout

    def _update_layout(self):
        """Update layout panels with current content."""
        if self._layout is None:
            return
        self._layout["header"].update(self._create_header())
        self._layout["list"].update(self._create_list_panel())
        self._layout["preview"].update(self._create_preview_panel())
        self._layout["help"].update(self._create_help_panel())

    def start_live(self):
        """Start live display mode."""
        self.console.clear()
        self._layout = self._create_layout()
        self._update_layout()
        self._live = Live(
            self._layout,
            console=self.console,
            auto_refresh=False,
            screen=True,
        )
        self._live.start()
        self._live.refresh()

    def stop_live(self):
        """Stop live display mode."""
        if self._live:
            self._live.stop()
            self._live = None
        self._layout = None

    def render(self):
        """Update the display with current state."""
        if self._live and self._layout:
            self._update_layout()
            self._live.refresh()
        else:
            layout = self._create_layout()
            layout["header"].update(self._create_header())
            layout["list"].update(self._create_list_panel())
            layout["preview"].update(self._create_preview_panel())
            layout["help"].update(self._create_help_panel())
            self.console.clear()
            self.console.print(layout)

    def handle_navigation(self, direction: str) -> bool:
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

    def get_selected_conversation_id(self) -> str | None:
        """Get the ID of the currently selected conversation."""
        if 0 <= self.selected_index < len(self.conversations):
            return self.conversations[self.selected_index].get("id")
        return None

    def get_selected_conversation_index(self) -> int:
        """Get the 1-based index of the currently selected conversation."""
        return self.selected_index + 1
