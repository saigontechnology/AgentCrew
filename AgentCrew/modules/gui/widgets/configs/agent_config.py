from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QLineEdit,
    QCheckBox,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QMenu,
    QStackedWidget,
    QFileDialog,
    QTabWidget,
)
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService

from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.gui.widgets.markdown_editor import MarkdownEditor
from AgentCrew.modules.gui.widgets.loading_overlay import LoadingOverlay
from .save_worker import SaveWorker


class AgentsConfigTab(QWidget):
    """Tab for configuring agents."""

    # Add signal for configuration changes
    config_changed = Signal()

    def __init__(self, config_manager: ConfigManagement):
        super().__init__()
        self.config_manager = config_manager
        self.agent_manager = AgentManager.get_instance()
        self.persistence_service = ContextPersistenceService()
        self.available_tools = [
            "memory",
            "clipboard",
            "code_analysis",
            "web_search",
            "image_generation",
            "browser",
            "file_editing",
            "command_execution",
        ]

        # Load agents configuration
        self.agents_config = self.config_manager.read_agents_config()
        self._is_dirty = False
        self.current_agent_behaviors = {}  # Cache for current agent's behaviors

        self.save_worker = None

        self.init_ui()
        self.load_agents()

    @staticmethod
    def _determine_file_format_and_path(
        file_path: str, selected_filter: str
    ) -> tuple[str, str]:
        """
        Determine file format and ensure correct file extension.

        Args:
            file_path: The selected file path
            selected_filter: The filter selected in the file dialog

        Returns:
            Tuple of (final_file_path, file_format)
        """
        # Prioritize existing extension if present
        if file_path.lower().endswith(".toml"):
            return file_path, "toml"
        elif file_path.lower().endswith(".json"):
            return file_path, "json"

        # If no extension, use filter preference or default to JSON
        if "toml" in selected_filter.lower():
            return file_path + ".toml", "toml"
        else:
            return file_path + ".json", "json"

    def init_ui(self):
        """Initialize the UI components."""
        # Main layout
        main_layout = QHBoxLayout()

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel - Agent list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        self.agents_list = QListWidget()
        self.agents_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )  # Enable multi-select
        self.agents_list.currentItemChanged.connect(self.on_agent_selected)
        self.agents_list.itemSelectionChanged.connect(self.on_selection_changed)

        # Buttons for agent list management
        list_buttons_layout = QHBoxLayout()

        self.add_agent_menu_btn = QPushButton("Add Agent")
        style_provider = StyleProvider()
        self.add_agent_menu_btn.setStyleSheet(
            style_provider.get_button_style("agent_menu")
        )
        add_agent_menu = QMenu(self)
        add_agent_menu.setStyleSheet(style_provider.get_agent_menu_style())
        add_local_action = add_agent_menu.addAction("Add Local Agent")
        add_remote_action = add_agent_menu.addAction("Add Remote Agent")
        self.add_agent_menu_btn.setMenu(add_agent_menu)

        add_local_action.triggered.connect(self.add_new_local_agent)
        add_remote_action.triggered.connect(self.add_new_remote_agent)

        self.import_agents_btn = QPushButton("Import")
        self.import_agents_btn.setStyleSheet(style_provider.get_button_style("green"))
        self.import_agents_btn.clicked.connect(self.import_agents)

        self.export_agents_btn = QPushButton("Export")
        self.export_agents_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.export_agents_btn.clicked.connect(self.export_agents)
        self.export_agents_btn.setEnabled(False)  # Disable until selection

        self.remove_agent_btn = QPushButton("Remove")
        self.remove_agent_btn.setStyleSheet(style_provider.get_button_style("red"))
        self.remove_agent_btn.clicked.connect(self.remove_agent)
        self.remove_agent_btn.setEnabled(False)  # Disable until selection

        list_buttons_layout.addWidget(self.add_agent_menu_btn)
        list_buttons_layout.addWidget(self.import_agents_btn)
        list_buttons_layout.addWidget(self.export_agents_btn)
        list_buttons_layout.addWidget(self.remove_agent_btn)

        left_layout.addWidget(QLabel("Agents:"))
        left_layout.addWidget(self.agents_list)
        left_layout.addLayout(list_buttons_layout)

        # Right panel - Agent editor
        right_panel = QScrollArea()
        right_panel.setWidgetResizable(True)
        # right_panel.setStyleSheet("background-color: #181825;") # Set by QDialog stylesheet

        editor_container_widget = (
            QWidget()
        )  # Container for stacked widget and save button
        editor_container_widget.setStyleSheet(
            style_provider.get_editor_container_widget_style()
        )
        self.editor_layout = QVBoxLayout(
            editor_container_widget
        )  # editor_layout now on container

        self.editor_stacked_widget = QStackedWidget()

        self.local_agent_editor_widget = QWidget()
        local_agent_layout = QVBoxLayout(self.local_agent_editor_widget)

        self.local_agent_tab_widget = QTabWidget()

        self.general_tab = QWidget()
        general_tab_layout = QVBoxLayout(self.general_tab)
        local_form_layout = QFormLayout()

        self.name_input = QLineEdit()  # This is for Local Agent Name
        local_form_layout.addRow("Name:", self.name_input)
        self.description_input = QLineEdit()
        local_form_layout.addRow("Description:", self.description_input)
        self.temperature_input = QLineEdit()
        self.temperature_input.setValidator(QDoubleValidator(0.0, 2.0, 1))
        self.temperature_input.setPlaceholderText("0.0 - 2.0")
        local_form_layout.addRow("Temperature:", self.temperature_input)

        self.enabled_checkbox = QCheckBox("Enabled")
        self.enabled_checkbox.setChecked(True)  # Default to enabled
        local_form_layout.addRow("", self.enabled_checkbox)

        # Voice Settings
        voice_group = QGroupBox("Voice Settings")
        voice_layout = QFormLayout()

        self.voice_enabled_checkbox = QCheckBox("Voice Enabled")
        self.voice_enabled_checkbox.setTristate(True)
        # Apply enhanced styling for tristate checkbox
        style_provider = StyleProvider()
        self.voice_enabled_checkbox.setStyleSheet(style_provider.get_checkbox_style())
        # Add tooltip explaining the three states
        self.voice_enabled_checkbox.setToolTip(
            "Voice Features:\n"
            "• Square (green): Full voice mode - TTS for full conversation\n"
            "• Circle (amber): Partial voice mode - TTS for first sentence only\n"
            "• Unchecked: Voice disabled"
        )
        voice_layout.addRow("", self.voice_enabled_checkbox)

        self.voice_id_input = QLineEdit()
        self.voice_id_input.setPlaceholderText("e.g., kHhWB9Fw3aF6ly7JvltC")
        voice_layout.addRow("Voice ID:", self.voice_id_input)

        voice_group.setLayout(voice_layout)

        tools_group = QGroupBox("Tools")
        tools_layout = QVBoxLayout()
        self.tool_checkboxes = {}
        for tool in self.available_tools:
            checkbox = QCheckBox(tool)
            self.tool_checkboxes[tool] = checkbox
            tools_layout.addWidget(checkbox)
        tools_group.setLayout(tools_layout)

        general_tab_layout.addLayout(local_form_layout)
        general_tab_layout.addWidget(voice_group)
        general_tab_layout.addWidget(tools_group)
        general_tab_layout.addStretch()

        self.system_prompt_tab = QWidget()
        system_prompt_tab_layout = QVBoxLayout(self.system_prompt_tab)

        system_prompt_label = QLabel("System Prompt:")
        system_prompt_label.setStyleSheet("font-weight: bold; margin-bottom: 5px;")

        self.system_prompt_input = MarkdownEditor()
        self.system_prompt_input.setMinimumHeight(400)
        self.system_prompt_input.clear()

        system_prompt_tab_layout.addWidget(system_prompt_label)
        system_prompt_tab_layout.addWidget(self.system_prompt_input, 1)

        self.behaviors_tab = QWidget()
        behaviors_tab_layout = QVBoxLayout(self.behaviors_tab)

        behaviors_group = QGroupBox("Adaptive Behaviors")
        behaviors_layout = QVBoxLayout()

        self.behaviors_list = QListWidget()
        self.behaviors_list.currentItemChanged.connect(self.on_behavior_selected)

        behaviors_buttons_layout = QHBoxLayout()
        self.add_behavior_btn = QPushButton("Add Behavior")
        self.add_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.add_behavior_btn.clicked.connect(self.add_new_behavior)

        self.edit_behavior_btn = QPushButton("Edit")
        self.edit_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.edit_behavior_btn.clicked.connect(self.edit_behavior)
        self.edit_behavior_btn.setEnabled(False)

        self.remove_behavior_btn = QPushButton("Remove")
        self.remove_behavior_btn.setStyleSheet(style_provider.get_button_style("red"))
        self.remove_behavior_btn.clicked.connect(self.remove_behavior)
        self.remove_behavior_btn.setEnabled(False)

        behaviors_buttons_layout.addWidget(self.add_behavior_btn)
        behaviors_buttons_layout.addWidget(self.edit_behavior_btn)
        behaviors_buttons_layout.addWidget(self.remove_behavior_btn)
        behaviors_buttons_layout.addStretch()

        self.behavior_form_widget = QWidget()
        behavior_form_layout = QFormLayout()

        self.behavior_id_input = QLineEdit()
        self.behavior_id_input.setPlaceholderText("e.g., communication_style_technical")
        behavior_form_layout.addRow("Behavior ID:", self.behavior_id_input)

        self.behavior_description_input = QLineEdit()
        self.behavior_description_input.setPlaceholderText(
            "when [condition] do [action]\n\nExample: when user asks about debugging, do provide step-by-step troubleshooting with code examples"
        )
        behavior_form_layout.addRow("Behavior:", self.behavior_description_input)

        behavior_form_buttons_layout = QHBoxLayout()
        self.save_behavior_btn = QPushButton("Save")
        self.save_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.save_behavior_btn.clicked.connect(self.save_behavior)

        self.cancel_behavior_btn = QPushButton("Cancel")
        self.cancel_behavior_btn.setStyleSheet(
            style_provider.get_button_style("secondary")
        )
        self.cancel_behavior_btn.clicked.connect(self.cancel_behavior_edit)

        behavior_form_buttons_layout.addWidget(self.save_behavior_btn)
        behavior_form_buttons_layout.addWidget(self.cancel_behavior_btn)
        behavior_form_buttons_layout.addStretch()

        behavior_form_layout.addRow("", behavior_form_buttons_layout)
        self.behavior_form_widget.setLayout(behavior_form_layout)
        self.behavior_form_widget.hide()

        behaviors_layout.addWidget(self.behaviors_list)
        behaviors_layout.addLayout(behaviors_buttons_layout)
        behaviors_layout.addWidget(self.behavior_form_widget)
        behaviors_group.setLayout(behaviors_layout)

        behaviors_tab_layout.addWidget(behaviors_group)

        # Add tabs to the tab widget
        self.local_agent_tab_widget.addTab(self.general_tab, "General")
        self.local_agent_tab_widget.addTab(self.system_prompt_tab, "System Prompt")
        self.local_agent_tab_widget.addTab(self.behaviors_tab, "Behaviors")

        # Add tab widget to main layout
        local_agent_layout.addWidget(self.local_agent_tab_widget)

        # Remote Agent Editor Widget
        self.remote_agent_editor_widget = QWidget()
        remote_agent_layout = QVBoxLayout(self.remote_agent_editor_widget)
        remote_form_layout = QFormLayout()

        self.remote_name_input = QLineEdit()
        remote_form_layout.addRow("Name:", self.remote_name_input)
        self.remote_base_url_input = QLineEdit()
        self.remote_base_url_input.setPlaceholderText("e.g., http://localhost:8000")
        remote_form_layout.addRow("Base URL:", self.remote_base_url_input)

        self.remote_enabled_checkbox = QCheckBox("Enabled")
        self.remote_enabled_checkbox.setChecked(True)  # Default to enabled
        remote_form_layout.addRow("", self.remote_enabled_checkbox)

        remote_agent_layout.addLayout(remote_form_layout)

        # Headers section for remote agents
        remote_headers_group = QGroupBox("HTTP Headers")
        remote_headers_layout = QVBoxLayout()

        self.remote_headers_layout = QVBoxLayout()
        self.remote_header_inputs = []

        # Add Header button
        remote_headers_btn_layout = QHBoxLayout()
        self.add_remote_header_btn = QPushButton("Add Header")
        self.add_remote_header_btn.setStyleSheet(
            style_provider.get_button_style("primary")
        )
        self.add_remote_header_btn.clicked.connect(
            lambda: self.add_remote_header_field("", "")
        )
        remote_headers_btn_layout.addWidget(self.add_remote_header_btn)
        remote_headers_btn_layout.addStretch()

        self.remote_headers_layout.addLayout(remote_headers_btn_layout)
        remote_headers_layout.addLayout(self.remote_headers_layout)
        remote_headers_group.setLayout(remote_headers_layout)

        remote_agent_layout.addWidget(remote_headers_group)
        remote_agent_layout.addStretch()

        self.editor_stacked_widget.addWidget(self.local_agent_editor_widget)
        self.editor_stacked_widget.addWidget(self.remote_agent_editor_widget)

        # Save button (common to both editors)
        self.save_btn = QPushButton("Save")
        # ... (save_btn styling and connect remains the same)
        self.save_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.save_btn.clicked.connect(self.save_agent)
        self.save_btn.setEnabled(False)

        self.editor_layout.addWidget(self.editor_stacked_widget)  # Changed
        self.editor_layout.addWidget(self.save_btn)
        # self.editor_layout.addStretch() # Removed, stretch is within individual editors

        # Create loading overlay (will be parented to the main widget)
        self.loading_overlay = LoadingOverlay(self, "Saving agents...")

        # Connect signals for editor fields to handle changes
        # Local agent fields
        self.name_input.textChanged.connect(self._on_editor_field_changed)
        self.description_input.textChanged.connect(self._on_editor_field_changed)
        self.temperature_input.textChanged.connect(self._on_editor_field_changed)
        self.system_prompt_input.markdown_changed.connect(self._on_editor_field_changed)
        self.enabled_checkbox.stateChanged.connect(self._on_editor_field_changed)

        # Voice settings field connections
        self.voice_enabled_checkbox.stateChanged.connect(self._on_editor_field_changed)
        self.voice_id_input.textChanged.connect(self._on_editor_field_changed)

        for checkbox in self.tool_checkboxes.values():
            checkbox.stateChanged.connect(self._on_editor_field_changed)
        # Behavior editing fields
        self.behavior_id_input.textChanged.connect(self._on_editor_field_changed)
        self.behavior_description_input.textChanged.connect(
            self._on_editor_field_changed
        )
        # Remote agent fields
        self.remote_name_input.textChanged.connect(self._on_editor_field_changed)
        self.remote_base_url_input.textChanged.connect(self._on_editor_field_changed)
        self.remote_enabled_checkbox.stateChanged.connect(self._on_editor_field_changed)

        right_panel.setWidget(editor_container_widget)  # Set the container widget

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 600])  # Initial sizes

        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

        self.set_editor_enabled(False)

    def load_agents(self):
        """Load agents from configuration."""
        self.agents_list.clear()

        local_agents = self.agents_config.get("agents", [])
        for agent_conf in local_agents:
            item_data = agent_conf.copy()
            item_data["agent_type"] = "local"
            item = QListWidgetItem(item_data.get("name", "Unnamed Local Agent"))
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.agents_list.addItem(item)

        remote_agents = self.agents_config.get("remote_agents", [])
        for agent_conf in remote_agents:
            item_data = agent_conf.copy()
            item_data["agent_type"] = "remote"
            item = QListWidgetItem(item_data.get("name", "Unnamed Remote Agent"))
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.agents_list.addItem(item)
        self.agents_list.setCurrentRow(0)

    def on_selection_changed(self):
        """Handle selection changes to update button states."""
        selected_items = self.agents_list.selectedItems()
        has_selection = len(selected_items) > 0

        # Enable/disable export and remove buttons based on selection
        self.export_agents_btn.setEnabled(has_selection)
        self.remove_agent_btn.setEnabled(has_selection)

    def on_agent_selected(self, current, _):
        """Handle agent selection."""
        if current is None:
            self.set_editor_enabled(False)
            # Optionally hide both editors or show a placeholder
            # self.editor_stacked_widget.setCurrentIndex(-1) # or a placeholder widget index
            return

        self.set_editor_enabled(True)

        agent_data = current.data(Qt.ItemDataRole.UserRole)
        agent_type = agent_data.get("agent_type", "local")

        all_editor_widgets = [
            self.name_input,
            self.description_input,
            self.temperature_input,
            self.system_prompt_input,
            self.enabled_checkbox,
            self.voice_enabled_checkbox,
            self.voice_id_input,
            self.remote_name_input,
            self.remote_base_url_input,
            self.remote_enabled_checkbox,
        ] + list(self.tool_checkboxes.values())
        for widget in all_editor_widgets:
            widget.blockSignals(True)

        if agent_type == "local":
            self.editor_stacked_widget.setCurrentWidget(self.local_agent_editor_widget)
            self.name_input.setText(agent_data.get("name", ""))
            self.description_input.setText(agent_data.get("description", ""))
            self.temperature_input.setText(str(agent_data.get("temperature", "0.5")))
            self.enabled_checkbox.setChecked(agent_data.get("enabled", True))

            # Load voice settings
            voice_state = agent_data.get("voice_enabled", "disabled")
            if voice_state == "full":
                self.voice_enabled_checkbox.setCheckState(Qt.CheckState.Checked)
            elif voice_state == "partial":
                self.voice_enabled_checkbox.setCheckState(
                    Qt.CheckState.PartiallyChecked
                )
            else:
                self.voice_enabled_checkbox.setCheckState(Qt.CheckState.Unchecked)

            self.voice_id_input.setText(agent_data.get("voice_id", ""))

            tools = agent_data.get("tools", [])
            for tool, checkbox in self.tool_checkboxes.items():
                checkbox.setChecked(tool in tools)
            self.system_prompt_input.set_markdown(agent_data.get("system_prompt", ""))
            # Load adaptive behaviors for this agent
            self.load_agent_behaviors(agent_data.get("name", ""))
            # Clear remote fields just in case
            self.remote_name_input.clear()
            self.remote_base_url_input.clear()
            self.remote_enabled_checkbox.setChecked(True)  # Default for clearing
        elif agent_type == "remote":
            self.editor_stacked_widget.setCurrentWidget(self.remote_agent_editor_widget)
            self.remote_name_input.setText(agent_data.get("name", ""))
            self.remote_base_url_input.setText(agent_data.get("base_url", ""))
            self.remote_enabled_checkbox.setChecked(agent_data.get("enabled", True))

            self.clear_remote_header_fields()
            headers = agent_data.get("headers", {})
            for key, value in headers.items():
                self.add_remote_header_field(key, value, mark_dirty_on_add=False)

            # Clear local fields
            self.name_input.clear()
            self.description_input.clear()
            self.temperature_input.clear()
            self.system_prompt_input.clear()
            self.enabled_checkbox.setChecked(True)  # Default for clearing

            # Clear voice settings
            self.voice_enabled_checkbox.setChecked(False)
            self.voice_id_input.clear()

            for checkbox in self.tool_checkboxes.values():
                checkbox.setChecked(False)
            # Clear behaviors
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}
            self.behavior_form_widget.hide()

        for widget in all_editor_widgets:
            widget.blockSignals(False)

        self._is_dirty = False
        self.save_btn.setEnabled(False)

    def _find_agent_index_by_name(self, agent_name):
        """Find the index of an agent in the agents_list by name."""
        for i in range(self.agents_list.count()):
            item = self.agents_list.item(i)
            agent_data = item.data(Qt.ItemDataRole.UserRole)
            if agent_data.get("name", "") == agent_name:
                return i
        return -1

    def _on_editor_field_changed(self):
        """Mark configuration as dirty and enable save if an agent is selected and editor is active."""
        if self.agents_list.currentItem():
            current_editor_widget = self.editor_stacked_widget.currentWidget()
            is_editor_active = False
            if (
                current_editor_widget == self.local_agent_editor_widget
                and self.local_agent_tab_widget.isEnabled()
            ):
                is_editor_active = True
            elif (
                current_editor_widget == self.remote_agent_editor_widget
                and self.remote_name_input.isEnabled()
            ):
                is_editor_active = True

            if is_editor_active:
                if not self._is_dirty:
                    self._is_dirty = True
                self.save_btn.setEnabled(True)

    def set_editor_enabled(self, enabled: bool):
        """Enable or disable all editor form fields."""
        # Local agent tab widget
        self.local_agent_tab_widget.setEnabled(enabled)

        # Local agent fields
        self.name_input.setEnabled(enabled)
        self.description_input.setEnabled(enabled)
        self.temperature_input.setEnabled(enabled)
        self.system_prompt_input.setEnabled(enabled)
        self.enabled_checkbox.setEnabled(enabled)
        for checkbox in self.tool_checkboxes.values():
            checkbox.setEnabled(enabled)

        # Remote agent fields
        self.remote_name_input.setEnabled(enabled)
        self.remote_base_url_input.setEnabled(enabled)
        self.remote_enabled_checkbox.setEnabled(enabled)
        self.add_remote_header_btn.setEnabled(enabled)

        # Enable/disable remote header fields
        for header_data in self.remote_header_inputs:
            header_data["key_input"].setEnabled(enabled)
            header_data["value_input"].setEnabled(enabled)
            header_data["remove_btn"].setEnabled(enabled)

        # Enable/disable behavior management
        self.behaviors_list.setEnabled(enabled)
        self.add_behavior_btn.setEnabled(enabled)
        self.edit_behavior_btn.setEnabled(
            enabled and self.behaviors_list.currentItem() is not None
        )
        self.remove_behavior_btn.setEnabled(
            enabled and self.behaviors_list.currentItem() is not None
        )
        if not enabled:
            self.behavior_form_widget.hide()

        if not enabled:
            # Clear all fields when disabling
            self.name_input.clear()
            self.description_input.clear()
            self.temperature_input.clear()
            self.system_prompt_input.clear()
            self.enabled_checkbox.setChecked(True)
            for checkbox in self.tool_checkboxes.values():
                checkbox.setChecked(False)

            self.remote_name_input.clear()
            self.remote_base_url_input.clear()
            self.remote_enabled_checkbox.setChecked(True)
            self.clear_remote_header_fields()

            # Clear behaviors
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}
            self.behavior_form_widget.hide()

            self.save_btn.setEnabled(False)
            self._is_dirty = False
            # self.editor_stacked_widget.setCurrentIndex(-1) # Optionally hide content

    def add_new_local_agent(self):
        """Add a new local agent to the configuration."""
        new_agent_data = {
            "name": "NewLocalAgent",
            "description": "Description for the new local agent",
            "temperature": 0.5,
            "tools": ["memory", "clipboard"],
            "system_prompt": "You are a helpful assistant. Today is {current_date}.",
            "enabled": True,
            "agent_type": "local",
        }

        item = QListWidgetItem(new_agent_data["name"])
        item.setData(Qt.ItemDataRole.UserRole, new_agent_data)
        self.agents_list.addItem(item)
        self.agents_list.setCurrentItem(item)  # Triggers on_agent_selected

        # on_agent_selected will switch to local editor and populate.
        self._is_dirty = True
        self.save_btn.setEnabled(True)
        self.name_input.setFocus()
        self.name_input.selectAll()

    def add_new_remote_agent(self):
        """Add a new remote agent to the configuration."""
        new_agent_data = {
            "name": "NewRemoteAgent",
            "base_url": "http://localhost:8000",
            "enabled": True,
            "headers": {},
            "agent_type": "remote",
        }

        item = QListWidgetItem(new_agent_data["name"])
        item.setData(Qt.ItemDataRole.UserRole, new_agent_data)
        self.agents_list.addItem(item)
        self.agents_list.setCurrentItem(item)  # Triggers on_agent_selected

        # on_agent_selected will switch to remote editor and populate.
        self._is_dirty = True
        self.save_btn.setEnabled(True)
        self.remote_name_input.setFocus()
        self.remote_name_input.selectAll()

    def add_remote_header_field(self, key="", value="", mark_dirty_on_add=True):
        """Add a field for a remote agent HTTP header."""
        header_layout = QHBoxLayout()

        key_input = QLineEdit()
        key_input.setText(str(key))
        key_input.setPlaceholderText("Header Name (e.g., Authorization)")
        key_input.textChanged.connect(self._on_editor_field_changed)

        value_input = QLineEdit()
        value_input.setText(str(value))
        value_input.setPlaceholderText("Header Value (e.g., Bearer token)")
        value_input.textChanged.connect(self._on_editor_field_changed)

        remove_btn = QPushButton("Remove")
        remove_btn.setMaximumWidth(80)
        style_provider = StyleProvider()
        remove_btn.setStyleSheet(style_provider.get_button_style("red"))

        header_layout.addWidget(key_input)
        header_layout.addWidget(value_input)
        header_layout.addWidget(remove_btn)

        # Insert before the add button
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
            self._on_editor_field_changed()
        return header_data

    def remove_remote_header_field(self, header_data):
        """Remove a remote agent header field."""
        # Remove from layout
        self.remote_headers_layout.removeItem(header_data["layout"])

        # Delete widgets
        header_data["key_input"].deleteLater()
        header_data["value_input"].deleteLater()
        header_data["remove_btn"].deleteLater()

        # Remove from list
        self.remote_header_inputs.remove(header_data)
        self._on_editor_field_changed()

    def clear_remote_header_fields(self):
        """Clear all remote agent header fields."""
        while self.remote_header_inputs:
            self.remove_remote_header_field(self.remote_header_inputs[0])

    def remove_agent(self):
        """Remove the selected agent(s)."""
        selected_items = self.agents_list.selectedItems()
        if not selected_items:
            return

        if len(selected_items) == 1:
            agent_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
            agent_name = agent_data.get("name", "this agent")
            message = f"Are you sure you want to delete the agent '{agent_name}'?"
        else:
            agent_names = [
                item.data(Qt.ItemDataRole.UserRole).get("name", "unnamed")
                for item in selected_items
            ]
            message = (
                f"Are you sure you want to delete {len(selected_items)} agents?\n\n• "
                + "\n• ".join(agent_names)
            )

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Remove items in reverse order to maintain valid row indices
            rows_to_remove = sorted(
                [self.agents_list.row(item) for item in selected_items], reverse=True
            )
            for row in rows_to_remove:
                self.agents_list.takeItem(row)

            # set_editor_enabled(False) is called by on_agent_selected when currentItem becomes None
            # or when a new item is selected. If list becomes empty, on_agent_selected(None, old_item) is called.
            if self.agents_list.count() == 0:
                self.set_editor_enabled(False)  # Explicitly disable if list is empty

            self.save_all_agents()

    def save_agent(self):
        """Save the current agent configuration."""
        current_item = self.agents_list.currentItem()
        if not current_item:
            return

        agent_data_from_list = current_item.data(Qt.ItemDataRole.UserRole)
        agent_type = agent_data_from_list.get("agent_type", "local")

        updated_agent_data = {}

        if agent_type == "local":
            name = self.name_input.text().strip()
            description = self.description_input.text().strip()
            system_prompt = self.system_prompt_input.get_markdown().strip()
            try:
                temperature = float(self.temperature_input.text().strip() or "0.5")
                temperature = max(0.0, min(2.0, temperature))
            except ValueError:
                temperature = 0.5

            if not name:
                QMessageBox.warning(
                    self, "Validation Error", "Local Agent name cannot be empty."
                )
                return

            tools = [t for t, cb in self.tool_checkboxes.items() if cb.isChecked()]

            voice_state = "disabled"
            if self.voice_enabled_checkbox.checkState() == Qt.CheckState.Checked:
                voice_state = "full"
            elif (
                self.voice_enabled_checkbox.checkState()
                == Qt.CheckState.PartiallyChecked
            ):
                voice_state = "partial"

            updated_agent_data = {
                "name": name,
                "description": description,
                "temperature": temperature,
                "tools": tools,
                "system_prompt": system_prompt,
                "enabled": self.enabled_checkbox.isChecked(),
                "voice_enabled": voice_state,
                "voice_id": self.voice_id_input.text().strip(),
                "agent_type": "local",
            }
            current_item.setText(name)
        elif agent_type == "remote":
            name = self.remote_name_input.text().strip()
            base_url = self.remote_base_url_input.text().strip()

            if not name:
                QMessageBox.warning(
                    self, "Validation Error", "Remote Agent name cannot be empty."
                )
                return
            if not base_url:  # Basic validation for URL
                QMessageBox.warning(
                    self, "Validation Error", "Remote Agent Base URL cannot be empty."
                )
                return

            headers = {}
            for header_data in self.remote_header_inputs:
                key = header_data["key_input"].text().strip()
                value = header_data["value_input"].text().strip()
                if key:
                    headers[key] = value

            updated_agent_data = {
                "name": name,
                "base_url": base_url,
                "enabled": self.remote_enabled_checkbox.isChecked(),
                "headers": headers,
                "agent_type": "remote",
            }
            current_item.setText(name)

        current_item.setData(Qt.ItemDataRole.UserRole, updated_agent_data)
        self.save_all_agents()
        self._is_dirty = False
        self.save_btn.setEnabled(False)

    def import_agents(self):
        """Import agent configurations from a file."""
        # Open file dialog to select a TOML or JSON file
        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("Import Agent Configuration")
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter("Agent Configuration (*.toml *.json)")

        if not file_dialog.exec():
            # User canceled the dialog
            return

        selected_files = file_dialog.selectedFiles()
        if not selected_files:
            return

        import_file_path = selected_files[0]

        # Check if file exists
        if not os.path.exists(import_file_path):
            QMessageBox.critical(
                self,
                "Import Error",
                f"The selected file does not exist: {import_file_path}",
            )
            return

        try:
            # First, try to load to check for conflicts
            temp_config = ConfigManagement(import_file_path)
            imported_config = temp_config.get_config()

            # Validate the configuration structure
            local_agents = imported_config.get("agents", [])
            remote_agents = imported_config.get("remote_agents", [])

            if not local_agents and not remote_agents:
                QMessageBox.warning(
                    self,
                    "Invalid Configuration",
                    "No agent configurations found in the selected file.",
                )
                return

            # Check for conflicts
            existing_agent_names = set()
            for i in range(self.agents_list.count()):
                item = self.agents_list.item(i)
                agent_data = item.data(Qt.ItemDataRole.UserRole)
                existing_agent_names.add(agent_data.get("name", ""))

            # Find conflicts
            conflict_names = []
            imported_names = []

            for agent in local_agents:
                name = agent.get("name", "")
                if name:
                    imported_names.append(name)
                    if name in existing_agent_names:
                        conflict_names.append(name)

            for agent in remote_agents:
                name = agent.get("name", "")
                if name:
                    imported_names.append(name)
                    if name in existing_agent_names:
                        conflict_names.append(name)

            # If there are conflicts, ask user how to proceed
            skip_conflicts = False
            if conflict_names:
                conflict_list = "\n".join([f"• {name}" for name in conflict_names])

                message_box = QMessageBox(self)
                message_box.setWindowTitle("Agent Name Conflicts")
                message_box.setIcon(QMessageBox.Icon.Warning)
                message_box.setText(
                    f"The following agent(s) already exist and will be overridden:\n\n"
                    f"{conflict_list}\n\n"
                    f"How would you like to proceed?"
                )

                override_btn = message_box.addButton(
                    "Override", QMessageBox.ButtonRole.AcceptRole
                )
                skip_btn = message_box.addButton(
                    "Skip Conflicts", QMessageBox.ButtonRole.ActionRole
                )

                message_box.exec()

                clicked_button = message_box.clickedButton()
                if clicked_button == override_btn:
                    skip_conflicts = False
                elif clicked_button == skip_btn:
                    skip_conflicts = True
                else:
                    return  # User canceled

            # Use ConfigManagement to import agents
            result = self.config_manager.import_agents(
                import_file_path, merge_strategy="update", skip_conflicts=skip_conflicts
            )

            if not result["success"]:
                QMessageBox.critical(
                    self,
                    "Import Error",
                    f"Failed to import agents:\n{result.get('error', 'Unknown error')}",
                )
                return

            # Reload agents in UI
            self.load_agents()

            # Select the first imported agent in the list if any were imported
            if result["imported_agents"]:
                index = self._find_agent_index_by_name(result["imported_agents"][0])
                if index >= 0:
                    self.agents_list.setCurrentRow(index)

            # Show success message
            status_message = f"Successfully imported {result['added_count'] + result['updated_count']} agent(s)."

            if result["added_count"] > 0:
                status_message += f"\n• Added: {result['added_count']}"
            if result["updated_count"] > 0:
                status_message += f"\n• Updated: {result['updated_count']}"
            if result["skipped_count"] > 0:
                status_message += f"\n• Skipped: {result['skipped_count']}"

            QMessageBox.information(self, "Import Complete", status_message)

        except Exception as e:
            QMessageBox.critical(
                self, "Import Error", f"Failed to import agent configuration: {str(e)}"
            )

    def export_agents(self):
        """Export selected agents to a file."""
        selected_items = self.agents_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "No Selection", "Please select one or more agents to export."
            )
            return

        # Collect agent names
        agent_names = []
        for item in selected_items:
            agent_data = item.data(Qt.ItemDataRole.UserRole)
            agent_name = agent_data.get("name")
            if agent_name:
                agent_names.append(agent_name)

        if not agent_names:
            QMessageBox.warning(
                self, "No Agents", "No valid agents selected for export."
            )
            return

        # Determine default filename
        if len(selected_items) == 1:
            default_filename = f"{agent_names[0]}_export"
        else:
            default_filename = f"agents_export_{len(selected_items)}_agents"

        # File dialog
        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("Export Agent Configuration")
        file_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        file_dialog.setNameFilter("TOML Files (*.toml);;JSON Files (*.json)")
        file_dialog.selectFile(default_filename)

        if not file_dialog.exec():
            return

        selected_files = file_dialog.selectedFiles()
        if not selected_files:
            return

        export_file_path = selected_files[0]
        selected_filter = file_dialog.selectedNameFilter()

        export_file_path, file_format = self._determine_file_format_and_path(
            export_file_path, selected_filter
        )

        try:
            # Use ConfigManagement to export agents
            result = self.config_manager.export_agents(
                agent_names, export_file_path, file_format=file_format
            )

            if not result["success"]:
                QMessageBox.critical(
                    self,
                    "Export Error",
                    f"Failed to export agents:\n{result.get('error', 'Unknown error')}",
                )
                return

            # Show success message
            agent_count = result["exported_count"]
            agent_word = "agent" if agent_count == 1 else "agents"

            message = f"Successfully exported {agent_count} {agent_word} to:\n{result['output_file']}"

            if result["missing_agents"]:
                message += (
                    "\n\nWarning: The following agents were not found:\n"
                    + "\n".join(result["missing_agents"])
                )

            QMessageBox.information(self, "Export Successful", message)

        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export agents:\n{str(e)}"
            )

    def save_all_agents(self):
        """Save all agents to the configuration file with loading indicator."""
        self.loading_overlay.set_message("Saving agents...")
        self.loading_overlay.show_loading()

        # Disable UI during save
        self.agents_list.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.add_agent_menu_btn.setEnabled(False)
        self.import_agents_btn.setEnabled(False)
        self.export_agents_btn.setEnabled(False)
        self.remove_agent_btn.setEnabled(False)

        local_agents_list = []
        remote_agents_list = []

        for i in range(self.agents_list.count()):
            item = self.agents_list.item(i)
            agent_data = item.data(Qt.ItemDataRole.UserRole)

            config_data = agent_data.copy()
            agent_type_for_sorting = config_data.pop("agent_type", "local")

            if agent_type_for_sorting == "local":
                local_agents_list.append(config_data)
            elif agent_type_for_sorting == "remote":
                remote_agents_list.append(config_data)

        self.agents_config["agents"] = local_agents_list
        self.agents_config["remote_agents"] = remote_agents_list

        self.save_worker = SaveWorker(self._perform_agent_save, self.agents_config)
        self.save_worker.finished.connect(self._on_save_complete)
        self.save_worker.error.connect(self._on_save_error)
        self.save_worker.start()

    def _perform_agent_save(self, config_data):
        """Perform the actual save operation (runs in worker thread)."""
        self.config_manager.write_agents_config(config_data)

    def _on_save_complete(self):
        """Handle successful save completion."""
        self.loading_overlay.hide_loading()

        self.agents_list.setEnabled(True)
        self.add_agent_menu_btn.setEnabled(True)
        self.import_agents_btn.setEnabled(True)

        self.on_selection_changed()

        self.config_changed.emit()

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    def _on_save_error(self, error_message: str):
        """Handle save error."""
        # Hide loading overlay
        self.loading_overlay.hide_loading()

        self.agents_list.setEnabled(True)
        self.add_agent_menu_btn.setEnabled(True)
        self.import_agents_btn.setEnabled(True)
        self.on_selection_changed()

        QMessageBox.critical(
            self, "Save Error", f"Failed to save agents configuration:\n{error_message}"
        )

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    def load_agent_behaviors(self, agent_name: str):
        """Load adaptive behaviors for the selected agent."""
        if not agent_name:
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}
            return

        try:
            behaviors = self.persistence_service.get_adaptive_behaviors(agent_name)
            self.current_agent_behaviors = behaviors

            self.behaviors_list.clear()
            for behavior_id, behavior_text in behaviors.items():
                # Create a shortened preview for the list
                item = QListWidgetItem(f"{behavior_text}")
                item.setData(
                    Qt.ItemDataRole.UserRole, {"id": behavior_id, "text": behavior_text}
                )
                self.behaviors_list.addItem(item)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Behavior Load Error",
                f"Failed to load behaviors for {agent_name}: {str(e)}",
            )
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}

    def on_behavior_selected(self, current, _):
        """Handle behavior selection in the list."""
        has_selection = current is not None
        self.edit_behavior_btn.setEnabled(has_selection)
        self.remove_behavior_btn.setEnabled(has_selection)

    def add_new_behavior(self):
        """Show form to add a new adaptive behavior."""
        self.behavior_id_input.clear()
        self.behavior_description_input.clear()
        self.behavior_form_widget.show()
        self.behavior_id_input.setFocus()

    def edit_behavior(self):
        """Edit the selected adaptive behavior."""
        current_item = self.behaviors_list.currentItem()
        if not current_item:
            return

        behavior_data = current_item.data(Qt.ItemDataRole.UserRole)
        self.behavior_id_input.setText(behavior_data["id"])
        self.behavior_description_input.setText(behavior_data["text"])
        self.behavior_form_widget.show()
        self.behavior_description_input.setFocus()

    def save_behavior(self):
        """Save the current behavior being edited."""
        current_agent_item = self.agents_list.currentItem()
        if not current_agent_item:
            QMessageBox.warning(
                self, "No Agent Selected", "Please select an agent first."
            )
            return

        agent_data = current_agent_item.data(Qt.ItemDataRole.UserRole)
        agent_name = agent_data.get("name", "")
        if not agent_name:
            QMessageBox.warning(self, "Invalid Agent", "Agent name is required.")
            return

        behavior_id = self.behavior_id_input.text().strip()
        behavior_text = self.behavior_description_input.text().strip()

        if not behavior_id:
            QMessageBox.warning(self, "Validation Error", "Behavior ID is required.")
            return

        if not behavior_text:
            QMessageBox.warning(
                self, "Validation Error", "Behavior description is required."
            )
            return

        # Validate behavior format
        behavior_lower = behavior_text.lower()
        if not behavior_lower.startswith("when "):
            QMessageBox.warning(
                self,
                "Format Error",
                "Behavior must follow 'when [condition], [action]' format.",
            )
            return

        try:
            success = self.persistence_service.store_adaptive_behavior(
                agent_name, behavior_id, behavior_text
            )
            if success:
                # Update the current cache and UI
                self.current_agent_behaviors[behavior_id] = behavior_text
                self.load_agent_behaviors(agent_name)
                self.behavior_form_widget.hide()
                QMessageBox.information(
                    self, "Success", f"Behavior '{behavior_id}' saved successfully."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Save Error",
                    "Failed to save behavior. Please check the format.",
                )
        except Exception as e:
            QMessageBox.critical(
                self, "Save Error", f"Failed to save behavior: {str(e)}"
            )

    def remove_behavior(self):
        """Remove the selected adaptive behavior."""
        current_item = self.behaviors_list.currentItem()
        if not current_item:
            return

        current_agent_item = self.agents_list.currentItem()
        if not current_agent_item:
            return

        agent_data = current_agent_item.data(Qt.ItemDataRole.UserRole)
        agent_name = agent_data.get("name", "")

        behavior_data = current_item.data(Qt.ItemDataRole.UserRole)
        behavior_id = behavior_data["id"]

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete the behavior '{behavior_id}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.persistence_service.remove_adaptive_behavior(
                    agent_name, behavior_id
                )
                if success:
                    # Update cache and UI
                    if behavior_id in self.current_agent_behaviors:
                        del self.current_agent_behaviors[behavior_id]
                    self.load_agent_behaviors(agent_name)
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Behavior '{behavior_id}' removed successfully.",
                    )
                else:
                    QMessageBox.warning(
                        self, "Remove Error", "Failed to remove behavior."
                    )
            except Exception as e:
                QMessageBox.critical(
                    self, "Remove Error", f"Failed to remove behavior: {str(e)}"
                )

    def cancel_behavior_edit(self):
        """Cancel behavior editing and hide the form."""
        self.behavior_form_widget.hide()
        self.behavior_id_input.clear()
        self.behavior_description_input.clear()
