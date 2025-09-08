import json
from typing import Any
import pyperclip
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Slot


class CommandHandler:
    """Handles command processing and execution for the chat window."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window

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

        # Copy command
        elif user_input.startswith("/copy"):
            self.copy_last_response()
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        # Debug command
        elif user_input.startswith("/debug"):
            self.display_debug_info()
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        elif (
            user_input.startswith("/mcp ")
            or user_input.startswith("/agent ")
            or user_input.startswith("/model ")
            or user_input.startswith("/think ")
            or user_input.startswith("/toggle_transfer")
        ):
            self.chat_window.llm_worker.process_request.emit(user_input)
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True
        elif user_input.startswith("/consolidate "):
            self.chat_window.llm_worker.process_request.emit(user_input)
            self.chat_window.ui_state_manager.set_input_controls_enabled(False)
            return True

        # Exit command
        elif user_input in ["/exit", "/quit"]:
            QApplication.quit()
            return True

        # Command not processed locally - let LLM worker handle it
        return False

    @Slot()
    def copy_last_response(self):
        """Copy the last assistant response to clipboard."""
        text = self.chat_window.message_handler.latest_assistant_response
        if text:
            pyperclip.copy(text)
            self.chat_window.status_bar.showMessage(
                "Last response copied to clipboard!", 3000
            )
        else:
            self.chat_window.status_bar.showMessage("No response to copy", 3000)

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
        self.chat_window.token_usage.update_token_info(0, 0, 0.0, 0.0)

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
        try:
            # Format the messages for display
            debug_info = json.dumps(
                self.chat_window.message_handler.agent.history, indent=2
            )
        except Exception as _:
            debug_info = str(self.chat_window.message_handler.agent.history)
        # Add as a system message
        self.chat_window.chat_components.add_system_message(
            f"DEBUG INFO:\n\n```json\n{debug_info}\n```"
        )

        try:
            # Format the messages for display
            debug_info = json.dumps(
                self.chat_window.message_handler.streamline_messages, indent=2
            )
        except Exception as _:
            debug_info = str(self.chat_window.message_handler.streamline_messages)
        # Add as a system message
        self.chat_window.chat_components.add_system_message(
            f"DEBUG INFO:\n\n```json\n{debug_info}\n```"
        )

        # Update status bar
        self.chat_window.display_status_message("Debug information displayed")

    def handle_event(self, event: str, data: Any) -> bool:
        """
        Handle command-related events. Returns True if event was processed, False otherwise.
        """
        if event == "clear_requested":
            self.chat_window.chat_components.clear_chat_ui()
            self.chat_window.session_cost = 0.0
            self.chat_window.token_usage.update_token_info(0, 0, 0.0, 0.0)
            self.chat_window.chat_components.add_system_message(
                "Welcome! Select a past conversation or start a new one."
            )
            self.chat_window.chat_components.add_system_message(
                "Press Ctrl+Enter to send, Ctrl+Shift+C to copy, Ctrl+L to clear chat."
            )
            self.chat_window.loading_conversation = False
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            self.chat_window.sidebar.update_conversation_list()
            return True

        elif event == "exit_requested":
            QApplication.quit()
            return True

        elif event == "copy_requested":
            if isinstance(data, str):
                pyperclip.copy(data)
                self.chat_window.display_status_message("Text copied to clipboard!")
            return True

        elif event == "debug_requested":
            try:
                debug_info = json.dumps(data, indent=2)
                self.chat_window.chat_components.add_system_message(
                    f"DEBUG INFO:\n\n```json\n{debug_info}\n```"
                )
            except Exception:
                self.chat_window.chat_components.add_system_message(
                    f"DEBUG INFO:\n\n{str(data)}"
                )
            return True

        elif event == "agent_changed":
            self.chat_window.chat_components.add_system_message(
                f"Switched to {data} agent"
            )
            self.chat_window.status_indicator.setText(
                f"Agent: {data} | Model: {self.chat_window.message_handler.agent.get_model()}"
            )
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        elif event == "model_changed":
            self.chat_window.chat_components.add_system_message(
                f"Switched to {data['name']} ({data['id']})"
            )
            self.chat_window.status_indicator.setText(
                f"Agent: {self.chat_window.message_handler.agent.name} | Model: {self.chat_window.message_handler.agent.get_model()}"
            )
            return True

        # elif event == "agent_changed_by_transfer":
        #     self.chat_window.chat_components.add_system_message(
        #         f"Transfered to {data} agent"
        #     )
        #     self.chat_window.status_indicator.setText(
        #         f"Agent: {data} | Model: {self.chat_window.message_handler.agent.get_model()}"
        #     )
        #     self.chat_window.current_response_bubble = None
        #     self.chat_window.current_response_container = None
        #     return True

        elif event == "think_budget_set":
            self.chat_window.chat_components.add_system_message(
                f"Set thinking budget at {data}"
            )
            self.chat_window.ui_state_manager.set_input_controls_enabled(True)
            return True

        elif event == "jump_performed":
            self.chat_window.chat_components.add_system_message(
                f"🕰️ Jumped to turn {data['turn_number']}: {data['preview']}"
            )
            return True

        # Event not handled by command handler
        return False
