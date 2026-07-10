from PySide6.QtWidgets import (
    QMessageBox,
    QTextEdit,
    QGridLayout,
    QDialog,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QScrollArea,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import Qt
from AgentCrew.modules.gui.widgets.tool_widget import ToolWidget
from AgentCrew.modules.gui.widgets.diff_widget import DiffWidget


class ToolEventHandler:
    """Handles tool-related events in the chat UI."""

    def __init__(self, chat_window):
        from AgentCrew.modules.gui import ChatWindow

        if isinstance(chat_window, ChatWindow):
            self.chat_window = chat_window

    def show_denial_reason_dialog(self, tool_name: str) -> str | None:
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

    def handle_agent_changed_by_transfer(self, data: dict):
        transfer_data = {
            **data,
            "tool_result": f"Transfered to Agent: {data['agent_name']}",
        }
        self.handle_tool_result(transfer_data)
        self.chat_window.status_indicator.setText(
            f"Agent: {data['agent_name']} | Model: {self.chat_window.message_handler.agent.get_model()}"
        )

    def handle_tool_use(self, tool_use: dict):
        """Display information about a tool being used."""
        # Create tool widget
        tool_widget = ToolWidget(tool_use["name"], tool_use)

        # Store reference to the tool widget for later result update
        tool_use["tool_widget_ref"] = tool_widget

        # Add to chat using convenience method
        self.chat_window.chat_components.add_tool_widget(tool_widget)

        # Display status message
        self.chat_window.display_status_message(f"Using tool: {tool_use['name']}")

    def handle_tool_result(self, data: dict):
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
        self.chat_window.bubble_state.current_response_bubble = None
        self.chat_window.bubble_state.current_response_container = None
        self.chat_window.bubble_state.current_thinking_bubble = None
        self.chat_window.stream_state.thinking_content = ""

        # Reset the current response bubble so the next agent message starts in a new bubble

    def handle_tool_error(self, data: dict):
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
        self.chat_window.bubble_state.current_response_bubble = None
        self.chat_window.bubble_state.current_response_container = None

    def handle_tool_confirmation_required(self, tool_info):
        """Display a dialog for tool confirmation request."""
        tool_use = tool_info.copy()
        confirmation_id = tool_use.pop("confirmation_id")

        if tool_use["name"] == "ask":
            self._handle_ask_tool_confirmation(tool_use, confirmation_id)
            return

        if tool_use["name"] == "write_file":
            self._handle_write_file_confirmation(tool_use, confirmation_id)
            return

        dialog = QMessageBox(self.chat_window)
        dialog.setWindowTitle("Tool Execution Confirmation")
        dialog.setIcon(QMessageBox.Icon.Question)

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
            from AgentCrew.modules.config.global_config import GlobalConfig

            GlobalConfig().write_auto_approval_tools(tool_use["name"], add=True)

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

        self.chat_window.bubble_state.current_response_bubble = None
        self.chat_window.bubble_state.current_response_container = None

    def _handle_write_file_confirmation(self, tool_use, confirmation_id):
        """Handle write_or_edit_file tool confirmation with diff view."""
        from PySide6.QtWidgets import QWidget, QHBoxLayout

        tool_input = tool_use.get("input", {})
        file_path = tool_input.get("file_path", "")
        text_or_blocks = tool_input.get("write_blocks", "")

        has_diff = DiffWidget.has_search_replace_blocks(text_or_blocks)

        dialog = QDialog(self.chat_window)
        dialog.setWindowTitle("File Edit Confirmation")
        dialog.setMinimumWidth(800 if has_diff else 600)
        dialog.setMinimumHeight(600 if has_diff else 400)

        layout = QVBoxLayout()

        diff_colors = self.chat_window.style_provider.get_diff_colors()
        header_label = QLabel(f"📝 <b>Edit File:</b> {file_path}")
        header_label.setStyleSheet(
            f"font-size: 14px; padding: 10px; color: {diff_colors.get('header_text', '#89b4fa')};"
        )
        layout.addWidget(header_label)

        info_label = QLabel(
            f"Mode: {'Search/Replace Blocks' if has_diff else 'Full Content'}"
        )
        info_label.setStyleSheet(
            f"font-size: 11px; color: {diff_colors.get('line_number_text', '#6c7086')}; padding: 0 10px;"
        )
        layout.addWidget(info_label)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(350)

        if has_diff:
            diff_widget = DiffWidget(style_provider=self.chat_window.style_provider)
            diff_widget.set_diff_content(text_or_blocks, file_path)
            scroll_area.setWidget(diff_widget)
        else:
            content_widget = QTextEdit()
            content_widget.setReadOnly(True)
            content_widget.setText(text_or_blocks)
            content_widget.setStyleSheet(
                self.chat_window.style_provider.get_tool_dialog_text_edit_style()
            )
            scroll_area.setWidget(content_widget)

        layout.addWidget(scroll_area)

        buttons_container = QWidget()
        buttons_layout = QHBoxLayout(buttons_container)
        buttons_layout.setContentsMargins(0, 10, 0, 0)

        yes_button = QPushButton("✓ Approve")
        yes_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_yes_button_style()
        )

        all_button = QPushButton("✓✓ Yes to All")
        all_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_all_button_style()
        )

        forever_button = QPushButton("∞ Forever")
        forever_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_all_button_style()
        )

        no_button = QPushButton("✗ Deny")
        no_button.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_no_button_style()
        )

        buttons_layout.addWidget(yes_button)
        buttons_layout.addWidget(all_button)
        buttons_layout.addWidget(forever_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(no_button)

        layout.addWidget(buttons_container)

        dialog.setLayout(layout)
        dialog.setStyleSheet(self.chat_window.style_provider.get_config_window_style())

        result = {"action": ""}

        def on_yes():
            result["action"] = "approve"
            dialog.accept()

        def on_all():
            result["action"] = "approve_all"
            dialog.accept()

        def on_forever():
            result["action"] = "approve_forever"
            dialog.accept()

        def on_no():
            result["action"] = "deny"
            dialog.reject()

        yes_button.clicked.connect(on_yes)
        all_button.clicked.connect(on_all)
        forever_button.clicked.connect(on_forever)
        no_button.clicked.connect(on_no)

        approve_shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
        approve_shortcut.activated.connect(on_yes)

        dialog.exec()

        if result["action"] == "approve":
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve"}
            )
            self.chat_window.display_status_message(f"Approved file edit: {file_path}")

        elif result["action"] == "approve_all":
            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            self.chat_window.display_status_message(
                "Approved all future write_file calls"
            )

        elif result["action"] == "approve_forever":
            from AgentCrew.modules.config.global_config import GlobalConfig

            GlobalConfig().write_auto_approval_tools("write_file", add=True)

            self.chat_window.message_handler.resolve_tool_confirmation(
                confirmation_id, {"action": "approve_all"}
            )
            self.chat_window.display_status_message(
                "write_file will be auto-approved forever"
            )

        else:
            denial_reason = self.show_denial_reason_dialog("write_file")

            if denial_reason:
                self.chat_window.message_handler.resolve_tool_confirmation(
                    confirmation_id, {"action": "deny", "reason": denial_reason}
                )
                self.chat_window.display_status_message(
                    f"Denied file edit - Reason: {denial_reason[:50]}..."
                    if len(denial_reason) > 50
                    else f"Denied file edit - Reason: {denial_reason}"
                )
            else:
                self.chat_window.message_handler.resolve_tool_confirmation(
                    confirmation_id, {"action": "deny"}
                )
                self.chat_window.display_status_message("Denied file edit")

    def _handle_ask_tool_confirmation(self, tool_use, confirmation_id):
        """Handle the ask tool - display one question at a time with navigation in GUI."""
        questions = tool_use["input"].get("questions", [])
        if isinstance(questions, str):
            import json

            try:
                questions = json.loads(questions)
            except (json.JSONDecodeError, TypeError):
                questions = []

        if not questions or not isinstance(questions, list):
            questions = [{"question": "(missing questions)", "guided_answers": ["OK"]}]

        # Normalize each question's guided_answers
        for q in questions:
            answers = q.get("guided_answers", [])
            if isinstance(answers, str):
                q["guided_answers"] = answers.strip("\n ").splitlines()

        total = len(questions)
        current_idx = 0
        question_answers: dict[str, str] = {}

        while current_idx < total:
            q = questions[current_idx]
            guided_answers = list(q.get("guided_answers", []))

            dialog = self._build_multi_question_dialog(
                questions, current_idx, question_answers
            )

            if dialog.exec() == QDialog.DialogCode.Accepted:
                # Collect the answer for this question
                custom_text = dialog._custom_input.toPlainText().strip()
                selected = []
                for i, cb in enumerate(dialog._checkboxes):
                    if cb.isChecked():
                        selected.append(guided_answers[i])

                if custom_text:
                    question_answers[str(current_idx)] = custom_text
                elif selected:
                    question_answers[str(current_idx)] = ", ".join(selected)

                # Determine navigation action
                action = getattr(dialog, "_nav_action", "next")
                if action == "back" and current_idx > 0:
                    current_idx -= 1
                elif action == "submit":
                    break
                else:
                    current_idx += 1
            else:
                # Dialog cancelled
                self.chat_window.message_handler.resolve_tool_confirmation(
                    confirmation_id,
                    {"action": "answer", "answer": "Cancelled by user"},
                )
                return

        # Build structured result
        answer_lines = []
        for i, q in enumerate(questions):
            idx_str = str(i)
            ans = question_answers.get(idx_str, "(skipped)")
            answer_lines.append(f"- {q['question']}: {ans}")

        result = "Answers:\n" + "\n".join(answer_lines)

        self.chat_window.message_handler.resolve_tool_confirmation(
            confirmation_id, {"action": "answer", "answer": result}
        )

        status_preview = result[:50] + "..." if len(result) > 50 else result
        self.chat_window.display_status_message(f"Answered agent: {status_preview}")

    def _build_multi_question_dialog(self, questions, current_idx, existing_answers):
        """Build a dialog showing one question with navigation."""
        from PySide6.QtWidgets import QCheckBox, QScrollArea, QWidget, QDialogButtonBox

        total = len(questions)
        q = questions[current_idx]
        question_text = q.get("question", "")
        guided_answers = list(q.get("guided_answers", []))

        dialog = QDialog(self.chat_window)
        nav_prefix = f"[{current_idx + 1}/{total}] " if total > 1 else ""
        dialog.setWindowTitle(f"{nav_prefix}Agent Question")
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(450)

        layout = QVBoxLayout()

        # Progress indicator
        if total > 1:
            progress_label = QLabel(f"Question {current_idx + 1} of {total}")
            progress_label.setStyleSheet(
                "font-size: 11px; color: #888; padding: 2px 10px;"
            )
            layout.addWidget(progress_label)

        # Question label
        question_label = QLabel(f"❓ {nav_prefix}{question_text}")
        question_label.setWordWrap(True)
        question_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 10px;"
        )
        layout.addWidget(question_label)

        # Instruction
        instruction_label = QLabel("Select an option or provide a custom answer below:")
        instruction_label.setWordWrap(True)
        instruction_label.setStyleSheet("font-size: 12px; color: #888; padding: 5px;")
        layout.addWidget(instruction_label)

        # Radio-style answer buttons
        answers_container = QWidget()
        answers_layout = QVBoxLayout()
        checkboxes = []

        for idx, answer in enumerate(guided_answers, 1):
            checkbox = QCheckBox(f"{idx}. {answer}")
            checkbox.setStyleSheet("font-size: 12px; padding: 5px;")
            checkboxes.append(checkbox)
            answers_layout.addWidget(checkbox)

        answers_container.setLayout(answers_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(answers_container)
        scroll_area.setMinimumHeight(100)
        layout.addWidget(scroll_area)

        # Custom answer section
        custom_label = QLabel("Or provide a custom answer:")
        custom_label.setStyleSheet("font-size: 12px; padding: 5px; margin-top: 10px;")
        layout.addWidget(custom_label)

        custom_input = QTextEdit()
        custom_input.setPlaceholderText("Type your custom answer here...")
        custom_input.setMinimumHeight(60)
        custom_input.setStyleSheet(
            self.chat_window.style_provider.get_tool_dialog_text_edit_style()
        )
        layout.addWidget(custom_input)

        # Pre-fill if already answered
        prev_answer = existing_answers.get(str(current_idx), "")
        if prev_answer:
            custom_input.setPlainText(prev_answer)

        # Navigation buttons
        button_box = QDialogButtonBox()
        if current_idx > 0 and total > 1:
            button_box.addButton("← Back", QDialogButtonBox.ButtonRole.ActionRole)
        button_box.addButton(
            "Next →" if current_idx < total - 1 else "Finish",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        if total > 1:
            button_box.addButton("Submit all", QDialogButtonBox.ButtonRole.AcceptRole)

        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.setStyleSheet(self.chat_window.style_provider.get_config_window_style())

        # Store references for result collection
        dialog._checkboxes = checkboxes
        dialog._custom_input = custom_input
        dialog._nav_action = "next"

        # Wire up navigation
        for btn in button_box.buttons():
            text = btn.text()
            if "←" in text:
                btn.clicked.connect(lambda: setattr(dialog, "_nav_action", "back"))
                btn.clicked.connect(dialog.accept)
            elif "Submit all" in text:
                btn.clicked.connect(lambda: setattr(dialog, "_nav_action", "submit"))
                btn.clicked.connect(dialog.accept)
            else:
                btn.clicked.connect(lambda: setattr(dialog, "_nav_action", "next"))
                btn.clicked.connect(dialog.accept)

        # Ctrl+Enter shortcut to submit
        submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
        submit_shortcut.activated.connect(dialog.accept)

        return dialog
