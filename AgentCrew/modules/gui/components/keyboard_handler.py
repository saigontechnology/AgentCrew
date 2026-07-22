from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QTextEdit
import sys


class KeyboardHandler:
    """Handles keyboard input for the chat window."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window
        # Don't setup shortcuts immediately - wait for setup_shortcuts() to be called

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Ctrl+Enter shortcut
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.chat_window)
        self.send_shortcut.activated.connect(self.chat_window.send_message)

        # Ctrl+Shift+C shortcut (copy last response)
        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self.chat_window)
        self.copy_shortcut.activated.connect(
            lambda: self.chat_window.llm_worker.process_request.emit("/copy")
        )

        # Ctrl+L shortcut (clear chat)
        self.clear_shortcut = QShortcut(QKeySequence("Ctrl+L"), self.chat_window)
        self.clear_shortcut.activated.connect(
            lambda: self.chat_window.command_handler.clear_chat(requested=False)
        )

        self.history_shortcut = QShortcut(QKeySequence("Ctrl+H"), self.chat_window)
        self.history_shortcut.activated.connect(self.chat_window.toggleSidebar)

        # Escape shortcut (stop message stream) - changed from Ctrl+C to avoid conflict
        self.stop_shortcut = QShortcut(QKeySequence("Escape"), self.chat_window)
        self.stop_shortcut.activated.connect(self.chat_window.stop_message_stream)

    def _handle_completer_accept(self, completer, event):
        current_index = completer.popup().currentIndex()
        if current_index.isValid():
            completion = completer.completionModel().data(
                current_index, Qt.ItemDataRole.DisplayRole
            )
            self.chat_window.input_components.insert_completion(completion)
            completer.popup().hide()
            event.accept()
            return

    def handle_key_press(self, event):
        """Handle key press events for the message input."""
        # Handle Tab key for completion
        real_control_key = (
            Qt.KeyboardModifier.ControlModifier
            if sys.platform != "darwin"
            else Qt.KeyboardModifier.MetaModifier
        )
        if event.key() == Qt.Key.Key_Tab:
            # Select the current completion
            if self.chat_window.file_completer.popup().isVisible():
                return self._handle_completer_accept(
                    self.chat_window.file_completer, event
                )
            elif self.chat_window.command_completer.popup().isVisible():
                return self._handle_completer_accept(
                    self.chat_window.command_completer, event
                )
            QTextEdit.keyPressEvent(self.chat_window.message_input, event)

        # Handle Enter key for completion
        elif event.key() == Qt.Key.Key_Return:
            # Select the current completion
            if self.chat_window.file_completer.popup().isVisible():
                return self._handle_completer_accept(
                    self.chat_window.file_completer, event
                )
            elif self.chat_window.command_completer.popup().isVisible():
                return self._handle_completer_accept(
                    self.chat_window.command_completer, event
                )
            QTextEdit.keyPressEvent(self.chat_window.message_input, event)

        # Ctrl+Enter to send
        elif (
            event.key() == Qt.Key.Key_Return
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            self.chat_window.send_message()
            event.accept()
            return

        # Up arrow to navigate history
        elif (
            event.key() == Qt.Key.Key_Up
            and event.modifiers() == real_control_key
            and not self.chat_window.file_completer.popup().isVisible()
        ):
            self.history_navigate(-1)
            event.accept()
            return

        # Down arrow to navigate history
        elif (
            event.key() == Qt.Key.Key_Down
            and event.modifiers() == real_control_key
            and not self.chat_window.file_completer.popup().isVisible()
        ):
            self.history_navigate(1)
            event.accept()
            return

        # Default behavior for other keys
        else:
            QTextEdit.keyPressEvent(self.chat_window.message_input, event)

    def history_navigate(self, direction):
        """Navigate through input history."""
        if not self.chat_window.message_handler.history_manager.history:
            return

        new_position = self.chat_window.history_position + direction

        if (
            0
            <= new_position
            < len(self.chat_window.message_handler.history_manager.history)
        ):
            self.chat_window.history_position = new_position
            history_entry = self.chat_window.message_handler.history_manager.history[
                self.chat_window.history_position
            ]
            self.chat_window.message_input.setText(history_entry)
        elif new_position < 0:
            self.chat_window.history_position = -1
            self.chat_window.message_input.clear()
        elif new_position >= len(
            self.chat_window.message_handler.history_manager.history
        ):
            self.chat_window.history_position = len(
                self.chat_window.message_handler.history_manager.history
            )
            self.chat_window.message_input.clear()
