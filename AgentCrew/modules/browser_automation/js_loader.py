"""
JavaScript file loader and template processor for browser automation.

This module provides utilities to load JavaScript files and inject variables
for use with Chrome DevTools Protocol.
"""

import time
from pathlib import Path
from typing import Any

from loguru import logger


class JavaScriptExecutor:
    """Handles JavaScript code execution and result parsing for browser automation."""

    @staticmethod
    def execute_and_parse_result(chrome_interface: Any, js_code: str) -> dict[str, Any]:
        """
        Execute JavaScript code and parse the result.

        Args:
            chrome_interface: Chrome DevTools Protocol interface
            js_code: JavaScript code to execute

        Returns:
            Parsed result dictionary
        """
        try:
            result = (None, [])
            retried = 0
            while result[0] is None and retried < 10:
                result = chrome_interface.Runtime.evaluate(
                    expression=js_code,
                    returnByValue=True,
                    awaitPromise=True,
                    timeout=60000,
                )
                retried += 1
                time.sleep(0.4)

            if isinstance(result, tuple) and len(result) >= 2:
                if isinstance(result[1], dict):
                    return (
                        result[1].get("result", {}).get("result", {}).get("value", {})
                    )
                elif isinstance(result[1], list) and len(result[1]) > 0:
                    return (
                        result[1][0]
                        .get("result", {})
                        .get("result", {})
                        .get("value", {})
                    )
                else:
                    return {
                        "success": False,
                        "error": "Invalid response format from JavaScript execution",
                    }
            else:
                return {
                    "success": False,
                    "error": "No response from JavaScript execution",
                }

        except Exception as e:
            logger.error(f"JavaScript execution error: {e}")
            return {"success": False, "error": f"JavaScript execution error: {e!s}"}

    @staticmethod
    def get_current_url(chrome_interface: Any) -> str:
        """
        Get the current page URL.

        Args:
            chrome_interface: Chrome DevTools Protocol interface

        Returns:
            Current URL or "Unknown" if retrieval fails
        """
        try:
            runtime_result = chrome_interface.Runtime.evaluate(
                expression="window.location.href"
            )

            if isinstance(runtime_result, tuple) and len(runtime_result) >= 2:
                if isinstance(runtime_result[1], dict):
                    current_url = (
                        runtime_result[1]
                        .get("result", {})
                        .get("result", {})
                        .get("value", "Unknown")
                    )
                elif isinstance(runtime_result[1], list) and len(runtime_result[1]) > 0:
                    current_url = (
                        runtime_result[1][0]
                        .get("result", {})
                        .get("result", {})
                        .get("value", "Unknown")
                    )
                else:
                    current_url = "Unknown"
            else:
                current_url = "Unknown"

            return current_url

        except Exception as e:
            logger.warning(f"Could not get current URL: {e}")
            return "Unknown"

    @staticmethod
    def focus_and_clear_element(chrome_interface: Any, xpath: str) -> dict[str, Any]:
        """
        Focus an element and clear its content.

        Args:
            chrome_interface: Chrome DevTools Protocol interface
            xpath: XPath selector for the element

        Returns:
            Result dictionary with success status
        """
        js_code = js_loader.get_focus_and_clear_element_js(xpath)
        return JavaScriptExecutor.execute_and_parse_result(chrome_interface, js_code)

    @staticmethod
    def draw_element_boxes(
        chrome_interface: Any, uuid_xpath_dict: dict[str, str]
    ) -> dict[str, Any]:
        """
        Draw colored rectangle boxes with UUID labels over elements.

        Args:
            uuid_xpath_dict: Dictionary mapping UUIDs to XPath selectors

        Returns:
            dict containing the result of the drawing operation
        """
        try:
            js_code = js_loader.get_draw_element_boxes_js(uuid_xpath_dict)
            eval_result = JavaScriptExecutor.execute_and_parse_result(
                chrome_interface, js_code
            )

            if not eval_result:
                return {
                    "success": False,
                    "error": "No result from drawing element boxes",
                }

            return eval_result

        except Exception as e:
            logger.error(f"Draw element boxes error: {e}")
            return {"success": False, "error": f"Draw element boxes error: {e!s}"}

    @staticmethod
    def remove_element_boxes(chrome_interface: Any) -> dict[str, Any]:
        """
        Remove the overlay container with element boxes.

        Returns:
            dict containing the result of the removal operation
        """
        try:
            js_code = js_loader.get_remove_element_boxes_js()
            eval_result = JavaScriptExecutor.execute_and_parse_result(
                chrome_interface, js_code
            )

            if not eval_result:
                return {
                    "success": False,
                    "error": "No result from removing element boxes",
                }

            return eval_result

        except Exception as e:
            logger.error(f"Remove element boxes error: {e}")
            return {"success": False, "error": f"Remove element boxes error: {e!s}"}

    @staticmethod
    def trigger_input_events(
        chrome_interface: Any, xpath: str, value: str
    ) -> dict[str, Any]:
        """
        Trigger input and change events on an element.

        Args:
            chrome_interface: Chrome DevTools Protocol interface
            xpath: XPath selector for the element
            value: Value to set

        Returns:
            Result dictionary with success status
        """
        js_code = js_loader.get_trigger_input_events_js(xpath, value)
        return JavaScriptExecutor.execute_and_parse_result(chrome_interface, js_code)

    @staticmethod
    def simulate_typing(chrome_interface: Any, text: str) -> dict[str, Any]:
        """
        Simulate keyboard typing character by character.

        Args:
            chrome_interface: Chrome DevTools Protocol interface
            text: Text to type

        Returns:
            Result dictionary with success status and characters typed
        """
        try:
            for char in text:
                time.sleep(0.05)

                if char == "\n":
                    chrome_interface.Input.dispatchKeyEvent(
                        type="rawKeyDown",
                        windowsVirtualKeyCode=13,
                        unmodifiedText="\r",
                        text="\r",
                    )
                    chrome_interface.Input.dispatchKeyEvent(
                        type="char",
                        windowsVirtualKeyCode=13,
                        unmodifiedText="\r",
                        text="\r",
                    )
                    chrome_interface.Input.dispatchKeyEvent(
                        type="keyUp",
                        windowsVirtualKeyCode=13,
                        unmodifiedText="\r",
                        text="\r",
                    )
                elif char == "\t":
                    chrome_interface.Input.dispatchKeyEvent(type="char", text="\t")
                else:
                    chrome_interface.Input.dispatchKeyEvent(type="char", text=char)

            return {
                "success": True,
                "message": f"Successfully typed {len(text)} characters",
                "characters_typed": len(text),
            }

        except Exception as e:
            logger.error(f"Error during typing simulation: {e}")
            return {"success": False, "error": f"Typing simulation failed: {e!s}"}

    @staticmethod
    def _get_key_definition(key: str) -> dict[str, Any] | None:
        """
        Get key definition for a given key.
        Handles both predefined special keys and dynamic alphanumeric keys.
        """
        key_name = key.lower().strip()

        if key_name in key_definitions:
            return key_definitions[key_name]

        if len(key) == 1:
            char = key
            char_lower = char.lower()
            char_upper = char.upper()

            if char_lower.isalpha():
                key_code = ord(char_upper)
                return {
                    "keyCode": key_code,
                    "key": char_lower,
                    "code": f"Key{char_upper}",
                    "text": char_lower,
                }
            elif char.isdigit():
                key_code = ord(char)
                return {
                    "keyCode": key_code,
                    "key": char,
                    "code": f"Digit{char}",
                    "text": char,
                }
            else:
                symbol_map = {
                    "`": {"keyCode": 192, "code": "Backquote", "key": "`", "text": "`"},
                    "-": {"keyCode": 189, "code": "Minus", "key": "-", "text": "-"},
                    "=": {"keyCode": 187, "code": "Equal", "key": "=", "text": "="},
                    "[": {
                        "keyCode": 219,
                        "code": "BracketLeft",
                        "key": "[",
                        "text": "[",
                    },
                    "]": {
                        "keyCode": 221,
                        "code": "BracketRight",
                        "key": "]",
                        "text": "]",
                    },
                    "\\": {
                        "keyCode": 220,
                        "code": "Backslash",
                        "key": "\\",
                        "text": "\\",
                    },
                    ";": {"keyCode": 186, "code": "Semicolon", "key": ";", "text": ";"},
                    "'": {"keyCode": 222, "code": "Quote", "key": "'", "text": "'"},
                    ",": {"keyCode": 188, "code": "Comma", "key": ",", "text": ","},
                    ".": {"keyCode": 190, "code": "Period", "key": ".", "text": "."},
                    "/": {"keyCode": 191, "code": "Slash", "key": "/", "text": "/"},
                    "~": {"keyCode": 192, "code": "Backquote", "key": "~", "text": "~"},
                    "!": {"keyCode": 49, "code": "Digit1", "key": "!", "text": "!"},
                    "@": {"keyCode": 50, "code": "Digit2", "key": "@", "text": "@"},
                    "#": {"keyCode": 51, "code": "Digit3", "key": "#", "text": "#"},
                    "$": {"keyCode": 52, "code": "Digit4", "key": "$", "text": "$"},
                    "%": {"keyCode": 53, "code": "Digit5", "key": "%", "text": "%"},
                    "^": {"keyCode": 54, "code": "Digit6", "key": "^", "text": "^"},
                    "&": {"keyCode": 55, "code": "Digit7", "key": "&", "text": "&"},
                    "*": {"keyCode": 56, "code": "Digit8", "key": "*", "text": "*"},
                    "(": {"keyCode": 57, "code": "Digit9", "key": "(", "text": "("},
                    ")": {"keyCode": 48, "code": "Digit0", "key": ")", "text": ")"},
                    "_": {"keyCode": 189, "code": "Minus", "key": "_", "text": "_"},
                    "+": {"keyCode": 187, "code": "Equal", "key": "+", "text": "+"},
                    "{": {
                        "keyCode": 219,
                        "code": "BracketLeft",
                        "key": "{",
                        "text": "{",
                    },
                    "}": {
                        "keyCode": 221,
                        "code": "BracketRight",
                        "key": "}",
                        "text": "}",
                    },
                    "|": {"keyCode": 220, "code": "Backslash", "key": "|", "text": "|"},
                    ":": {"keyCode": 186, "code": "Semicolon", "key": ":", "text": ":"},
                    '"': {"keyCode": 222, "code": "Quote", "key": '"', "text": '"'},
                    "<": {"keyCode": 188, "code": "Comma", "key": "<", "text": "<"},
                    ">": {"keyCode": 190, "code": "Period", "key": ">", "text": ">"},
                    "?": {"keyCode": 191, "code": "Slash", "key": "?", "text": "?"},
                }
                if char in symbol_map:
                    return symbol_map[char]

        return None

    @staticmethod
    def dispatch_key_event(
        chrome_interface: Any, key: str, modifiers: list | None = None
    ) -> dict[str, Any]:
        """
        Dispatch key events using CDP with full key definition support.

        Args:
            chrome_interface: Chrome DevTools Protocol interface
            key: Key to dispatch (e.g., 'a', 'Enter', 'Up', 'F1', any single character)
            modifiers: Optional list of modifiers ('ctrl', 'alt', 'shift', 'meta')

        Returns:
            Result dictionary with success status
        """
        if modifiers is None:
            modifiers = []

        try:
            key_def = JavaScriptExecutor._get_key_definition(key)

            if key_def is None:
                return {
                    "success": False,
                    "error": f"Unknown key '{key}'. Supported: a-z, 0-9, symbols, and special keys (enter, escape, tab, f1-f12, up, down, left, right, etc.)",
                    "key": key,
                    "modifiers": modifiers,
                }

            key_code = key_def["keyCode"]
            key_value = key_def["key"]
            code_value = key_def["code"]
            location = key_def.get("location", 0)
            text_value = key_def.get("text", "")

            modifier_flags = 0
            modifier_keys_to_press = []
            if modifiers:
                modifier_names = [m.strip().lower() for m in modifiers]
                for mod in modifier_names:
                    if mod in ["alt"]:
                        modifier_flags |= 1
                        modifier_keys_to_press.append("alt")
                    elif mod in ["ctrl", "control"]:
                        modifier_flags |= 2
                        modifier_keys_to_press.append("ctrl")
                    elif mod in ["meta", "cmd", "command"]:
                        modifier_flags |= 4
                        modifier_keys_to_press.append("meta")
                    elif mod in ["shift"]:
                        modifier_flags |= 8
                        modifier_keys_to_press.append("shift")

            for mod_key in modifier_keys_to_press:
                mod_def = key_definitions.get(mod_key)
                if mod_def:
                    chrome_interface.Input.dispatchKeyEvent(
                        type="keyDown",
                        key=mod_def["key"],
                        code=mod_def["code"],
                        windowsVirtualKeyCode=mod_def["keyCode"],
                        location=mod_def.get("location", 0),
                        modifiers=modifier_flags,
                    )

            chrome_interface.Input.dispatchKeyEvent(
                type="keyDown",
                key=key_value,
                code=code_value,
                windowsVirtualKeyCode=key_code,
                location=location,
                modifiers=modifier_flags,
            )

            if text_value:
                chrome_interface.Input.dispatchKeyEvent(
                    type="char",
                    key=key_value,
                    code=code_value,
                    windowsVirtualKeyCode=key_code,
                    text=text_value,
                    unmodifiedText=text_value,
                    location=location,
                    modifiers=modifier_flags,
                )

            chrome_interface.Input.dispatchKeyEvent(
                type="keyUp",
                key=key_value,
                code=code_value,
                windowsVirtualKeyCode=key_code,
                location=location,
                modifiers=modifier_flags,
            )

            for mod_key in reversed(modifier_keys_to_press):
                mod_def = key_definitions.get(mod_key)
                if mod_def:
                    chrome_interface.Input.dispatchKeyEvent(
                        type="keyUp",
                        key=mod_def["key"],
                        code=mod_def["code"],
                        windowsVirtualKeyCode=mod_def["keyCode"],
                        location=mod_def.get("location", 0),
                        modifiers=0,
                    )

            time.sleep(0.1)

            return {
                "success": True,
                "message": f"Successfully dispatched key '{key}' with modifiers '{modifiers}'",
                "key": key,
                "key_code": key_code,
                "key_value": key_value,
                "code_value": code_value,
                "modifiers": modifiers,
                "modifier_flags": modifier_flags,
            }

        except Exception as e:
            logger.error(f"Key dispatch error: {e}")
            return {
                "success": False,
                "error": f"Key dispatch error: {e!s}",
                "key": key,
                "modifiers": modifiers,
            }

    @staticmethod
    def filter_hidden_elements(chrome_interface: Any) -> dict[str, Any]:
        """
        Filter hidden elements from HTML using computed styles.
        Does not modify the actual page, returns filtered HTML string.

        Args:
            chrome_interface: Chrome DevTools Protocol interface

        Returns:
            Result dictionary with filtered HTML string
        """
        js_code = js_loader.get_filter_hidden_elements_js()
        return JavaScriptExecutor.execute_and_parse_result(chrome_interface, js_code)


