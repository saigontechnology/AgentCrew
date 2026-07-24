from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider


class MCPListPanel(QWidget):
    """Left panel containing the MCP server list and Add/Remove buttons."""

    server_selected = Signal(str, dict)
    add_requested = Signal()
    remove_requested = Signal()
    selection_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style_provider = StyleProvider()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("MCP Servers:"))

        self.mcps_list = QListWidget()
        self.mcps_list.currentItemChanged.connect(self._on_current_item_changed)
        layout.addWidget(self.mcps_list)

        buttons_layout = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.setStyleSheet(self._style_provider.get_button_style("primary"))
        self.add_btn.clicked.connect(self.add_requested.emit)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setStyleSheet(self._style_provider.get_button_style("red"))
        self.remove_btn.clicked.connect(self.remove_requested.emit)
        self.remove_btn.setEnabled(False)

        buttons_layout.addWidget(self.add_btn)
        buttons_layout.addWidget(self.remove_btn)
        layout.addLayout(buttons_layout)

    def _on_current_item_changed(self, current, _previous):
        if current is None:
            self.remove_btn.setEnabled(False)
            self.selection_cleared.emit()
            return

        self.remove_btn.setEnabled(True)
        server_id, server_config = current.data(Qt.ItemDataRole.UserRole)
        self.server_selected.emit(server_id, server_config)

    def load_mcps(self, mcps_config: dict):
        """Load MCP servers from config dict into the list."""
        self.mcps_list.clear()
        for server_id, server_config in mcps_config.items():
            config_dict = (
                server_config.to_dict()
                if hasattr(server_config, "to_dict")
                else server_config
            )
            self.add_server(server_id, config_dict)
        self.mcps_list.setCurrentRow(0)

    def add_server(self, server_id: str, server_config: dict):
        """Add a single server entry to the list."""
        item = QListWidgetItem(server_config.get("name", server_id))
        item.setData(Qt.ItemDataRole.UserRole, (server_id, server_config))
        self.mcps_list.addItem(item)
        return item

    def remove_selected_server(self):
        """Remove the currently selected server from the list and return its data."""
        current_item = self.mcps_list.currentItem()
        if not current_item:
            return None
        row = self.mcps_list.row(current_item)
        self.mcps_list.takeItem(row)
        return current_item.data(Qt.ItemDataRole.UserRole)

    def current_server(self):
        """Return (server_id, server_config) for the current selection, or None."""
        current_item = self.mcps_list.currentItem()
        if current_item is None:
            return None
        return current_item.data(Qt.ItemDataRole.UserRole)

    def set_enabled(self, enabled: bool):
        self.mcps_list.setEnabled(enabled)
        self.add_btn.setEnabled(enabled)
        # remove_btn enable state is managed by selection

    def update_item_text(self, text: str):
        """Update the text of the currently selected item."""
        current_item = self.mcps_list.currentItem()
        if current_item:
            current_item.setText(text)

    def update_item_data(self, server_id: str, server_config: dict):
        """Update the data of the currently selected item."""
        current_item = self.mcps_list.currentItem()
        if current_item:
            current_item.setData(Qt.ItemDataRole.UserRole, (server_id, server_config))
            current_item.setText(server_config.get("name", server_id))

    def select_and_focus(self, item: QListWidgetItem):
        """Select an item and set focus."""
        self.mcps_list.setCurrentItem(item)
