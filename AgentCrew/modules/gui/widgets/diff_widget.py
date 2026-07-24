"""
Diff display widget for GUI showing file changes with split view.
Provides visual diff comparison for search/replace blocks.
"""

import difflib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class DiffWidget(QWidget):
    """Widget to display split diff view for file changes."""

    def __init__(self, parent=None, style_provider=None):
        super().__init__(parent)
        self._style_provider = style_provider
        self._colors = self._get_colors()
        self.setup_ui()

    def _get_colors(self):
        """Get colors from style provider or use defaults."""
        if self._style_provider:
            return self._style_provider.get_diff_colors()
        return {
            "background": "#1e1e2e",
            "panel_bg": "#313244",
            "header_bg": "#45475a",
            "header_text": "#cdd6f4",
            "line_number_bg": "#181825",
            "line_number_text": "#6c7086",
            "removed_bg": "#3b2d33",
            "removed_text": "#f38ba8",
            "removed_highlight": "#f38ba8",
            "added_bg": "#2d3b33",
            "added_text": "#a6e3a1",
            "added_highlight": "#a6e3a1",
            "unchanged_text": "#6c7086",
            "border": "#45475a",
            "block_header_bg": "#585b70",
            "block_header_text": "#b4befe",
        }

    def set_style_provider(self, style_provider):
        """Update style provider and refresh colors."""
        self._style_provider = style_provider
        self._colors = self._get_colors()

    def setup_ui(self):
        """Setup the main layout."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(8)

    @staticmethod
    def has_search_replace_blocks(blocks: list[dict]) -> bool:
        """Check if input is a valid list of search/replace blocks."""
        if not isinstance(blocks, list):
            return False
        return len(blocks) > 0 and all(
            isinstance(b, dict) and "search" in b and "replace" in b for b in blocks
        )

    @staticmethod
    def parse_search_replace_blocks(blocks: list[dict]) -> list[dict]:
        """
        Parse search/replace blocks from list format.

        Args:
            blocks: list of dicts with 'search' and 'replace' keys

        Returns:
            list of dicts with 'index', 'search', and 'replace' keys
        """
        if not isinstance(blocks, list):
            return []

        return [
            {
                "index": i,
                "search": block.get("search", ""),
                "replace": block.get("replace", ""),
            }
            for i, block in enumerate(blocks)
            if isinstance(block, dict)
        ]

    def set_diff_content(self, blocks: list[dict], file_path: str = ""):
        """
        Set the diff content to display.

        Args:
            blocks: list of search/replace block dicts
            file_path: Optional file path to display in header
        """
        self._clear_layout()
        colors = self._colors

        if file_path:
            header = QLabel(f"📝 File: {file_path}")
            header.setStyleSheet(
                f"font-weight: bold; font-size: 13px; padding: 4px; color: {colors['block_header_text']};"
            )
            self.main_layout.addWidget(header)

        blocks = self.parse_search_replace_blocks(blocks)

        if not blocks:
            no_blocks_label = QLabel("No valid search/replace blocks found")
            no_blocks_label.setStyleSheet(
                f"color: {colors['removed_text']}; padding: 8px;"
            )
            self.main_layout.addWidget(no_blocks_label)
            return

        for idx, block in enumerate(blocks):
            if idx > 0:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setStyleSheet(
                    f"background-color: {colors['border']}; margin: 4px 0;"
                )
                self.main_layout.addWidget(separator)

            block_widget = self._create_diff_block(
                block["search"], block["replace"], idx + 1
            )
            self.main_layout.addWidget(block_widget)

    def _clear_layout(self):
        """Clear all widgets from the layout."""
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _create_diff_block(
        self, original: str, modified: str, block_num: int
    ) -> QWidget:
        """Create a single diff block widget."""
        colors = self._colors
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)

        block_header = QLabel(f"Block {block_num}")
        block_header.setStyleSheet(
            f"font-weight: bold; font-size: 11px; color: {colors['header_text']}; padding: 2px;"
        )
        container_layout.addWidget(block_header)

        # diff_container = QWidget()
        # diff_layout = QHBoxLayout(diff_container)
        # diff_layout.setContentsMargins(0, 0, 0, 0)
        # diff_layout.setSpacing(8)
        #
        # original_panel = self._create_side_panel("Original", original, is_original=True)
        # modified_panel = self._create_side_panel(
        #     "Modified", modified, is_original=False
        # )
        #
        # diff_layout.addWidget(original_panel, 1)
        # diff_layout.addWidget(modified_panel, 1)

        # container_layout.addWidget(diff_container)

        diff_view = self._create_inline_diff(original, modified)
        container_layout.addWidget(diff_view)

        return container

    def _create_side_panel(
        self, title: str, content: str, is_original: bool
    ) -> QWidget:
        """Create a side panel for original or modified content."""
        colors = self._colors
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        bg_color = colors["removed_bg"] if is_original else colors["added_bg"]
        border_color = colors["removed_text"] if is_original else colors["added_text"]
        text_color = colors["removed_text"] if is_original else colors["added_text"]

        panel.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 4px;
            }}
        """
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        header = QLabel(title)
        header.setStyleSheet(
            f"font-weight: bold; font-size: 11px; color: {text_color}; padding: 2px;"
        )
        layout.addWidget(header)

        content_label = QLabel(content if content else "(empty)")
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content_label.setStyleSheet(
            f"""
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 12px;
            color: {text_color};
            padding: 4px;
        """
        )
        layout.addWidget(content_label)

        return panel

    def _create_inline_diff(self, original: str, modified: str) -> QWidget:
        """Create inline diff view with character-level highlighting."""
        colors = self._colors
        container = QFrame()
        container.setStyleSheet(
            f"""
            QFrame {{
                background-color: {colors["background"]};
                border: 1px solid {colors["border"]};
                border-radius: 4px;
            }}
        """
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        original_lines = original.split("\n")
        modified_lines = modified.split("\n")

        matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for i in range(i1, i2):
                    line_label = self._create_diff_line(
                        f"  {original_lines[i]}", "equal"
                    )
                    layout.addWidget(line_label)

            elif tag == "delete":
                for i in range(i1, i2):
                    line_label = self._create_diff_line(
                        f"- {original_lines[i]}", "delete"
                    )
                    layout.addWidget(line_label)

            elif tag == "insert":
                for j in range(j1, j2):
                    line_label = self._create_diff_line(
                        f"+ {modified_lines[j]}", "insert"
                    )
                    layout.addWidget(line_label)

            elif tag == "replace":
                max_lines = max(i2 - i1, j2 - j1)

                for offset in range(max_lines):
                    orig_idx = i1 + offset
                    mod_idx = j1 + offset

                    if orig_idx < i2:
                        line_label = self._create_diff_line(
                            f"- {original_lines[orig_idx]}", "delete"
                        )
                        layout.addWidget(line_label)

                    if mod_idx < j2:
                        line_label = self._create_diff_line(
                            f"+ {modified_lines[mod_idx]}", "insert"
                        )
                        layout.addWidget(line_label)

        return container

    def _create_diff_line(self, text: str, line_type: str) -> QLabel:
        """Create a single diff line label with appropriate styling."""
        colors = self._colors
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        base_font = "font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 11px;"

        if line_type == "equal":
            style = f"{base_font} color: {colors['unchanged_text']}; padding: 1px 4px;"
        elif line_type == "delete":
            style = f"{base_font} color: {colors['removed_text']}; background-color: {colors['removed_bg']}; padding: 1px 4px; border-radius: 2px;"
        elif line_type == "insert":
            style = f"{base_font} color: {colors['added_text']}; background-color: {colors['added_bg']}; padding: 1px 4px; border-radius: 2px;"
        else:
            style = f"{base_font} color: {colors['header_text']}; padding: 1px 4px;"

        label.setStyleSheet(style)
        return label


class CompactDiffWidget(QWidget):
    """Compact diff widget for use inside ToolWidget."""

    def __init__(self, parent=None, style_provider=None):
        super().__init__(parent)
        self._style_provider = style_provider
        self._colors = self._get_colors()
        self.setup_ui()

    def _get_colors(self):
        """Get colors from style provider or use defaults."""
        if self._style_provider:
            return self._style_provider.get_diff_colors()
        return {
            "background": "#1e1e2e",
            "panel_bg": "#313244",
            "border": "#45475a",
            "removed_bg": "#2d0a0a",
            "removed_text": "#f38ba8",
            "added_bg": "#0a2d0a",
            "added_text": "#a6e3a1",
            "unchanged_text": "#585b70",
            "line_number_text": "#6c7086",
        }

    def set_style_provider(self, style_provider):
        """Update style provider and refresh colors."""
        self._style_provider = style_provider
        self._colors = self._get_colors()

    def setup_ui(self):
        """Setup the compact layout."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)

    def set_diff_content(self, blocks: list[dict], file_path: str = ""):
        """Set the diff content to display in compact form."""
        self._clear_layout()
        colors = self._colors

        blocks = DiffWidget.parse_search_replace_blocks(blocks)

        if not blocks:
            return

        for idx, block in enumerate(blocks):
            if idx > 0:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFixedHeight(1)
                separator.setStyleSheet(f"background-color: {colors['border']};")
                self.main_layout.addWidget(separator)

            diff_view = self._create_compact_diff(
                block["search"], block["replace"], idx + 1
            )
            self.main_layout.addWidget(diff_view)

    def _clear_layout(self):
        """Clear all widgets from the layout."""
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _create_compact_diff(
        self, original: str, modified: str, block_num: int
    ) -> QWidget:
        """Create a compact inline diff view."""
        colors = self._colors
        container = QFrame()
        container.setStyleSheet(
            f"""
            QFrame {{
                background-color: {colors["background"]};
                border: 1px solid {colors["panel_bg"]};
                border-radius: 4px;
            }}
        """
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(1)

        if block_num > 1:
            block_header = QLabel(f"Block {block_num}")
            block_header.setStyleSheet(
                f"font-size: 10px; color: {colors['line_number_text']}; padding: 0 0 2px 0;"
            )
            layout.addWidget(block_header)

        original_lines = original.split("\n")
        modified_lines = modified.split("\n")

        matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)

        max_display_lines = 20
        line_count = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if line_count >= max_display_lines:
                truncate_label = QLabel("... (diff truncated)")
                truncate_label.setStyleSheet(
                    f"font-size: 10px; color: {colors['line_number_text']}; font-style: italic;"
                )
                layout.addWidget(truncate_label)
                break

            if tag == "equal":
                lines_to_show = min(2, i2 - i1)
                if i2 - i1 > 2:
                    context_label = QLabel(f"  ... ({i2 - i1 - 2} unchanged lines)")
                    context_label.setStyleSheet(
                        f"font-size: 10px; color: {colors['unchanged_text']}; font-style: italic;"
                    )
                    layout.addWidget(context_label)
                    line_count += 1

                for i in range(i1, min(i1 + lines_to_show, i2)):
                    line_label = self._create_compact_line(
                        f"  {original_lines[i]}", "equal"
                    )
                    layout.addWidget(line_label)
                    line_count += 1

            elif tag == "delete":
                for i in range(i1, i2):
                    if line_count >= max_display_lines:
                        break
                    line_label = self._create_compact_line(
                        f"- {original_lines[i]}", "delete"
                    )
                    layout.addWidget(line_label)
                    line_count += 1

            elif tag == "insert":
                for j in range(j1, j2):
                    if line_count >= max_display_lines:
                        break
                    line_label = self._create_compact_line(
                        f"+ {modified_lines[j]}", "insert"
                    )
                    layout.addWidget(line_label)
                    line_count += 1

            elif tag == "replace":
                for i in range(i1, i2):
                    if line_count >= max_display_lines:
                        break
                    line_label = self._create_compact_line(
                        f"- {original_lines[i]}", "delete"
                    )
                    layout.addWidget(line_label)
                    line_count += 1

                for j in range(j1, j2):
                    if line_count >= max_display_lines:
                        break
                    line_label = self._create_compact_line(
                        f"+ {modified_lines[j]}", "insert"
                    )
                    layout.addWidget(line_label)
                    line_count += 1

        return container

    def _create_compact_line(self, text: str, line_type: str) -> QLabel:
        """Create a compact diff line label."""
        colors = self._colors
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        base_style = "font-family: monospace; font-size: 11px; padding: 0 2px;"

        if line_type == "equal":
            label.setStyleSheet(f"{base_style} color: {colors['unchanged_text']};")
        elif line_type == "delete":
            label.setStyleSheet(
                f"{base_style} color: {colors['removed_text']}; background-color: {colors['removed_bg']};"
            )
        elif line_type == "insert":
            label.setStyleSheet(
                f"{base_style} color: {colors['added_text']}; background-color: {colors['added_bg']};"
            )

        return label
