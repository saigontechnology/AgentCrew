from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.mcp_config import MCPConfig, MCPServerEntry
from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.gui.widgets.loading_overlay import LoadingOverlay

from ..save_worker import SaveWorker
from .mcp_form import MCPForm
from .mcp_json_sync import MCPJsonSync
from .mcp_list_panel import MCPListPanel


class MCPsConfigTab(QWidget):
    """Tab for configuring MCP servers."""

    config_changed = Signal()

    def __init__(self, config_manager: ConfigManagement):
        super().__init__()
        self.config_manager = config_manager
        self.is_dirty = False
        self.current_server_data = None
        self.style_provider = StyleProvider()
        self.save_worker = None

        self.mcps_config = MCPConfig().read()

        self._init_ui()
        self._load_mcps()

        self.style_provider.theme_changed.connect(self._on_theme_changed)

    def _init_ui(self):
        main_layout = QHBoxLayout()

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel
        self.mcp_list_panel = MCPListPanel()
        self.mcp_list_panel.server_selected.connect(self._on_mcp_selected)
        self.mcp_list_panel.add_requested.connect(self._add_new_mcp)
        self.mcp_list_panel.remove_requested.connect(self._remove_mcp)
        self.mcp_list_panel.selection_cleared.connect(self._on_selection_cleared)

        # Right panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # JSON sync / view toggle
        self.json_sync = MCPJsonSync(parent=self)
        self.json_sync.view_switched_to_form.connect(self._on_view_switched_to_form)
        self.json_sync.view_switched_to_code.connect(self._on_view_switched_to_code)
        self.json_sync.validation_error.connect(self._on_json_validation_error)
        self.json_sync.json_changed.connect(self._on_json_changed)

        # MCP form
        self.mcp_form = MCPForm()
        self.mcp_form.dirty.connect(self._mark_dirty)

        # Stacked widget: form at index 0, json editor at index 1
        self.json_sync.setup_stacked_widget(self.mcp_form)

        right_layout.addWidget(self.json_sync.stacked_widget)

        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.json_sync.show_code_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setStyleSheet(self.style_provider.get_button_style("primary"))
        self.save_btn.clicked.connect(self._save_mcp)
        self.save_btn.setEnabled(False)
        button_layout.addWidget(self.save_btn)

        right_layout.addLayout(button_layout)

        splitter.addWidget(self.mcp_list_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 600])

        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

        self.loading_overlay = LoadingOverlay(self, "Saving MCP servers...")

        self._set_editor_enabled(False)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_mcps(self):
        self.mcp_list_panel.load_mcps(self.mcps_config)

    def load_mcps(self):
        self.mcp_list_panel.load_mcps(self.mcps_config)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_mcp_selected(self, server_id, server_config):
        self._set_editor_enabled(True)
        self.current_server_data = server_config

        self.json_sync.is_code_view = False

        self.mcp_form.populate(server_config)

        self.is_dirty = False
        self._update_save_button_state()

    def _on_selection_cleared(self):
        self._set_editor_enabled(False)

    # ------------------------------------------------------------------
    # Dirty state
    # ------------------------------------------------------------------

    def _mark_dirty(self):
        if (
            self.mcp_list_panel.mcps_list.currentItem()
            and self.mcp_form.name_input.isEnabled()
        ):
            self.is_dirty = True
            self._update_save_button_state()

    def _update_save_button_state(self):
        current_item_selected = self.mcp_list_panel.mcps_list.currentItem() is not None
        can_save = current_item_selected and self.is_dirty
        self.save_btn.setEnabled(can_save)

    # ------------------------------------------------------------------
    # Editor enable/disable
    # ------------------------------------------------------------------

    def _set_editor_enabled(self, enabled: bool):
        self.mcp_form.set_enabled(enabled)
        self.json_sync.show_code_btn.setEnabled(enabled)
        self.json_sync.set_read_only(not enabled)

        if not enabled:
            self.is_dirty = False
            self.json_sync.is_code_view = False
            self._update_save_button_state()

    # ------------------------------------------------------------------
    # Add / Remove
    # ------------------------------------------------------------------

    def _add_new_mcp(self):
        server_id = f"new_server_{len(self.mcps_config) + 1}"
        new_server = {
            "name": "New Server",
            "command": "docker",
            "args": ["run", "-i", "--rm"],
            "env": {},
            "enabledForAgents": [],
            "streaming_server": False,
            "url": "",
            "headers": {},
            "includeTools": [],
        }

        item = self.mcp_list_panel.add_server(server_id, new_server)
        self.mcp_list_panel.select_and_focus(item)

        self.is_dirty = True
        self._update_save_button_state()

        self.mcp_form.name_input.setFocus()
        self.mcp_form.name_input.selectAll()

    def _remove_mcp(self):
        result = self.mcp_list_panel.current_server()
        if result is None:
            return

        server_id, server_config = result
        server_name = server_config.get("name", server_id)

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete the MCP server '{server_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.mcp_list_panel.remove_selected_server()
            self._set_editor_enabled(False)
            self.mcp_form.clear()
            self._save_all_mcps()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_mcp(self):
        current_item = self.mcp_list_panel.mcps_list.currentItem()
        if not current_item:
            return

        server_id, _old_config = current_item.data(Qt.ItemDataRole.UserRole)

        if self.json_sync.is_code_view:
            try:
                server_config = self.json_sync.get_json()
            except ValueError as e:
                QMessageBox.warning(
                    self,
                    "Invalid JSON",
                    f"Cannot save configuration: {e!s}\n"
                    "Please fix the JSON syntax first.",
                )
                return
        else:
            server_config = self.mcp_form.collect()

        name = server_config.get("name", "").strip()
        streaming_server = server_config.get("streaming_server", False)
        url = server_config.get("url", "").strip()
        command = server_config.get("command", "").strip()

        if not name:
            QMessageBox.warning(
                self, "Validation Error", "Server name cannot be empty."
            )
            return

        if streaming_server:
            if not url:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "URL cannot be empty for streaming servers.",
                )
                return
        else:
            if not command:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "Command cannot be empty for stdio servers.",
                )
                return

        current_item.setText(name)
        current_item.setData(Qt.ItemDataRole.UserRole, (server_id, server_config))

        if not self.json_sync.is_code_view:
            self._update_form_from_json(server_config, server_id)

        self.is_dirty = False
        self._update_save_button_state()

        self._save_all_mcps()

    def _save_all_mcps(self):
        self.loading_overlay.set_message("Saving MCP servers...")
        self.loading_overlay.show_loading()

        self.mcp_list_panel.mcps_list.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.mcp_list_panel.add_btn.setEnabled(False)
        self.mcp_list_panel.remove_btn.setEnabled(False)
        self.json_sync.show_code_btn.setEnabled(False)

        mcps_config = {}
        for i in range(self.mcp_list_panel.mcps_list.count()):
            item = self.mcp_list_panel.mcps_list.item(i)
            server_id, server_config = item.data(Qt.ItemDataRole.UserRole)
            mcps_config[server_id] = server_config

        self.mcps_config = mcps_config

        self.save_worker = SaveWorker(self._perform_mcp_save, self.mcps_config)
        self.save_worker.finished.connect(self._on_save_complete)
        self.save_worker.error.connect(self._on_save_error)
        self.save_worker.start()

    def _perform_mcp_save(self, mcps_config):
        typed_config = {
            k: MCPServerEntry.from_dict(k, v) if isinstance(v, dict) else v
            for k, v in mcps_config.items()
        }
        MCPConfig().write(typed_config)

    def _on_save_complete(self):
        self.loading_overlay.hide_loading()

        self.mcp_list_panel.mcps_list.setEnabled(True)
        self.mcp_list_panel.add_btn.setEnabled(True)

        if self.mcp_list_panel.mcps_list.currentItem():
            self.mcp_list_panel.remove_btn.setEnabled(True)
            self.json_sync.show_code_btn.setEnabled(True)

        self.config_changed.emit()

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    def _on_save_error(self, error_message: str):
        self.loading_overlay.hide_loading()

        self.mcp_list_panel.mcps_list.setEnabled(True)
        self.mcp_list_panel.add_btn.setEnabled(True)
        if self.mcp_list_panel.mcps_list.currentItem():
            self.mcp_list_panel.remove_btn.setEnabled(True)
            self.json_sync.show_code_btn.setEnabled(True)

        QMessageBox.critical(
            self,
            "Save Error",
            f"Failed to save MCP servers configuration:\n{error_message}",
        )

        if self.save_worker:
            self.save_worker.deleteLater()
            self.save_worker = None

    # ------------------------------------------------------------------
    # JSON sync / view toggle
    # ------------------------------------------------------------------

    def _on_view_switched_to_form(self, json_data: dict):
        current_item = self.mcp_list_panel.mcps_list.currentItem()
        if current_item:
            server_id, _ = current_item.data(Qt.ItemDataRole.UserRole)
            self._update_form_from_json(json_data, server_id)

    def _on_view_switched_to_code(self, _json_data: dict):
        server_data = self.mcp_form.collect()
        if server_data:
            self.json_sync.set_json(server_data)

    def _on_json_changed(self, json_data: dict):
        if self.json_sync.is_code_view:
            self.is_dirty = True
            self._update_save_button_state()

    def _on_json_validation_error(self, error_msg: str):
        if self.json_sync.is_code_view:
            self.save_btn.setEnabled(False)

    def _update_form_from_json(self, json_data: dict, server_id: str):
        current_item = self.mcp_list_panel.mcps_list.currentItem()
        if current_item:
            current_item.setData(Qt.ItemDataRole.UserRole, (server_id, json_data))
            if "name" in json_data:
                current_item.setText(json_data["name"])

        self.mcp_form.populate(json_data)

        self.is_dirty = True
        self._update_save_button_state()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _on_theme_changed(self, theme_name: str):
        self.json_sync.update_theme()
