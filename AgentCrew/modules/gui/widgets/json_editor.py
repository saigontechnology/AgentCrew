import json
import re
from typing import Any

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


class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for JSON files."""

    def __init__(self, parent: QTextDocument):
        super().__init__(parent)
        self.style_provider = StyleProvider()
        self._setup_highlighting_rules()

    def _setup_highlighting_rules(self):
        """Set up the highlighting rules for JSON syntax."""
        self.highlighting_rules = []

        # Get colors from the style provider
        colors = self.style_provider.get_json_editor_colors()

        # JSON key format (quoted strings followed by colon)
        key_format = QTextCharFormat()
        key_format.setForeground(QColor(colors["string"]))
        key_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r'"[^"]*"(?=\s*:)', key_format))

        # JSON string values (quoted strings not followed by colon)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor(colors["string"]))
        self.highlighting_rules.append((r'"[^"]*"(?!\s*:)', string_format))

        # JSON numbers
        number_format = QTextCharFormat()
        number_format.setForeground(QColor(colors["number"]))
        self.highlighting_rules.append((r"\b\d+\.?\d*\b", number_format))

        # JSON booleans
        boolean_format = QTextCharFormat()
        boolean_format.setForeground(QColor(colors["keyword"]))
        self.highlighting_rules.append((r"\b(true|false|null)\b", boolean_format))

        # JSON punctuation
        punctuation_format = QTextCharFormat()
        punctuation_format.setForeground(QColor(colors["punctuation"]))
        self.highlighting_rules.append((r"[{}\[\]:,]", punctuation_format))

    def highlightBlock(self, text: str):
        """Apply syntax highlighting to a block of text."""
        for pattern, format_obj in self.highlighting_rules:
            for match in re.finditer(pattern, text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, format_obj)


class JsonEditor(QWidget):
    """A JSON editor widget with syntax highlighting and validation."""

    # Signal emitted when the JSON content changes and is valid
    json_changed = Signal(dict)
    # Signal emitted when there are validation errors
    validation_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.style_provider = StyleProvider()
        self.setup_ui()

    def setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)

        # Error label for validation messages
        self.error_label = QLabel()
        colors = self.style_provider.get_json_editor_colors()
        self.error_label.setStyleSheet(f"color: {colors['error']}; font-weight: bold;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # Text editor
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText("{}")

        # Apply JSON syntax highlighting
        self.highlighter = JsonSyntaxHighlighter(self.text_edit.document())

        # Apply styling from theme
        self.text_edit.setStyleSheet(self.style_provider.get_json_editor_style())

        layout.addWidget(self.text_edit)

        # Connect text change signal
        self.text_edit.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        """Handle text changes in the editor."""
        try:
            json_text = self.text_edit.toPlainText().strip()
            if not json_text:
                json_data = {}
            else:
                json_data = json.loads(json_text)

            # Clear error display
            self.error_label.hide()

            # Emit the valid JSON
            self.json_changed.emit(json_data)

        except json.JSONDecodeError as e:
            # Show validation error
            error_msg = f"JSON Error: {e!s}"
            self.error_label.setText(error_msg)
            self.error_label.show()
            self.validation_error.emit(error_msg)

    def set_json(self, json_data: dict[str, Any]):
        """Set the JSON content of the editor."""
        try:
            json_text = json.dumps(json_data, indent=2, ensure_ascii=False)
            self.text_edit.setPlainText(json_text)
        except Exception as e:
            self.error_label.setText(f"Error setting JSON: {e!s}")
            self.error_label.show()

    def get_json(self) -> dict[str, Any]:
        """Get the current JSON content as a dictionary."""
        try:
            json_text = self.text_edit.toPlainText().strip()
            if not json_text:
                return {}
            return json.loads(json_text)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON content")

    def is_valid_json(self) -> bool:
        """Check if the current content is valid JSON."""
        try:
            self.get_json()
            return True
        except ValueError:
            return False

    def clear(self):
        """Clear the editor content."""
        self.text_edit.setPlainText("{}")

    def set_read_only(self, read_only: bool):
        """Set the editor to read-only mode."""
        self.text_edit.setReadOnly(read_only)

    def update_theme(self):
        """Update the editor theme when the global theme changes."""
        # Refresh style provider
        self.style_provider.update_theme()

        # Update text editor styling
        self.text_edit.setStyleSheet(self.style_provider.get_json_editor_style())

        # Update error label color
        colors = self.style_provider.get_json_editor_colors()
        self.error_label.setStyleSheet(f"color: {colors['error']}; font-weight: bold;")

        # Recreate syntax highlighter with new colors
        self.highlighter = JsonSyntaxHighlighter(self.text_edit.document())
