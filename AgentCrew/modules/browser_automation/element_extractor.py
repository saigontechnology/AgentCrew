"""
Web element extraction utilities for browser automation.

Provides functionality to extract clickable elements and page content
for browser automation operations.
"""

import re
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from loguru import logger

from .js_loader import js_loader


def remove_duplicate_lines(content: str) -> str:
    """
    Remove consecutive duplicate lines from content while preserving structure.

    This function:
    1. Splits content into lines
    2. Removes consecutive duplicate lines (keeps first occurrence)
    3. Preserves empty lines and markdown structure
    4. Handles whitespace variations by stripping for comparison

    Args:
        content: The content to deduplicate

    Returns:
        Content with consecutive duplicate lines removed
    """
    if not content:
        return content

    lines = content.split("\n")
    if len(lines) <= 1:
        return content

    deduplicated_lines = []
    previous_line_stripped = None

    for line in lines:
        current_line_stripped = line.strip()
        if not current_line_stripped:
            continue

        if not current_line_stripped or current_line_stripped != previous_line_stripped:
            deduplicated_lines.append(line)
            previous_line_stripped = current_line_stripped

    return "\n".join(deduplicated_lines)


def clean_markdown_images(markdown_content: str) -> str:
    """
    Clean markdown output by:
    1. Replace data: format image URLs with REDACTED
    2. Handle both single and double quotes in image tags
    3. Reduce length of image links (truncate long URLs)
    4. Replace HTML img tags with alt text, or remove if no alt attribute

    Args:
        markdown_content: The markdown content to clean

    Returns:
        Cleaned markdown content
    """
    markdown_img_pattern = r"!?\[([^\]]*)\]\(([^)]+)\)"

    def replace_markdown_img(match):
        alt_text = match.group(1)
        url = match.group(2)

        if url.startswith("data:"):
            return f"![{alt_text}](REDACTED)"

        if len(url) > 50:
            url = url[:50] + "..."

        return f"![{alt_text}]({url})"

    cleaned_content = re.sub(
        markdown_img_pattern, replace_markdown_img, markdown_content
    )

    html_img_pattern = r"<img\s+([^>]*?)/?>"

    def replace_html_img(match):
        attributes = match.group(1)
        alt_match = re.search(r'alt\s*=\s*(["\'])([^"\']*?)\1', attributes)
        alt = alt_match.group(2) if alt_match else ""

        if alt:
            return f"<img alt='({alt})' /> "
        return ""

    cleaned_content = re.sub(html_img_pattern, replace_html_img, cleaned_content)

    return cleaned_content


def filter_hidden_elements(html_content: str) -> str:
    """Filter out HTML elements that have style='display:none' or aria-hidden='true'."""

    class HiddenElementFilter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.filtered_html = []
            self.skip_depth = 0
            self.tag_stack = []

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            should_hide = False

            if self.skip_depth > 0:
                if tag in self.tag_stack:
                    self.skip_depth += 1
                return

            if tag.lower() in ["script", "style", "svg"]:
                should_hide = True

            style = attr_dict.get("style", "")
            if style:
                style_clean = re.sub(r"\s+", "", style.lower())
                if (
                    "display:none" in style_clean
                    or "display=none" in style_clean
                    or "visibility:hidden" in style_clean
                ):
                    should_hide = True

            aria_hidden = attr_dict.get("aria-hidden", "")
            if aria_hidden and aria_hidden.lower() == "true":
                should_hide = True

            if should_hide:
                if tag.lower() in ["img", "input", "br", "hr", "meta", "link"]:
                    return
                self.tag_stack.append(tag)
                self.skip_depth += 1
                return

            if self.skip_depth == 0:
                attr_string = " ".join([f'{k}="{v}"' for k, v in attrs])
                if attr_string:
                    self.filtered_html.append(f"<{tag} {attr_string}>")
                else:
                    self.filtered_html.append(f"<{tag}>")

        def handle_endtag(self, tag):
            if self.skip_depth > 0:
                if tag in self.tag_stack:
                    self.skip_depth -= 1
                    if self.skip_depth == 0:
                        self.tag_stack.remove(tag)
                    return

            if self.skip_depth == 0:
                self.filtered_html.append(f"</{tag}>")

        def handle_data(self, data):
            if self.skip_depth == 0:
                self.filtered_html.append(data)

        def handle_comment(self, data):
            if self.skip_depth == 0:
                self.filtered_html.append(f"<!--{data}-->")

        def handle_entityref(self, name):
            if self.skip_depth == 0:
                self.filtered_html.append(f"&{name};")

        def handle_charref(self, name):
            if self.skip_depth == 0:
                self.filtered_html.append(f"&#{name};")

        def get_filtered_html(self):
            return "".join(self.filtered_html)

    try:
        parser = HiddenElementFilter()
        parser.feed(html_content)
        return parser.get_filtered_html()
    except Exception as e:
        logger.warning(f"Error filtering hidden elements: {e}")
        return html_content


