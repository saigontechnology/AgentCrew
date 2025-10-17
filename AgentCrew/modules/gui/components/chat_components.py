from PySide6.QtWidgets import (
    QSizePolicy,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QApplication,
    QLabel,
)
from PySide6.QtCore import Qt
from AgentCrew.modules.gui.widgets import (
    TokenUsageWidget,
    SystemMessageWidget,
    MessageBubble,
)


class ChatComponents:
    """Handles chat-specific UI components and message display."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window
        self._setup_chat_area()
        self._setup_status_components()

    def _setup_chat_area(self):
        """Set up the main chat area components."""
        # Create widget for chat messages
        self.chat_window.chat_container = QWidget()
        self.chat_window.chat_container.setObjectName("chatContainer")

        # Create a scroll area for messages
        self.chat_window.chat_scroll = QScrollArea()
        self.chat_window.chat_scroll.setWidgetResizable(True)
        self.chat_window.chat_scroll.setWidget(self.chat_window.chat_container)
        self.chat_window.chat_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.chat_window.chat_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.chat_window.chat_layout = QVBoxLayout(self.chat_window.chat_container)
        self.chat_window.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_window.chat_layout.setSpacing(15)
        self.chat_window.chat_layout.setContentsMargins(2, 2, 5, 5)
        self.chat_window.chat_container.setMinimumHeight(100)
        self.chat_window.chat_container.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.MinimumExpanding
        )

    def _setup_status_components(self):
        """Set up status and token usage components."""
        # Create token usage widget
        self.chat_window.token_usage = TokenUsageWidget()

        # Create the status indicator (showing current agent and model)
        self.chat_window.status_indicator = QLabel(
            f"Agent: {self.chat_window.message_handler.agent.name} | Model: {self.chat_window.message_handler.agent.get_model()}"
        )
        self.chat_window.status_indicator.setStyleSheet(
            self.chat_window.style_provider.get_status_indicator_style()
        )

        # Add version label
        import AgentCrew

        version_text = f"AgentCrew v{getattr(AgentCrew, '__version__', 'Unknown')}"
        self.chat_window.version_label = QLabel(version_text)
        self.chat_window.version_label.setStyleSheet(
            self.chat_window.style_provider.get_version_label_style()
        )

    def add_system_message(self, text):
        """Add a system message to the chat."""
        system_widget = SystemMessageWidget(text)
        self.chat_window.chat_layout.addWidget(system_widget)

        # Scroll to show the new message
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()

    def append_file(self, file_path, is_user=False, is_base64=False):
        """Add a file display to the chat."""
        # Create container for message alignment
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create the message bubble
        if is_user:
            message_bubble = MessageBubble(None, True, "You")
        else:
            message_bubble = MessageBubble(
                None, False, self.chat_window.message_handler.agent.name
            )

        # Add the file display to the message bubble
        if is_base64:
            message_bubble.display_base64_img(file_path)
        else:
            message_bubble.display_file(file_path)

        # Connect remove button if it exists
        if message_bubble.remove_button:
            message_bubble.remove_button.clicked.connect(
                lambda: self._handle_file_remove(message_bubble)
            )

        if is_user:
            container_layout.addStretch(1)  # Push to left
            container_layout.addWidget(message_bubble, 1)
        else:
            container_layout.addWidget(message_bubble, 1)

        # Add the container to the chat layout
        self.chat_window.chat_layout.addWidget(container)

        # Process events and scroll to show the new message
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()
        return message_bubble

    def append_message(self, text, is_user=True, message_index=None, agent_name=None):
        """Add a message bubble to the chat container."""
        # Create container for message alignment
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create the message bubble with agent name for non-user messages
        agent_name = (
            agent_name
            if agent_name
            else self.chat_window.message_handler.agent.name
            if not is_user
            else "YOU"
        )

        message_bubble = MessageBubble(
            text, is_user, agent_name, message_index=message_index
        )

        # Add bubble to container with appropriate alignment
        if message_bubble.rollback_button:
            message_bubble.rollback_button.clicked.connect(
                lambda: self.chat_window.rollback_to_message(message_bubble)
            )
        if message_bubble.consolidated_button:
            message_bubble.consolidated_button.clicked.connect(
                lambda: self.chat_window.conslidate_messages(message_bubble)
            )
        if is_user:
            container_layout.addStretch(1)  # Push to left
            container_layout.addWidget(message_bubble, 2)
        else:
            # container_layout.addStretch(1)  # Push to right
            container_layout.addWidget(message_bubble)

        # Add the container to the chat layout
        self.chat_window.chat_layout.addWidget(container)

        # If this is an assistant message, store references for potential future chunks
        if not is_user:
            self.chat_window.current_response_bubble = message_bubble
            self.chat_window.current_response_container = container
        else:
            self.chat_window.current_user_bubble = message_bubble

        # Process events to ensure UI updates immediately
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()

        return message_bubble

    def append_thinking_message(self, text, agent_name):
        """Add a thinking message bubble to the chat container."""
        # Create container for message alignment
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create the message bubble with agent name and thinking flag
        message_bubble = MessageBubble(text, False, agent_name, is_thinking=True)

        # Add bubble to container with appropriate alignment (same as assistant messages)
        container_layout.addWidget(message_bubble)

        # Add the container to the chat layout
        self.chat_window.chat_layout.addWidget(container)

        # Process events to ensure UI updates immediately
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()

        return message_bubble

    def append_consolidated_message(self, text, metadata=None):
        """Add a consolidated message with special styling to the chat."""
        # Create container for message
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create the message bubble with special styling
        message_bubble = MessageBubble(
            text, False, "Conversation Summary", is_consolidated=True
        )

        # If we have metadata, add it to the message bubble
        if metadata:
            msg_count = metadata.get("messages_consolidated", 0)
            consolidated_tokens = metadata.get("consolidated_token_count", 0)
            origin_tokens = metadata.get("original_token_count", 0)
            message_bubble.add_metadata_header(
                f"📝 {msg_count} messages consolidated (~{origin_tokens - consolidated_tokens} tokens saved)"
            )

        # Center the consolidated message
        container_layout.addWidget(message_bubble)
        if message_bubble.unconsolidate_button:
            message_bubble.unconsolidate_button.clicked.connect(
                lambda: self.chat_window.unconsolidate_messages(message_bubble)
            )

        self.chat_window.chat_layout.addWidget(container)

        # Process events and scroll
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()

    def add_tool_widget(self, tool_widget):
        """Add a tool widget to the chat with proper centering and scrolling."""
        # Create container for alignment (centered)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 5, 0, 5)
        container_layout.addWidget(tool_widget)

        # Add to chat layout
        self.chat_window.chat_layout.addWidget(container)

        # Scroll to show the new widget
        if not self.chat_window.loading_conversation:
            QApplication.processEvents()

        return container

    def clear_chat_ui(self):
        """Clear only the chat message widgets from the UI."""
        # Stop any active streaming before clearing
        if self.chat_window.current_response_bubble:
            self.chat_window.current_response_bubble.stop_streaming()
        if self.chat_window.current_thinking_bubble:
            self.chat_window.current_thinking_bubble.stop_streaming()

        while self.chat_window.chat_layout.count():
            item = self.chat_window.chat_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        # Reset tracking variables related to response streaming
        self.chat_window.current_response_bubble = None
        self.chat_window.current_response_container = None
        self.chat_window.current_thinking_bubble = None
        self.chat_window.thinking_content = ""
        self.chat_window.expecting_response = False

    def remove_messages_after(self, message_bubble):
        """Remove all message widgets that appear after the given message bubble."""
        # Find the index of the container widget that holds the message bubble
        container_index = -1
        for i in range(self.chat_window.chat_layout.count()):
            item = self.chat_window.chat_layout.itemAt(i)
            if item and item.widget():
                # Check if this widget contains our message bubble
                if message_bubble in item.widget().findChildren(MessageBubble):
                    container_index = i
                    break

        if container_index == -1:
            return  # Message bubble not found

        # Remove the container with the message bubble and all widgets after it
        while self.chat_window.chat_layout.count() > container_index:
            item = self.chat_window.chat_layout.takeAt(container_index)
            if item.widget():
                item.widget().deleteLater()

        # Reset current response tracking
        self.chat_window.current_response_bubble = None
        self.chat_window.current_response_container = None
        self.chat_window.expecting_response = False

    def _handle_file_remove(self, message_bubble):
        """Handle removal of a file from the processing queue."""
        try:
            if message_bubble.file_path:
                # Use the chat window's worker to process the drop command
                self.chat_window.llm_worker.process_request.emit(
                    f"/drop {message_bubble.file_path}"
                )

                # Remove the message bubble from the UI immediately
                self._remove_file_bubble(message_bubble)

        except Exception as e:
            print(f"Error removing file: {e}")

    def _remove_file_bubble(self, message_bubble):
        """Remove a specific file bubble from the chat UI."""
        # Find the container that holds this message bubble
        for i in range(self.chat_window.chat_layout.count()):
            item = self.chat_window.chat_layout.itemAt(i)
            if item and item.widget():
                # Check if this widget contains our message bubble
                from AgentCrew.modules.gui.widgets.message_bubble import MessageBubble

                if message_bubble in item.widget().findChildren(MessageBubble):
                    # Remove the container
                    container = self.chat_window.chat_layout.takeAt(i)
                    if container.widget():
                        container.widget().deleteLater()
                    break

    def mark_file_processed(self, file_path):
        """Mark a file as processed in all relevant file bubbles."""
        # Find all file bubbles with matching file path and mark them as processed
        for i in range(self.chat_window.chat_layout.count()):
            item = self.chat_window.chat_layout.itemAt(i)
            if item and item.widget():
                # Find message bubbles in this container
                from AgentCrew.modules.gui.widgets.message_bubble import MessageBubble

                message_bubbles = item.widget().findChildren(MessageBubble)
                for bubble in message_bubbles:
                    if hasattr(bubble, "file_path") and bubble.file_path == file_path:
                        bubble.mark_file_processed()
