from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider


class DynamicFieldList(QWidget):
    """Reusable widget for managing dynamic key-value or single-value field rows."""

    dirty = Signal()

    def __init__(
        self,
        mode: str = "key_value",
        add_button_label: str = "Add Item",
        key_placeholder: str = "Key",
        value_placeholder: str = "Value",
        parent=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self._key_placeholder = key_placeholder
        self._value_placeholder = value_placeholder
        self._field_inputs: list[dict] = []
        self._style_provider = StyleProvider()

        self._group = QGroupBox()
        self._layout = QVBoxLayout(self._group)
        self._layout.setContentsMargins(0, 0, 0, 0)

        btn_layout = QHBoxLayout()
        self._add_btn = QPushButton(add_button_label)
        self._add_btn.setStyleSheet(self._style_provider.get_button_style("primary"))
        self._add_btn.clicked.connect(lambda: self.add_field())
        btn_layout.addWidget(self._add_btn)
        btn_layout.addStretch()
        self._layout.addLayout(btn_layout)

        outer = QVBoxLayout(self)
        outer.addWidget(self._group)

    @property
    def group(self) -> QGroupBox:
        return self._group

    @property
    def add_button(self) -> QPushButton:
        return self._add_btn

    @property
    def field_inputs(self) -> list[dict]:
        return self._field_inputs

    def add_field(
        self, key: str = "", value: str = "", mark_dirty_on_add: bool = True
    ) -> dict:
        row_layout = QHBoxLayout()

        field_data: dict = {"layout": row_layout}

        if self.mode == "key_value":
            key_input = QLineEdit()
            key_input.setText(str(key))
            key_input.setPlaceholderText(self._key_placeholder)
            key_input.textChanged.connect(lambda: self.dirty.emit())
            row_layout.addWidget(key_input)
            field_data["key_input"] = key_input

        value_input = QLineEdit()
        value_input.setText(str(value))
        if self.mode == "key_value":
            value_input.setPlaceholderText(self._value_placeholder)
        row_layout.addWidget(value_input)
        field_data["input"] = value_input

        remove_btn = QPushButton("Remove")
        remove_btn.setMaximumWidth(80)
        remove_btn.setStyleSheet(self._style_provider.get_button_style("red"))
        row_layout.addWidget(remove_btn)
        field_data["remove_btn"] = remove_btn

        self._layout.insertLayout(len(self._field_inputs), row_layout)
        self._field_inputs.append(field_data)

        remove_btn.clicked.connect(lambda: self.remove_field(field_data))

        if mark_dirty_on_add:
            self.dirty.emit()

        return field_data

    def remove_field(self, field_data: dict):
        self._layout.removeItem(field_data["layout"])
        field_data["input"].deleteLater()
        field_data["remove_btn"].deleteLater()
        if self.mode == "key_value":
            field_data["key_input"].deleteLater()
        self._field_inputs.remove(field_data)
        self.dirty.emit()

    def clear_fields(self):
        while self._field_inputs:
            self.remove_field(self._field_inputs[0])

    def collect_dict(self):
        result = {}
        if self.mode == "key_value":
            for field_data in self._field_inputs:
                key = field_data["key_input"].text().strip()
                val = field_data["input"].text().strip()
                if key:
                    result[key] = val
        return result

    def collect_list(self):
        result = []
        if self.mode == "single_value":
            for field_data in self._field_inputs:
                val = field_data["input"].text().strip()
                if val:
                    result.append(val)
        return result

    def set_enabled(self, enabled: bool):
        self._add_btn.setEnabled(enabled)
        for field_data in self._field_inputs:
            field_data["input"].setEnabled(enabled)
            field_data["remove_btn"].setEnabled(enabled)
            if self.mode == "key_value":
                field_data["key_input"].setEnabled(enabled)

    def set_visible(self, visible: bool):
        self._group.setVisible(visible)
        self._add_btn.setVisible(visible)
        for field_data in self._field_inputs:
            field_data["input"].setVisible(visible)
            field_data["remove_btn"].setVisible(visible)
            if self.mode == "key_value":
                field_data["key_input"].setVisible(visible)
