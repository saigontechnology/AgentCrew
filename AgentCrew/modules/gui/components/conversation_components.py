from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from AgentCrew.modules.chat.agent_evaluation import parse_agent_evaluation
from AgentCrew.modules.gui.utils.strings import need_print_check, tag_action_strip
from AgentCrew.modules.gui.widgets import ConversationLoader


class ConversationComponents:
    """Handles conversation loading, saving, and management."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window

    @Slot(str)
    def load_conversation(self, conversation_id):
        """Initiate loading a conversation asynchronously."""
        if self.chat_window.loading_conversation:
            self.chat_window.display_status_message("Already loading a conversation.")
            return

        self.chat_window.loading_conversation = True
        self.chat_window.ui_state_manager.set_input_controls_enabled(False)
        self.chat_window.display_status_message(
            f"Loading conversation: {conversation_id}..."
        )

        # Use the ConversationLoader thread
        self.loader_thread = ConversationLoader(
            self.chat_window.message_handler, conversation_id
        )
        self.loader_thread.loaded.connect(self.display_conversation)
        self.loader_thread.error.connect(self.handle_load_error)
        # Clean up thread when finished
        self.loader_thread.finished.connect(self.loader_thread.deleteLater)
        self.loader_thread.start()

    @Slot(list, str)
    def display_conversation(self, messages, conversation_id):
        """Display the loaded conversation messages in the UI."""
        self.chat_window.chat_components.clear_chat_ui()

        # Reset session cost when loading a new conversation
        self.chat_window.session_cost = 0.0
        self.chat_window.token_usage.update_token_info(0, 0, 0, 0.0, 0.0)

        last_consolidated_idx = 0

        for i, msg in reversed(list(enumerate(messages))):
            if msg.get("role") == "consolidated":
                last_consolidated_idx = i
                break

        # Add messages from the loaded conversation, filtering for user/assistant roles
        msg_idx = last_consolidated_idx
        for msg in messages[last_consolidated_idx:]:
            role = msg.get("role")
            if role == "user" or role == "assistant":
                content = msg.get("content", "")
                message_content = ""
                is_user = role == "user"

                # Handle different content structures (standardized format)
                if isinstance(content, str):
                    message_content = content
                elif isinstance(content, list) and content:
                    # Assuming the first item in the list contains the primary text
                    first_item = content[0]
                    if (
                        isinstance(first_item, dict)
                        and first_item.get("type") == "text"
                    ):
                        message_content = first_item.get("text", "")
                    elif (
                        isinstance(first_item, dict)
                        and first_item.get("type") == "image_url"
                    ):
                        self.chat_window.chat_components.append_file(
                            first_item.get("image_url", {}).get("url", ""),
                            is_user,
                            True,
                        )
                        msg_idx += 1
                        continue

                if message_content.startswith("Content of "):
                    file_path = (
                        message_content.split(":\n\n")[0]
                        .removeprefix("Content of")
                        .removesuffix("(converted to Markdown)")
                        .strip()
                    )
                    self.chat_window.chat_components.append_file(file_path, True)
                elif message_content.strip() and need_print_check(message_content):
                    message_content = tag_action_strip(message_content)
                    if is_user:
                        self.chat_window.chat_components.append_message(
                            message_content,
                            is_user,
                            msg_idx,
                            msg.get("agent", None),
                        )
                    else:
                        parsed = parse_agent_evaluation(message_content)
                        if parsed["planning_content"]:
                            self.chat_window.chat_components.add_planning_message(
                                parsed["planning_content"]
                            )
                        if parsed["visible_content"].strip():
                            self.chat_window.chat_components.append_message(
                                parsed["visible_content"],
                                is_user,
                                msg_idx,
                                msg.get("agent", None),
                            )
                # Add handling for other potential content formats if necessary
                if "tool_calls" in msg:
                    for tool_call in msg["tool_calls"]:
                        self.chat_window.tool_event_handler.handle_tool_use(tool_call)
                        self.chat_window.tool_event_handler.handle_tool_result(
                            {"tool_use": tool_call, "tool_result": ""}
                        )
            elif role == "consolidated":
                # Handle consolidated message
                content = msg.get("content", "")
                message_content = ""

                if isinstance(content, list) and content:
                    first_item = content[0]
                    if (
                        isinstance(first_item, dict)
                        and first_item.get("type") == "text"
                    ):
                        message_content = first_item.get("text", "")

                if message_content.strip():
                    metadata = msg.get("metadata", {})
                    self.chat_window.chat_components.append_consolidated_message(
                        message_content, metadata
                    )
            msg_idx += 1

        # Update status bar and re-enable controls
        self.chat_window.display_status_message(
            f"Loaded conversation: {conversation_id}"
        )
        self.chat_window.loading_conversation = False
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)
        QApplication.processEvents()
        self.chat_window.chat_container.adjustSize()
        self.chat_window.chat_scroll.updateGeometry()
        QApplication.processEvents()
        self.chat_window.chat_scroll.verticalScrollBar().setValue(
            self.chat_window.chat_scroll.verticalScrollBar().maximum()
        )
        QApplication.processEvents()

    @Slot(str)
    def handle_load_error(self, error_message):
        """Handle errors during conversation loading."""
        self.chat_window.display_error(error_message)
        self.chat_window.loading_conversation = False
        self.chat_window.ui_state_manager.set_input_controls_enabled(True)

    @Slot()
    def start_new_conversation(self):
        """Start a new conversation by clearing the current one."""
        # Check if there are unsaved changes or ongoing operations
        if self.chat_window.waiting_for_response:
            QMessageBox.warning(
                self.chat_window,
                "Operation in Progress",
                "Please wait for the current operation to complete before starting a new conversation.",
            )
            return

        self.chat_window.command_handler.clear_chat()

    def display_consolidation(self, result: dict[str, Any]):
        """Display the result of a conversation consolidation."""
        self.display_conversation(
            self.chat_window.message_handler.streamline_messages,
            self.chat_window.message_handler.current_conversation_id,
        )

    def display_unconsolidation(self, result: dict[str, Any]):
        """Display the result of a conversation unconsolidation."""
        if result.get("success"):
            # Reload the conversation to show unconsolidated state
            self.display_conversation(
                self.chat_window.message_handler.streamline_messages,
                self.chat_window.message_handler.current_conversation_id,
            )

            # Show success message
            messages_restored = result.get("messages_restored", 0)
            self.chat_window.display_status_message(
                f"Successfully unconsolidated {messages_restored} messages"
            )
        else:
            # Show error message
            reason = result.get("reason", "Unknown error")
            self.chat_window.display_status_message(f"Unconsolidation failed: {reason}")
