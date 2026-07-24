import re

from PySide6.QtCore import Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from AgentCrew.modules.gui.themes import StyleProvider


class MarkdownSyntaxHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for Markdown files."""

    def __init__(self, parent: QTextDocument):
        super().__init__(parent)
        self.style_provider = StyleProvider()
        self._setup_highlighting_rules()

    def _setup_highlighting_rules(self):
        """Set up the highlighting rules for Markdown syntax."""
        self.highlighting_rules = []

        # Get colors from the style provider
        colors = self.style_provider.get_markdown_editor_colors()

        # Headers (# ## ### etc.)
        header_format = QTextCharFormat()
        header_format.setForeground(QColor(colors["header"]))
        header_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r"^#{1,6}\s.*$", header_format))

        # Bold text (**text** or __text__)
        bold_format = QTextCharFormat()
        bold_format.setForeground(QColor(colors["bold"]))
        bold_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r"\*\*[^*]+\*\*", bold_format))
        self.highlighting_rules.append((r"__[^_]+__", bold_format))

        # Italic text (*text* or _text_)
        italic_format = QTextCharFormat()
        italic_format.setForeground(QColor(colors["italic"]))
        italic_format.setFontItalic(True)
        self.highlighting_rules.append((r"\*[^*]+\*", italic_format))
        self.highlighting_rules.append((r"_[^_]+_", italic_format))

        # Code blocks (```code```)
        code_block_format = QTextCharFormat()
        code_block_format.setForeground(QColor(colors["code"]))
        code_block_format.setBackground(QColor(colors["code_background"]))
        code_block_format.setFontFamily("monospace")
        self.highlighting_rules.append((r"```[^`]*```", code_block_format))

        # Inline code (`code`)
        inline_code_format = QTextCharFormat()
        inline_code_format.setForeground(QColor(colors["code"]))
        inline_code_format.setBackground(QColor(colors["code_background"]))
        inline_code_format.setFontFamily("monospace")
        self.highlighting_rules.append((r"`[^`]+`", inline_code_format))

        # Links [text](url) and [text][ref]
        link_format = QTextCharFormat()
        link_format.setForeground(QColor(colors["link"]))
        link_format.setFontUnderline(True)
        self.highlighting_rules.append((r"\[([^\]]+)\]\([^\)]+\)", link_format))
        self.highlighting_rules.append((r"\[([^\]]+)\]\[[^\]]*\]", link_format))

        # Images ![alt](url)
        image_format = QTextCharFormat()
        image_format.setForeground(QColor(colors["image"]))
        self.highlighting_rules.append((r"!\[[^\]]*\]\([^\)]+\)", image_format))

        # Lists (- * +)
        list_format = QTextCharFormat()
        list_format.setForeground(QColor(colors["list"]))
        list_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r"^[\s]*[-*+]\s", list_format))

        # Numbered lists (1. 2. etc.)
        numbered_list_format = QTextCharFormat()
        numbered_list_format.setForeground(QColor(colors["list"]))
        numbered_list_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r"^[\s]*\d+\.\s", numbered_list_format))

        # Blockquotes (> text)
        blockquote_format = QTextCharFormat()
        blockquote_format.setForeground(QColor(colors["blockquote"]))
        blockquote_format.setFontItalic(True)
        self.highlighting_rules.append((r"^>+.*$", blockquote_format))

        # Horizontal rules (--- or ***)
        hr_format = QTextCharFormat()
        hr_format.setForeground(QColor(colors["hr"]))
        hr_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r"^[-*]{3,}$", hr_format))

        # Strikethrough (~~text~~)
        strikethrough_format = QTextCharFormat()
        strikethrough_format.setForeground(QColor(colors["strikethrough"]))
        strikethrough_format.setFontStrikeOut(True)
        self.highlighting_rules.append((r"~~[^~]+~~", strikethrough_format))

    def highlightBlock(self, text: str):
        """Apply syntax highlighting to a block of text."""
        for pattern, format_obj in self.highlighting_rules:
            for match in re.finditer(pattern, text, re.MULTILINE):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, format_obj)


class MarkdownEditor(QWidget):
    """A Markdown editor widget with syntax highlighting and preview capabilities."""

    # Signal emitted when the markdown content changes
    markdown_changed = Signal(str)
    # Signal emitted when there are parsing errors (currently not used but kept for future)
    validation_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.style_provider = StyleProvider()
        self.setup_ui()

    # Add a textChanged signal for QTextEdit compatibility
    @property
    def textChanged(self):
        """Compatibility property for QTextEdit's textChanged signal."""
        return self.text_edit.textChanged

    def setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)

        # Error label for validation messages (hidden by default)
        self.error_label = QLabel()
        colors = self.style_provider.get_markdown_editor_colors()
        self.error_label.setStyleSheet(f"color: {colors['error']}; font-weight: bold;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # Text editor
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText("")  # Start empty by default

        # Apply Markdown syntax highlighting
        self.highlighter = MarkdownSyntaxHighlighter(self.text_edit.document())

        # Apply styling from theme
        self.text_edit.setStyleSheet(self.style_provider.get_markdown_editor_style())

        layout.addWidget(self.text_edit)

        # Connect text change signal
        self.text_edit.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        """Handle text changes in the editor."""
        markdown_text = self.text_edit.toPlainText()

        # Clear any previous errors
        self.error_label.hide()

        # Emit the markdown content
        self.markdown_changed.emit(markdown_text)

    def set_markdown(self, markdown_text: str):
        """Set the markdown content of the editor."""
        try:
            self.text_edit.setPlainText(markdown_text)
        except Exception as e:
            self.error_label.setText(f"Error setting markdown: {e!s}")
            self.error_label.show()

    def get_markdown(self) -> str:
        """Get the current markdown content as a string."""
        return self.text_edit.toPlainText()

    def clear(self):
        """Clear the editor content."""
        self.text_edit.setPlainText("")

    def set_read_only(self, read_only: bool):
        """Set the editor to read-only mode."""
        self.text_edit.setReadOnly(read_only)

    def setEnabled(self, enabled: bool):
        """Set the editor enabled/disabled state (QTextEdit compatibility)."""
        super().setEnabled(enabled)
        self.text_edit.setEnabled(enabled)

    def setMinimumHeight(self, height: int):
        """Set the minimum height (QTextEdit compatibility)."""
        super().setMinimumHeight(height)
        self.text_edit.setMinimumHeight(height)

    def setText(self, text: str):
        """Set the text content (QTextEdit compatibility)."""
        self.set_markdown(text)

    def toPlainText(self) -> str:
        """Get the plain text content (QTextEdit compatibility)."""
        return self.get_markdown()

    def blockSignals(self, block: bool) -> bool:
        """Block or unblock signals (QTextEdit compatibility)."""
        # Block signals for both the main widget and the text editor
        old_state = super().blockSignals(block)
        self.text_edit.blockSignals(block)
        return old_state

    def insert_text(self, text: str):
        """Insert text at the current cursor position."""
        cursor = self.text_edit.textCursor()
        cursor.insertText(text)

    def get_selected_text(self) -> str:
        """Get the currently selected text."""
        return self.text_edit.textCursor().selectedText()

    def replace_selected_text(self, text: str):
        """Replace the currently selected text."""
        cursor = self.text_edit.textCursor()
        cursor.insertText(text)

    def set_font_size(self, size: int):
        """Set the font size of the editor."""
        font = self.text_edit.font()
        font.setPointSize(size)
        self.text_edit.setFont(font)

    def zoom_in(self):
        """Increase font size."""
        self.text_edit.zoomIn()

    def zoom_out(self):
        """Decrease font size."""
        self.text_edit.zoomOut()

    def update_theme(self):
        """Update the editor theme when the global theme changes."""
        # Refresh style provider
        self.style_provider.update_theme()

        # Update text editor styling
        self.text_edit.setStyleSheet(self.style_provider.get_markdown_editor_style())

        # Update error label color
        colors = self.style_provider.get_markdown_editor_colors()
        self.error_label.setStyleSheet(f"color: {colors['error']}; font-weight: bold;")

        # Recreate syntax highlighter with new colors
        self.highlighter = MarkdownSyntaxHighlighter(self.text_edit.document())

    def show_error(self, message: str):
        """Show an error message."""
        self.error_label.setText(message)
        self.error_label.show()
        self.validation_error.emit(message)

    def hide_error(self):
        """Hide the error message."""
        self.error_label.hide()
