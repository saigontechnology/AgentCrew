from typing import ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.gui.themes import StyleProvider


class CustomPasswordStyle(QProxyStyle):
    """
    A custom QStyle to change the password character in QLineEdit.
    """

    def __init__(self, style=None):
        super().__init__(style)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        """
        Overrides the default password character for QLineEdit.
        """
        if hint == QStyle.StyleHint.SH_LineEdit_PasswordCharacter:
            return ord("•")
        return super().styleHint(hint, option, widget, returnData)


class SettingsTab(QWidget):
    """Tab for configuring global settings like API keys."""

    config_changed = Signal()

    API_KEY_DEFINITIONS: ClassVar[list[dict[str, str]]] = [
        {
            "label": "Anthropic API Key:",
            "key_name": "ANTHROPIC_API_KEY",
            "placeholder": "e.g., sk-ant-...",
        },
        {
            "label": "Gemini API Key:",
            "key_name": "GEMINI_API_KEY",
            "placeholder": "e.g., AIzaSy...",
        },
        {
            "label": "OpenAI API Key:",
            "key_name": "OPENAI_API_KEY",
            "placeholder": "e.g., sk-...",
        },
        {
            "label": "DeepInfra API Key:",
            "key_name": "DEEPINFRA_API_KEY",
            "placeholder": "e.g., ...",
        },
        {
            "label": "Together API Key:",
            "key_name": "TOGETHER_API_KEY",
            "placeholder": "e.g., ...",
        },
        {
            "label": "Fireworks API Key:",
            "key_name": "FIREWORKS_API_KEY",
            "placeholder": "e.g., ...",
        },
        {
            "label": "OpenCode API Key:",
            "key_name": "OPENCODE_API_KEY",
            "placeholder": "e.g., ...",
        },
        {
            "label": "Github Copilot API Key:",
            "key_name": "GITHUB_COPILOT_API_KEY",
            "placeholder": "e.g., ...",
        },
        {
            "label": "Tavily API Key:",
            "key_name": "TAVILY_API_KEY",
            "placeholder": "e.g., tvly-...",
        },
        {
            "label": "Voyage API Key:",
            "key_name": "VOYAGE_API_KEY",
            "placeholder": "e.g., pa-...",
        },
        {
            "label": "ElevenLabs API Key:",
            "key_name": "ELEVENLABS_API_KEY",
            "placeholder": "e.g., ...",
        },
    ]

    def __init__(self, config_manager: ConfigManagement):
        super().__init__()
        self.config_manager = config_manager
        self.global_config = GlobalConfig().read()

        self.api_key_inputs = {}
        self.theme_dropdown = None
        self.yolo_mode_checkbox = None
        self.auto_context_shrink_checkbox = None
        self.shrink_excluded_input = None

        self.init_ui()
        self.load_api_keys()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        style_provider = StyleProvider()
        scroll_area.setStyleSheet(style_provider.get_sidebar_style())

        editor_widget = QWidget()
        editor_widget.setStyleSheet(style_provider.get_sidebar_style())
        form_layout = QFormLayout(editor_widget)
        form_layout.setContentsMargins(10, 10, 10, 10)
        form_layout.setSpacing(10)

        # Global Settings Group
        global_settings_group = QGroupBox("Global Settings")
        global_settings_group.setStyleSheet(style_provider.get_api_keys_group_style())
        global_settings_form_layout = QFormLayout()

        # Theme dropdown
        theme_label = QLabel("Theme:")
        self.theme_dropdown = QComboBox()
        from AgentCrew.modules.gui.themes import ThemeLoader

        self.theme_dropdown.addItems(sorted(ThemeLoader.get_available_themes()))
        self.theme_dropdown.setCurrentText("catppuccin")
        self.theme_dropdown.setStyleSheet(style_provider.get_combo_box_style())
        global_settings_form_layout.addRow(theme_label, self.theme_dropdown)

        # YOLO Mode checkbox
        yolo_label = QLabel("YOLO Mode:")
        self.yolo_mode_checkbox = QCheckBox()
        self.yolo_mode_checkbox.setChecked(False)  # Default to unchecked
        global_settings_form_layout.addRow(yolo_label, self.yolo_mode_checkbox)

        # Auto Context Shrink checkbox
        auto_context_shrink_label = QLabel("Auto Context Shrink:")
        self.auto_context_shrink_checkbox = QCheckBox()
        self.auto_context_shrink_checkbox.setChecked(False)  # Default to unchecked
        global_settings_form_layout.addRow(
            auto_context_shrink_label, self.auto_context_shrink_checkbox
        )

        # Shrink Excluded Tools input
        shrink_excluded_label = QLabel("Shrink Excluded Tools:")
        self.shrink_excluded_input = QLineEdit()
        self.shrink_excluded_input.setPlaceholderText(
            "e.g., web_search, code_analysis, browser_navigate"
        )
        self.shrink_excluded_input.setStyleSheet(style_provider.get_input_style())
        global_settings_form_layout.addRow(
            shrink_excluded_label, self.shrink_excluded_input
        )

        global_settings_group.setLayout(global_settings_form_layout)
        form_layout.addWidget(global_settings_group)

        api_keys_group = QGroupBox("API Keys")
        api_keys_group.setStyleSheet(style_provider.get_api_keys_group_style())
        api_keys_form_layout = QFormLayout()
        custom_style = CustomPasswordStyle()

        for item in self.API_KEY_DEFINITIONS:
            label = QLabel(item["label"])
            line_edit = QLineEdit()
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            line_edit.setStyle(custom_style)
            line_edit.setPlaceholderText(item["placeholder"])
            self.api_key_inputs[item["key_name"]] = line_edit
            api_keys_form_layout.addRow(label, line_edit)

        api_keys_group.setLayout(api_keys_form_layout)
        form_layout.addWidget(api_keys_group)

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet(style_provider.get_button_style("primary"))
        self.save_btn.clicked.connect(self.save_settings)

        form_layout.addWidget(self.save_btn)
        editor_widget.setLayout(form_layout)
        scroll_area.setWidget(editor_widget)
        main_layout.addWidget(scroll_area)

        self.setLayout(main_layout)

    def load_api_keys(self):
        api_keys_data = self.global_config.get("api_keys", {})
        for key_name, line_edit in self.api_key_inputs.items():
            line_edit.setText(api_keys_data.get(key_name, ""))

        self.load_global_settings()

    def load_global_settings(self):
        global_settings_data = self.global_config.get("global_settings", {})

        # Load theme setting
        theme = global_settings_data.get("theme", "dark")
        if self.theme_dropdown:
            self.theme_dropdown.setCurrentText(theme)

        # Load YOLO mode setting
        yolo_mode = global_settings_data.get("yolo_mode", False)
        if self.yolo_mode_checkbox:
            self.yolo_mode_checkbox.setChecked(yolo_mode)

        # Load Auto Context Shrink setting
        auto_context_shrink = global_settings_data.get("auto_context_shrink", False)
        if self.auto_context_shrink_checkbox:
            self.auto_context_shrink_checkbox.setChecked(auto_context_shrink)

        # Load Shrink Excluded Tools setting
        shrink_excluded = global_settings_data.get("shrink_excluded", [])
        if self.shrink_excluded_input:
            # Convert array to comma-separated string
            shrink_excluded_str = ", ".join(shrink_excluded) if shrink_excluded else ""
            self.shrink_excluded_input.setText(shrink_excluded_str)

    def save_settings(self):
        if "api_keys" not in self.global_config:
            self.global_config["api_keys"] = {}

        for key_name, line_edit in self.api_key_inputs.items():
            self.global_config["api_keys"][key_name] = line_edit.text().strip()

        # Save global settings
        if "global_settings" not in self.global_config:
            self.global_config["global_settings"] = {}

        self.global_config["global_settings"]["theme"] = (
            self.theme_dropdown.currentText() if self.theme_dropdown else "dark"
        )
        self.global_config["global_settings"]["yolo_mode"] = (
            self.yolo_mode_checkbox.isChecked() if self.yolo_mode_checkbox else False
        )
        self.global_config["global_settings"]["auto_context_shrink"] = (
            self.auto_context_shrink_checkbox.isChecked()
            if self.auto_context_shrink_checkbox
            else False
        )

        # Save Shrink Excluded Tools setting
        shrink_excluded_str = (
            self.shrink_excluded_input.text().strip()
            if self.shrink_excluded_input
            else ""
        )
        if shrink_excluded_str:
            # Convert comma-separated string to array, trim whitespace from each item
            shrink_excluded_list = [
                tool.strip() for tool in shrink_excluded_str.split(",") if tool.strip()
            ]
        else:
            shrink_excluded_list = []
        self.global_config["global_settings"]["shrink_excluded"] = shrink_excluded_list

        try:
            # Save the configuration
            GlobalConfig().write(self.global_config)

            # Get the style provider and update the theme
            style_provider = StyleProvider()
            theme_changed = style_provider.update_theme()

            # Show different message based on whether theme changed
            self.config_changed.emit()
            if theme_changed:
                QMessageBox.information(
                    self,
                    "Settings Saved",
                    "Settings saved successfully. The theme has been updated immediately.\n\n"
                    "Some components may require a restart to fully apply the new theme.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Settings Saved",
                    "Settings saved successfully.",
                )

        except Exception as e:
            QMessageBox.critical(
                self, "Error Saving Settings", f"Could not save settings: {e!s}"
            )
