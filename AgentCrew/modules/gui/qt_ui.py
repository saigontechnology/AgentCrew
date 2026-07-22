from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any

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
from AgentCrew.modules.events import AppEvents, EventBus
from loguru import logger

from AgentCrew.modules.gui.widgets.system_message import SystemMessageWidget
from AgentCrew.modules.llm.token_usage import TokenUsage

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


@dataclass
class BubbleState:
    current_response_bubble: MessageBubble | None = None
    current_response_container: QWidget | None = None
    current_user_bubble: MessageBubble | None = None
    current_thinking_bubble: MessageBubble | None = None
    current_planning_widget: SystemMessageWidget | None = None
    current_file_bubble: MessageBubble | None = None


@dataclass
class StreamState:
    current_planning_content: str = ""
    processing_plan: bool = False
    thinking_content: str = ""
    expecting_response: bool = False
    delegated_user_input: str | None = None


class ChatWindow(QMainWindow):
    ui_call_requested = Signal(object, object, object)
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

    bubble_state: BubbleState
    stream_state: StreamState

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
        self.bus = EventBus.get_instance()
        self._subscriptions: list[Any] = []

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
        chat_area_widget = QWidget()
        chat_area_widget.setStyleSheet(
            self.style_provider.get_chat_container_bg_style()
        )
        chat_area_layout = QVBoxLayout(chat_area_widget)
        chat_area_layout.setContentsMargins(12, 12, 12, 10)
        chat_area_layout.setSpacing(10)
        chat_area_layout.addWidget(self.chat_scroll, 1)
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

        self.ui_call_requested.connect(self._run_ui_call)

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

        self.bubble_state = BubbleState()
        self.stream_state = StreamState()

        # Track session cost
        self.session_cost = 0.0

        # Individual message bubbles now handle their own streaming
        # No need for global chunk buffering timers

        # Add welcome message
        self.chat_components.add_system_message(
            "Welcome to AgentCrew — select a conversation or start a new one."
        )
        self.chat_components.add_system_message(
            "Tip: Ctrl+Enter to send, Ctrl+Shift+C to copy, Ctrl+L to clear chat."
        )
        self._register_subscriptions()

    def reset_bubble_state(self, **overrides):
        self.bubble_state = BubbleState(**overrides)

    def reset_stream_state(self, **overrides):
        self.stream_state = StreamState(**overrides)

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
        for subscription in self._subscriptions:
            self.bus.off(subscription)
        self._subscriptions.clear()
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

    def _update_cost_info(self, token_usage: TokenUsage):
        """Update cost statistic."""
        # Calculate cost
        total_cost = self.message_handler.agent.calculate_usage_cost(
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.cached_tokens,
        )

        # Update token usage
        self.update_token_usage(
            {
                "input_tokens": token_usage.input_tokens,
                "output_tokens": token_usage.output_tokens,
                "total_input_tokens": token_usage.total_input_tokens,
                "cached_tokens": token_usage.cached_tokens,
                "cache_creation_tokens": token_usage.cache_creation_tokens,
                "total_cost": total_cost,
            }
        )

    @Slot(str, object)
    def handle_response(self, response, token_usage):
        """Handle the full response from the LLM worker"""
        self._update_cost_info(token_usage)

        voice_service = self.message_handler.voice_service
        if voice_service and hasattr(voice_service, "audio_handler"):
            voice_service.audio_handler.is_processing = False

        self.ui_state_manager.set_input_controls_enabled(True)
        QApplication.processEvents()

    def _set_voice_processing_state(self, is_processing: bool):
        voice_service = self.message_handler.voice_service
        if voice_service and hasattr(voice_service, "audio_handler"):
            voice_service.audio_handler.is_processing = is_processing
            if is_processing:
                voice_service.audio_handler.clear_buffered_audio()

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
        self.stream_state.expecting_response = False

    @Slot(str)
    def display_status_message(self, message):
        self.status_bar.showMessage(message, 5000)

    @Slot(dict)
    def update_token_usage(self, usage_data):
        """Update token usage display."""
        input_tokens = usage_data.get("input_tokens", 0)
        output_tokens = usage_data.get("output_tokens", 0)
        total_input_tokens = usage_data.get("total_input_tokens", 0)
        cached_tokens = usage_data.get("cached_tokens", 0)
        cache_creation_tokens = usage_data.get("cache_creation_tokens", 0)
        total_cost = usage_data.get("total_cost", 0.0)

        # Update session cost
        self.session_cost += total_cost

        # Update the token usage widget
        self.token_usage.update_token_info(
            input_tokens,
            output_tokens,
            total_input_tokens,
            total_cost,
            self.session_cost,
            cached_tokens,
            cache_creation_tokens,
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
            self.display_status_message("Stopping message stream...")
            try:
                self.llm_worker.cancel_current_request()
            except RuntimeError as e:
                logger.warning(f"Error requesting stream stop: {e}")
            except Exception as e:
                logger.warning(f"Exception requesting stream stop: {e}")

    def show_context_menu(self, position):
        """Show context menu with options."""
        context_menu = QMenu(self)

        # Add Catppuccin styling to context menu
        context_menu.setStyleSheet(self.style_provider.get_context_menu_style())

        # Add menu actions
        clear_action = context_menu.addAction("Clear Chat")

        # Connect actions to slots
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

    def _register_subscriptions(self):
        self._subscriptions = [
            self.bus.on(AppEvents.THINKING_STARTED, self._on_thinking_started),
            self.bus.on(AppEvents.THINKING_CHUNK, self._on_thinking_chunk),
            self.bus.on(AppEvents.THINKING_COMPLETED, self._on_thinking_completed),
            self.bus.on(AppEvents.RESPONSE_CHUNK, self._on_response_chunk),
            self.bus.on(AppEvents.RESPONSE_COMPLETED, self._on_response_completed),
            self.bus.on(
                AppEvents.ASSISTANT_MESSAGE_ADDED, self._on_assistant_message_added
            ),
            self.bus.on(
                AppEvents.STREAM_CANCEL_REQUESTED, self._on_stream_cancel_requested
            ),
            self.bus.on(AppEvents.STREAM_CANCELED, self._on_stream_canceled),
            self.bus.on(AppEvents.STREAM_OPEN_TIMEOUT, self._on_stream_open_timeout),
            self.bus.on(AppEvents.STREAMING_STOPPED, self._on_streaming_stopped),
            self.bus.on(AppEvents.TOOL_USE, self._on_tool_use),
            self.bus.on(AppEvents.TOOL_RESULT, self._on_tool_result),
            self.bus.on(AppEvents.TOOL_ERROR, self._on_tool_error),
            self.bus.on(AppEvents.TOOL_CONFIRMATION_REQ, self._on_tool_confirmation),
            self.bus.on(AppEvents.TOOL_DENIED, self._on_tool_denied),
            self.bus.on(AppEvents.CLEAR_REQUESTED, self._on_clear_requested),
            self.bus.on(AppEvents.FILE_PROCESSING, self._on_file_processing),
            self.bus.on(AppEvents.FILE_PROCESSED, self._on_file_processed),
            self.bus.on(AppEvents.FILE_DROPPED, self._on_file_dropped),
            self.bus.on(AppEvents.CONVERSATION_LOADED, self._on_conversation_loaded),
            self.bus.on(AppEvents.CONVERSATION_SAVED, self._on_conversation_saved),
            self.bus.on(
                AppEvents.CONVERSATIONS_CHANGED, self._on_conversations_changed
            ),
            self.bus.on(
                AppEvents.CONSOLIDATION_COMPLETED, self._on_consolidation_completed
            ),
            self.bus.on(
                AppEvents.UNCONSOLIDATION_COMPLETED, self._on_unconsolidation_completed
            ),
            self.bus.on(AppEvents.AGENT_CHANGED, self._on_agent_changed),
            self.bus.on(
                AppEvents.AGENT_CHANGED_BY_TRANSFER, self._on_agent_changed_by_transfer
            ),
            self.bus.on(AppEvents.AGENTS_LISTED, self._on_agents_listed),
            self.bus.on(AppEvents.MODEL_CHANGED, self._on_model_changed),
            self.bus.on(AppEvents.MODELS_LISTED, self._on_models_listed),
            self.bus.on(AppEvents.EVOLUTION_STARTED, self._on_evolution_started),
            self.bus.on(AppEvents.EVOLUTION_FINISHED, self._on_evolution_finished),
            self.bus.on(AppEvents.EVOLUTION_SUMMARY, self._on_evolution_summary),
            self.bus.on(AppEvents.EVOLUTION_APPLIED, self._on_evolution_applied),
            self.bus.on(AppEvents.EVOLUTION_DECLINED, self._on_evolution_declined),
            self.bus.on(
                AppEvents.EVOLUTION_QUESTIONS_REQUESTED,
                self._on_evolution_questions_requested,
            ),
            self.bus.on(AppEvents.ERROR, self._on_error),
            self.bus.on(AppEvents.SYSTEM_MESSAGE, self._on_system_message),
            self.bus.on(AppEvents.DEBUG_REQUESTED, self._on_debug_requested),
            self.bus.on(AppEvents.THINK_BUDGET_SET, self._on_think_budget_set),
            self.bus.on(AppEvents.UPDATE_TOKEN_USAGE, self._on_update_token_usage),
            self.bus.on(AppEvents.JUMP_PERFORMED, self._on_jump_performed),
            self.bus.on(AppEvents.FORK_AND_SWITCH, self._on_fork_and_switch),
            self.bus.on(AppEvents.LEARN_CONFIRMATION, self._on_learn_confirmation),
            self.bus.on(AppEvents.MCP_PROMPT, self._on_mcp_prompt),
            self.bus.on(AppEvents.COPY_REQUESTED, self._on_copy_requested),
            self.bus.on(
                AppEvents.VOICE_RECORDING_STARTED, self._on_voice_recording_started
            ),
            self.bus.on(AppEvents.VOICE_ACTIVATE, self._on_voice_activate),
            self.bus.on(
                AppEvents.VOICE_RECORDING_STOPPING, self._on_voice_recording_stopping
            ),
            self.bus.on(
                AppEvents.VOICE_RECORDING_COMPLETED, self._on_voice_recording_completed
            ),
            self.bus.on(
                AppEvents.TRANSFER_ENFORCE_TOGGLE, self._on_transfer_enforce_toggled
            ),
            self.bus.on(AppEvents.AGENT_COMMAND_RESULT, self._on_agent_command_result),
            self.bus.on(AppEvents.USER_MESSAGE_CREATED, self._on_user_message_created),
        ]

    def _queue_ui(self, handler, *args, **kwargs):
        self.ui_call_requested.emit(handler, args, kwargs)

    @Slot(object, object, object)
    def _run_ui_call(self, handler, args, kwargs):
        handler(*args, **kwargs)

    def _on_thinking_started(self, agent_name: str):
        self._queue_ui(self.message_event_handler.handle_thinking_started, agent_name)

    def _on_thinking_chunk(self, chunk: str):
        if chunk.strip():
            self._queue_ui(self.message_event_handler.handle_thinking_chunk, chunk)

    def _on_thinking_completed(self, content: str):
        self._queue_ui(self.message_event_handler.handle_thinking_completed)

    def _on_response_chunk(self, chunk: str, full_response: str):
        self._queue_ui(
            self.message_event_handler.handle_response_chunk, (chunk, full_response)
        )

    def _on_response_completed(self, response: str):
        self._queue_ui(self.message_event_handler.handle_response_completed, response)

    def _on_assistant_message_added(self, response: str):
        self._queue_ui(self.message_event_handler.handle_response_completed, response)

    def _on_stream_cancel_requested(self):
        self._queue_ui(self.message_event_handler.handle_stream_cancel_requested)

    def _on_stream_canceled(self, session_id: int, assistant_response: str):
        self._queue_ui(self.message_event_handler.handle_stream_canceled, None)

    def _on_stream_open_timeout(self, session_id: int, timeout: float):
        self._queue_ui(self.message_event_handler.handle_stream_open_timeout, None)

    def _on_tool_use(self, **data):
        self._queue_ui(self.tool_event_handler.handle_tool_use, data)

    def _on_tool_result(self, **data):
        self._queue_ui(self.tool_event_handler.handle_tool_result, data)

    def _on_tool_error(self, **data):
        self._queue_ui(self.tool_event_handler.handle_tool_error, data)

    def _on_tool_confirmation(self, tool_use: dict, confirmation_id: int):
        self._queue_ui(
            self.tool_event_handler.handle_tool_confirmation_required,
            {**tool_use, "confirmation_id": confirmation_id},
        )

    def _on_tool_denied(self, **data):
        self._queue_ui(self.tool_event_handler.handle_tool_denied, data)

    def _on_user_message_created(self, **data):
        self._queue_ui(self.message_event_handler.handle_user_message_created, data)

    def _on_clear_requested(self):
        self._queue_ui(self.command_handler.handle_clear_event)

    def _on_file_processing(self, file_path: str):
        self._queue_ui(self._handle_file_processing, file_path)

    def _on_file_processed(self, **data):
        self._queue_ui(self._handle_file_processed, data)

    def _on_file_dropped(self, **data):
        return None

    def _on_conversation_loaded(self, **data):
        self._queue_ui(self._handle_conversation_loaded, data)

    def _on_conversation_saved(self, **data):
        self._queue_ui(self._handle_conversation_saved, data)

    def _on_conversations_changed(self):
        self._queue_ui(self._handle_conversations_changed)

    def _on_consolidation_completed(self, result: dict):
        self._queue_ui(self._handle_consolidation_completed, result)

    def _on_unconsolidation_completed(self, result: dict):
        self._queue_ui(self._handle_unconsolidation_completed, result)

    def _on_agent_changed(self, agent_name: str):
        self._queue_ui(self.command_handler.handle_agent_changed, agent_name)

    def _on_agent_changed_by_transfer(self, **data):
        self._queue_ui(self.tool_event_handler.handle_agent_changed_by_transfer, data)

    def _on_agents_listed(self, agents: dict):
        return None

    def _on_agent_command_result(self, **data):
        self._queue_ui(self.command_handler.handle_agent_command_result, data)

    def _on_model_changed(self, **data):
        self._queue_ui(self.command_handler.handle_model_changed, data)

    def _on_models_listed(self, models_by_provider: dict):
        return None

    def _on_evolution_started(self, **data):
        self._queue_ui(self.command_handler.handle_evolution_started, data)

    def _on_evolution_finished(self):
        self._queue_ui(self.command_handler.handle_evolution_finished)

    def _on_evolution_summary(self, **data):
        self._queue_ui(self.command_handler.handle_evolution_summary, data)

    def _on_evolution_applied(self, **data):
        self._queue_ui(self.command_handler.handle_evolution_applied, data)

    def _on_evolution_questions_requested(self, **data):
        self._queue_ui(self.command_handler.handle_evolution_questions, data)

    def _on_evolution_declined(self):
        self._queue_ui(self.command_handler.handle_evolution_declined)

    def _on_error(self, message: str, **details):
        self._queue_ui(self._handle_event_error, {"message": message, **details})

    def _on_system_message(self, message):
        self._queue_ui(self.chat_components.add_system_message, str(message))

    def _on_debug_requested(self, **debug_info):
        self._queue_ui(self.command_handler.handle_debug_requested, debug_info)

    def _on_think_budget_set(self, budget):
        self._queue_ui(self.command_handler.handle_think_budget_set, budget)

    def _on_update_token_usage(self, **data):
        self._queue_ui(self._handle_update_token_usage, data)

    def _on_jump_performed(self, **data):
        self._queue_ui(self.command_handler.handle_jump_performed, data)

    def _on_fork_and_switch(self, **data):
        self._queue_ui(self.command_handler.handle_fork_and_switch, data)

    def _on_learn_confirmation(self, **data):
        self._queue_ui(self.command_handler.handle_learn_confirmation, data)

    def _on_mcp_prompt(self, **data):
        self._queue_ui(self.message_input.setPlainText, data.get("content", ""))

    def _on_copy_requested(self, **data):
        text = data.get("text", "")
        if text:
            self._queue_ui(self.command_handler.handle_copy_requested, text)

    def _on_transfer_enforce_toggled(self, status: str):
        self._queue_ui(
            self.chat_components.add_system_message,
            f"🔄 Transfer enforcement is now {status}.",
        )

    def _on_voice_recording_started(self):
        self._queue_ui(self._handle_voice_recording_started)

    def _on_voice_activate(self, transcript: str):
        self._queue_ui(self._handle_voice_activate, transcript)

    def _on_voice_recording_stopping(self):
        return None

    def _on_voice_recording_completed(self):
        self._queue_ui(self._handle_voice_recording_completed)

    def _on_streaming_stopped(self, response: str):
        self._queue_ui(self._handle_streaming_stopped)

    def _handle_event_error(self, error: dict):
        self.loading_conversation = False
        self.ui_state_manager.set_input_controls_enabled(True)
        if self.bubble_state.current_file_bubble:
            self.chat_components.remove_messages_after(
                self.bubble_state.current_file_bubble
            )
            self.bubble_state.current_file_bubble = None
        self.display_error(error)

    def _handle_file_processing(self, file_path: str):
        self.bubble_state.current_file_bubble = self.chat_components.append_file(
            file_path, is_user=True
        )
        if not self.loading_conversation:
            self.ui_state_manager.set_input_controls_enabled(True)

    def _handle_file_processed(self, data: dict):
        file_path = data.get("file_path")
        if file_path:
            self.chat_components.mark_file_processed(file_path)
        self.bubble_state.current_file_bubble = None

    def _handle_conversation_saved(self, data: dict):
        self.display_status_message(f"Conversation saved: {data.get('id', 'N/A')}")
        self.sidebar.update_conversation_list()
        if not self.loading_conversation:
            self.ui_state_manager.set_input_controls_enabled(True)

    def _handle_conversations_changed(self):
        self.display_status_message("Conversation list updated.")
        self.sidebar.update_conversation_list()

    def _handle_conversation_loaded(self, data: dict):
        self.display_status_message(f"Conversation loaded: {data.get('id', 'N/A')}")
        token_usage = data.get("token_usage")
        if token_usage is not None:
            self.session_cost = 0.0
            total_cost = self.message_handler.agent.calculate_usage_cost(
                token_usage.input_tokens,
                token_usage.output_tokens,
                token_usage.cached_tokens,
            )
            self.token_usage.update_token_info(
                token_usage.input_tokens,
                token_usage.output_tokens,
                token_usage.total_input_tokens,
                total_cost,
                0.0,
                token_usage.cached_tokens,
                token_usage.cache_creation_tokens,
            )

    def _handle_consolidation_completed(self, result: dict):
        self.conversation_components.display_consolidation(result)
        self.ui_state_manager.set_input_controls_enabled(True)

    def _handle_unconsolidation_completed(self, result: dict):
        self.conversation_components.display_unconsolidation(result)
        self.ui_state_manager.set_input_controls_enabled(True)

    def _handle_update_token_usage(self, data: dict):
        self._update_cost_info(
            TokenUsage(
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                cached_tokens=data.get("cached_tokens", 0),
                total_input_tokens=data.get("total_input_tokens", 0),
                cache_creation_tokens=data.get("cache_creation_tokens", 0),
            )
        )

    def _handle_streaming_stopped(self):
        self.chat_components.add_system_message("Message streaming stopped by user.")
        self.ui_state_manager.set_input_controls_enabled(True)

    def _handle_voice_recording_started(self):
        self.ui_state_manager.set_input_controls_enabled(False)
        self.message_input.setPlaceholderText(
            "🎤 Recording... Click voice button to stop"
        )
        self.input_components.update_voice_button_state(True)

    def _handle_voice_activate(self, transcript: str):
        if not transcript:
            self._set_voice_processing_state(False)
            return
        self._set_voice_processing_state(True)
        self._add_user_message_bubble(transcript)
        self.llm_worker.process_request.emit(transcript)
        self.ui_state_manager._set_send_button_state(True)

    def _handle_voice_recording_completed(self):
        self._set_voice_processing_state(False)
        self.message_input.setPlaceholderText("Type a message...")
        self.input_components.update_voice_button_state(False)
        self.ui_state_manager.set_input_controls_enabled(
            self.ui_state_manager._last_enabled_state
        )

    def _add_user_message_bubble(self, data):
        self.chat_components.append_message(
            data, True, self.message_handler.current_user_input_idx
        )  # True = user message

        # Set flag to expect a response (for chunking)
        self.reset_bubble_state(
            current_user_bubble=self.bubble_state.current_user_bubble
        )
        self.reset_stream_state(expecting_response=True)

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

        voice_service = getattr(self.message_handler, "voice_service", None)
        if voice_service and voice_service.is_recording():
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
