from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.gui.themes import StyleProvider

from .dynamic_fields import DynamicFieldList
from .mcp_config_mapper import form_data_to_dict, normalize_include_tools


class MCPForm(QWidget):
    """Right panel form for editing MCP server properties."""

    dirty = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style_provider = StyleProvider()
        self._agent_manager = AgentManager.get_instance()
        self._init_ui()

    def _init_ui(self):
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)

        self.editor_widget = QWidget()
        self.editor_widget.setStyleSheet(
            self._style_provider.get_editor_container_widget_style()
        )
        editor_layout = QVBoxLayout(self.editor_widget)

        form_layout = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.textChanged.connect(lambda: self.dirty.emit())
        form_layout.addRow("Name:", self.name_input)

        self.streaming_server_checkbox = QCheckBox("Streaming Server")
        self.streaming_server_checkbox.stateChanged.connect(
            self._on_streaming_server_changed
        )
        form_layout.addRow("", self.streaming_server_checkbox)

        self.url_input = QLineEdit()
        self.url_input.textChanged.connect(lambda: self.dirty.emit())
        self.url_input.setPlaceholderText("http://localhost:8080/mcp")
        self.url_label = QLabel("URL:")
        form_layout.addRow(self.url_label, self.url_input)

        self.command_input = QLineEdit()
        self.command_input.textChanged.connect(lambda: self.dirty.emit())
        self.command_label = QLabel("Command:")
        form_layout.addRow(self.command_label, self.command_input)

        self.include_tools_input = QLineEdit()
        self.include_tools_input.textChanged.connect(lambda: self.dirty.emit())
        self.include_tools_input.setPlaceholderText("tool_a, tool_b")
        form_layout.addRow("Included Tools:", self.include_tools_input)

        editor_layout.addLayout(form_layout)

        self.args_field_list = DynamicFieldList(
            mode="single_value",
            add_button_label="Add Argument",
        )
        self.args_field_list.group.setTitle("Arguments")
        self.args_field_list.dirty.connect(lambda: self.dirty.emit())
        editor_layout.addWidget(self.args_field_list.group)

        self.env_field_list = DynamicFieldList(
            mode="key_value",
            add_button_label="Add Environment Variable",
            key_placeholder="Key",
            value_placeholder="Value",
        )
        self.env_field_list.group.setTitle("Environment Variables")
        self.env_field_list.dirty.connect(lambda: self.dirty.emit())
        editor_layout.addWidget(self.env_field_list.group)

        self.headers_field_list = DynamicFieldList(
            mode="key_value",
            add_button_label="Add Header",
            key_placeholder="Header Name (e.g., Authorization)",
            value_placeholder="Header Value (e.g., Bearer token)",
        )
        self.headers_field_list.group.setTitle("HTTP Headers")
        self.headers_field_list.dirty.connect(lambda: self.dirty.emit())
        editor_layout.addWidget(self.headers_field_list.group)

        enabled_group_label = QLabel("Enabled For Agents")
        self.agent_checkboxes: dict[str, QCheckBox] = {}
        self._enabled_layout = QVBoxLayout()
        available_agents = list(self._agent_manager.agents.keys())
        for agent in available_agents:
            checkbox = QCheckBox(agent)
            checkbox.stateChanged.connect(lambda: self.dirty.emit())
            self.agent_checkboxes[agent] = checkbox
            self._enabled_layout.addWidget(checkbox)

        editor_layout.addWidget(enabled_group_label)
        editor_layout.addLayout(self._enabled_layout)
        editor_layout.addStretch()

        form_scroll.setWidget(self.editor_widget)

        outer_layout = QVBoxLayout(self)
        outer_layout.addWidget(form_scroll)

    def _on_streaming_server_changed(self, state):
        is_streaming = state == Qt.CheckState.Checked.value
        self.set_streaming_visible(is_streaming)
        self.set_stdio_visible(not is_streaming)
        self.dirty.emit()

    def populate(self, server_config: dict):
        """Populate form fields from server config dict."""
        self.name_input.setText(server_config.get("name", ""))
        self.streaming_server_checkbox.setChecked(
            server_config.get("streaming_server", False)
        )
        self.url_input.setText(server_config.get("url", ""))
        self.command_input.setText(server_config.get("command", ""))
        self.include_tools_input.setText(
            ", ".join(normalize_include_tools(server_config.get("includeTools")))
        )

        self.args_field_list.clear_fields()
        for arg in server_config.get("args", []):
            self.args_field_list.add_field(value=arg, mark_dirty_on_add=False)

        self.env_field_list.clear_fields()
        for key, value in server_config.get("env", {}).items():
            self.env_field_list.add_field(key=key, value=value, mark_dirty_on_add=False)

        self.headers_field_list.clear_fields()
        for key, value in server_config.get("headers", {}).items():
            self.headers_field_list.add_field(
                key=key, value=value, mark_dirty_on_add=False
            )

        enabled_agents = server_config.get("enabledForAgents", [])
        for agent, checkbox in self.agent_checkboxes.items():
            checkbox.setChecked(agent in enabled_agents)

        is_streaming = server_config.get("streaming_server", False)
        self.streaming_server_checkbox.setChecked(is_streaming)
        self.set_streaming_visible(is_streaming)
        self.set_stdio_visible(not is_streaming)

    def collect(self) -> dict:
        """Collect form data into a server config dict."""
        return form_data_to_dict(
            name=self.name_input.text(),
            streaming_server=self.streaming_server_checkbox.isChecked(),
            url=self.url_input.text(),
            command=self.command_input.text(),
            include_tools_str=self.include_tools_input.text(),
            args_list=self.args_field_list.collect_list(),
            env_dict=self.env_field_list.collect_dict(),
            headers_dict=self.headers_field_list.collect_dict(),
            enabled_agents_list=[
                agent
                for agent, checkbox in self.agent_checkboxes.items()
                if checkbox.isChecked()
            ],
        )

    def clear(self):
        """Clear all form fields."""
        self.name_input.clear()
        self.streaming_server_checkbox.setChecked(False)
        self.url_input.clear()
        self.command_input.clear()
        self.include_tools_input.clear()
        self.args_field_list.clear_fields()
        self.env_field_list.clear_fields()
        self.headers_field_list.clear_fields()
        for checkbox in self.agent_checkboxes.values():
            checkbox.setChecked(False)

    def set_enabled(self, enabled: bool):
        """Enable or disable all form fields."""
        self.name_input.setEnabled(enabled)
        self.streaming_server_checkbox.setEnabled(enabled)
        self.include_tools_input.setEnabled(enabled)
        self.url_input.setEnabled(enabled)
        self.command_input.setEnabled(enabled)

        self.args_field_list.set_enabled(enabled)
        self.env_field_list.set_enabled(enabled)
        self.headers_field_list.set_enabled(enabled)

        for checkbox in self.agent_checkboxes.values():
            checkbox.setEnabled(enabled)

        if enabled:
            is_streaming = self.streaming_server_checkbox.isChecked()
            self.set_streaming_visible(is_streaming)
            self.set_stdio_visible(not is_streaming)
        else:
            self.url_input.setVisible(False)
            self.url_label.setVisible(False)
            self.command_input.setVisible(False)
            self.command_label.setVisible(False)
            self.args_field_list.set_visible(False)
            self.env_field_list.set_visible(False)
            self.headers_field_list.set_visible(False)

    def set_streaming_visible(self, visible: bool):
        """Show SSE/streaming-specific fields, hide stdio fields."""
        self.url_input.setVisible(visible)
        self.url_label.setVisible(visible)
        self.headers_field_list.set_visible(visible)

    def set_stdio_visible(self, visible: bool):
        """Show stdio-specific fields, hide SSE/streaming fields."""
        self.command_input.setVisible(visible)
        self.command_label.setVisible(visible)
        self.args_field_list.set_visible(visible)
        self.env_field_list.set_visible(visible)
