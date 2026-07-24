import os
from typing import Any

import yaml


class ThemeLoader:
    _cache: dict[str, "ThemeData"] = {}
    _themes_dir: str | None = None

    @classmethod
    def get_themes_dir(cls) -> str:
        if cls._themes_dir is None:
            cls._themes_dir = os.path.dirname(os.path.abspath(__file__))
        return cls._themes_dir

    @classmethod
    def load_theme(cls, theme_name: str) -> "ThemeData":
        if theme_name in cls._cache:
            return cls._cache[theme_name]

        theme_path = os.path.join(cls.get_themes_dir(), f"{theme_name}.yaml")
        if not os.path.exists(theme_path):
            theme_path = os.path.join(cls.get_themes_dir(), "catppuccin.yaml")

        with open(theme_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        theme = ThemeData(data)
        cls._cache[theme_name] = theme
        return theme

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    @classmethod
    def get_available_themes(cls) -> list:
        themes_dir = cls.get_themes_dir()
        return [
            f.replace(".yaml", "")
            for f in os.listdir(themes_dir)
            if f.endswith(".yaml")
        ]


class ThemeData:
    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._colors = data.get("colors", {})
        self._styles = data.get("styles", {})
        self._icons = data.get("icons", {})
        self._computed_cache: dict[str, str] = {}

    def _interpolate(self, template: str) -> str:
        if template in self._computed_cache:
            return self._computed_cache[template]

        result = template
        for key, value in self._colors.items():
            result = result.replace(f"${{{key}}}", value)

        self._computed_cache[template] = result
        return result

    def get_style(self, name: str, default: str = "") -> str:
        style = self._styles.get(name, default)
        if isinstance(style, str):
            return self._interpolate(style)
        return default

    def get_color(self, name: str, default: str = "") -> str:
        return self._colors.get(name, default)

    def get_icon(self, name: str, default: str = "\U0001f527") -> str:
        return self._icons.get(name, self._icons.get("default", default))

    def get_dict(self, name: str) -> dict[str, Any]:
        value = self._styles.get(name, {})
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if isinstance(v, str):
                    result[k] = self._interpolate(v)
                else:
                    result[k] = v
            return result
        return {}

    @property
    def MAIN_STYLE(self) -> str:
        return self.get_style("main")

    @property
    def PRIMARY_BUTTON(self) -> str:
        return self.get_style("button_primary")

    @property
    def SECONDARY_BUTTON(self) -> str:
        return self.get_style("button_secondary")

    @property
    def STOP_BUTTON(self) -> str:
        return self.get_style("button_stop")

    @property
    def STOP_BUTTON_STOPPING(self) -> str:
        return self.get_style("button_stop_stopping")

    @property
    def RED_BUTTON(self) -> str:
        return self.get_style("button_red")

    @property
    def GREEN_BUTTON(self) -> str:
        return self.get_style("button_green")

    @property
    def DISABLED_BUTTON(self) -> str:
        return self.get_style("button_disabled")

    @property
    def MENU_BUTTON(self) -> str:
        return self.get_style("button_menu")

    @property
    def AGENT_MENU_BUTTON(self) -> str:
        return self.get_style("button_agent_menu")

    @property
    def ROLLBACK_BUTTON(self) -> str:
        return self.get_style("button_rollback")

    @property
    def CONSOLIDATED_BUTTON(self) -> str:
        return self.get_style("button_consolidated")

    @property
    def UNCONSOLIDATE_BUTTON(self) -> str:
        return self.get_style("button_unconsolidate")

    @property
    def API_KEYS_GROUP(self) -> str:
        return self.get_style("api_keys_group")

    @property
    def EDITOR_CONTAINER_WIDGET(self) -> str:
        return self.get_style("editor_container_widget")

    @property
    def COMBO_BOX(self) -> str:
        return self.get_style("combo_box")

    @property
    def TEXT_INPUT(self) -> str:
        return self.get_style("text_input")

    @property
    def LINE_EDIT(self) -> str:
        return self.get_style("line_edit")

    @property
    def MENU_BAR(self) -> str:
        return self.get_style("menu_bar")

    @property
    def CONTEXT_MENU(self) -> str:
        return self.get_style("context_menu")

    @property
    def AGENT_MENU(self) -> str:
        return self.get_style("agent_menu")

    @property
    def STATUS_INDICATOR(self) -> str:
        return self.get_style("status_indicator")

    @property
    def VERSION_LABEL(self) -> str:
        return self.get_style("version_label")

    @property
    def SYSTEM_MESSAGE_TOGGLE_BUTTON(self) -> str:
        return self.get_style("system_message_toggle_button")

    @property
    def SIDEBAR(self) -> str:
        return self.get_style("sidebar")

    @property
    def CONVERSATION_LIST(self) -> str:
        return self.get_style("conversation_list")

    @property
    def SEARCH_BOX(self) -> str:
        return self.get_style("search_box")

    @property
    def TOKEN_USAGE(self) -> str:
        return self.get_style("token_usage")

    @property
    def TOKEN_USAGE_WIDGET(self) -> str:
        return self.get_style("token_usage_widget")

    @property
    def USER_BUBBLE(self) -> str:
        return self.get_style("user_bubble")

    @property
    def ASSISTANT_BUBBLE(self) -> str:
        return self.get_style("assistant_bubble")

    @property
    def THINKING_BUBBLE(self) -> str:
        return self.get_style("thinking_bubble")

    @property
    def CONSOLIDATED_BUBBLE(self) -> str:
        return self.get_style("consolidated_bubble")

    @property
    def TOOL_DIALOG_TEXT_EDIT(self) -> str:
        return self.get_style("tool_dialog_text_edit")

    @property
    def TOOL_DIALOG_YES_BUTTON(self) -> str:
        return self.get_style("tool_dialog_yes_button")

    @property
    def TOOL_DIALOG_ALL_BUTTON(self) -> str:
        return self.get_style("tool_dialog_all_button")

    @property
    def TOOL_DIALOG_NO_BUTTON(self) -> str:
        return self.get_style("tool_dialog_no_button")

    @property
    def SYSTEM_MESSAGE_LABEL(self) -> str:
        return self.get_style("system_message_label")

    @property
    def SYSTEM_MESSAGE_TOGGLE(self) -> str:
        return self.get_style("system_message_toggle")

    @property
    def CONFIG_DIALOG(self) -> str:
        return self.get_style("config_dialog")

    @property
    def PANEL(self) -> str:
        return self.get_style("panel")

    @property
    def SCROLL_AREA(self) -> str:
        return self.get_style("scroll_area")

    @property
    def EDITOR_WIDGET(self) -> str:
        return self.get_style("editor_widget")

    @property
    def GROUP_BOX(self) -> str:
        return self.get_style("group_box")

    @property
    def SPLITTER_COLOR(self) -> str:
        return self.get_style("splitter_color")

    @property
    def METADATA_HEADER_LABEL(self) -> str:
        return self.get_style("metadata_header_label")

    @property
    def USER_MESSAGE_LABEL(self) -> str:
        return self.get_style("user_message_label")

    @property
    def ASSISTANT_MESSAGE_LABEL(self) -> str:
        return self.get_style("assistant_message_label")

    @property
    def THINKING_MESSAGE_LABEL(self) -> str:
        return self.get_style("thinking_message_label")

    @property
    def USER_SENDER_LABEL(self) -> str:
        return self.get_style("user_sender_label")

    @property
    def ASSISTANT_SENDER_LABEL(self) -> str:
        return self.get_style("assistant_sender_label")

    @property
    def THINKING_SENDER_LABEL(self) -> str:
        return self.get_style("thinking_sender_label")

    @property
    def USER_FILE_NAME_LABEL(self) -> str:
        return self.get_style("user_file_name_label")

    @property
    def ASSISTANT_FILE_NAME_LABEL(self) -> str:
        return self.get_style("assistant_file_name_label")

    @property
    def USER_FILE_INFO_LABEL(self) -> str:
        return self.get_style("user_file_info_label")

    @property
    def ASSISTANT_FILE_INFO_LABEL(self) -> str:
        return self.get_style("assistant_file_info_label")

    @property
    def CODE_CSS(self) -> str:
        return self.get_style("code_css")

    @property
    def CHECKBOX_STYLE(self) -> str:
        return self.get_style("checkbox")

    @property
    def TOOL_WIDGET(self) -> str:
        return self.get_style("tool_widget")

    @property
    def TOOL_CARD(self) -> str:
        return self.get_style("tool_card")

    @property
    def TOOL_CARD_ERROR(self) -> str:
        return self.get_style("tool_card_error")

    @property
    def TOOL_HEADER(self) -> str:
        return self.get_style("tool_header")

    @property
    def TOOL_TOGGLE_BUTTON(self) -> str:
        return self.get_style("tool_toggle_button")

    @property
    def TOOL_STATUS(self) -> str:
        return self.get_style("tool_status")

    @property
    def TOOL_CONTENT(self) -> str:
        return self.get_style("tool_content")

    @property
    def TOOL_PROGRESS(self) -> str:
        return self.get_style("tool_progress")

    @property
    def TOOL_SEPARATOR(self) -> str:
        return self.get_style("tool_separator")

    @property
    def AVATAR_BG(self) -> str:
        return self.get_style("avatar_bg")

    @property
    def AVATAR_TEXT(self) -> str:
        return self.get_style("avatar_text")

    @property
    def CHAT_CONTAINER_BG(self) -> str:
        return self.get_style("chat_container_bg")

    @property
    def INPUT_CONTAINER(self) -> str:
        return self.get_style("input_container")

    @property
    def INPUT_CONTAINER_FOCUS(self) -> str:
        return self.get_style("input_container_focus")

    @property
    def TOOL_ICONS(self) -> dict[str, str]:
        return self._icons

    @property
    def DIFF_COLORS(self) -> dict[str, str]:
        return self.get_dict("diff_colors")

    @property
    def JSON_EDITOR_COLORS(self) -> dict[str, str]:
        return self.get_dict("json_editor_colors")

    @property
    def JSON_EDITOR_STYLE(self) -> str:
        return self.get_style("json_editor")

    @property
    def MARKDOWN_EDITOR_COLORS(self) -> dict[str, str]:
        return self.get_dict("markdown_editor_colors")

    @property
    def MARKDOWN_EDITOR_STYLE(self) -> str:
        return self.get_style("markdown_editor")
