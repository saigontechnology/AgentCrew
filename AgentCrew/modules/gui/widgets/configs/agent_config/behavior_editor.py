from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService


class BehaviorEditor(QWidget):
    """Composite widget for editing adaptive behaviors of an agent."""

    behavior_changed = Signal()

    def __init__(
        self,
        persistence_service: ContextPersistenceService,
        on_dirty_callback: Callable[[], None] | None = None,
        get_current_agent_name: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.persistence_service = persistence_service
        self._on_dirty_callback = on_dirty_callback
        self._get_current_agent_name = get_current_agent_name or (lambda: "")
        self.current_agent_behaviors: dict[str, str] = {}

        self._init_ui()

    def _init_ui(self):
        """Initialize the behavior editor UI."""
        style_provider = StyleProvider()

        behaviors_group = QGroupBox("Adaptive Behaviors")
        behaviors_layout = QVBoxLayout()

        self.behaviors_list = QListWidget()
        self.behaviors_list.currentItemChanged.connect(self._on_behavior_selected)

        behaviors_buttons_layout = QHBoxLayout()
        self.add_behavior_btn = QPushButton("Add Behavior")
        self.add_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.add_behavior_btn.clicked.connect(self._add_new_behavior)

        self.edit_behavior_btn = QPushButton("Edit")
        self.edit_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.edit_behavior_btn.clicked.connect(self._edit_behavior)
        self.edit_behavior_btn.setEnabled(False)

        self.remove_behavior_btn = QPushButton("Remove")
        self.remove_behavior_btn.setStyleSheet(style_provider.get_button_style("red"))
        self.remove_behavior_btn.clicked.connect(self._remove_behavior)
        self.remove_behavior_btn.setEnabled(False)

        behaviors_buttons_layout.addWidget(self.add_behavior_btn)
        behaviors_buttons_layout.addWidget(self.edit_behavior_btn)
        behaviors_buttons_layout.addWidget(self.remove_behavior_btn)
        behaviors_buttons_layout.addStretch()

        self.behavior_form_widget = QWidget()
        behavior_form_layout = QFormLayout()

        self.behavior_id_input = QLineEdit()
        self.behavior_id_input.setPlaceholderText("e.g., communication_style_technical")
        behavior_form_layout.addRow("Behavior ID:", self.behavior_id_input)

        self.behavior_description_input = QLineEdit()
        self.behavior_description_input.setPlaceholderText(
            "when [condition] do [action]\n\nExample: when user asks about debugging, do provide step-by-step troubleshooting with code examples"
        )
        behavior_form_layout.addRow("Behavior:", self.behavior_description_input)

        behavior_form_buttons_layout = QHBoxLayout()
        self.save_behavior_btn = QPushButton("Save")
        self.save_behavior_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.save_behavior_btn.clicked.connect(self._save_behavior)

        self.cancel_behavior_btn = QPushButton("Cancel")
        self.cancel_behavior_btn.setStyleSheet(
            style_provider.get_button_style("secondary")
        )
        self.cancel_behavior_btn.clicked.connect(self._cancel_behavior_edit)

        behavior_form_buttons_layout.addWidget(self.save_behavior_btn)
        behavior_form_buttons_layout.addWidget(self.cancel_behavior_btn)
        behavior_form_buttons_layout.addStretch()

        behavior_form_layout.addRow("", behavior_form_buttons_layout)
        self.behavior_form_widget.setLayout(behavior_form_layout)
        self.behavior_form_widget.hide()

        self.behavior_id_input.textChanged.connect(self._on_field_changed)
        self.behavior_description_input.textChanged.connect(self._on_field_changed)

        behaviors_layout.addWidget(self.behaviors_list)
        behaviors_layout.addLayout(behaviors_buttons_layout)
        behaviors_layout.addWidget(self.behavior_form_widget)
        behaviors_group.setLayout(behaviors_layout)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(behaviors_group)

    def _on_field_changed(self):
        """Notify parent that a behavior field has changed."""
        if self._on_dirty_callback:
            self._on_dirty_callback()
        self.behavior_changed.emit()

    def _on_behavior_selected(self, current, _):
        """Handle behavior selection in the list."""
        has_selection = current is not None
        self.edit_behavior_btn.setEnabled(has_selection)
        self.remove_behavior_btn.setEnabled(has_selection)

    def load_agent_behaviors(self, agent_name: str):
        """Load adaptive behaviors for the given agent."""
        if not agent_name:
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}
            return

        try:
            behaviors = self.persistence_service.get_adaptive_behaviors(agent_name)
            self.current_agent_behaviors = behaviors

            self.behaviors_list.clear()
            for behavior_id, behavior_text in behaviors.items():
                item = QListWidgetItem(f"{behavior_text}")
                item.setData(
                    Qt.ItemDataRole.UserRole, {"id": behavior_id, "text": behavior_text}
                )
                self.behaviors_list.addItem(item)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Behavior Load Error",
                f"Failed to load behaviors for {agent_name}: {e!s}",
            )
            self.behaviors_list.clear()
            self.current_agent_behaviors = {}

    def _add_new_behavior(self):
        """Show form to add a new adaptive behavior."""
        self.behavior_id_input.clear()
        self.behavior_description_input.clear()
        self.behavior_form_widget.show()
        self.behavior_id_input.setFocus()

    def _edit_behavior(self):
        """Edit the selected adaptive behavior."""
        current_item = self.behaviors_list.currentItem()
        if not current_item:
            return

        behavior_data = current_item.data(Qt.ItemDataRole.UserRole)
        self.behavior_id_input.setText(behavior_data["id"])
        self.behavior_description_input.setText(behavior_data["text"])
        self.behavior_form_widget.show()
        self.behavior_description_input.setFocus()

    def _save_behavior(self):
        """Save the current behavior being edited."""
        agent_name = self._get_current_agent_name()
        if not agent_name:
            QMessageBox.warning(
                self, "No Agent Selected", "Please select an agent first."
            )
            return

        behavior_id = self.behavior_id_input.text().strip()
        behavior_text = self.behavior_description_input.text().strip()

        if not behavior_id:
            QMessageBox.warning(self, "Validation Error", "Behavior ID is required.")
            return

        if not behavior_text:
            QMessageBox.warning(
                self, "Validation Error", "Behavior description is required."
            )
            return

        behavior_lower = behavior_text.lower()
        if not behavior_lower.startswith("when "):
            QMessageBox.warning(
                self,
                "Format Error",
                "Behavior must follow 'when [condition], [action]' format.",
            )
            return

        try:
            success = self.persistence_service.store_adaptive_behavior(
                agent_name, behavior_id, behavior_text
            )
            if success:
                self.current_agent_behaviors[behavior_id] = behavior_text
                self.load_agent_behaviors(agent_name)
                self.behavior_form_widget.hide()
                QMessageBox.information(
                    self, "Success", f"Behavior '{behavior_id}' saved successfully."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Save Error",
                    "Failed to save behavior. Please check the format.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save behavior: {e!s}")

    def _remove_behavior(self):
        """Remove the selected adaptive behavior."""
        current_item = self.behaviors_list.currentItem()
        if not current_item:
            return

        agent_name = self._get_current_agent_name()
        if not agent_name:
            return

        behavior_data = current_item.data(Qt.ItemDataRole.UserRole)
        behavior_id = behavior_data["id"]

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete the behavior '{behavior_id}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.persistence_service.remove_adaptive_behavior(
                    agent_name, behavior_id
                )
                if success:
                    if behavior_id in self.current_agent_behaviors:
                        del self.current_agent_behaviors[behavior_id]
                    self.load_agent_behaviors(agent_name)
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Behavior '{behavior_id}' removed successfully.",
                    )
                else:
                    QMessageBox.warning(
                        self, "Remove Error", "Failed to remove behavior."
                    )
            except Exception as e:
                QMessageBox.critical(
                    self, "Remove Error", f"Failed to remove behavior: {e!s}"
                )

    def _cancel_behavior_edit(self):
        """Cancel behavior editing and hide the form."""
        self.behavior_form_widget.hide()
        self.behavior_id_input.clear()
        self.behavior_description_input.clear()

    def clear(self):
        """Clear all behavior editor state."""
        self.behaviors_list.clear()
        self.current_agent_behaviors = {}
        self.behavior_form_widget.hide()

    def setEnabled(self, enabled: bool):
        """Enable or disable all behavior editor widgets."""
        self.behaviors_list.setEnabled(enabled)
        self.add_behavior_btn.setEnabled(enabled)
        self.edit_behavior_btn.setEnabled(
            enabled and self.behaviors_list.currentItem() is not None
        )
        self.remove_behavior_btn.setEnabled(
            enabled and self.behaviors_list.currentItem() is not None
        )
        if not enabled:
            self.behavior_form_widget.hide()
        super().setEnabled(enabled)
