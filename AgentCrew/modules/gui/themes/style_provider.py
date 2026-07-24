from PySide6.QtCore import QObject, Signal

from AgentCrew.modules.config.global_config import GlobalConfig

from .theme_loader import ThemeData, ThemeLoader


class StyleProvider(QObject):
    theme_changed = Signal(str)
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        super().__init__()
        self._initialized = True

        global_config = GlobalConfig().read()
        self.theme = global_config.get("global_settings", {}).get("theme", "catppuccin")

        self._set_theme_class()

    def _set_theme_class(self):
        self.theme_class: ThemeData = ThemeLoader.load_theme(self.theme)

    def update_theme(self, reload=True):
        if reload:
            global_config = GlobalConfig().read()
            new_theme = global_config.get("global_settings", {}).get(
                "theme", "catppuccin"
            )

            if new_theme != self.theme:
                self.theme = new_theme
                ThemeLoader.clear_cache()
                self._set_theme_class()
                self.theme_changed.emit(self.theme)
                return True
        return False

    def _get_attr(self, name: str, default: str = "") -> str:
        return getattr(self.theme_class, name, default)

    def get_main_style(self):
        return self._get_attr("MAIN_STYLE")

    def get_config_window_style(self):
        return self._get_attr("CONFIG_DIALOG")

    def get_button_style(self, button_type="primary"):
        button_map = {
            "primary": "PRIMARY_BUTTON",
            "secondary": "SECONDARY_BUTTON",
            "stop": "STOP_BUTTON",
            "disabled": "DISABLED_BUTTON",
            "stop_stopping": "STOP_BUTTON_STOPPING",
            "red": "RED_BUTTON",
            "green": "GREEN_BUTTON",
            "agent_menu": "AGENT_MENU_BUTTON",
        }
        attr_name = button_map.get(button_type, "")
        return self._get_attr(attr_name) if attr_name else ""

    def get_input_style(self):
        return self._get_attr("TEXT_INPUT")

    def get_menu_style(self):
        return self._get_attr("MENU_BAR")

    def get_status_indicator_style(self):
        return self._get_attr("STATUS_INDICATOR")

    def get_version_label_style(self):
        return self._get_attr("VERSION_LABEL")

    def get_tool_dialog_text_edit_style(self):
        return self._get_attr("TOOL_DIALOG_TEXT_EDIT")

    def get_tool_dialog_yes_button_style(self):
        return self._get_attr("TOOL_DIALOG_YES_BUTTON")

    def get_tool_dialog_all_button_style(self):
        return self._get_attr("TOOL_DIALOG_ALL_BUTTON")

    def get_tool_dialog_no_button_style(self):
        return self._get_attr("TOOL_DIALOG_NO_BUTTON")

    def get_system_message_label_style(self):
        return self._get_attr("SYSTEM_MESSAGE_LABEL")

    def get_system_message_toggle_style(self):
        return self._get_attr("SYSTEM_MESSAGE_TOGGLE")

    def get_sidebar_style(self):
        return self._get_attr("SIDEBAR")

    def get_conversation_list_style(self):
        return self._get_attr("CONVERSATION_LIST")

    def get_search_box_style(self):
        return self._get_attr("SEARCH_BOX")

    def get_token_usage_style(self):
        return self._get_attr("TOKEN_USAGE")

    def get_token_usage_widget_style(self):
        return self._get_attr("TOKEN_USAGE_WIDGET")

    def get_context_menu_style(self):
        return self._get_attr("CONTEXT_MENU")

    def get_agent_menu_style(self):
        return self._get_attr("AGENT_MENU")

    def get_user_bubble_style(self):
        return self._get_attr("USER_BUBBLE")

    def get_assistant_bubble_style(self):
        return self._get_attr("ASSISTANT_BUBBLE")

    def get_thinking_bubble_style(self):
        return self._get_attr("THINKING_BUBBLE")

    def get_consolidated_bubble_style(self):
        return self._get_attr("CONSOLIDATED_BUBBLE")

    def get_splitter_style(self):
        return self._get_attr("SPLITTER_COLOR")

    def get_code_color_style(self):
        return self._get_attr("CODE_CSS")

    def get_rollback_button_style(self):
        return self._get_attr("ROLLBACK_BUTTON")

    def get_consolidated_button_style(self):
        return self._get_attr("CONSOLIDATED_BUTTON")

    def get_unconsolidate_button_style(self):
        return self._get_attr("UNCONSOLIDATE_BUTTON")

    def get_user_message_label_style(self):
        return self._get_attr("USER_MESSAGE_LABEL")

    def get_assistant_message_label_style(self):
        return self._get_attr("ASSISTANT_MESSAGE_LABEL")

    def get_thinking_message_label_style(self):
        return self._get_attr("THINKING_MESSAGE_LABEL")

    def get_user_sender_label_style(self):
        return self._get_attr("USER_SENDER_LABEL")

    def get_assistant_sender_label_style(self):
        return self._get_attr("ASSISTANT_SENDER_LABEL")

    def get_thinking_sender_label_style(self):
        return self._get_attr("THINKING_SENDER_LABEL")

    def get_metadata_header_label_style(self):
        return self._get_attr("METADATA_HEADER_LABEL")

    def get_user_file_name_label_style(self):
        return self._get_attr("USER_FILE_NAME_LABEL")

    def get_assistant_file_name_label_style(self):
        return self._get_attr("ASSISTANT_FILE_NAME_LABEL")

    def get_user_file_info_label_style(self):
        return self._get_attr("USER_FILE_INFO_LABEL")

    def get_assistant_file_info_label_style(self):
        return self._get_attr("ASSISTANT_FILE_INFO_LABEL")

    def get_api_keys_group_style(self):
        return self._get_attr("API_KEYS_GROUP")

    def get_editor_container_widget_style(self):
        return self._get_attr("EDITOR_CONTAINER_WIDGET")

    def get_combo_box_style(self):
        return self._get_attr("COMBO_BOX")

    def get_checkbox_style(self):
        return self._get_attr("CHECKBOX_STYLE", "")

    def get_tool_widget_style(self):
        return self._get_attr("TOOL_WIDGET", "")

    def get_tool_card_style(self):
        return self._get_attr("TOOL_CARD", "")

    def get_tool_card_error_style(self):
        return self._get_attr("TOOL_CARD_ERROR", "")

    def get_tool_header_style(self):
        return self._get_attr("TOOL_HEADER", "")

    def get_tool_toggle_button_style(self):
        return self._get_attr("TOOL_TOGGLE_BUTTON", "")

    def get_tool_status_style(self):
        return self._get_attr("TOOL_STATUS", "")

    def get_tool_content_style(self):
        return self._get_attr("TOOL_CONTENT", "")

    def get_tool_progress_style(self):
        return self._get_attr("TOOL_PROGRESS", "")

    def get_tool_separator_style(self):
        return self._get_attr("TOOL_SEPARATOR", "")

    def get_tool_icon(self, tool_name):
        return self.theme_class.get_icon(tool_name, "\U0001f527")

    def get_json_editor_colors(self):
        return self.theme_class.JSON_EDITOR_COLORS

    def get_json_editor_style(self):
        return self.theme_class.JSON_EDITOR_STYLE

    def get_markdown_editor_colors(self):
        return self.theme_class.MARKDOWN_EDITOR_COLORS

    def get_markdown_editor_style(self):
        return self.theme_class.MARKDOWN_EDITOR_STYLE

    def get_avatar_bg_style(self):
        return self._get_attr("AVATAR_BG")

    def get_avatar_text_style(self):
        return self._get_attr("AVATAR_TEXT")

    def get_chat_container_bg_style(self):
        return self._get_attr("CHAT_CONTAINER_BG")

    def get_input_container_style(self):
        return self._get_attr("INPUT_CONTAINER")

    def get_input_container_focus_style(self):
        return self._get_attr("INPUT_CONTAINER_FOCUS")

    def get_diff_colors(self):
        return self.theme_class.DIFF_COLORS
