"""Visual mode UI for displaying raw message content."""

from __future__ import annotations

import re
from typing import Any

from rich.box import HORIZONTALS, SIMPLE
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..constants import (
    RICH_STYLE_BLUE,
    RICH_STYLE_BLUE_BOLD,
    RICH_STYLE_GRAY,
    RICH_STYLE_GREEN,
    RICH_STYLE_GREEN_BOLD,
    RICH_STYLE_YELLOW_BOLD,
)


class VisualModeUI:
    """UI component for visual mode viewer."""

    def __init__(self, console: Console):
        self.console = console
        self._messages: list[dict[str, Any]] = []
        self._lines: list[tuple[str, str, int]] = []
        self._line_styles: list[Any] = []
        self._cursor_line = 0
        self._cursor_col = 0
        self._scroll_offset = 0
        self._horizontal_scroll = 0
        self._selection_start: tuple[int, int] | None = None
        self._selection_end: tuple[int, int] | None = None
        self._visual_mode = False
        self._live: Live | None = None
        self._layout: Layout | None = None
        self._search_mode = False
        self._search_query = ""
        self._search_matches: list[tuple[int, int]] = []
        self._current_match_idx = -1
        self._cached_search_set: set = set()

    @property
    def total_lines(self) -> int:
        return len(self._lines)

    @property
    def viewport_height(self) -> int:
        return max(5, self.console.size.height - 10)

    @property
    def viewport_width(self) -> int:
        return max(20, self.console.size.width - 10)

    @property
    def current_line_length(self) -> int:
        if 0 <= self._cursor_line < len(self._lines):
            return len(self._lines[self._cursor_line][0])
        return 0

    def set_messages(self, messages: list[dict[str, Any]]):
        self._messages = messages
        self._build_lines()
        self._cursor_line = max(0, self.total_lines - 1)
        self._cursor_col = 0
        self._scroll_offset = max(0, self.total_lines - self.viewport_height)
        self._horizontal_scroll = 0
        self._selection_start = None
        self._selection_end = None
        self._visual_mode = False
        self._search_mode = False
        self._search_query = ""
        self._search_matches = []
        self._current_match_idx = -1
        self._cached_search_set = set()

    def _extract_content(self, message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            result = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        result.append(item.get("text", ""))
                elif isinstance(item, str):
                    result.append(item)
            return "\n".join(result)
        return str(content)

    def _build_lines(self):
        self._lines = []
        self._line_styles = []
        for msg_idx, msg in enumerate(self._messages):
            role = msg.get("role", "unknown")
            if role == "tool":
                continue
            agent = msg.get("agent", "")
            content = self._extract_content(msg)
            if not content.strip():
                continue

            if role == "user":
                header_style = RICH_STYLE_BLUE_BOLD
            elif role == "assistant":
                header_style = RICH_STYLE_GREEN_BOLD
            else:
                header_style = RICH_STYLE_YELLOW_BOLD

            header = f"--- {role.upper()}"
            if agent:
                header += f" ({agent})"
            header += " ---"
            self._lines.append((header, role, msg_idx))
            self._line_styles.append(header_style)

            for line in content.split("\n"):
                self._lines.append((line, role, msg_idx))
                self._line_styles.append("white")

            self._lines.append(("", role, msg_idx))
            self._line_styles.append("white")

    def start_search_mode(self):
        self._search_mode = True
        self._search_query = ""
        self._search_matches = []
        self._current_match_idx = -1

    def exit_search_mode(self, clear_results: bool = False):
        self._search_mode = False
        if clear_results:
            self._search_query = ""
            self._search_matches = []
            self._current_match_idx = -1
            self._cached_search_set = set()

    def append_search_char(self, char: str):
        self._search_query += char
        self._perform_search()

    def backspace_search(self):
        if self._search_query:
            self._search_query = self._search_query[:-1]
            self._perform_search()

    def _perform_search(self):
        self._search_matches = []
        self._current_match_idx = -1
        self._cached_search_set = set()
        if not self._search_query:
            return

        pattern = re.escape(self._search_query.lower())
        query_len = len(self._search_query)
        for line_idx, (line_text, _, _) in enumerate(self._lines):
            for match in re.finditer(pattern, line_text.lower()):
                self._search_matches.append((line_idx, match.start()))
                for c in range(match.start(), match.start() + query_len):
                    self._cached_search_set.add((line_idx, c))

        if self._search_matches:
            self._current_match_idx = 0
            self._jump_to_match(0)

    def next_search_match(self):
        if not self._search_matches:
            return
        self._current_match_idx = (self._current_match_idx + 1) % len(
            self._search_matches
        )
        self._jump_to_match(self._current_match_idx)

    def prev_search_match(self):
        if not self._search_matches:
            return
        self._current_match_idx = (self._current_match_idx - 1) % len(
            self._search_matches
        )
        self._jump_to_match(self._current_match_idx)

    def _jump_to_match(self, match_idx: int):
        if 0 <= match_idx < len(self._search_matches):
            line_idx, col_idx = self._search_matches[match_idx]
            self._cursor_line = line_idx
            self._cursor_col = col_idx
            self._adjust_scroll()
            self._adjust_horizontal_scroll()

    def toggle_visual_mode(self):
        if self._visual_mode:
            self._visual_mode = False
            self._selection_start = None
            self._selection_end = None
        else:
            self._visual_mode = True
            self._selection_start = (self._cursor_line, self._cursor_col)
            self._selection_end = (self._cursor_line, self._cursor_col)

    def update_selection(self):
        if self._visual_mode and self._selection_start is not None:
            self._selection_end = (self._cursor_line, self._cursor_col)

    def get_selected_text(self) -> str:
        if self._selection_start is None or self._selection_end is None:
            if 0 <= self._cursor_line < len(self._lines):
                return self._lines[self._cursor_line][0]
            return ""

        start_line, start_col = self._selection_start
        end_line, end_col = self._selection_end

        if (start_line, start_col) > (end_line, end_col):
            start_line, start_col, end_line, end_col = (
                end_line,
                end_col,
                start_line,
                start_col,
            )

        if start_line == end_line:
            line_text = self._lines[start_line][0]
            return line_text[start_col : end_col + 1]

        result = []
        for i in range(start_line, end_line + 1):
            line_text = self._lines[i][0]
            if i == start_line:
                result.append(line_text[start_col:])
            elif i == end_line:
                result.append(line_text[: end_col + 1])
            else:
                result.append(line_text)
        return "\n".join(result)

    def move_cursor(self, direction: str) -> bool:
        old_line = self._cursor_line
        old_col = self._cursor_col

        if direction == "up":
            self._cursor_line = max(0, self._cursor_line - 1)
            self._cursor_col = min(self._cursor_col, self.current_line_length)
        elif direction == "down":
            self._cursor_line = min(self.total_lines - 1, self._cursor_line + 1)
            self._cursor_col = min(self._cursor_col, self.current_line_length)
        elif direction == "left":
            if self._cursor_col > 0:
                self._cursor_col -= 1
            elif self._cursor_line > 0:
                self._cursor_line -= 1
                self._cursor_col = self.current_line_length
        elif direction == "right":
            if self._cursor_col < self.current_line_length:
                self._cursor_col += 1
            elif self._cursor_line < self.total_lines - 1:
                self._cursor_line += 1
                self._cursor_col = 0
        elif direction == "line_start":
            self._cursor_col = 0
        elif direction == "line_end":
            self._cursor_col = max(0, self.current_line_length - 1)
        elif direction == "word_forward":
            self._move_word_forward()
        elif direction == "word_backward":
            self._move_word_backward()
        elif direction == "top":
            self._cursor_line = 0
            self._cursor_col = 0
        elif direction == "bottom":
            self._cursor_line = max(0, self.total_lines - 1)
            self._cursor_col = 0
        elif direction == "page_up":
            self._cursor_line = max(0, self._cursor_line - self.viewport_height)
            self._cursor_col = min(self._cursor_col, self.current_line_length)
        elif direction == "page_down":
            self._cursor_line = min(
                self.total_lines - 1, self._cursor_line + self.viewport_height
            )
            self._cursor_col = min(self._cursor_col, self.current_line_length)
        elif direction == "half_up":
            self._cursor_line = max(0, self._cursor_line - self.viewport_height // 2)
            self._cursor_col = min(self._cursor_col, self.current_line_length)
        elif direction == "half_down":
            self._cursor_line = min(
                self.total_lines - 1, self._cursor_line + self.viewport_height // 2
            )
            self._cursor_col = min(self._cursor_col, self.current_line_length)

        self._adjust_scroll()
        self._adjust_horizontal_scroll()
        self.update_selection()
        return old_line != self._cursor_line or old_col != self._cursor_col

    def _move_word_forward(self):
        if self._cursor_line >= len(self._lines):
            return
        line = self._lines[self._cursor_line][0]
        col = self._cursor_col

        while col < len(line) and line[col].isalnum():
            col += 1
        while col < len(line) and not line[col].isalnum():
            col += 1

        if col >= len(line) and self._cursor_line < self.total_lines - 1:
            self._cursor_line += 1
            self._cursor_col = 0
        else:
            self._cursor_col = min(col, max(0, len(line) - 1))

    def _move_word_backward(self):
        if self._cursor_line >= len(self._lines):
            return
        line = self._lines[self._cursor_line][0]
        col = self._cursor_col

        if col == 0 and self._cursor_line > 0:
            self._cursor_line -= 1
            line = self._lines[self._cursor_line][0]
            col = len(line)

        while col > 0 and not line[col - 1].isalnum():
            col -= 1
        while col > 0 and line[col - 1].isalnum():
            col -= 1

        self._cursor_col = col

    def _adjust_scroll(self):
        if self._cursor_line < self._scroll_offset:
            self._scroll_offset = self._cursor_line
        elif self._cursor_line >= self._scroll_offset + self.viewport_height:
            self._scroll_offset = self._cursor_line - self.viewport_height + 1

    def _adjust_horizontal_scroll(self):
        visible_width = self.viewport_width - 6
        if self._cursor_col < self._horizontal_scroll:
            self._horizontal_scroll = self._cursor_col
        elif self._cursor_col >= self._horizontal_scroll + visible_width:
            self._horizontal_scroll = self._cursor_col - visible_width + 1

    def _create_header(self) -> Panel:
        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left", ratio=1)
        header_table.add_column(justify="center", ratio=2)
        header_table.add_column(justify="right", ratio=1)

        mode_text = Text()
        if self._search_mode:
            mode_text.append("-- SEARCH --", style="bold cyan")
        elif self._visual_mode:
            mode_text.append("-- VISUAL --", style="bold yellow")
        else:
            mode_text.append("-- NORMAL --", style="bold green")

        position = (
            f"L{self._cursor_line + 1}:C{self._cursor_col + 1} [{self.total_lines}]"
        )
        title = "Visual Mode - Raw Content Viewer"

        header_table.add_row(
            Text(title, style=RICH_STYLE_YELLOW_BOLD),
            mode_text,
            Text(position, style=RICH_STYLE_GRAY),
        )

        return Panel(header_table, box=SIMPLE, style=RICH_STYLE_BLUE)

    def _is_position_selected(self, line: int, col: int) -> bool:
        if self._selection_start is None or self._selection_end is None:
            return False

        start_line, start_col = self._selection_start
        end_line, end_col = self._selection_end

        if (start_line, start_col) > (end_line, end_col):
            start_line, start_col, end_line, end_col = (
                end_line,
                end_col,
                start_line,
                start_col,
            )

        if line < start_line or line > end_line:
            return False
        if line == start_line and line == end_line:
            return start_col <= col <= end_col
        if line == start_line:
            return col >= start_col
        if line == end_line:
            return col <= end_col
        return True

    def _is_search_match(self, line: int, col: int) -> bool:
        if not self._search_query or not self._search_matches:
            return False
        query_len = len(self._search_query)
        for match_line, match_col in self._search_matches:
            if line == match_line and match_col <= col < match_col + query_len:
                return True
        return False

    def _create_content_panel(self) -> Panel:
        content = Text()
        end_line = min(self._scroll_offset + self.viewport_height, self.total_lines)
        visible_width = self.viewport_width - 6

        sel_start = None
        sel_end = None
        if self._selection_start is not None and self._selection_end is not None:
            s_line, s_col = self._selection_start
            e_line, e_col = self._selection_end
            if (s_line, s_col) > (e_line, e_col):
                s_line, s_col, e_line, e_col = e_line, e_col, s_line, s_col
            sel_start = (s_line, s_col)
            sel_end = (e_line, e_col)

        for i in range(self._scroll_offset, end_line):
            line_text, _, _ = self._lines[i]
            base_style = self._line_styles[i]

            line_num = f"{i + 1:4d} "
            content.append(line_num, style=RICH_STYLE_GRAY)

            visible_text = line_text[
                self._horizontal_scroll : self._horizontal_scroll + visible_width
            ]

            if not visible_text and i == self._cursor_line and self._cursor_col == 0:
                content.append(" ", style="reverse")
            else:
                segment_start = 0
                current_style = None

                for col_idx, _ in enumerate(visible_text):
                    actual_col = self._horizontal_scroll + col_idx
                    is_cursor = (
                        i == self._cursor_line and actual_col == self._cursor_col
                    )
                    is_selected = False
                    if sel_start and sel_end:
                        if sel_start[0] == sel_end[0] == i:
                            is_selected = sel_start[1] <= actual_col <= sel_end[1]
                        elif sel_start[0] == i:
                            is_selected = actual_col >= sel_start[1]
                        elif sel_end[0] == i:
                            is_selected = actual_col <= sel_end[1]
                        elif sel_start[0] < i < sel_end[0]:
                            is_selected = True
                    is_match = (i, actual_col) in self._cached_search_set

                    if is_cursor:
                        char_style = "reverse"
                    elif is_selected:
                        char_style = "on blue"
                    elif is_match:
                        char_style = "on yellow black"
                    else:
                        char_style = base_style

                    if char_style != current_style:
                        if current_style is not None and segment_start < col_idx:
                            content.append(
                                visible_text[segment_start:col_idx], style=current_style
                            )
                        segment_start = col_idx
                        current_style = char_style

                if current_style is not None and segment_start < len(visible_text):
                    content.append(visible_text[segment_start:], style=current_style)

                if (
                    i == self._cursor_line
                    and self._cursor_col >= len(line_text)
                    and self._cursor_col == self._horizontal_scroll + len(visible_text)
                ):
                    content.append(" ", style="reverse")

            content.append("\n")

        scroll_info = ""
        if self._scroll_offset > 0:
            scroll_info += f"↑ {self._scroll_offset} more "
        remaining = self.total_lines - end_line
        if remaining > 0:
            scroll_info += f"↓ {remaining} more"
        if self._horizontal_scroll > 0:
            scroll_info += f" ← {self._horizontal_scroll}"

        return Panel(
            content,
            box=HORIZONTALS,
            subtitle=Text(scroll_info, style=RICH_STYLE_GRAY) if scroll_info else None,
            border_style=RICH_STYLE_GREEN,
            height=self.viewport_height + 2,
        )

    def _create_search_bar(self) -> Panel:
        search_text = Text()
        search_text.append("/", style="bold cyan")
        search_text.append(self._search_query, style="white")
        search_text.append("█", style="blink")

        if self._search_matches:
            match_info = f" [{self._current_match_idx + 1}/{len(self._search_matches)}]"
            search_text.append(match_info, style=RICH_STYLE_GRAY)
        elif self._search_query:
            search_text.append(" [no matches]", style="red")

        return Panel(search_text, box=SIMPLE, border_style="cyan")

    def _create_help_panel(self) -> Panel:
        help_table = Table.grid(expand=True)
        help_table.add_column(justify="left", ratio=1)
        help_table.add_column(justify="left", ratio=1)
        help_table.add_column(justify="left", ratio=1)

        if self._search_mode:
            nav_text = Text()
            nav_text.append("Enter", style="bold")
            nav_text.append(": confirm  ")
            nav_text.append("Esc", style="bold")
            nav_text.append(": cancel")

            action_text = Text()
            action_text.append("n/N", style="bold")
            action_text.append(": next/prev match")

            exit_text = Text()
            exit_text.append("Backspace", style="bold")
            exit_text.append(": delete char")
        else:
            nav_text = Text()
            nav_text.append("h/j/k/l", style="bold")
            nav_text.append(": move  ")
            nav_text.append("w/b", style="bold")
            nav_text.append(": word  ")
            nav_text.append("0/$", style="bold")
            nav_text.append(": line start/end")

            action_text = Text()
            action_text.append("v", style="bold")
            action_text.append(": visual  ")
            action_text.append("y", style="bold")
            action_text.append(": yank  ")
            action_text.append("/", style="bold")
            action_text.append(": search")

            exit_text = Text()
            exit_text.append("gg/G", style="bold")
            exit_text.append(": top/bottom  ")
            exit_text.append("q/Esc", style="bold")
            exit_text.append(": quit")

        help_table.add_row(nav_text, action_text, exit_text)

        return Panel(help_table, box=SIMPLE, border_style=RICH_STYLE_GRAY)

    def _create_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="content"),
            Layout(name="search", size=3),
            Layout(name="help", size=3),
        )
        return layout

    def _update_layout(self):
        if self._layout:
            self._layout["header"].update(self._create_header())
            self._layout["content"].update(self._create_content_panel())
            if self._search_mode:
                self._layout["search"].update(self._create_search_bar())
                self._layout["search"].visible = True
            else:
                self._layout["search"].update("")
                self._layout["search"].visible = False
            self._layout["help"].update(self._create_help_panel())

    def render(self):
        if self._live and self._layout:
            self._update_layout()
            self._live.refresh()

    def start_live(self):
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
        if self._live:
            self._live.stop()
            self._live = None
        self._layout = None
