import json
from typing import Any
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from PySide6.QtCore import Slot

from AgentCrew.modules.gui.widgets.evolution_loading_dialog import (
    EvolutionLoadingDialog,
)


class CommandHandler:
    """Handles command processing and execution for the chat window."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window
        self._evolution_loading_dialog = None

    def _hide_evolution_loading_dialog(self):
        if self._evolution_loading_dialog is not None:
            self._evolution_loading_dialog.hide()

    def process_command(self, user_input: str) -> bool:
        """
        Process a command input. Returns True if a command was processed, False otherwise.
        """
        if not user_input.startswith("/"):
            return False

        # Clear command
        if user_input.startswith("/clear"):
            self.clear_chat()
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        # Debug command
        elif user_input.startswith("/debug"):
            self.chat_window.llm_worker.process_request.emit(user_input)
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        elif (
            user_input.startswith("/mcp")
            or user_input.startswith("/model")
            or user_input.startswith("/think")
            or user_input.startswith("/usage")
            or user_input.startswith("/toggle_transfer")
            or user_input.startswith("/agent_mode")
            or user_input.startswith("/file")
        ):
            self.chat_window.llm_worker.process_request.emit(user_input)
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True
        elif (
            user_input.startswith("/consolidate")
            or user_input.startswith("/agent")
            or user_input.startswith("/evolve")
            or user_input.startswith("/learn")
            or user_input.startswith("/jump")
            or user_input.startswith("/fork")
            or user_input.startswith("/drop")
        ):
            self.chat_window.llm_worker.process_request.emit(user_input)
            self.chat_window.ui_state_manager.set_input_controls_enabled(False)
            return True

        # Exit command
        elif user_input in ["/exit", "/quit"]:
            QApplication.quit()
            return True

        # Catch-all: any unrecognised /command should not fall through to the LLM
        self.chat_window.display_error(
            "Invalid command: type /help to view all available commands"
        )
        return True

    @Slot()
    def handle_clear_request(self):
        """Handle clear request from worker thread"""
        self.clear_chat(requested=True)

    @Slot()
    def clear_chat(self, requested=False):
        """Clear the chat history and UI."""

        # Clear the UI immediately
        self.chat_window.chat_components.clear_chat_ui()

        # Reset session cost display
        self.chat_window.session_cost = 0.0
        self.chat_window.token_usage.update_token_info(0, 0, 0, 0.0, 0.0)

        # If the clear was initiated by the user (not loading a conversation),
        # tell the message handler to clear its state.
        if not requested:
            self.chat_window.llm_worker.process_request.emit("/clear")
            # Add a confirmation message after clearing
            self.chat_window.chat_components.add_system_message("Chat history cleared.")
            self.chat_window.display_status_message("Chat history cleared")

        # Ensure input controls are enabled after clearing
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)
        self.chat_window.loading_conversation = False  # Ensure loading flag is reset

    def change_agent(self, agent_name):
        """Change the current agent"""
        # Process the agent change command
        self.chat_window.ui_state_manager.set_input_controls_enabled(False)
        self.chat_window.ui_state_manager._set_send_button_state(True)
        self.chat_window.llm_worker.process_request.emit(f"/agent {agent_name}")

    def change_model(self, model_id):
        """Change the current model"""
        # Process the model change command
        self.chat_window.llm_worker.process_request.emit(f"/model {model_id}")

    def open_agents_config(self):
        """Open the agents configuration window."""
        from AgentCrew.modules.gui.widgets.config_window import ConfigWindow

        config_window = ConfigWindow(self.chat_window)
        config_window.tab_widget.setCurrentIndex(0)  # Show Agents tab
        config_window.exec()

        # Refresh agent list in case changes were made
        self.chat_window.menu_builder.refresh_agent_menu()

    def open_mcps_config(self):
        """Open the MCP servers configuration window."""
        from AgentCrew.modules.gui.widgets.config_window import ConfigWindow

        config_window = ConfigWindow(self.chat_window)
        config_window.tab_widget.setCurrentIndex(1)  # Show MCPs tab
        config_window.exec()

    def open_global_settings_config(self):
        """Open the global settings configuration window (API Keys)."""
        from AgentCrew.modules.gui.widgets.config_window import ConfigWindow

        config_window = ConfigWindow(self.chat_window)
        config_window.tab_widget.setCurrentIndex(3)  # Show Settings tab
        config_window.exec()

    def display_debug_info(self):
        """Display debug information about the current messages."""
        # Display agent messages
        self._display_debug_messages(
            "Agent Messages", self.chat_window.message_handler.agent.history
        )

        # Display chat/streamline messages
        self._display_debug_messages(
            "Chat Messages", self.chat_window.message_handler.streamline_messages
        )

        # Update status bar
        self.chat_window.display_status_message("Debug information displayed")

    def _display_debug_messages(
        self, title: str, messages: list, max_content_length: int = 200
    ):
        """Display formatted debug messages with content truncation.

        Args:
            title: Section title for the debug output
            messages: list of message dictionaries
            max_content_length: Maximum length for message content (default: 200)
        """
        formatted_messages = self._format_messages_for_debug(
            messages, max_content_length
        )

        try:
            debug_info = json.dumps(formatted_messages, indent=2)
        except Exception:
            debug_info = str(formatted_messages)

        self.chat_window.chat_components.add_system_message(
            f"DEBUG - {title} ({len(messages)} messages):\n\n```json\n{debug_info}\n```"
        )

    def _format_messages_for_debug(
        self, messages: list, max_content_length: int = 200
    ) -> list:
        """Format messages for debug display with truncated content.

        Args:
            messages: list of message dictionaries
            max_content_length: Maximum length for message content

        Returns:
            list of formatted message dictionaries
        """
        formatted = []

        for i, msg in enumerate(messages):
            formatted_msg = {"#": i, "tool_call_id": ""}

            if "role" in msg:
                formatted_msg["role"] = msg["role"]
            if "agent" in msg:
                formatted_msg["agent"] = msg["agent"]

            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "tool" or role == "tool_result":
                if "tool_call_id" in msg:
                    formatted_msg["tool_call_id"] = msg["tool_call_id"]
                if "tool_use_id" in msg:
                    formatted_msg["tool_use_id"] = msg["tool_use_id"]
                if "tool_id" in msg:
                    formatted_msg["tool_id"] = msg["tool_id"]
                if "tool_name" in msg:
                    formatted_msg["tool_name"] = msg["tool_name"]
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )
            elif role == "assistant" and "tool_calls" in msg:
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )
                formatted_msg["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "tool_call"),
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    }
                    for tc in msg["tool_calls"]
                ]
            elif isinstance(content, list):
                formatted_content = []
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type", "unknown")
                        if item_type == "tool_use":
                            formatted_content.append(
                                {
                                    "type": "tool_use",
                                    "id": item.get("id", ""),
                                    "name": item.get("name", "unknown"),
                                    "input": item.get("input", {}),
                                }
                            )
                        elif item_type == "tool_result":
                            formatted_content.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": item.get("tool_use_id", ""),
                                    "content": self._truncate_content(
                                        item.get("content", ""), max_content_length
                                    ),
                                }
                            )

                        elif item_type == "thinking":
                            formatted_content.append(
                                {
                                    "type": item_type,
                                    "thinking": self._truncate_content(
                                        item.get("thinking", item), max_content_length
                                    ),
                                    "signature": self._truncate_content(
                                        item.get("signature", ""), max_content_length
                                    ),
                                }
                            )
                        else:
                            formatted_content.append(
                                {
                                    "type": item_type,
                                    "content": self._truncate_content(
                                        item.get("text", item), max_content_length
                                    ),
                                }
                            )
                    elif isinstance(item, str):
                        formatted_content.append(
                            {
                                "type": "text",
                                "content": self._truncate_content(
                                    item, max_content_length
                                ),
                            }
                        )
                formatted_msg["content"] = formatted_content
            else:
                formatted_msg["content"] = self._truncate_content(
                    content, max_content_length
                )

            formatted.append(formatted_msg)

        return formatted

    def _truncate_content(self, content: Any, max_length: int) -> str:
        """Truncate content to max_length with ellipsis.

        Args:
            content: Message content (can be string, list, or dict)
            max_length: Maximum length for the output

        Returns:
            Truncated string representation
        """
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Extract text from content blocks
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "tool_use":
                        text_parts.append(f"[tool:{item.get('name', 'unknown')}]")
                    elif item.get("type") == "tool_result":
                        result = item.get("content", "")
                        if isinstance(result, str):
                            text_parts.append(f"[result:{result[:50]}...]")
                        else:
                            text_parts.append("[result:...]")
                elif isinstance(item, str):
                    text_parts.append(item)
            text = " | ".join(text_parts)
        else:
            text = str(content)

        # Clean up whitespace
        text = " ".join(text.split())

        if len(text) <= max_length:
            return text

        return text[: max_length - 3] + "..."

    def handle_clear_event(self):
        self.chat_window.chat_components.clear_chat_ui()
        self.chat_window.session_cost = 0.0
        self.chat_window.token_usage.update_token_info(0, 0, 0, 0.0, 0.0)
        self.chat_window.chat_components.add_system_message(
            "Welcome! Select a past conversation or start a new one."
        )
        self.chat_window.chat_components.add_system_message(
            "Press Ctrl+Enter to send, Ctrl+Shift+C to copy, Ctrl+L to clear chat."
        )
        self.chat_window.loading_conversation = False
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)
        self.chat_window.sidebar.update_conversation_list()

    def handle_exit_requested(self):
        QApplication.quit()

    def handle_debug_requested(self, data: dict):
        if data.get("type") == "system":
            self.chat_window.chat_components.add_system_message(
                "DEBUG - System Prompt:\n\n"
                f"```text\n{data.get('system_prompt') or ''}\n```"
            )
        elif "type" in data and "messages" in data:
            title = "Agent Messages" if data["type"] == "agent" else "Chat Messages"
            self._display_debug_messages(title, data["messages"])

    def handle_agent_changed(self, agent_name: str):
        self.chat_window.chat_components.add_system_message(
            f"Switched to {agent_name} agent"
        )
        self.chat_window.status_indicator.setText(
            f"Agent: {agent_name} | Model: {self.chat_window.message_handler.agent.get_model()}"
        )

    def handle_agent_command_result(self, data: dict):
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    def handle_model_changed(self, data: dict):
        self.chat_window.chat_components.add_system_message(
            f"Switched to {data['name']} ({data['id']})"
        )
        self.chat_window.status_indicator.setText(
            f"Agent: {self.chat_window.message_handler.agent.name} | Model: {self.chat_window.message_handler.agent.get_model()}"
        )

    def handle_think_budget_set(self, budget):
        self.chat_window.chat_components.add_system_message(
            f"Set thinking budget at {budget}"
        )
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    def handle_jump_performed(self, data: dict):
        self.chat_window.chat_components.add_system_message(
            f"🕰️ Jumped to turn {data['turn_number']}: {data['preview']}"
        )

    def handle_fork_and_switch(self, data: dict):
        self.chat_window.chat_components.add_system_message(
            f"🍴 Forked at turn {data['turn_number']}: {data['preview']}"
        )

    def handle_evolution_started(self, data: dict):
        agent_name = data.get("agent_name", "Agent")
        self.chat_window.chat_components.add_system_message(
            f"🧬 Starting prompt evolution for {agent_name}..."
        )
        self.chat_window.display_status_message(f"Evolving {agent_name} prompt...")
        self.chat_window.ui_state_manager.set_input_controls_enabled(False)
        if self._evolution_loading_dialog is None:
            self._evolution_loading_dialog = EvolutionLoadingDialog(
                self.chat_window, agent_name=agent_name
            )
        else:
            self._evolution_loading_dialog.set_agent_name(agent_name)
        self._evolution_loading_dialog.show()
        self._evolution_loading_dialog.raise_()
        self._evolution_loading_dialog.activateWindow()

    def handle_evolution_summary(self, data: dict):
        from AgentCrew.modules.gui.widgets.evolution_review_dialog import (
            EvolutionReviewDialog,
        )

        self._hide_evolution_loading_dialog()
        dialog = EvolutionReviewDialog(
            self.chat_window,
            agent_name=data.get("agent_name", ""),
            summary=data.get("user_editable_summary", ""),
            analysis_summary=data.get("analysis_summary"),
            source_memory_count=data.get("source_memory_count", 0),
        )
        if dialog.exec():
            summary = dialog.get_summary()
            if summary:
                self.chat_window.llm_worker.process_evolution_action.emit(
                    "edit", summary
                )
                self.chat_window.ui_state_manager.set_input_controls_enabled(False)
            else:
                self.chat_window.llm_worker.process_evolution_action.emit("decline", "")
                self.chat_window.ui_state_manager.set_input_controls_enabled(True)
        else:
            self.chat_window.llm_worker.process_evolution_action.emit("decline", "")
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    def handle_evolution_applied(self, data: dict):
        self._hide_evolution_loading_dialog()
        self.chat_window.chat_components.add_system_message(
            f"Updated persisted system prompt for {data['agent_name']}."
        )
        self.chat_window.chat_components.add_diff_system_message(
            f"🧬 Prompt evolution result for {data['agent_name']}",
            data.get("previous_system_prompt", ""),
            data.get("revised_system_prompt", ""),
        )
        self.chat_window.display_status_message(
            f"Prompt evolution applied for {data['agent_name']}"
        )
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    def handle_evolution_questions(self, data: dict):
        """Handle the evolution questions event - show dialog with 3 optional questions."""
        from AgentCrew.modules.gui.widgets.evolution_questions_dialog import (
            EvolutionQuestionsDialog,
        )

        questions = data.get("questions", [])
        questions_id = data.get("questions_id")

        dialog = EvolutionQuestionsDialog(self.chat_window, questions=questions)
        dialog.exec()

        answers = dialog.get_answers()
        self.chat_window.llm_worker.process_evolution_questions.emit(
            questions_id, answers
        )

    def handle_evolution_declined(self):
        self._hide_evolution_loading_dialog()
        self.chat_window.chat_components.add_system_message(
            "Prompt evolution declined."
        )
        self.chat_window.display_status_message("Prompt evolution declined")
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    def handle_learn_confirmation(self, data: dict):
        self._handle_learn_behavior_confirmation(data)

    def handle_evolution_finished(self):
        self._hide_evolution_loading_dialog()
        self.chat_window.display_status_message("Prompt evolution processing finished")

    def _handle_learn_behavior_confirmation(self, data: Any):
        """Handle learn behavior confirmation request from the /learn command."""
        confirmation_id = data.get("confirmation_id")
        behavior_id = data.get("id", "")
        behavior_text = data.get("behavior", "")

        self.chat_window.ui_state_manager.set_input_controls_enabled(True)
        self.chat_window.chat_components.add_system_message(
            f"🧠 Proposed behavior ({behavior_id}):\n  {behavior_text}"
        )

        dialog = QDialog(self.chat_window)
        dialog.setWindowTitle("Learn Behavior Confirmation")
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout()

        label = QLabel(f"Store this behavior?\n\n{behavior_text}")
        label.setWordWrap(True)
        layout.addWidget(label)

        button_layout = QGridLayout()

        global_btn = QPushButton("Confirm (Global)")
        project_btn = QPushButton("Confirm (Project)")
        skip_btn = QPushButton("Skip")

        global_btn.clicked.connect(dialog.accept)
        global_btn.clicked.connect(lambda: setattr(dialog, "_choice", "global"))
        project_btn.clicked.connect(dialog.accept)
        project_btn.clicked.connect(lambda: setattr(dialog, "_choice", "project"))
        skip_btn.clicked.connect(dialog.accept)
        skip_btn.clicked.connect(lambda: setattr(dialog, "_choice", "skip"))

        button_layout.addWidget(global_btn, 0, 0)
        button_layout.addWidget(project_btn, 0, 1)
        button_layout.addWidget(skip_btn, 0, 2)
        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        dialog.setStyleSheet(self.chat_window.style_provider.get_config_window_style())
        dialog._choice = None
        dialog.exec()

        choice = getattr(dialog, "_choice", None)
        if choice in ("global", "project"):
            self.chat_window.message_handler.resolve_learn_confirmation(
                confirmation_id, {"action": "confirm", "scope": choice}
            )
        else:
            self.chat_window.message_handler.resolve_learn_confirmation(
                confirmation_id, {"action": "skip"}
            )
