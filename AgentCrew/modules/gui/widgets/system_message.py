import markdown
from PySide6.QtCore import (
    Qt,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider


class SystemMessageWidget(QWidget):
    """Widget to display system messages."""

    def __init__(self, text, parent=None):
        super().__init__(parent)

        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Store the full text
        self.full_text = text
        self.is_expanded = False

        # Create collapsible container with modern styling
        self.container = QWidget()
        self.container.setStyleSheet(
            "background-color: rgba(137, 180, 250, 0.06); border-radius: 10px; border: 1px solid rgba(137, 180, 250, 0.15);"
        )
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(4)

        # Create label with HTML support
        self.message_label = QLabel()
        self.message_label.setTextFormat(Qt.TextFormat.RichText)

        self.style_provider = StyleProvider()
        self.message_label.setStyleSheet(
            self.style_provider.get_system_message_label_style()
        )
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

        message_label_font = self.message_label.font()
        message_label_font.setPixelSize(13)
        message_label_font.setFamilies(
            ["Inter", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"]
        )
        self.message_label.setFont(message_label_font)

        # Create expand/collapse button
        self.toggle_button = QPushButton("Show More ▼")
        self.toggle_button.setStyleSheet(
            self.style_provider.get_system_message_toggle_style()
        )
        self.toggle_button.setFont(QFont("Inter", 10))
        self.toggle_button.setMaximumHeight(22)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.clicked.connect(self.toggle_expansion)

        # Add widgets to container
        container_layout.addWidget(self.message_label)
        container_layout.addWidget(self.toggle_button)

        # Add container to main layout
        layout.addWidget(self.container)

        # Set the collapsed text initially
        self.set_collapsed_text()

    def set_collapsed_text(self):
        """Set the text to show only 2 lines when collapsed."""
        md_extensions = ["tables", "fenced_code", "codehilite", "nl2br", "sane_lists"]
        lines = self.full_text.split("\n")

        text_to_render_md = ""
        add_ellipsis = False

        if len(lines) <= 2:
            text_to_render_md = self.full_text
            self.toggle_button.hide()
        else:
            text_to_render_md = "\n".join(lines[:2])
            # If original text had code blocks, attempt to fix unclosed ones in snippet
            if "```" in self.full_text and text_to_render_md.count("```") % 2 != 0:
                text_to_render_md += "\n```"
            add_ellipsis = True
            self.toggle_button.show()

        try:
            html_content = markdown.markdown(
                text_to_render_md, output_format="html", extensions=md_extensions
            )
            # Apply CODE_CSS
            html_content = (
                f"""<style>
            pre {{ white-space: pre-wrap; margin-bottom: 0;}}
                {self.style_provider.get_code_color_style()}
            </style>"""
                + html_content
            )
            if add_ellipsis:
                # Ensure RichText for the "..." if HTML is used
                self.message_label.setTextFormat(Qt.TextFormat.RichText)
                self.message_label.setText(html_content)
            else:
                self.message_label.setText(html_content)
        except Exception as e:
            print(f"Error rendering collapsed markdown for system message: {e}")
            # Fallback: use plain text
            fallback_text = text_to_render_md
            self.message_label.setTextFormat(
                Qt.TextFormat.PlainText
            )  # Set to PlainText for fallback
            self.message_label.setText(fallback_text)

    def set_expanded_text(self):
        """Set the text to show all content."""
        md_extensions = ["tables", "fenced_code", "codehilite", "nl2br", "sane_lists"]
        try:
            html_content = markdown.markdown(
                self.full_text, output_format="html", extensions=md_extensions
            )
            html_content = (
                f"""<style>
            pre {{ white-space: pre-wrap; margin-bottom: 0;}}
                {self.style_provider.get_code_color_style()}
            </style>"""
                + html_content
            )
            self.message_label.setTextFormat(Qt.TextFormat.RichText)  # Ensure RichText
            self.message_label.setText(html_content)
        except Exception as e:
            print(f"Error rendering expanded markdown for system message: {e}")
            self.message_label.setTextFormat(
                Qt.TextFormat.PlainText
            )  # Set to PlainText for fallback
            self.message_label.setText(self.full_text)  # Fallback to full plain text

    def set_text(self, text):
        self.full_text = text
        if self.is_expanded:
            self.set_expanded_text()
        else:
            self.set_collapsed_text()

    def toggle_expansion(self):
        """Toggle between expanded and collapsed states."""
        self.is_expanded = not self.is_expanded

        if self.is_expanded:
            self.set_expanded_text()
            self.toggle_button.setText("Show Less ▲")
        else:
            self.set_collapsed_text()
            self.toggle_button.setText("Show More ▼")
