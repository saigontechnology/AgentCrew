from typing import Any, Dict, Optional
from PySide6.QtWidgets import (
    QMessageBox,
    QTextEdit,
    QGridLayout,
    QDialog,
    QVBoxLayout,
    QPushButton,
    QLabel,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import Qt
from AgentCrew.modules.gui.widgets.tool_widget import ToolWidget


class ToolEventHandler:
    """Handles tool-related events in the chat UI."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window

    def show_denial_reason_dialog(self, tool_name: str) -> Optional[str]:
        """
        Show a dialog to collect the reason for denying a tool execution.

        Args:
            tool_name: Name of the tool being denied

        Returns:
            The denial reason if submitted, None if cancelled
        """
        dialog = QDialog(self.chat_window)
        dialog.setWindowTitle("Tool Execution Denied")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(300)

        # Create layout
        layout = QVBoxLayout()

        # Add label
        label = QLabel(f"Please explain why you are denying the '{tool_name}' tool:")
        label.setWordWrap(True)
        layout.addWidget(label)

        # Add multiline text edit
        reason_edit = QTextEdit()
        reason_edit.setPlaceholderText("Enter your reason here...")
        reason_edit.setMinimumHeight(100)

        # Apply theme styling
        reason_edit.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_text_edit_style()
        )
        layout.addWidget(reason_edit)

        # Add submit button
        submit_button = QPushButton("Submit (Ctrl+Enter)")
        submit_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_yes_button_style()
        )
        submit_button.clicked.connect(dialog.accept)
        submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
        submit_shortcut.activated.connect(submit_button.click)
        layout.addWidget(submit_button)

        dialog.setLayout(layout)

        # Apply dialog styling
        dialog.setStyleSheet(self.chat_window.style_provider.get_config_window_style())

        # Show dialog and get result
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return reason_edit.toPlainText().strip()
        return None

    def handle_event(self, event: str, data: Any):
        """Handle a tool-related event."""
        if event == "tool_use":
            self.handle_tool_use(data)
        elif event == "tool_result":
            self.handle_tool_result(data)
        elif event == "agent_changed_by_transfer":
            data["tool_result"] = f"Transfered to Agent: {data['agent_name']}"
            self.handle_tool_result(data)
            self.chat_window.status_indicator.setText(
                f"Agent: {data['agent_name']} | Model: {self.chat_window.message_handler.agent.get_model()}"
            )
        elif event == "tool_error":
            self.handle_tool_error(data)
        elif event == "tool_confirmation_required":
            self.handle_tool_confirmation_required(data)
        elif event == "tool_denied":
            self.handle_tool_denied(data)

    def handle_tool_use(self, tool_use: Dict):
        """Display information about a tool being used."""
        # Create tool widget
        tool_widget = ToolWidget(tool_use["name"], tool_use)

        # Store reference to the tool widget for later result update
        tool_use["tool_widget_ref"] = tool_widget

        # Add to chat using convenience method
        self.chat_window.chat_components.add_tool_widget(tool_widget)

        # Display status message
        self.chat_window.display_status_message(f"Using tool: {tool_use['name']}")

    def handle_tool_result(self, data: Dict):
        """Display the result of a tool execution."""
        tool_use = data["tool_use"]
        tool_result = data["tool_result"]

        # If we have a reference to the tool widget, update it
        if "tool_widget_ref" in tool_use:
            tool_widget = tool_use["tool_widget_ref"]
            tool_widget.update_with_result(tool_result)
        else:
            # Create a new tool widget with both use and result
            tool_widget = ToolWidget(tool_use["name"], tool_use, tool_result)

            # Add to chat using convenience method
            self.chat_window.chat_components.add_tool_widget(tool_widget)
        self.chat_window.current_response_bubble = None
        self.chat_window.current_response_container = None
        self.chat_window.current_thinking_bubble = None
        self.chat_window.thinking_content = ""

        # Reset the current response bubble so the next agent message starts in a new bubble

    def handle_tool_error(self, data: Dict):
        """Display an error that occurred during tool execution."""
        tool_use = data["tool_use"]
        error = data["error"]

        # If we have a reference to the tool widget, update it
        if "tool_widget_ref" in tool_use:
            tool_widget = tool_use["tool_widget_ref"]
            tool_widget.update_with_result(error, is_error=True)
        else:
            # Create a new tool widget with both use and error result
            tool_widget = ToolWidget(tool_use["name"], tool_use, error, is_error=True)

            # Add to chat using convenience method
            self.chat_window.chat_components.add_tool_widget(tool_widget)

        self.chat_window.display_status_message(f"Error in tool {tool_use['name']}")

        # Reset the current response bubble so the next agent message starts in a new bubble
        self.chat_window.current_response_bubble = None
        self.chat_window.current_response_container = None

    def handle_tool_confirmation_required(self, tool_info):
        """Display a dialog for tool confirmation request."""
        tool_use = tool_info.copy()
        confirmation_id = tool_use.pop("confirmation_id")

        # Special handling for 'ask' tool
        if tool_use["name"] == "ask":
            self._handle_ask_tool_confirmation(tool_use, confirmation_id)
            return

        # Create dialog
        dialog = QMessageBox(self.chat_window)
        dialog.setWindowTitle("Tool Execution Confirmation")
        dialog.setIcon(QMessageBox.Icon.Question)

        # Format tool information for display
        tool_description = f"The assistant wants to use the '{tool_use['name']}' tool."
        params_text = ""

        if isinstance(tool_use["input"], dict):
            params_text = "Parameters:"
            for key, value in tool_use["input"].items():
                params_text += f"\n• {key}: {value}"
        else:
            params_text = f"\n\nInput: {tool_use['input']}"

        dialog.setInformativeText("Do you want to allow this tool to run?")
        dialog.setText(tool_description)

        lt = dialog.layout()
        text_edit = QTextEdit()
        text_edit.setMinimumWidth(500)
        text_edit.setMinimumHeight(300)
        text_edit.setReadOnly(True)
        text_edit.setText(params_text)

        # Style the text edit to match the main theme
        text_edit.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_text_edit_style()
        )

        if isinstance(lt, QGridLayout):
            lt.addWidget(
                text_edit,
                lt.rowCount() - 2,
                2,
                1,
                lt.columnCount() - 2,
                Qt.AlignmentFlag.AlignLeft,
            )

        # Add buttons
        yes_button = dialog.addButton("Yes (Once)", QMessageBox.ButtonRole.YesRole)
        no_button = dialog.addButton("No", QMessageBox.ButtonRole.NoRole)
        all_button = dialog.addButton("Yes to All", QMessageBox.ButtonRole.AcceptRole)
        forever_button = dialog.addButton("Forever", QMessageBox.ButtonRole.AcceptRole)

        # Style the buttons with Catppuccin colors
        yes_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_yes_button_style()
        )

        all_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_all_button_style()
        )

        forever_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_all_button_style()
        )

        no_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_no_button_style()
        )

        # Execute dialog and get result
        dialog.exec()
        clicked_button = dialog.clickedButton()

        # Process result
        if clicked_button == yes_button:
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve"}
            )
            self.chat_window.display_status_message(
                f"Approved tool: {tool_use['name']}"
            )
        elif clicked_button == all_button:
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            self.chat_window.display_status_message(
                f"Approved all future calls to tool: {tool_use['name']}"
            )
        elif clicked_button == forever_button:
            from AgentCrew.modules.config import ConfigManagement

            config_manager = ConfigManagement()
            config_manager.write_auto_approval_tools(tool_use["name"], add=True)

            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            self.chat_window.display_status_message(
                f"Tool '{tool_use['name']}' will be auto-approved forever"
            )
        else:  # No or dialog closed
            # Show dialog to collect denial reason
            denial_reason = self.show_denial_reason_dialog(tool_use["name"])

            if denial_reason:
                # User provided a reason
                self.chat_window.message_handler.resolve_tool_confirmation(
                    confirmation_id, {"action": "deny", "reason": denial_reason}
                )
                self.chat_window.display_status_message(
                    f"Denied tool: {tool_use['name']} - Reason: {denial_reason[:50]}..."
                    if len(denial_reason) > 50
                    else f"Denied tool: {tool_use['name']} - Reason: {denial_reason}"
                )
            else:
                # User cancelled the reason dialog, just deny without reason
                self.chat_window.message_handler.resolve_tool_confirmation(
                    confirmation_id, {"action": "deny"}
                )
                self.chat_window.display_status_message(
                    f"Denied tool: {tool_use['name']}"
                )

    def handle_tool_denied(self, data):
        """Display a message about a denied tool execution."""
        self.chat_window.chat_components.add_system_message(
            f"❌ Tool execution rejected: {data['message']}"
        )
        self.chat_window.display_status_message(
            f"Tool execution rejected: {data['message']}"
        )

        self.chat_window.current_response_bubble = None
        self.chat_window.current_response_container = None

    def _handle_ask_tool_confirmation(self, tool_use, confirmation_id):
        """Handle the ask tool - display question and guided answers in GUI."""
        question = tool_use["input"].get("question", "")
        guided_answers = tool_use["input"].get("guided_answers", [])
        if isinstance(guided_answers, str):
            guided_answers = guided_answers.strip("\n ").splitlines()

        # Create dialog
        dialog = QDialog(self.chat_window)
        dialog.setWindowTitle("Agent Question")
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(400)

        # Create layout
        layout = QVBoxLayout()

        # Add question label
        question_label = QLabel(f"❓ {question}")
        question_label.setWordWrap(True)
        question_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 10px;"
        )
        layout.addWidget(question_label)

        # Add instruction label
        instruction_label = QLabel(
            "Select one or more options (hold Ctrl/Cmd for multiple), or provide a custom answer below:"
        )
        instruction_label.setWordWrap(True)
        instruction_label.setStyleSheet("font-size: 12px; color: #888; padding: 5px;")
        layout.addWidget(instruction_label)

        # Add guided answers as checkable buttons
        from PySide6.QtWidgets import QCheckBox, QScrollArea, QWidget

        answers_container = QWidget()
        answers_layout = QVBoxLayout()
        checkboxes = []

        for idx, answer in enumerate(guided_answers, 1):
            checkbox = QCheckBox(f"{idx}. {answer}")
            checkbox.setStyleSheet("font-size: 12px; padding: 5px;")
            checkboxes.append(checkbox)
            answers_layout.addWidget(checkbox)

        answers_container.setLayout(answers_layout)

        # Add scroll area for answers
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(answers_container)
        scroll_area.setMinimumHeight(150)
        layout.addWidget(scroll_area)

        # Add custom answer section
        custom_label = QLabel("Or provide a custom answer:")
        custom_label.setStyleSheet("font-size: 12px; padding: 5px; margin-top: 10px;")
        layout.addWidget(custom_label)

        custom_input = QTextEdit()
        custom_input.setPlaceholderText("Type your custom answer here...")
        custom_input.setMinimumHeight(80)
        custom_input.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_text_edit_style()
        )
        layout.addWidget(custom_input)

        # Add submit button
        submit_button = QPushButton("Submit Answer")
        submit_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_yes_button_style()
        )
        layout.addWidget(submit_button)

        dialog.setLayout(layout)
        dialog.setStyleSheet(self.chat_window.style_provider.get_config_window_style())

        submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
        submit_shortcut.activated.connect(submit_button.click)

        submit_button.clicked.connect(dialog.accept)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            custom_text = custom_input.toPlainText().strip()

            if custom_text:
                # User provided custom answer
                final_answer = custom_text
            else:
                # Collect selected checkboxes
                selected = [
                    guided_answers[i]
                    for i, cb in enumerate(checkboxes)
                    if cb.isChecked()
                ]

                if not selected:
                    final_answer = "Cancelled by user"

                else:
                    final_answer = ", ".join(selected)

            # Resolve the confirmation with the answer
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "answer", "answer": final_answer}
            )

            self.chat_window.display_status_message(
                f"Answered agent question: {final_answer[:50]}..."
                if len(final_answer) > 50
                else f"Answered agent question: {final_answer}"
            )
        else:
            # Resolve the confirmation with the answer
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "answer", "answer": "Cancelled by user"}
            )
