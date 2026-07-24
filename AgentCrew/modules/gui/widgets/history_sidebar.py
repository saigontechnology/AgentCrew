from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qtpy.QtWidgets import QApplication

from AgentCrew.modules.chat.fork_utils import format_fork_title
from AgentCrew.modules.gui.themes import StyleProvider

if TYPE_CHECKING:
    from AgentCrew.modules.chat.message_handler import MessageHandler


class ConversationSidebar(QWidget):
    """Sidebar widget showing conversation history"""

    conversation_selected = Signal(str)  # Emits conversation_id
    error_occurred = Signal(str)
    new_conversation_requested = Signal()  # Add this new signal

    def __init__(self, message_handler: MessageHandler, parent=None):
        super().__init__(parent)
        self.message_handler = message_handler
        # Store conversations locally to filter
        self._conversations = []
        self.setup_ui()
        # Initial load
        self.update_conversation_list()

    def setup_ui(self):
        self.setFixedWidth(250)
        style_provider = StyleProvider()
        self.setStyleSheet(style_provider.get_sidebar_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search conversations...")
        self.search_box.textChanged.connect(self.filter_conversations)
        self.search_box.setStyleSheet(style_provider.get_search_box_style())
        layout.addWidget(self.search_box)

        # Conversation list
        self.conversation_list = QListWidget()
        self.conversation_list.itemClicked.connect(self.on_conversation_selected)
        self.conversation_list.setAlternatingRowColors(
            False
        )  # Disable default alternating colors
        self.conversation_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.conversation_list.customContextMenuRequested.connect(
            self.show_conversation_context_menu
        )
        self.conversation_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.conversation_list.setStyleSheet(
            style_provider.get_conversation_list_style()
        )
        layout.addWidget(self.conversation_list)

        # Button row with Refresh and New buttons
        button_layout = QHBoxLayout()

        # Refresh button
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.update_conversation_list)
        button_layout.addWidget(self.refresh_btn)

        # New conversation button
        self.new_btn = QPushButton("New")
        self.new_btn.clicked.connect(self.request_new_conversation)
        button_layout.addWidget(self.new_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Apply button styles
        self.refresh_btn.setStyleSheet(style_provider.get_button_style("secondary"))
        self.new_btn.setStyleSheet(style_provider.get_button_style("primary"))

    def update_conversation_list(self):
        """Fetches and displays the list of conversations."""
        try:
            # Use the enhanced list with fork information if available
            self._conversations = self.message_handler.list_conversations_with_forks()
            self.filter_conversations()  # Apply current filter
        except Exception as e:
            self.error_occurred.emit(f"Failed to load conversations: {e!s}")
            self._conversations = []  # Clear local cache on error
            self.conversation_list.clear()  # Clear UI list

    def filter_conversations(self):
        """Filters the displayed list based on search text."""
        search_term = self.search_box.text().lower()
        self.conversation_list.clear()
        if not self._conversations:
            # Handle case where conversations haven't loaded or failed to load
            if not self.search_box.text():  # Avoid showing error if user is just typing
                # Optionally display a message in the list
                # item = QListWidgetItem("No conversations found.")
                # item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable) # Make it unselectable
                # self.conversation_list.addItem(item)
                pass
            return

        # Sort conversations by timestamp descending (most recent first)
        # Assuming timestamp is sortable (e.g., ISO format string or datetime object)

        for metadata in self._conversations:
            title = metadata.get("title") or metadata.get(
                "preview", "Untitled Conversation"
            )
            timestamp = metadata.get("timestamp", "N/A")
            conv_id = metadata.get("id", "N/A")

            is_fork = metadata.get("is_fork", False)
            fork_children = metadata.get("fork_children", [])
            has_children = len(fork_children) > 0

            display_title = format_fork_title(title, metadata)

            if search_term in title.lower():
                item_text = (
                    f"{display_title}\n{timestamp}"  # Display title and timestamp
                )
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, conv_id)  # Store ID in UserRole
                item.setToolTip(f"ID: {conv_id}\nLast updated: {timestamp}")

                # Set visual style based on fork status
                if is_fork:
                    # Fork conversations in cyan/teal color
                    item.setForeground(Qt.GlobalColor.darkCyan)
                elif has_children:
                    # Parent with children in dark yellow
                    item.setForeground(Qt.GlobalColor.darkYellow)

                self.conversation_list.addItem(item)

    @Slot(QListWidgetItem)
    def on_conversation_selected(self, item):
        """Emits the ID of the selected conversation."""
        if item and item.data(Qt.ItemDataRole.UserRole):
            self.conversation_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def request_new_conversation(self):
        """Emit signal to request a new conversation."""
        self.new_conversation_requested.emit()

    def show_conversation_context_menu(self, position):
        """Shows a context menu for selected conversation item(s)."""
        selected_items = self.conversation_list.selectedItems()

        if not selected_items:
            # If no items are selected, try to get the item under the cursor
            # This handles the case where a user right-clicks without prior selection
            item_at_position = self.conversation_list.itemAt(position)
            if item_at_position:
                selected_items = [item_at_position]
            else:
                return  # No item under cursor and no selection

        conversation_ids = []
        for item in selected_items:
            conv_id = item.data(Qt.ItemDataRole.UserRole)
            if conv_id:
                conversation_ids.append(conv_id)

        if not conversation_ids:
            return

        menu = QMenu(self)

        style_provider = StyleProvider()
        menu.setStyleSheet(style_provider.get_context_menu_style())

        num_selected = len(conversation_ids)
        if num_selected == 1:
            action_text = "Delete Conversation"
        else:
            action_text = f"Delete {num_selected} Conversations"

        delete_action = QAction(action_text, self)
        delete_action.triggered.connect(
            lambda: self.handle_delete_conversation_request(conversation_ids)
        )
        menu.addAction(delete_action)

        menu.exec_(self.conversation_list.mapToGlobal(position))

    def update_style(self, style_provider=None):
        """Update the widget's style based on the current theme."""
        if not style_provider:
            style_provider = StyleProvider()

        # Update sidebar style
        self.setStyleSheet(style_provider.get_sidebar_style())

        # Update search box style
        self.search_box.setStyleSheet(style_provider.get_search_box_style())

        # Update conversation list style
        self.conversation_list.setStyleSheet(
            style_provider.get_conversation_list_style()
        )

        # Update button styles
        self.refresh_btn.setStyleSheet(style_provider.get_button_style("secondary"))
        self.new_btn.setStyleSheet(style_provider.get_button_style("primary"))

    def handle_delete_conversation_request(self, conversation_ids: list[str]):
        """Handles the request to delete one or more conversations after confirmation."""
        if not conversation_ids:
            return

        num_to_delete = len(conversation_ids)
        if num_to_delete == 1:
            conv_id_short = conversation_ids[0][:8]
            confirm_message = (
                f"Are you sure you want to delete this conversation ({conv_id_short}...)?\n"
                "This action cannot be undone."
            )
        else:
            confirm_message = (
                f"Are you sure you want to delete these {num_to_delete} conversations?\n"
                "This action cannot be undone."
            )

        reply = QMessageBox.warning(
            self,
            "Confirm Delete",
            confirm_message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            any_failed = False
            for conv_id in conversation_ids:
                if not self.message_handler.delete_conversation_by_id(conv_id):
                    any_failed = True
                QApplication.processEvents()

            if any_failed:
                # MessageHandler already notifies specific errors.
                # This is a general notification if any of them failed.
                self.error_occurred.emit(
                    "One or more conversations could not be deleted. "
                    "Check system messages for details."
                )


class ConversationLoader(QThread):
    """Thread for async conversation loading"""

    loaded = Signal(list, str)  # Emit messages and conversation_id
    error = Signal(str)

    def __init__(self, message_handler, conv_id):
        super().__init__()
        self.message_handler = message_handler
        self.conv_id = conv_id

    def run(self):
        try:
            # Assuming message_handler has load_conversation method
            messages = self.message_handler.load_conversation(self.conv_id)
            if messages is not None:
                self.loaded.emit(messages, self.conv_id)
            else:
                # Handle case where conversation load returns None (e.g., not found)
                self.error.emit(f"Conversation '{self.conv_id}' not found or empty.")
        except Exception as e:
            # Log the full exception for debugging
            import traceback

            print(
                f"Error loading conversation {self.conv_id}: {traceback.format_exc()}"
            )
            self.error.emit(f"Failed to load conversation '{self.conv_id}': {e!s}")