class JavaScriptLoader:
    """Loads and processes JavaScript files for browser automation."""

    def __init__(self):
        self.js_dir = Path(__file__).parent / "js"
        self._js_cache: dict[str, str] = {}

    def load_js_file(self, filename: str) -> str:
        """
        Load a JavaScript file from the js directory.

        Args:
            filename: Name of the JavaScript file (with or without .js extension)

        Returns:
            JavaScript code as string

        Raises:
            FileNotFoundError: If the JavaScript file doesn't exist
        """
        if not filename.endswith(".js"):
            filename += ".js"

        if filename in self._js_cache:
            return self._js_cache[filename]

        file_path = self.js_dir / filename
        if not file_path.exists():
            raise FileNotFoundError(f"JavaScript file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        self._js_cache[filename] = js_code
        return js_code

    def get_extract_clickable_elements_js(self) -> str:
        return self.load_js_file("extract_clickable_elements.js")

    def get_extract_input_elements_js(self) -> str:
        return self.load_js_file("extract_input_elements.js")

    def get_extract_scrollable_elements_js(self) -> str:
        return self.load_js_file("extract_scrollable_elements.js")

    def get_extract_elements_by_text_js(self, text: str) -> str:
        js_code = self.load_js_file("extract_elements_by_text.js")
        escaped_text = text.replace("'", "\\'").replace("\\", "\\\\")
        wrapper = f"""
        (() => {{
            const text = `{escaped_text}`;
            return extractElementsByText(text);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_click_element_js(self, xpath: str) -> str:
        js_code = self.load_js_file("click_element.js")
        escaped_xpath = xpath.replace("`", "\\`").replace("\\", "\\\\")
        wrapper = f"""
        (() => {{
            const xpath = `{escaped_xpath}`;
            return clickElement(xpath);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_scroll_to_element_js(self, xpath: str) -> str:
        js_code = self.load_js_file("scroll_to_element.js")
        escaped_xpath = xpath.replace("`", "\\`").replace("\\", "\\\\")
        wrapper = f"""
        (() => {{
            const xpath = `{escaped_xpath}`;
            return scrollToElement(xpath);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_focus_and_clear_element_js(self, xpath: str) -> str:
        js_code = self.load_js_file("focus_and_clear_element.js")
        escaped_xpath = xpath.replace("`", "\\`").replace("\\", "\\\\")
        wrapper = f"""
        (() => {{
            const xpath = `{escaped_xpath}`;
            return focusAndClearElement(xpath);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_trigger_input_events_js(self, xpath: str, value: str) -> str:
        js_code = self.load_js_file("trigger_input_events.js")
        escaped_xpath = xpath.replace("`", "\\`").replace("\\", "\\\\")
        wrapper = f"""
        (() => {{
            const xpath = `{escaped_xpath}`;
            const value = `{value}`;
            return triggerInputEvents(xpath, value);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_draw_element_boxes_js(self, uuid_xpath_dict: dict[str, str]) -> str:
        import json

        js_code = self.load_js_file("draw_element_boxes.js")
        json_str = json.dumps(uuid_xpath_dict)
        wrapper = f"""
        (() => {{
            const uuidXpathMap = {json_str};
            return drawElementBoxes(uuidXpathMap);
        }})();
        """
        return js_code + "\n" + wrapper

    def get_remove_element_boxes_js(self) -> str:
        js_code = self.load_js_file("remove_element_boxes.js")
        wrapper = """
        (() => {
            return removeElementBoxes();
        })();
        """
        return js_code + "\n" + wrapper

    def get_filter_hidden_elements_js(self) -> str:
        js_code = self.load_js_file("filter_hidden_elements.js")
        wrapper = """
        (() => {
            return filterHiddenElements();
        })();
        """
        return js_code + "\n" + wrapper

    def clear_cache(self):
        self._js_cache.clear()


js_loader = JavaScriptLoader()

key_definitions = {
    "up": {"keyCode": 38, "key": "ArrowUp", "code": "ArrowUp"},
    "down": {"keyCode": 40, "key": "ArrowDown", "code": "ArrowDown"},
    "left": {"keyCode": 37, "key": "ArrowLeft", "code": "ArrowLeft"},
    "right": {"keyCode": 39, "key": "ArrowRight", "code": "ArrowRight"},
    "home": {"keyCode": 36, "key": "Home", "code": "Home"},
    "end": {"keyCode": 35, "key": "End", "code": "End"},
    "pageup": {"keyCode": 33, "key": "PageUp", "code": "PageUp"},
    "pagedown": {"keyCode": 34, "key": "PageDown", "code": "PageDown"},
    "enter": {"keyCode": 13, "key": "Enter", "code": "Enter", "text": "\r"},
    "escape": {"keyCode": 27, "key": "Escape", "code": "Escape"},
    "tab": {"keyCode": 9, "key": "Tab", "code": "Tab", "text": "\t"},
    "backspace": {"keyCode": 8, "key": "Backspace", "code": "Backspace"},
    "delete": {"keyCode": 46, "key": "Delete", "code": "Delete"},
    "space": {"keyCode": 32, "key": " ", "code": "Space", "text": " "},
    "insert": {"keyCode": 45, "key": "Insert", "code": "Insert"},
    "f1": {"keyCode": 112, "key": "F1", "code": "F1"},
    "f2": {"keyCode": 113, "key": "F2", "code": "F2"},
    "f3": {"keyCode": 114, "key": "F3", "code": "F3"},
    "f4": {"keyCode": 115, "key": "F4", "code": "F4"},
    "f5": {"keyCode": 116, "key": "F5", "code": "F5"},
    "f6": {"keyCode": 117, "key": "F6", "code": "F6"},
    "f7": {"keyCode": 118, "key": "F7", "code": "F7"},
    "f8": {"keyCode": 119, "key": "F8", "code": "F8"},
    "f9": {"keyCode": 120, "key": "F9", "code": "F9"},
    "f10": {"keyCode": 121, "key": "F10", "code": "F10"},
    "f11": {"keyCode": 122, "key": "F11", "code": "F11"},
    "f12": {"keyCode": 123, "key": "F12", "code": "F12"},
    "numpad0": {"keyCode": 96, "key": "0", "code": "Numpad0", "location": 3},
    "numpad1": {"keyCode": 97, "key": "1", "code": "Numpad1", "location": 3},
    "numpad2": {"keyCode": 98, "key": "2", "code": "Numpad2", "location": 3},
    "numpad3": {"keyCode": 99, "key": "3", "code": "Numpad3", "location": 3},
    "numpad4": {"keyCode": 100, "key": "4", "code": "Numpad4", "location": 3},
    "numpad5": {"keyCode": 101, "key": "5", "code": "Numpad5", "location": 3},
    "numpad6": {"keyCode": 102, "key": "6", "code": "Numpad6", "location": 3},
    "numpad7": {"keyCode": 103, "key": "7", "code": "Numpad7", "location": 3},
    "numpad8": {"keyCode": 104, "key": "8", "code": "Numpad8", "location": 3},
    "numpad9": {"keyCode": 105, "key": "9", "code": "Numpad9", "location": 3},
    "volumeup": {"keyCode": 175, "key": "AudioVolumeUp", "code": "AudioVolumeUp"},
    "volume_up": {"keyCode": 175, "key": "AudioVolumeUp", "code": "AudioVolumeUp"},
    "volumedown": {"keyCode": 174, "key": "AudioVolumeDown", "code": "AudioVolumeDown"},
    "volume_down": {
        "keyCode": 174,
        "key": "AudioVolumeDown",
        "code": "AudioVolumeDown",
    },
    "volumemute": {"keyCode": 173, "key": "AudioVolumeMute", "code": "AudioVolumeMute"},
    "volume_mute": {
        "keyCode": 173,
        "key": "AudioVolumeMute",
        "code": "AudioVolumeMute",
    },
    "capslock": {"keyCode": 20, "key": "CapsLock", "code": "CapsLock"},
    "numlock": {"keyCode": 144, "key": "NumLock", "code": "NumLock"},
    "scrolllock": {"keyCode": 145, "key": "ScrollLock", "code": "ScrollLock"},
    "shift": {"keyCode": 16, "key": "Shift", "code": "ShiftLeft", "location": 1},
    "ctrl": {"keyCode": 17, "key": "Control", "code": "ControlLeft", "location": 1},
    "control": {"keyCode": 17, "key": "Control", "code": "ControlLeft", "location": 1},
    "alt": {"keyCode": 18, "key": "Alt", "code": "AltLeft", "location": 1},
    "meta": {"keyCode": 91, "key": "Meta", "code": "MetaLeft", "location": 1},
    "cmd": {"keyCode": 91, "key": "Meta", "code": "MetaLeft", "location": 1},
    "command": {"keyCode": 91, "key": "Meta", "code": "MetaLeft", "location": 1},
    "windows": {"keyCode": 91, "key": "Meta", "code": "MetaLeft", "location": 1},
}

key_codes = {k: v["keyCode"] for k, v in key_definitions.items()}
