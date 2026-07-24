from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.gui.themes import StyleProvider
from AgentCrew.modules.gui.widgets.markdown_editor import MarkdownEditor

from .behavior_editor import BehaviorEditor


class LocalAgentEditor(QWidget):
    """Composite widget for editing local agent configuration."""

    field_changed = Signal()

    def __init__(
        self,
        available_tools: list[str],
        persistence_service,
        on_dirty_callback: Callable[[], None] | None = None,
        get_current_agent_name: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.available_tools = available_tools
        self.persistence_service = persistence_service
        self._on_dirty_callback = on_dirty_callback
        self._get_current_agent_name = get_current_agent_name or (lambda: "")
        self._init_ui()

    def _init_ui(self):
        style_provider = StyleProvider()

        layout = QVBoxLayout(self)

        self.local_agent_tab_widget = QTabWidget()

        # --- General Tab ---
        self.general_tab = QWidget()
        general_tab_layout = QVBoxLayout(self.general_tab)
        local_form_layout = QFormLayout()

        self.name_input = QLineEdit()
        local_form_layout.addRow("Name:", self.name_input)

        self.description_input = QLineEdit()
        local_form_layout.addRow("Description:", self.description_input)

        self.temperature_input = QLineEdit()
        self.temperature_input.setValidator(QDoubleValidator(0.0, 2.0, 1))
        self.temperature_input.setPlaceholderText("0.0 - 2.0")
        local_form_layout.addRow("Temperature:", self.temperature_input)

        self.model_id_combo = QComboBox()
        self.model_id_combo.setEditable(True)
        self.model_id_combo.setPlaceholderText("(use global model)")
        self._populate_model_id_combo()
        local_form_layout.addRow("Model ID:", self.model_id_combo)

        self.enabled_checkbox = QCheckBox("Enabled")
        self.enabled_checkbox.setChecked(True)
        local_form_layout.addRow("", self.enabled_checkbox)

        # Voice Settings
        self.voice_group = QGroupBox("Voice Settings")
        voice_layout = QFormLayout()

        self.voice_enabled_checkbox = QCheckBox("Voice Enabled")
        self.voice_enabled_checkbox.setStyleSheet(style_provider.get_checkbox_style())
        self.voice_enabled_checkbox.setToolTip(
            "Enable or disable voice features for this agent."
        )
        voice_layout.addRow("", self.voice_enabled_checkbox)

        self.voice_id_input = QLineEdit()
        self.voice_id_input.setPlaceholderText("e.g., kHhWB9Fw3aF6ly7JvltC")
        voice_layout.addRow("Voice ID:", self.voice_id_input)

        self.voice_group.setLayout(voice_layout)

        # Tools
        self.tools_group = QGroupBox("Tools")
        tools_layout = QVBoxLayout()
        self.tool_checkboxes = {}
        for tool in self.available_tools:
            checkbox = QCheckBox(tool)
            self.tool_checkboxes[tool] = checkbox
            tools_layout.addWidget(checkbox)
        self.tools_group.setLayout(tools_layout)

        general_tab_layout.addLayout(local_form_layout)
        general_tab_layout.addWidget(self.voice_group)
        general_tab_layout.addWidget(self.tools_group)
        general_tab_layout.addStretch()

        # --- System Prompt Tab ---
        self.system_prompt_tab = QWidget()
        system_prompt_tab_layout = QVBoxLayout(self.system_prompt_tab)

        system_prompt_label = QLabel("System Prompt:")
        system_prompt_label.setStyleSheet("font-weight: bold; margin-bottom: 5px;")

        self.system_prompt_input = MarkdownEditor()
        self.system_prompt_input.setMinimumHeight(400)
        self.system_prompt_input.clear()

        system_prompt_tab_layout.addWidget(system_prompt_label)
        system_prompt_tab_layout.addWidget(self.system_prompt_input, 1)

        # --- Behaviors Tab ---
        self.behaviors_tab = QWidget()
        behaviors_tab_layout = QVBoxLayout(self.behaviors_tab)

        self.behavior_editor = BehaviorEditor(
            persistence_service=self.persistence_service,
            on_dirty_callback=self._on_field_changed,
            get_current_agent_name=self._get_current_agent_name,
        )
        self.behavior_editor.behavior_changed.connect(self._on_field_changed)

        behaviors_tab_layout.addWidget(self.behavior_editor)

        # Add tabs
        self.local_agent_tab_widget.addTab(self.general_tab, "General")
        self.local_agent_tab_widget.addTab(self.system_prompt_tab, "System Prompt")
        self.local_agent_tab_widget.addTab(self.behaviors_tab, "Behaviors")

        layout.addWidget(self.local_agent_tab_widget)

        # Connect signals
        self.name_input.textChanged.connect(self._on_field_changed)
        self.description_input.textChanged.connect(self._on_field_changed)
        self.temperature_input.textChanged.connect(self._on_field_changed)
        self.model_id_combo.currentTextChanged.connect(self._on_field_changed)
        self.system_prompt_input.markdown_changed.connect(self._on_field_changed)
        self.enabled_checkbox.stateChanged.connect(self._on_field_changed)
        self.voice_enabled_checkbox.stateChanged.connect(self._on_field_changed)
        self.voice_id_input.textChanged.connect(self._on_field_changed)
        for checkbox in self.tool_checkboxes.values():
            checkbox.stateChanged.connect(self._on_field_changed)

    def _populate_model_id_combo(self):
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        self.model_id_combo.clear()
        self.model_id_combo.addItem("")
        try:
            registry = ModelRegistry.get_instance()
            for provider in registry.get_providers():
                for model in registry.get_models_by_provider(provider):
                    self.model_id_combo.addItem(f"{model.provider}/{model.id}")
        except Exception:
            pass

    def _on_field_changed(self):
        self.field_changed.emit()
        if self._on_dirty_callback:
            self._on_dirty_callback()

    def setEnabled(self, enabled: bool):
        """Enable or disable all local agent fields."""
        self.local_agent_tab_widget.setEnabled(enabled)
        self.name_input.setEnabled(enabled)
        self.description_input.setEnabled(enabled)
        self.temperature_input.setEnabled(enabled)
        self.model_id_combo.setEnabled(enabled)
        self.system_prompt_input.setEnabled(enabled)
        self.enabled_checkbox.setEnabled(enabled)
        for checkbox in self.tool_checkboxes.values():
            checkbox.setEnabled(enabled)
        self.behavior_editor.setEnabled(enabled)
        super().setEnabled(enabled)

    def clear(self):
        """Clear all local agent fields to defaults."""
        self.name_input.clear()
        self.description_input.clear()
        self.temperature_input.clear()
        self.model_id_combo.setCurrentText("")
        self.system_prompt_input.clear()
        self.enabled_checkbox.setChecked(True)
        self.voice_enabled_checkbox.setChecked(False)
        self.voice_id_input.clear()
        for checkbox in self.tool_checkboxes.values():
            checkbox.setChecked(False)
        self.behavior_editor.clear()

    def populate(self, agent_data: dict):
        """Populate fields from agent config data."""
        self.name_input.setText(agent_data.get("name", ""))
        self.description_input.setText(agent_data.get("description", ""))
        self.temperature_input.setText(str(agent_data.get("temperature", "0.5")))
        self.model_id_combo.setCurrentText(agent_data.get("model_id", ""))
        self.enabled_checkbox.setChecked(agent_data.get("enabled", True))

        voice_state = agent_data.get("voice_enabled", "disabled")
        self.voice_enabled_checkbox.setChecked(voice_state == "enabled")
        self.voice_id_input.setText(agent_data.get("voice_id", ""))

        tools = agent_data.get("tools", [])
        for tool, checkbox in self.tool_checkboxes.items():
            checkbox.setChecked(tool in tools)

        self.system_prompt_input.set_markdown(agent_data.get("system_prompt", ""))

    def collect(self) -> dict:
        """Collect the current field values into a config dict."""
        try:
            temperature = float(self.temperature_input.text().strip() or "0.5")
            temperature = max(0.0, min(2.0, temperature))
        except ValueError:
            temperature = 0.5

        voice_state = (
            "enabled" if self.voice_enabled_checkbox.isChecked() else "disabled"
        )

        return {
            "name": self.name_input.text().strip(),
            "description": self.description_input.text().strip(),
            "temperature": temperature,
            "tools": [
                tool
                for tool, checkbox in self.tool_checkboxes.items()
                if checkbox.isChecked()
            ],
            "system_prompt": self.system_prompt_input.get_markdown().strip(),
            "enabled": self.enabled_checkbox.isChecked(),
            "voice_enabled": voice_state,
            "voice_id": self.voice_id_input.text().strip(),
            "model_id": self.model_id_combo.currentText().strip() or None,
            "agent_type": "local",
        }