def _extract_runtime_value(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, tuple) and len(result) >= 2:
        if isinstance(result[1], dict):
            return result[1].get("result", {}).get("result", {}).get("value", [])
        if isinstance(result[1], list) and len(result[1]) > 0:
            return result[1][0].get("result", {}).get("result", {}).get("value", [])
    return []


def _evaluate_elements(chrome_interface, js_code: str) -> list[dict[str, Any]]:
    result = chrome_interface.Runtime.evaluate(expression=js_code, returnByValue=True)
    elements_data = _extract_runtime_value(result)
    return elements_data if isinstance(elements_data, list) else []


def extract_clickable_elements(
    chrome_interface, resolve_uuid: Callable[[str], str]
) -> tuple[str, set[str]]:
    """
    Extract all clickable elements from the current webpage in a concise format.

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        resolve_uuid: Callable that returns a stable UUID for a given XPath

    Returns:
        Tuple of markdown output and the set of active XPaths
    """
    try:
        js_code = js_loader.get_extract_clickable_elements_js()
        elements_data = _evaluate_elements(chrome_interface, js_code)

        if not elements_data:
            return (
                "\n\n## Clickable Elements\n\nNo clickable elements found on this page.\n",
                set(),
            )

        markdown_output = []
        active_xpaths: set[str] = set()
        markdown_output.append(
            "\n\n## Clickable Elements\nUse browser_click with UUID to click elements.\n"
        )
        markdown_output.append("| UUID | Type | Text/Alt |\n")
        markdown_output.append("|------|------|-----------|\n")

        for element in elements_data:
            xpath = element.get("xpath", "")
            if not xpath:
                continue

            active_xpaths.add(xpath)
            text = element.get("text", "").strip()
            element_type = element.get("type", "").strip()
            element_uuid = resolve_uuid(xpath)
            text = text.replace("|", "\\|")
            markdown_output.append(f"| `{element_uuid}` | {element_type} | {text} |\n")

        return "".join(markdown_output), active_xpaths

    except Exception as e:
        logger.error(f"Error extracting clickable elements: {e}")
        return (
            f"\n\n## Clickable Elements\n\nError extracting clickable elements: {e!s}\n",
            set(),
        )


def extract_elements_by_text(
    chrome_interface, resolve_uuid: Callable[[str], str], text: str
) -> tuple[str, int]:
    """Extract elements containing specified text using XPath."""
    try:
        js_code = js_loader.get_extract_elements_by_text_js(text)
        elements_data = _evaluate_elements(chrome_interface, js_code)

        if not elements_data:
            return (
                f"\n\n## Elements Containing Text: '{text}'\n\nNo elements found.\n",
                0,
            )

        markdown_output = [
            f"\n\n## Elements Containing Text: '{text}'\n",
            "| UUID | Tag | Text | Class | ID |\n",
            "|------|-----|------|-------|----|\n",
        ]
        elements_found = 0

        for element in elements_data:
            xpath = element.get("xpath", "")
            if not xpath:
                continue

            element_uuid = resolve_uuid(xpath)
            tag_name = element.get("tagName", "")
            element_text = element.get("text", "").replace("|", "\\|")[:50]
            class_name = element.get("className", "").replace("|", "\\|")[:30]
            element_id = element.get("id", "").replace("|", "\\|")[:20]

            if len(element.get("text", "")) > 50:
                element_text += "..."
            if len(element.get("className", "")) > 30:
                class_name += "..."
            if len(element.get("id", "")) > 20:
                element_id += "..."

            markdown_output.append(
                f"| `{element_uuid}` | {tag_name} | {element_text} | {class_name} | {element_id} |\n"
            )
            elements_found += 1

        return "".join(markdown_output), elements_found

    except Exception as e:
        logger.error(f"Error extracting elements by text: {e}")
        return f"\n\n## Elements Containing Text: '{text}'\n\nError: {e!s}\n", 0


