from __future__ import annotations
import os
from typing import Any, Optional

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QMessageBox,
    QMainWindow,
    QStatusBar,
    QMenu,
    QSplitter,
)
from PySide6.QtCore import (
    Qt,
    Slot,
    QThread,
    Signal,
)
from PySide6.QtGui import QIcon
from AgentCrew.modules.chat.message_handler import Observer
from loguru import logger

from .worker import LLMWorker
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .widgets import MessageBubble
    from AgentCrew.modules.chat.message_handler import MessageHandler
    from PySide6.QtWidgets import (
        QPushButton,
        QLabel,
        QCompleter,
        QScrollArea,
        QTextEdit,
    )
    from .widgets import TokenUsageWidget


class ChatWindow(QMainWindow, Observer):
    # Signal for thread-safe event handling
    event_received = Signal(str, object)
    # # Widgets
    status_indicator: QLabel
    chat_scroll: QScrollArea
    chat_layout: QVBoxLayout
    chat_container: QWidget
    version_label: QWidget  # Placeholder for all components
    send_button: QPushButton
    file_button: QPushButton
    voice_button: QPushButton
    message_input: QTextEdit
    file_completer: QCompleter
    command_completer: QCompleter
    # Custom Widgets
    token_usage: TokenUsageWidget

    current_response_bubble: Optional[MessageBubble]
    current_response_container: Optional[QWidget]
    current_user_bubble: Optional[MessageBubble]
    current_thinking_bubble: Optional[MessageBubble]

    def __init__(self, message_handler: MessageHandler):
        from .widgets import ConversationSidebar

        super().__init__()
        self.setWindowTitle("AgentCrew - Interactive Chat")
        self.setGeometry(100, 100, 1000, 700)  # Adjust size for sidebar

        # Set application icon
        icon_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "assets",
            "agentcrew_logo.png",
        )
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)

        # Initialize MessageHandler - kept in main thread
        self.message_handler = message_handler
        self.message_handler.attach(self)

        # Track if we're waiting for a response
        self.waiting_for_response = False
        self.loading_conversation = False  # Track conversation loading state

        # Initialize component handlers (these create UI widgets during __init__)
        self._setup_components()

        # Connect to the theme changed signal for hot-reloading
        self.style_provider.theme_changed.connect(self._handle_theme_changed)

        # Set application-wide style
        self.setStyleSheet(self.style_provider.get_main_style())

        # Create menu bar with styling
        self.menu_builder.create_menu_bar()

        # Status Bar (created after components so version_label exists)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.addPermanentWidget(self.version_label)

        # --- Assemble Chat Area Layout ---
        chat_area_widget = QWidget()  # Container for everything right of the sidebar
        chat_area_layout = QVBoxLayout(chat_area_widget)
        chat_area_layout.setContentsMargins(5, 5, 5, 0)
        chat_area_layout.addWidget(self.chat_scroll, 1)  # Give chat area more space
        chat_area_layout.addWidget(self.status_indicator)

        # Create horizontal layout for input and buttons
        input_row = self.input_components.get_input_layout()
        chat_area_layout.addLayout(input_row)
        chat_area_layout.addWidget(self.token_usage)

        # --- Create Sidebar ---
        self.sidebar = ConversationSidebar(self.message_handler, self)
        self.sidebar.conversation_selected.connect(
            self.conversation_components.load_conversation
        )
        self.sidebar.error_occurred.connect(self.display_error)
        self.sidebar.new_conversation_requested.connect(
            self.conversation_components.start_new_conversation
        )

        # --- Create Splitter and Set Central Widget ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(chat_area_widget)
        self.splitter.setStretchFactor(0, 0)  # Sidebar doesn't stretch
        self.splitter.setStretchFactor(1, 1)  # Chat area stretches
        self.splitter.setSizes([250, 750])  # Initial sizes

        # Connect double-click event to toggle sidebar
        self.splitter.handle(1).installEventFilter(self)

        # Update the splitter style to a darker color
        self.splitter.setStyleSheet(self.style_provider.get_splitter_style())

        self.setCentralWidget(self.splitter)

        # --- Connect signals and slots (rest of the setup) ---
        self.send_button.clicked.connect(self.send_message)
        self.file_button.clicked.connect(self.input_components.browse_file)
        self.voice_button.clicked.connect(
            self.input_components.handle_voice_button_click
        )

        # Setup context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Connect event handling signal
        self.event_received.connect(self.handle_event)

        # Setup keyboard handling after all UI components are ready
        self.keyboard_handler._setup_shortcuts()

        # Override key press event
        self.message_input.keyPressEvent = self.keyboard_handler.handle_key_press

        # Thread and worker for LLM interaction
        self.llm_thread = QThread()
        self.llm_worker = LLMWorker()  # No message_handler passed to worker

        # Connect worker signals to UI slots
        self.llm_worker.response_ready.connect(self.handle_response)
        self.llm_worker.error.connect(self.display_error)
        self.llm_worker.status_message.connect(self.display_status_message)
        self.llm_worker.request_exit.connect(self.handle_exit_request)
        self.llm_worker.request_clear.connect(self.command_handler.handle_clear_request)

        # Connect message handler to worker in the main thread
        self.llm_worker.connect_handler(self.message_handler)

        # Move worker to thread and start it
        self.llm_worker.moveToThread(self.llm_thread)
        self.llm_thread.start()

        # Initialize history position
        self.history_position = len(self.message_handler.history_manager.history)
        self.message_input.setFocus()

        # Track current response bubble for chunked responses
        self.current_response_bubble = None
        self.current_user_bubble = None
        self.current_response_container = None
        self.current_thinking_bubble = None
        self.current_file_bubble = None
        self.thinking_content = ""
        self.expecting_response = False
        self._delegated_user_input = None

        # Track session cost
        self.session_cost = 0.0

        # Individual message bubbles now handle their own streaming
        # No need for global chunk buffering timers

        # Add welcome message
        self.chat_components.add_system_message(
            "Welcome! Select a past conversation or start a new one."
        )
        self.chat_components.add_system_message(
            "Press Ctrl+Enter to send, Ctrl+Shift+C to copy, Ctrl+L to clear chat."
        )

    def _setup_components(self):
        """Initialize all component handlers."""

        from .components import (
            MenuBuilder,
            KeyboardHandler,
            MessageEventHandler,
            ToolEventHandler,
            ChatComponents,
            UIStateManager,
            InputComponents,
            ConversationComponents,
            CommandHandler,
        )

        from .themes import StyleProvider

        self.style_provider = StyleProvider()
        self.menu_builder = MenuBuilder(self)
        self.keyboard_handler = KeyboardHandler(self)
        self.message_event_handler = MessageEventHandler(self)
        self.tool_event_handler = ToolEventHandler(self)
        self.chat_components = ChatComponents(self)
        self.ui_state_manager = UIStateManager(self)
        self.input_components = InputComponents(self)
        self.conversation_components = ConversationComponents(self)
        self.command_handler = CommandHandler(self)

    def closeEvent(self, event):
        """Handle window close event to clean up threads properly"""
        # Terminate worker thread properly
        self.llm_thread.quit()
        self.llm_thread.wait(1000)  # Wait up to 1 second for thread to finish
        # If the thread didn't quit cleanly, terminate it
        if self.llm_thread.isRunning():
            self.llm_thread.terminate()
            self.llm_thread.wait()
        super().closeEvent(event)

    @Slot()
    def send_message(self):
        user_input = self.message_input.toPlainText().strip()  # Get text from QTextEdit
        if not user_input:  # Skip if empty
            return

        # Disable input controls while waiting for response
        self.ui_state_manager.set_input_controls_enabled(False)

        self.message_input.clear()

        self.ui_state_manager._set_send_button_state(True)

        # Process commands using command handler
        if self.command_handler.process_command(user_input):
            return  # Command was processed locally

        # Add user message to chat
        if user_input.strip() != "/retry":
            self._add_user_message_bubble(user_input)

        # Update status bar
        self.display_status_message("Processing your message...")
        self.llm_worker.process_request.emit(user_input)

    def _update_cost_info(self, input_tokens, output_tokens):
        """Update cost statistic."""
        # Calculate cost
        total_cost = self.message_handler.agent.calculate_usage_cost(
            input_tokens, output_tokens
        )

        # Update token usage
        self.update_token_usage(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_cost": total_cost,
            }
        )

    @Slot(str, int, int)
    def handle_response(self, response, input_tokens, output_tokens):
        """Handle the full response from the LLM worker"""
        self._update_cost_info(input_tokens, output_tokens)

        # # Reset response expectation
        # self.expecting_response = False
        #
        # # Re-enable input controls
        self.ui_state_manager.set_input_controls_enabled(True)
        QApplication.processEvents()  # Ensure UI updates immediately

    @Slot(str)
    def display_error(self, error):
        """Display an error message."""
        # Handle both string and dictionary error formats
        if isinstance(error, dict):
            # Extract error message from dictionary
            error_message = error.get("message", str(error))
        else:
            error_message = str(error)

        QMessageBox.critical(self, "Error", error_message)
        self.status_bar.showMessage(
            f"Error: {error_message}", 5000
        )  # Display error in status bar
        self.expecting_response = False

    @Slot(str)
    def display_status_message(self, message):
        self.status_bar.showMessage(message, 5000)

    @Slot(dict)
    def update_token_usage(self, usage_data):
        """Update token usage display."""
        input_tokens = usage_data.get("input_tokens", 0)
        output_tokens = usage_data.get("output_tokens", 0)
        total_cost = usage_data.get("total_cost", 0.0)

        # Update session cost
        self.session_cost += total_cost

        # Update the token usage widget
        self.token_usage.update_token_info(
            input_tokens, output_tokens, total_cost, self.session_cost
        )

    @Slot()
    def handle_exit_request(self):
        """Handle exit request from worker thread"""
        QApplication.quit()

    def stop_message_stream(self):
        """Stop the current message stream."""
        if self.message_handler.voice_service:
            self.message_handler.voice_service.clear_tts_queue()
            self.input_components.stop_voice_recording()
        if self.waiting_for_response:
            self.ui_state_manager.stop_button_stopping_state()
            if self.message_handler.stream_generator:
                try:
                    self.message_handler.stop_streaming = True
                except RuntimeError as e:
                    logger.warning(f"Error closing stream generator: {e}")
                except Exception as e:
                    logger.warning(f"Exception closing stream generator: {e}")
        self.ui_state_manager.set_input_controls_enabled(True)
        if self.current_response_bubble:
            self.current_response_bubble.stop_streaming()
        if self.current_thinking_bubble:
            self.current_thinking_bubble.stop_streaming()
            self.display_status_message("Stopping message stream...")

    def show_context_menu(self, position):
        """Show context menu with options."""
        context_menu = QMenu(self)

        # Add Catppuccin styling to context menu
        context_menu.setStyleSheet(self.style_provider.get_context_menu_style())

        # Add menu actions
        copy_action = context_menu.addAction("Copy Last Response")
        clear_action = context_menu.addAction("Clear Chat")

        # Connect actions to slots
        copy_action.triggered.connect(self.command_handler.copy_last_response)
        clear_action.triggered.connect(self.command_handler.clear_chat)

        # Show the menu at the cursor position
        context_menu.exec(self.mapToGlobal(position))

    def rollback_to_message(self, message_bubble):
        """Rollback the conversation to the selected message."""
        if message_bubble.message_index is None:
            self.display_status_message("Cannot rollback: no message index available")
            return

        current_text = message_bubble.raw_text

        # Find the turn number for this message
        turn_number = None

        for i, turn in enumerate(self.message_handler.conversation_turns):
            if turn.message_index == message_bubble.message_index:
                turn_number = i + 1  # Turn numbers are 1-indexed
                break

        if turn_number is None:
            self.display_status_message(
                "Cannot rollback: message not found in conversation history"
            )
            return

        # Execute the jump command
        self.llm_worker.process_request.emit(f"/jump {turn_number}")

        # Find and remove all widgets after this message in the UI
        self.chat_components.remove_messages_after(message_bubble)
        self.message_input.setPlainText(current_text)

    def conslidate_messages(self, message_bubble):
        """Consolidate message to the selected message."""
        if message_bubble.message_index is None:
            self.display_status_message(
                "Cannot conslidate messages: no message index available"
            )
            return

        preseved_messages = (
            len(self.message_handler.streamline_messages) - message_bubble.message_index
        )

        # Execute the consolidated command
        self.llm_worker.process_request.emit(f"/consolidate {preseved_messages}")

        self.ui_state_manager.set_input_controls_enabled(
            False
        )  # Disable input while processing
        self.ui_state_manager._set_send_button_state(
            True
        )  # Change button to stop state

    def unconsolidate_messages(self, message_bubble=None):
        """Unconsolidate the last consolidated message."""
        # Check if there are any consolidated messages
        has_consolidated = any(
            msg.get("role") == "consolidated"
            for msg in self.message_handler.streamline_messages
        )

        if not has_consolidated:
            self.display_status_message(
                "No consolidated messages found to unconsolidate."
            )
            return

        # Execute the unconsolidate command
        self.llm_worker.process_request.emit("/unconsolidate")

        # Update UI state
        self.ui_state_manager.set_input_controls_enabled(False)
        self.display_status_message("Unconsolidating messages...")

    def listen(self, event: str, data: Any = None):
        """Handle events from the message handler."""
        # Use a signal to ensure thread-safety
        self.event_received.emit(event, data)

    def eventFilter(self, obj, event):
        """Event filter to handle double-click on splitter handle."""
        if (
            obj is self.splitter.handle(1)
            and event.type() == event.Type.MouseButtonDblClick
        ):
            self.toggleSidebar()
            return True
        return super().eventFilter(obj, event)

    def toggleSidebar(self):
        # Get current sizes
        sizes = self.splitter.sizes()
        if sizes[0] > 0:
            # If sidebar is visible, hide it
            self.splitter.setSizes([0, sum(sizes)])
        else:
            # If sidebar is hidden, show it
            self.splitter.setSizes([250, max(sum(sizes) - 250, 0)])

    @Slot(str, object)
    def handle_event(self, event: str, data: Any):
        # Delegate to appropriate event handlers
        message_events = [
            "response_chunk",
            "user_message_created",
            "response_completed",
            "assistant_message_added",
            "thinking_started",
            "thinking_chunk",
            "thinking_completed",
            "user_context_request",
        ]

        tool_events = [
            "tool_use",
            "tool_result",
            "tool_error",
            "tool_confirmation_required",
            "tool_denied",
            "agent_changed_by_transfer",
        ]
        command_events = [
            "clear_requested",
            "exit_requested",
            "copy_requested",
            "debug_requested",
            "agent_changed",
            "agent_command_result",
            "model_changed",
            "think_budget_set",
            "jump_performed",
        ]

        if event in message_events:
            # make sure file bubble is cleared if we are processing a new message
            if self.current_file_bubble:
                self.current_file_bubble = None
            self.message_event_handler.handle_event(event, data)
        elif event in tool_events:
            self.tool_event_handler.handle_event(event, data)
        elif event in command_events:
            self.command_handler.handle_event(event, data)
        elif event == "error":
            # If an error occurs during LLM processing, ensure loading flag is false
            self.loading_conversation = False
            self.ui_state_manager.set_input_controls_enabled(True)
            if self.current_file_bubble:
                self.chat_components.remove_messages_after(self.current_file_bubble)
                self.current_file_bubble = None
            self.display_error(data)
        elif event == "consolidation_completed":
            self.conversation_components.display_consolidation(data)
            self.ui_state_manager.set_input_controls_enabled(True)
        elif event == "unconsolidation_completed":
            self.conversation_components.display_unconsolidation(data)
            self.ui_state_manager.set_input_controls_enabled(True)
        elif event == "file_processing":
            file_path = data["file_path"]
            self.current_file_bubble = self.chat_components.append_file(
                file_path, is_user=True
            )
            if not self.loading_conversation:
                self.ui_state_manager.set_input_controls_enabled(True)
        elif event == "file_processed":
            # Mark the file as processed in the chat components
            file_path = data.get("file_path")
            if file_path:
                self.chat_components.mark_file_processed(file_path)
            self.current_file_bubble = None
        elif event == "image_generated":
            self.chat_components.append_file(data, False, True)
        # Command-related events are now handled by command_handler above
        elif event == "conversation_saved":
            self.display_status_message(f"Conversation saved: {data.get('id', 'N/A')}")
            self.sidebar.update_conversation_list()
            if not self.loading_conversation:
                self.ui_state_manager.set_input_controls_enabled(True)
        elif event == "conversations_changed":
            self.display_status_message("Conversation list updated.")
            self.sidebar.update_conversation_list()
        elif event == "conversation_loaded":
            self.display_status_message(f"Conversation loaded: {data.get('id', 'N/A')}")
        elif event == "streaming_stopped":
            self.chat_components.add_system_message(
                "Message streaming stopped by user."
            )
            self.ui_state_manager.set_input_controls_enabled(True)
        elif event == "update_token_usage":
            self._update_cost_info(data["input_tokens"], data["output_tokens"])
        elif event == "mcp_prompt":
            self.message_input.setPlainText(data.get("content", ""))
        elif event == "transfer_enforce_toggled":
            self.chat_components.add_system_message(
                f"🔄 Transfer enforcement is now {data}."
            )
        elif event == "voice_recording_started":
            # Update UI to show recording state
            self.ui_state_manager.set_input_controls_enabled(False)
            self.ui_state_manager.is_voice_activated = True
            self.message_input.setPlaceholderText(
                "🎤 Recording... Click voice button or press Enter to stop"
            )
            # Update voice button to show recording state
            self.input_components.update_voice_button_state(True)
        elif event == "voice_activate":
            if data:
                self._add_user_message_bubble(data)
            self.llm_worker.process_request.emit(data)
            self.ui_state_manager._set_send_button_state(True)
        # elif event == "voice_recording_completed":
        #     self.ui_state_manager.is_voice_activated = False
        #     # Restore normal UI state
        #     self.message_input.setPlaceholderText("Type a message...")
        #     # Update voice button to show normal state
        #     self.input_components.update_voice_button_state(False)
        #     self.ui_state_manager.set_input_controls_enabled(
        #         self.ui_state_manager._last_enabled_state
        #     )

    def _add_user_message_bubble(self, data):
        self.chat_components.append_message(
            data, True, self.message_handler.current_user_input_idx
        )  # True = user message

        # Set flag to expect a response (for chunking)
        self.expecting_response = True
        self.current_response_bubble = None
        self.current_response_container = None

    def _handle_theme_changed(self, theme_name):
        """
        Handle theme change events by updating the UI components with the new theme.

        Args:
            theme_name (str): The name of the new theme
        """
        # Update main window style
        self.setStyleSheet(self.style_provider.get_main_style())

        # Update splitter style
        self.splitter.setStyleSheet(self.style_provider.get_splitter_style())

        # Update all menu styles
        self.menu_builder.update_menu_style()

        # Refresh context menu style (will be applied next time it's shown)

        # Update token usage widget style
        self.token_usage.update_style(self.style_provider)

        # Update sidebar style
        self.sidebar.update_style(self.style_provider)

        self.message_input.setStyleSheet(self.style_provider.get_input_style())

        self.send_button.setStyleSheet(self.style_provider.get_button_style("primary"))

        # Create File button
        self.file_button.setStyleSheet(
            self.style_provider.get_button_style("secondary")
        )

        if (
            hasattr(self.input_components, "is_voice_recording")
            and self.input_components.is_voice_recording
        ):
            self.voice_button.setStyleSheet(self.style_provider.get_button_style("red"))
        else:
            self.voice_button.setStyleSheet(
                self.style_provider.get_button_style("secondary")
            )

        self.status_indicator.setStyleSheet(
            self.style_provider.get_status_indicator_style()
        )
        self.version_label.setStyleSheet(self.style_provider.get_version_label_style())

        # Display status message about theme change
        self.display_status_message(f"Theme changed to: {theme_name}")
