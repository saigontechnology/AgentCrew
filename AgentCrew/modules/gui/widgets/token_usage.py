from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)


class TokenUsageWidget(QWidget):
    """Widget to display token usage information."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # self.setAutoFillBackground(True) # Remove this line

        # Set background color directly via stylesheet
        from AgentCrew.modules.gui.themes import StyleProvider

        style_provider = StyleProvider()
        self.setStyleSheet(style_provider.get_token_usage_widget_style())

        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # Remove default margins if any

        # Create labels
        self.token_label = QLabel(
            "📊 Token Usage: Input: 0 | Output: 0 | Total: 0 | Cost: $0.0000 | Session: $0.0000"
        )
        self.token_label.setStyleSheet(style_provider.get_token_usage_style())

        # Add to layout
        layout.addWidget(self.token_label)

    def update_token_info(
        self,
        input_tokens: int,
        output_tokens: int,
        total_input_tokens: int,
        total_cost: float,
        session_cost: float,
        cached_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ):
        """Update the token usage information."""
        parts = [f"📊  Input: {input_tokens:,}"]
        if cached_tokens > 0:
            parts.append(f"Cached: {cached_tokens:,}")
        if cache_creation_tokens > 0:
            parts.append(f"Cache Write: {cache_creation_tokens:,}")
        parts.append(f"Output: {output_tokens:,}")
        parts.append(f"Total: {total_input_tokens:,}")
        parts.append(f"Cost: ${total_cost:.4f}")
        parts.append(f"Session: ${session_cost:.4f}")
        self.token_label.setText("  ·  ".join(parts))

    def update_style(self, style_provider=None):
        """Update the widget's style based on the current theme."""
        if not style_provider:
            from AgentCrew.modules.gui.themes import StyleProvider

            style_provider = StyleProvider()

        # Update widget style
        self.setStyleSheet(style_provider.get_token_usage_widget_style())

        # Update label style
        self.token_label.setStyleSheet(style_provider.get_token_usage_style())
