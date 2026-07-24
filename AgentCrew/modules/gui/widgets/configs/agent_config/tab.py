import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.agents_config import (
    AgentsConfig,
    AgentsFileConfig,
    LocalAgentConfig,
    RemoteAgentConfig,
)
from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.gui.widgets.loading_overlay import LoadingOverlay
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService

from ..save_worker import SaveWorker
from .agent_list_panel import AgentListPanel
from .import_export_actions import determine_file_format_and_path
from .local_agent_editor import LocalAgentEditor
from .remote_agent_editor import RemoteAgentEditor


class AgentsConfigTab(QWidget):
    """Tab for configuring agents."""

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
            "browser",
            "image_generation",
            "file_editing",
            "command_execution",
        ]

        self.agents_config = AgentsConfig().read()
        self._is_dirty = False
        self.save_worker = None

        self.init_ui()
        self.load_agents()

    @staticmethod
    def _determine_file_format_and_path(
        file_path: str, selected_filter: str
    ) -> tuple[str, str]:
        return determine_file_format_and_path(file_path, selected_filter)

    def init_ui(self):
        """Initialize the UI components."""
        main_layout = QHBoxLayout()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel — agent list
        self.agent_list_panel = AgentListPanel()
        self.agent_list_panel.add_local_agent_requested.connect(
            self.add_new_local_agent
        )
        self.agent_list_panel.add_remote_agent_requested.connect(
            self.add_new_remote_agent
        )
        self.agent_list_panel.import_requested.connect(self.import_agents)
        self.agent_list_panel.export_requested.connect(self.export_agents)
        self.agent_list_panel.remove_requested.connect(self.remove_agent)
        self.agent_list_panel.agent_selected.connect(self.on_agent_selected)
        self.agent_list_panel.selection_changed.connect(self.on_selection_changed)

        # Right panel — editor area
        right_panel = QScrollArea()
        right_panel.setWidgetResizable(True)

        editor_container_widget = QWidget()
        editor_container_widget.setStyleSheet(
            StyleProvider().get_editor_container_widget_style()
        )
        self.editor_layout = QVBoxLayout(editor_container_widget)

        self.editor_stacked_widget = QStackedWidget()

        # Local agent editor
        self.local_agent_editor = LocalAgentEditor(
            available_tools=self.available_tools,
            persistence_service=self.persistence_service,
            on_dirty_callback=self._on_editor_field_changed,
            get_current_agent_name=self._get_current_agent_name,
        )
        self.local_agent_editor.field_changed.connect(self._on_editor_field_changed)

        # Remote agent editor
        self.remote_agent_editor = RemoteAgentEditor(
            on_dirty_callback=self._on_editor_field_changed,
        )
        self.remote_agent_editor.field_changed.connect(self._on_editor_field_changed)

        self.editor_stacked_widget.addWidget(self.local_agent_editor)
        self.editor_stacked_widget.addWidget(self.remote_agent_editor)

        # Save button
        self.save_btn = QPushButton("Save")
        self.save_btn.setStyleSheet(StyleProvider().get_button_style("primary"))
        self.save_btn.clicked.connect(self.save_agent)
        self.save_btn.setEnabled(False)

        self.editor_layout.addWidget(self.editor_stacked_widget)
        self.editor_layout.addWidget(self.save_btn)

        right_panel.setWidget(editor_container_widget)

        # Loading overlay
        self.loading_overlay = LoadingOverlay(self, "Saving agents...")

        splitter.addWidget(self.agent_list_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 600])

        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

        self.set_editor_enabled(False)

    # ------------------------------------------------------------------
    # Agent list helpers
    # ------------------------------------------------------------------

    def load_agents(self):
        """Load agents from configuration."""
        self.agent_list_panel.load_agents(self.agents_config)

    def on_selection_changed(self):
        """Handle selection changes to update button states."""
        # The AgentListPanel already manages export/remove enable state

    def on_agent_selected(self, current):
        """Handle agent selection."""
        if current is None:
            self.set_editor_enabled(False)
            return

        self.set_editor_enabled(True)

        agent_data = current.data(Qt.ItemDataRole.UserRole)
        agent_type = agent_data.get("agent_type", "local")

        # Block signals during populate to avoid spurious dirty flags
        all_signal_widgets = self._all_editor_signal_widgets()
        for widget in all_signal_widgets:
            widget.blockSignals(True)

        if agent_type == "local":
            self.editor_stacked_widget.setCurrentWidget(self.local_agent_editor)
            self.local_agent_editor.populate(agent_data)
            self.local_agent_editor.behavior_editor.load_agent_behaviors(
                agent_data.get("name", "")
            )
            self.remote_agent_editor.clear()
        elif agent_type == "remote":
            self.editor_stacked_widget.setCurrentWidget(self.remote_agent_editor)
            self.remote_agent_editor.populate(agent_data)
            self.local_agent_editor.clear()

        for widget in all_signal_widgets:
            widget.blockSignals(False)

        self._is_dirty = False
        self.save_btn.setEnabled(False)

    def _all_editor_signal_widgets(self):
        """Collect all signal-emitting editor widgets for blockSignals."""
        widgets = [
            self.local_agent_editor.name_input,
            self.local_agent_editor.description_input,
            self.local_agent_editor.temperature_input,
            self.local_agent_editor.system_prompt_input,
            self.local_agent_editor.enabled_checkbox,
            self.local_agent_editor.voice_enabled_checkbox,
            self.local_agent_editor.voice_id_input,
            self.remote_agent_editor.remote_name_input,
            self.remote_agent_editor.remote_base_url_input,
            self.remote_agent_editor.remote_enabled_checkbox,
        ] + list(self.local_agent_editor.tool_checkboxes.values())
        return widgets

    # ------------------------------------------------------------------
    # Editor enable / dirty
    # ------------------------------------------------------------------

    def _on_editor_field_changed(self):
        """Mark configuration as dirty and enable save."""
        if self.agent_list_panel.agents_list.currentItem():
            current_editor_widget = self.editor_stacked_widget.currentWidget()
            is_editor_active = False
            if (
                current_editor_widget == self.local_agent_editor
                and self.local_agent_editor.local_agent_tab_widget.isEnabled()
            ) or (
                current_editor_widget == self.remote_agent_editor
                and self.remote_agent_editor.remote_name_input.isEnabled()
            ):
                is_editor_active = True

            if is_editor_active:
                if not self._is_dirty:
                    self._is_dirty = True
                self.save_btn.setEnabled(True)

    def set_editor_enabled(self, enabled: bool):
        """Enable or disable all editor form fields."""
        self.local_agent_editor.setEnabled(enabled)
        self.remote_agent_editor.setEnabled(enabled)

        if not enabled:
            self.local_agent_editor.clear()
            self.remote_agent_editor.clear()
            self.save_btn.setEnabled(False)
            self._is_dirty = False

    # ------------------------------------------------------------------
    # Add / remove agents
    # ------------------------------------------------------------------

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
        self.agent_list_panel.add_agent(new_agent_data)
        self._is_dirty = True
        self.save_btn.setEnabled(True)
        self.local_agent_editor.name_input.setFocus()
        self.local_agent_editor.name_input.selectAll()

    def add_new_remote_agent(self):
        """Add a new remote agent to the configuration."""
        new_agent_data = {
            "name": "NewRemoteAgent",
            "base_url": "http://localhost:8000",
            "enabled": True,
            "headers": {},
            "agent_type": "remote",
        }
        self.agent_list_panel.add_agent(new_agent_data)
        self._is_dirty = True
        self.save_btn.setEnabled(True)
        self.remote_agent_editor.remote_name_input.setFocus()
        self.remote_agent_editor.remote_name_input.selectAll()

    def remove_agent(self):
        """Remove the selected agent(s)."""
        removed = self.agent_list_panel.remove_selected_agents()
        if not removed:
            return

        if self.agent_list_panel.agents_list.count() == 0:
            self.set_editor_enabled(False)

        self.save_all_agents()

    # ------------------------------------------------------------------
    # Save logic
    # ------------------------------------------------------------------

    def save_agent(self):
        """Save the current agent configuration."""
        current_item = self.agent_list_panel.agents_list.currentItem()
        if not current_item:
            return

        agent_data_from_list = current_item.data(Qt.ItemDataRole.UserRole)
        agent_type = agent_data_from_list.get("agent_type", "local")

        updated_agent_data = {}

        if agent_type == "local":
            updated_agent_data = self.local_agent_editor.collect()
            name = updated_agent_data["name"]

            if not name:
                QMessageBox.warning(
                    self, "Validation Error", "Local Agent name cannot be empty."
                )
                return

            current_item.setText(name)
        elif agent_type == "remote":
            updated_agent_data = self.remote_agent_editor.collect()
            name = updated_agent_data["name"]
            base_url = updated_agent_data["base_url"]

            if not name:
                QMessageBox.warning(
                    self, "Validation Error", "Remote Agent name cannot be empty."
                )
                return
            if not base_url:
                QMessageBox.warning(
                    self, "Validation Error", "Remote Agent Base URL cannot be empty."
                )
                return

            current_item.setText(name)

        current_item.setData(Qt.ItemDataRole.UserRole, updated_agent_data)
        self.save_all_agents()
        self._is_dirty = False
        self.save_btn.setEnabled(False)

    def save_all_agents(self):
        """Save all agents to the configuration file with loading indicator."""
        self.loading_overlay.set_message("Saving agents...")
        self.loading_overlay.show_loading()

        # Disable UI during save
        self.agent_list_panel.agents_list.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.agent_list_panel.add_agent_menu_btn.setEnabled(False)
        self.agent_list_panel.import_agents_btn.setEnabled(False)
        self.agent_list_panel.export_agents_btn.setEnabled(False)
        self.agent_list_panel.remove_agent_btn.setEnabled(False)

        local_agents_list = []
        remote_agents_list = []

        for i in range(self.agent_list_panel.agents_list.count()):
            item = self.agent_list_panel.agents_list.item(i)
            agent_data = item.data(Qt.ItemDataRole.UserRole)

            config_data = agent_data.copy()
            agent_type_for_sorting = config_data.pop("agent_type", "local")

            if agent_type_for_sorting == "local":
                local_agents_list.append(config_data)
            elif agent_type_for_sorting == "remote":
                remote_agents_list.append(config_data)

        self.agents_config = AgentsFileConfig(
            agents=[LocalAgentConfig.from_dict(d) for d in local_agents_list],
            remote_agents=[RemoteAgentConfig.from_dict(d) for d in remote_agents_list],
        )

        self.save_worker = SaveWorker(self._perform_agent_save, self.agents_config)
        self.save_worker.finished.connect(self._on_save_complete)
        self.save_worker.error.connect(self._on_save_error)
        self.save_worker.start()

    def _perform_agent_save(self, config_data):
        """Perform the actual save operation (runs in worker thread)."""
        AgentsConfig().write(config_data)

    def _on_save_complete(self):
        """Handle successful save completion."""
        self.loading_overlay.hide_loading()

        self.agent_list_panel.agents_list.setEnabled(True)
        self.agent_list_panel.add_agent_menu_btn.setEnabled(True)
        self.agent_list_panel.import_agents_btn.setEnabled(True)
        self.agent_list_panel.update_selection_state()

        self.config_changed.emit()

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    def _on_save_error(self, error_message: str):
        """Handle save error."""
        self.loading_overlay.hide_loading()

        self.agent_list_panel.agents_list.setEnabled(True)
        self.agent_list_panel.add_agent_menu_btn.setEnabled(True)
        self.agent_list_panel.import_agents_btn.setEnabled(True)
        self.agent_list_panel.update_selection_state()

        QMessageBox.critical(
            self, "Save Error", f"Failed to save agents configuration:\n{error_message}"
        )

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def import_agents(self):
        """Import agent configurations from a file."""
        from PySide6.QtWidgets import QFileDialog

        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("Import Agent Configuration")
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter("Agent Configuration (*.toml *.json)")

        if not file_dialog.exec():
            return

        selected_files = file_dialog.selectedFiles()
        if not selected_files:
            return

        import_file_path = selected_files[0]

        if not os.path.exists(import_file_path):
            QMessageBox.critical(
                self,
                "Import Error",
                f"The selected file does not exist: {import_file_path}",
            )
            return

        try:
            temp_config = ConfigManagement(import_file_path)
            imported_config = temp_config.get_config()

            local_agents = imported_config.get("agents", [])
            remote_agents = imported_config.get("remote_agents", [])

            if not local_agents and not remote_agents:
                QMessageBox.warning(
                    self,
                    "Invalid Configuration",
                    "No agent configurations found in the selected file.",
                )
                return

            existing_agent_names = set()
            for i in range(self.agent_list_panel.agents_list.count()):
                item = self.agent_list_panel.agents_list.item(i)
                agent_data = item.data(Qt.ItemDataRole.UserRole)
                existing_agent_names.add(agent_data.get("name", ""))

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
                    return

            result = AgentsConfig().import_agents(
                import_file_path, merge_strategy="update", skip_conflicts=skip_conflicts
            )

            if not result["success"]:
                QMessageBox.critical(
                    self,
                    "Import Error",
                    f"Failed to import agents:\n{result.get('error', 'Unknown error')}",
                )
                return

            self.agents_config = AgentsConfig().read()
            self.load_agents()

            if result["imported_agents"]:
                index = self.agent_list_panel.find_agent_index_by_name(
                    result["imported_agents"][0]
                )
                if index >= 0:
                    self.agent_list_panel.agents_list.setCurrentRow(index)

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
                self, "Import Error", f"Failed to import agent configuration: {e!s}"
            )

    def export_agents(self):
        """Export selected agents to a file."""
        from PySide6.QtWidgets import QFileDialog

        selected_items = self.agent_list_panel.agents_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "No Selection", "Please select one or more agents to export."
            )
            return

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

        if len(selected_items) == 1:
            default_filename = f"{agent_names[0]}_export"
        else:
            default_filename = f"agents_export_{len(selected_items)}_agents"

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
            result = AgentsConfig().export(
                agent_names, export_file_path, file_format=file_format
            )

            if not result["success"]:
                QMessageBox.critical(
                    self,
                    "Export Error",
                    f"Failed to export agents:\n{result.get('error', 'Unknown error')}",
                )
                return

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
                self, "Export Error", f"Failed to export agents:\n{e!s}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_agent_index_by_name(self, agent_name):
        """Find the index of an agent in the agents_list by name."""
        return self.agent_list_panel.find_agent_index_by_name(agent_name)

    def _get_current_agent_name(self) -> str:
        """Return the name of the currently selected agent, or empty string."""
        current_item = self.agent_list_panel.agents_list.currentItem()
        if not current_item:
            return ""
        agent_data = current_item.data(Qt.ItemDataRole.UserRole)
        return agent_data.get("name", "")