def extract_input_elements(
    chrome_interface, resolve_uuid: Callable[[str], str]
) -> tuple[str, set[str]]:
    """
    Extract all input elements from the current webpage in a concise format.

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        resolve_uuid: Callable that returns a stable UUID for a given XPath

    Returns:
        Tuple of markdown output and the set of active XPaths
    """
    try:
        js_code = js_loader.get_extract_input_elements_js()
        elements_data = _evaluate_elements(chrome_interface, js_code)

        if not elements_data:
            return (
                "\n\n## Input Elements\n\nNo input elements found on this page.\n",
                set(),
            )

        markdown_output = []
        active_xpaths: set[str] = set()
        markdown_output.append(
            "\n\n## Input Elements\nUse browser_input with UUID and value to fill inputs.\n"
        )
        markdown_output.append(
            "| UUID | Type | Description | Required | Disabled | Name | Value |\n"
        )
        markdown_output.append(
            "|------|------|-------------|----------|----------|------|-------|\n"
        )

        for element in elements_data:
            xpath = element.get("xpath", "")
            if not xpath:
                continue

            active_xpaths.add(xpath)
            element_type = element.get("type", "")
            description = element.get("description", "").strip()
            required = "yes" if element.get("required", False) else "no"
            disabled = "yes" if element.get("disabled", False) else "no"
            name = element.get("name", "").strip()
            value = element.get("value", "").strip()
            element_uuid = resolve_uuid(xpath)

            if description:
                description = description.replace("|", "\\|")
            else:
                description = "_no description_"

            element_type = element_type.replace("|", "\\|")
            name = name.replace("|", "\\|") if name else ""
            value = value.replace("|", "\\|") if value else ""

            if len(value) > 30:
                value = value[:27] + "..."

            markdown_output.append(
                f"| `{element_uuid}` | {element_type} | {description} | {required} | {disabled} | {name} | {value} |\n"
            )

        return "".join(markdown_output), active_xpaths

    except Exception as e:
        logger.error(f"Error extracting input elements: {e}")
        return (
            f"\n\n## Input Elements\n\nError extracting input elements: {e!s}\n",
            set(),
        )


def extract_scrollable_elements(
    chrome_interface, resolve_uuid: Callable[[str], str]
) -> tuple[str, set[str]]:
    """
    Extract all scrollable elements from the current webpage.

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        resolve_uuid: Callable that returns a stable UUID for a given XPath

    Returns:
        Tuple of markdown output and the set of active XPaths
    """
    try:
        js_code = js_loader.get_extract_scrollable_elements_js()
        elements_data = _evaluate_elements(chrome_interface, js_code)

        if not elements_data:
            return (
                "\n\n## Scrollable Elements\n\nNo scrollable elements found on this page.\n",
                set(),
            )

        markdown_output = []
        active_xpaths: set[str] = set()
        markdown_output.append(
            "\n\n## Scrollable Elements\nUse browser_scroll with UUID and direction to scroll specific elements.\n"
        )
        markdown_output.append("| UUID | Tag | Scroll Direction | Description |\n")
        markdown_output.append("|------|-----|------------------|-------------|\n")

        for element in elements_data:
            xpath = element.get("xpath", "")
            if not xpath:
                continue

            active_xpaths.add(xpath)
            tag_name = element.get("tagName", "")
            scroll_directions = element.get("scrollDirections", "")
            description = element.get("description", "").strip()
            element_uuid = resolve_uuid(xpath)

            if description:
                description = description.replace("|", "\\|")
            else:
                description = "_no description_"

            tag_name = tag_name.replace("|", "\\|")
            scroll_directions = scroll_directions.replace("|", "\\|")

            markdown_output.append(
                f"| `{element_uuid}` | {tag_name} | {scroll_directions} | {description} |\n"
            )

        return "".join(markdown_output), active_xpaths

    except Exception as e:
        logger.error(f"Error extracting scrollable elements: {e}")
        return (
            f"\n\n## Scrollable Elements\n\nError extracting scrollable elements: {e!s}\n",
            set(),
        )
