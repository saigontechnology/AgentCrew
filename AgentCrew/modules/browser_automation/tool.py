from __future__ import annotations

from typing import TYPE_CHECKING
import difflib
import time

if TYPE_CHECKING:
    from .service import BrowserAutomationService
    from typing import Dict, Any, Callable, Union, List


def get_browser_navigate_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser navigation."""
    tool_description = "Navigate to a URL in the browser. Check result before proceeding with other actions."
    tool_arguments = {
        "url": {
            "type": "string",
            "description": "Valid HTTP/HTTPS URL to navigate to (e.g., 'https://example.com').",
        },
        "profile": {
            "type": "string",
            "description": "Chrome user profile directory name (default: 'Default'). Allows agent to choose which Chrome user profile to use.",
            "default": "Default",
        },
    }
    tool_required = ["url"]

    if provider == "claude":
        return {
            "name": "browser_navigate",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_click_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser element clicking."""
    tool_description = (
        "Click an element using its UUID. Get UUIDs from browser_get_content first."
    )
    tool_arguments = {
        "element_uuid": {
            "type": "string",
            "description": "UUID identifier from browser_get_content clickable elements table.",
        }
    }
    tool_required = ["element_uuid"]

    if provider == "claude":
        return {
            "name": "browser_click",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_scroll_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser scrolling."""
    tool_description = (
        "Scroll page or element in specified direction. Each unit moves ~300px."
    )
    tool_arguments = {
        "direction": {
            "type": "string",
            "enum": ["up", "down", "left", "right"],
            "description": "Scroll direction.",
        },
        "amount": {
            "type": "integer",
            "description": "Scroll units (default: 3). Range 1-10.",
            "default": 3,
            "minimum": 1,
            "maximum": 10,
        },
        "element_uuid": {
            "type": "string",
            "description": "Optional UUID for specific element to scroll. Scrolls document if not provided.",
        },
    }
    tool_required = ["direction"]

    if provider == "claude":
        return {
            "name": "browser_scroll",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_scroll",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_get_content_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser content extraction."""
    tool_description = (
        "Extract page content as markdown with tables of clickable, input, and scrollable elements. UUIDs reset on each call."
        "browser_get_content result is UNIQUE in whole conversation. Remember to summarize important information before calling again."
    )
    tool_arguments = {}
    tool_required = []

    if provider == "claude":
        return {
            "name": "browser_get_content",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_get_content",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_get_content_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser content extraction tool."""

    def handle_browser_get_content(**params) -> Union[List[Dict[str, Any]], str]:
        result = browser_service.get_page_content()
        browser_service._last_page_content = result.get("content", "")
        context_image = browser_service.capture_screenshot(
            format="jpeg",
            quality=70,
        )

        if result["success"]:
            tool_result = [
                {
                    "type": "text",
                    "text": f"[UNIQUE]{result.get('content', 'Cannot get page content. Please try again.')}[/UNIQUE]",
                },
            ]
            if context_image.get("success", False):
                tool_result.append(context_image.get("screenshot", {}))
            return tool_result
        else:
            raise RuntimeError(f"❌ Content extraction failed: {result['error']}")

    return handle_browser_get_content


def get_browser_navigate_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser navigate tool."""

    def handle_browser_navigate(**params) -> str:
        url = params.get("url")
        profile = params.get("profile", "Default")

        if not url:
            return "Error: No URL provided for navigation."

        result = browser_service.navigate(url, profile=profile)

        if result.get("success", True):
            profile_info = (
                f"\nProfile: {result.get('profile', profile)}"
                if result.get("profile")
                else ""
            )
            return f"✅ {result.get('message', 'Success')}. Use `browser_get_content` to read the url content.\nCurrent URL: {result.get('current_url', 'Unknown')}{profile_info}"
        else:
            raise RuntimeError(f"❌ Navigation failed: {result['error']}")

    return handle_browser_navigate


def get_browser_click_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser click tool."""

    def handle_browser_click(**params) -> str:
        element_uuid = params.get("element_uuid")

        if not element_uuid:
            return "Error: No element UUID provided for element clicking."

        result = browser_service.click_element(element_uuid)

        diff_summary = _get_content_delta_changes(browser_service)

        if result.get("success", True):
            return (
                f"✅ {result.get('message', 'Success')}. Use `browser_get_content` to get the updated content.\n"
                f"UUID: {element_uuid}\nClickedElement: {result.get('elementInfo', {}).get('text', 'Unknown')}.\n"
                f"Content delta changes:\n{diff_summary}"
            )
        else:
            return f"❌ Click failed: {result['error']}\nUUID: {element_uuid}.\nUse `browser_get_content` to get the updated UUID"

    return handle_browser_click


def get_browser_scroll_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser scroll tool."""

    def handle_browser_scroll(**params) -> str:
        direction = params.get("direction")
        amount = params.get("amount", 3)
        element_uuid = params.get("element_uuid")

        if not direction:
            return "Error: No scroll direction provided."

        if direction not in ["up", "down", "left", "right"]:
            return (
                "Error: Invalid scroll direction. Use 'up', 'down', 'left', or 'right'."
            )

        result = browser_service.scroll_page(direction, amount, element_uuid)

        if result.get("success", True):
            return f"✅ {result.get('message', 'Success')}, Use `browser_get_content` to get the updated content."
        else:
            uuid_info = f"\nUUID: {element_uuid}" if element_uuid else ""
            raise RuntimeError(f"❌ Scroll failed: {result['error']}{uuid_info}")

    return handle_browser_scroll


def get_browser_input_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser input."""
    tool_description = "Input data into form fields using UUID. Get UUIDs from browser_get_content first."
    tool_arguments = {
        "element_uuid": {
            "type": "string",
            "description": "UUID identifier from browser_get_content input elements table.",
        },
        "value": {
            "type": "string",
            "description": "Value to input. For text: enter text. For select: option value/text. For checkbox: 'true'/'false'.",
        },
    }
    tool_required = ["element_uuid", "value"]

    if provider == "claude":
        return {
            "name": "browser_input",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_input",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_input_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser input tool."""

    def handle_browser_input(**params) -> str:
        element_uuid = params.get("element_uuid")
        value = params.get("value")

        if not element_uuid:
            return "Error: No element UUID provided for input element."

        if value is None:
            return "Error: No value provided for input."

        result = browser_service.input_data(element_uuid, str(value))
        diff_summary = _get_content_delta_changes(browser_service)

        if result.get("success", True):
            return f"✅ {result.get('message', 'Success')}\nUUID: {element_uuid}\nValue: {value}\nContent delta changes:\n{diff_summary}"
        else:
            raise RuntimeError(
                f"❌ Input failed: {result['error']}\nUUID: {element_uuid}\nValue: {value}.\n Use `browser_get_content` to get updated UUID."
            )

    return handle_browser_input


def get_browser_get_elements_by_text_tool_definition(
    provider="claude",
) -> Dict[str, Any]:
    """Get tool definition for browser elements by text search."""
    tool_description = "Find div elements containing specific text. Returns UUID table for use with other tools."
    tool_arguments = {
        "text": {
            "type": "string",
            "description": "Text to search for in div elements (case-insensitive).",
        }
    }
    tool_required = ["text"]

    if provider == "claude":
        return {
            "name": "browser_get_elements_by_text",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:
        return {
            "type": "function",
            "function": {
                "name": "browser_get_elements_by_text",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_get_elements_by_text_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get handler function for browser get elements by text tool."""

    def handle_browser_get_elements_by_text(**params) -> str:
        text = params.get("text")
        if not text:
            return "Error: No text provided for element search."

        result = browser_service.get_elements_by_text(text)

        if result.get("success", False):
            elements_found = result.get("elements_found", 0)
            if elements_found == 0:
                return f"✅ No elements found containing text: '{text}'"

            content = result.get("content", "")
            return (
                f"✅ Found {elements_found} elements containing text: '{text}'\n"
                + content
            )
        else:
            raise RuntimeError(
                f"❌ Search failed: {result.get('error', 'Unknown error')}\nSearch text: '{text}'"
            )

    return handle_browser_get_elements_by_text


def get_browser_capture_screenshot_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser screenshot capture."""
    tool_description = "Capture page screenshot as base64 image data. Supports different formats and full page capture."
    tool_arguments = {
        "format": {
            "type": "string",
            "enum": ["png", "jpeg", "webp"],
            "description": "Image format (default: png).",
            "default": "png",
        },
        "quality": {
            "type": "integer",
            "description": "JPEG quality 0-100 (ignored for PNG/WebP).",
            "minimum": 0,
            "maximum": 100,
        },
        "capture_beyond_viewport": {
            "type": "boolean",
            "description": "Capture full page beyond viewport (default: false).",
            "default": True,
        },
    }
    tool_required = []

    if provider == "claude":
        return {
            "name": "browser_capture_screenshot",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_capture_screenshot",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_capture_screenshot_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser screenshot capture tool."""

    def handle_browser_capture_screenshot(**params) -> Any:
        format_param = params.get("format", "png")
        quality = params.get("quality")
        capture_beyond_viewport = params.get("capture_beyond_viewport", False)

        # Validate format
        if format_param not in ["png", "jpeg", "webp"]:
            return "Error: Invalid format. Must be 'png', 'jpeg', or 'webp'."

        # Validate quality for JPEG
        if format_param == "jpeg" and quality is not None:
            if not isinstance(quality, int) or quality < 0 or quality > 100:
                return "Error: Quality must be an integer between 0 and 100 for JPEG format."

        result = browser_service.capture_screenshot(
            format=format_param,
            quality=quality,
            capture_beyond_viewport=capture_beyond_viewport,
        )

        if result.get("success", False):
            # Return the screenshot data in the format that can be processed by the LLM
            screenshot_data = result.get("screenshot", {})
            return [screenshot_data]
        else:
            raise RuntimeError(
                f"❌ Screenshot capture failed: {result.get('error', 'Unknown error')}"
            )

    return handle_browser_capture_screenshot


def get_browser_send_key_tool_definition(provider="claude") -> Dict[str, Any]:
    """Get tool definition for browser key event send."""
    tool_description = "Send keyboard events like arrow keys, function keys, page navigation keys, etc. useful when combine with input tool."
    tool_arguments = {
        "key": {
            "type": "string",
            "enum": [
                "up",
                "down",
                "left",
                "right",
                "home",
                "end",
                "pageup",
                "pagedown",
                "enter",
                "escape",
                "tab",
                "backspace",
                "delete",
                "space",
                "f1",
                "f2",
                "f3",
                "f4",
                "f5",
                "f6",
                "f7",
                "f8",
                "f9",
                "f10",
                "f11",
                "f12",
                "numpad0",
                "numpad1",
                "numpad2",
                "numpad3",
                "numpad4",
                "numpad5",
                "numpad6",
                "numpad7",
                "numpad8",
                "numpad9",
                "volumeup",
                "volumedown",
                "volumemute",
                "capslock",
                "numlock",
                "scrolllock",
            ],
            "description": "Key to send.",
        },
        "modifiers": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["ctrl", "alt", "shift", "meta"],
            },
            "description": "Optional modifier keys: 'ctrl', 'alt', 'shift', 'meta'. Example: 'ctrl,shift' for Ctrl+Shift+Key.",
            "default": [],
        },
    }
    tool_required = ["key"]

    if provider == "claude":
        return {
            "name": "browser_send_key",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "groq" or other OpenAI-compatible
        return {
            "type": "function",
            "function": {
                "name": "browser_send_key",
                "description": tool_description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


def get_browser_send_key_tool_handler(
    browser_service: BrowserAutomationService,
) -> Callable:
    """Get the handler function for the browser key send tool."""

    def handle_browser_send_key(**params) -> str:
        key = params.get("key")
        modifiers = params.get("modifiers", "")

        if not key:
            return "Error: No key provided for send."

        result = browser_service.dispatch_key_event(key, modifiers)

        if result.get("success", False):
            key_info = f"Key: {result.get('key')} (Code: {result.get('key_code')})"
            modifiers_info = (
                f"Modifiers: {result.get('modifiers')}"
                if result.get("modifiers")
                else ""
            )
            diff_summary = _get_content_delta_changes(browser_service)
            success_msg = f"✅ {result.get('message', 'Success')}. {key_info}\nContent delta changes:\n{diff_summary}"
            if modifiers_info:
                success_msg += f". {modifiers_info}"
            return success_msg
        else:
            raise RuntimeError(
                f"❌ Key send failed: {result.get('error', 'Unknown error')}"
            )

    return handle_browser_send_key


def _get_content_delta_changes(browser_service: BrowserAutomationService):
    time.sleep(1)  # wait for page to stabilize
    current_content = browser_service.get_page_content()
    differ = difflib.Differ()
    _last_page_content_lines = browser_service._last_page_content.splitlines()
    try:
        cutoff_idx = _last_page_content_lines.index("## Clickable Elements")
    except ValueError:
        cutoff_idx = len(_last_page_content_lines)
    diffs = list(
        differ.compare(
            _last_page_content_lines[:cutoff_idx],
            current_content.get("content", "").splitlines(),
        )
    )
    diff_summary = "\n".join([d.lstrip("+- ") for d in diffs if d.startswith("+ ")])
    return diff_summary


def register(service_instance=None, agent=None):
    """Register browser automation tools with the central registry or directly with an agent."""
    from AgentCrew.modules.tools.registration import register_tool

    # Register all eight browser automation tools
    register_tool(
        get_browser_navigate_tool_definition,
        get_browser_navigate_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_click_tool_definition,
        get_browser_click_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_scroll_tool_definition,
        get_browser_scroll_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_get_content_tool_definition,
        get_browser_get_content_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_input_tool_definition,
        get_browser_input_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_get_elements_by_text_tool_definition,
        get_browser_get_elements_by_text_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_capture_screenshot_tool_definition,
        get_browser_capture_screenshot_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_browser_send_key_tool_definition,
        get_browser_send_key_tool_handler,
        service_instance,
        agent,
    )
