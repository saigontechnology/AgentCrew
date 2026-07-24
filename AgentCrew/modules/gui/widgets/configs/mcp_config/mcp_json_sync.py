from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QPushButton, QStackedWidget

from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.gui.widgets.json_editor import JsonEditor


class MCPJsonSync(QObject):
    """Manages the QStackedWidget for toggling between form and JSON code views."""

    view_switched_to_form = Signal(dict)
    view_switched_to_code = Signal(dict)
    validation_error = Signal(str)
    json_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style_provider = StyleProvider()
        self.stacked_widget = QStackedWidget()
        self.json_editor = JsonEditor()
        self.json_editor.json_changed.connect(self._on_json_changed)
        self.json_editor.validation_error.connect(self._on_json_validation_error)

        self.show_code_btn = QPushButton("Show Code")
        self.show_code_btn.setStyleSheet(
            self._style_provider.get_button_style("secondary")
        )
        self.show_code_btn.setEnabled(False)
        self.show_code_btn.clicked.connect(self.toggle_view_mode)

        self._is_code_view = False

    def setup_stacked_widget(self, form_widget, json_widget=None):
        """Add form and JSON editor to the stacked widget in the correct order.

        Form view is at index 0, JSON/code view is at index 1.
        """
        if json_widget is None:
            json_widget = self.json_editor
        self.stacked_widget.addWidget(form_widget)  # index 0 — form view
        self.stacked_widget.addWidget(json_widget)  # index 1 — code view

    def _on_json_changed(self, json_data: dict):
        self.json_changed.emit(json_data)

    def _on_json_validation_error(self, error_msg: str):
        self.validation_error.emit(error_msg)

    def toggle_view_mode(self):
        """Toggle between form and code view. Returns True if toggle succeeded."""
        if self._is_code_view:
            try:
                json_data = self.json_editor.get_json()
                self.stacked_widget.setCurrentIndex(0)
                self.show_code_btn.setText("Show Code")
                self._is_code_view = False
                self.view_switched_to_form.emit(json_data)
                return True
            except ValueError as e:
                QMessageBox.warning(
                    None,
                    "Invalid JSON",
                    f"Cannot switch to form view: {e!s}\n"
                    "Please fix the JSON syntax first.",
                )
                return False
        else:
            self._is_code_view = True
            self.show_code_btn.setText("Show Form")
            self.stacked_widget.setCurrentIndex(1)
            self.view_switched_to_code.emit({})
            return True

    def set_json(self, data: dict):
        self.json_editor.set_json(data)

    def get_json(self) -> dict:
        return self.json_editor.get_json()

    def set_read_only(self, read_only: bool):
        self.json_editor.set_read_only(read_only)

    def update_theme(self):
        self.json_editor.update_theme()

    @property
    def is_code_view(self) -> bool:
        return self._is_code_view

    @is_code_view.setter
    def is_code_view(self, value: bool):
        self._is_code_view = value
        if value:
            self.show_code_btn.setText("Show Form")
            self.stacked_widget.setCurrentIndex(1)
        else:
            self.show_code_btn.setText("Show Code")
            self.stacked_widget.setCurrentIndex(0)

    def reset_to_form_view(self):
        self._is_code_view = False
        self.stacked_widget.setCurrentIndex(0)
        self.show_code_btn.setText("Show Code")
