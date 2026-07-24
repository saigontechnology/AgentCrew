from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider


class RemoteAgentEditor(QWidget):
    """Composite widget for editing remote agent configuration."""

    field_changed = Signal()

    def __init__(
        self,
        on_dirty_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._on_dirty_callback = on_dirty_callback
        self._init_ui()

    def _init_ui(self):
        style_provider = StyleProvider()

        layout = QVBoxLayout(self)

        form_layout = QFormLayout()

        self.remote_name_input = QLineEdit()
        form_layout.addRow("Name:", self.remote_name_input)

        self.remote_base_url_input = QLineEdit()
        self.remote_base_url_input.setPlaceholderText("e.g., http://localhost:8000")
        form_layout.addRow("Base URL:", self.remote_base_url_input)

        self.remote_enabled_checkbox = QCheckBox("Enabled")
        self.remote_enabled_checkbox.setChecked(True)
        form_layout.addRow("", self.remote_enabled_checkbox)

        layout.addLayout(form_layout)

        # Headers section
        self.headers_group = QGroupBox("HTTP Headers")
        headers_outer_layout = QVBoxLayout()
        self.remote_headers_layout = QVBoxLayout()
        self.remote_header_inputs: list[dict] = []

        headers_btn_layout = QHBoxLayout()
        self.add_remote_header_btn = QPushButton("Add Header")
        self.add_remote_header_btn.setStyleSheet(
            style_provider.get_button_style("primary")
        )
        self.add_remote_header_btn.clicked.connect(
            lambda: self.add_remote_header_field("", "")
        )
        headers_btn_layout.addWidget(self.add_remote_header_btn)
        headers_btn_layout.addStretch()

        headers_outer_layout.addLayout(headers_btn_layout)
        headers_outer_layout.addLayout(self.remote_headers_layout)
        self.headers_group.setLayout(headers_outer_layout)

        layout.addWidget(self.headers_group)
        layout.addStretch()

        # Connect signals
        self.remote_name_input.textChanged.connect(self._on_field_changed)
        self.remote_base_url_input.textChanged.connect(self._on_field_changed)
        self.remote_enabled_checkbox.stateChanged.connect(self._on_field_changed)

    def _on_field_changed(self):
        self.field_changed.emit()
        if self._on_dirty_callback:
            self._on_dirty_callback()

    def setEnabled(self, enabled: bool):
        """Enable or disable all remote agent fields and headers."""
        self.remote_name_input.setEnabled(enabled)
        self.remote_base_url_input.setEnabled(enabled)
        self.remote_enabled_checkbox.setEnabled(enabled)
        self.add_remote_header_btn.setEnabled(enabled)
        for header_data in self.remote_header_inputs:
            header_data["key_input"].setEnabled(enabled)
            header_data["value_input"].setEnabled(enabled)
            header_data["remove_btn"].setEnabled(enabled)
        super().setEnabled(enabled)

    def clear(self):
        """Clear all remote agent fields and headers."""
        self.remote_name_input.clear()
        self.remote_base_url_input.clear()
        self.remote_enabled_checkbox.setChecked(True)
        self.clear_remote_header_fields()

    def populate(self, agent_data: dict):
        """Populate fields from agent config data."""
        self.remote_name_input.setText(agent_data.get("name", ""))
        self.remote_base_url_input.setText(agent_data.get("base_url", ""))
        self.remote_enabled_checkbox.setChecked(agent_data.get("enabled", True))

        self.clear_remote_header_fields()
        headers = agent_data.get("headers", {})
        for key, value in headers.items():
            self.add_remote_header_field(key, value, mark_dirty_on_add=False)

    def collect(self) -> dict:
        """Collect the current field values into a config dict."""
        headers = self._collect_remote_headers()
        return {
            "name": self.remote_name_input.text().strip(),
            "base_url": self.remote_base_url_input.text().strip(),
            "enabled": self.remote_enabled_checkbox.isChecked(),
            "headers": headers,
            "agent_type": "remote",
        }

    def add_remote_header_field(self, key="", value="", mark_dirty_on_add=True):
        """Add a field for a remote agent HTTP header."""
        header_layout = QHBoxLayout()

        key_input = QLineEdit()
        key_input.setText(str(key))
        key_input.setPlaceholderText("Header Name (e.g., Authorization)")
        key_input.textChanged.connect(self._on_field_changed)

        value_input = QLineEdit()
        value_input.setText(str(value))
        value_input.setPlaceholderText("Header Value (e.g., Bearer token)")
        value_input.textChanged.connect(self._on_field_changed)

        remove_btn = QPushButton("Remove")
        remove_btn.setMaximumWidth(80)
        style_provider = StyleProvider()
        remove_btn.setStyleSheet(style_provider.get_button_style("red"))

        header_layout.addWidget(key_input)
        header_layout.addWidget(value_input)
        header_layout.addWidget(remove_btn)

        # Insert before the add button row
        self.remote_headers_layout.insertLayout(
            len(self.remote_header_inputs), header_layout
        )

        header_data = {
            "layout": header_layout,
            "key_input": key_input,
            "value_input": value_input,
            "remove_btn": remove_btn,
        }
        self.remote_header_inputs.append(header_data)

        remove_btn.clicked.connect(lambda: self.remove_remote_header_field(header_data))

        if mark_dirty_on_add:
            self._on_field_changed()
        return header_data

    def remove_remote_header_field(self, header_data):
        """Remove a remote agent header field."""
        self.remote_headers_layout.removeItem(header_data["layout"])
        header_data["key_input"].deleteLater()
        header_data["value_input"].deleteLater()
        header_data["remove_btn"].deleteLater()
        self.remote_header_inputs.remove(header_data)
        self._on_field_changed()

    def clear_remote_header_fields(self):
        """Clear all remote agent header fields."""
        while self.remote_header_inputs:
            self.remove_remote_header_field(self.remote_header_inputs[0])

    def _collect_remote_headers(self) -> dict:
        """Collect all header key-value pairs."""
        headers = {}
        for header_data in self.remote_header_inputs:
            key = header_data["key_input"].text().strip()
            value = header_data["value_input"].text().strip()
            if key:
                headers[key] = value
        return headers
