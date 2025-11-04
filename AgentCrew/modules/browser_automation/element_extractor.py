"""
Web element extraction utilities for browser automation.

Provides functionality to extract clickable elements and page content
for browser automation operations.
"""

import re
import uuid
from typing import Dict

from .js_loader import js_loader

from loguru import logger


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
        # Strip whitespace for comparison but keep original for output
        current_line_stripped = line.strip()
        if not current_line_stripped:
            continue  # Skip adding multiple empty lines

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
    # Pattern for markdown images: ![alt](url)
    markdown_img_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"

    def replace_markdown_img(match):
        alt_text = match.group(1)
        url = match.group(2)

        # Replace data: URLs with REDACTED
        if url.startswith("data:"):
            return f"![{alt_text}](REDACTED)"

        # Truncate long URLs (keep first 50 chars + "...")
        if len(url) > 50:
            url = url[:50] + "..."

        return f"![{alt_text}]({url})"

    # Replace markdown images
    cleaned_content = re.sub(
        markdown_img_pattern, replace_markdown_img, markdown_content
    )

    # Pattern for HTML img tags with flexible quote handling
    # This handles both single and double quotes around attributes
    html_img_pattern = r"<img\s+([^>]*?)/?>"

    def replace_html_img(match):
        attributes = match.group(1)

        # Extract alt attribute (handle both quote types)
        alt_match = re.search(r'alt\s*=\s*(["\'])([^"\']*?)\1', attributes)
        alt = alt_match.group(2) if alt_match else ""

        # Replace img tag with alt text if available, otherwise remove it
        if alt:
            return f"<img alt='({alt})' /> "
        else:
            return ""

    # Replace HTML img tags
    cleaned_content = re.sub(html_img_pattern, replace_html_img, cleaned_content)

    return cleaned_content


def extract_clickable_elements(chrome_interface, uuid_mapping: Dict[str, str]) -> str:
    """
    Extract all clickable elements from the current webpage in a concise format.

    For each clickable element, extracts:
    - UUID: Short unique identifier for the element
    - Text/Alt: Display text or alt text from images

    Deduplication:
    - Elements with href: Deduplicated by href value
    - Elements without href: Deduplicated by tagName + text combination

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        uuid_mapping: Dictionary to store UUID to XPath mappings

    Returns:
        Concise markdown table with UUID and text/alt for each unique element
    """
    try:
        # Load JavaScript code from external file
        js_code = js_loader.get_extract_clickable_elements_js()

        # Execute JavaScript to get clickable elements
        result = chrome_interface.Runtime.evaluate(
            expression=js_code, returnByValue=True
        )

        if isinstance(result, tuple) and len(result) >= 2:
            if isinstance(result[1], dict):
                elements_data = (
                    result[1].get("result", {}).get("result", {}).get("value", [])
                )
            elif isinstance(result[1], list) and len(result[1]) > 0:
                elements_data = (
                    result[1][0].get("result", {}).get("result", {}).get("value", [])
                )
            else:
                elements_data = []
        else:
            elements_data = []

        if not elements_data:
            return "\n\n## Clickable Elements\n\nNo clickable elements found on this page.\n"

        # Format clickable elements into concise markdown with UUID mapping
        markdown_output = []
        markdown_output.append(
            "\n\n## Clickable Elements\nUse browser_click with UUID to click elements.\n"
        )
        markdown_output.append("| UUID | Text/Alt |\n")
        markdown_output.append("|------|----------|\n")

        for element in elements_data:
            xpath = element.get("xpath", "")
            text = element.get("text", "").strip()

            # Skip empty text entries
            if not text:
                continue

            # Generate UUID and store mapping
            element_uuid = str(uuid.uuid4())[:8]  # Use first 8 characters for brevity
            uuid_mapping[element_uuid] = xpath

            # Escape pipe characters in text for markdown table
            text = text.replace("|", "\\|")

            markdown_output.append(f"| `{element_uuid}` | {text} |\n")

        return "".join(markdown_output)

    except Exception as e:
        logger.error(f"Error extracting clickable elements: {e}")
        return f"\n\n## Clickable Elements\n\nError extracting clickable elements: {str(e)}\n"


def extract_elements_by_text(
    chrome_interface, uuid_mapping: Dict[str, str], text: str
) -> str:
    """Extract elements containing specified text using XPath."""
    try:
        # Load JavaScript code from external file with text parameter
        js_code = js_loader.get_extract_elements_by_text_js(text)

        result = chrome_interface.Runtime.evaluate(
            expression=js_code, returnByValue=True
        )

        if isinstance(result, tuple) and len(result) >= 2:
            if isinstance(result[1], dict):
                elements_data = (
                    result[1].get("result", {}).get("result", {}).get("value", [])
                )
            elif isinstance(result[1], list) and len(result[1]) > 0:
                elements_data = (
                    result[1][0].get("result", {}).get("result", {}).get("value", [])
                )
            else:
                elements_data = []
        else:
            elements_data = []

        if not elements_data:
            return f"\n\n## Elements Containing Text: '{text}'\n\nNo elements found.\n"

        markdown_output = [
            f"\n\n## Elements Containing Text: '{text}'\n",
            "| UUID | Tag | Text | Class | ID |\n",
            "|------|-----|------|-------|----|\n",
        ]

        for element in elements_data:
            xpath = element.get("xpath", "")
            if not xpath:
                continue

            element_uuid = str(uuid.uuid4())[:8]
            uuid_mapping[element_uuid] = xpath

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

        return "".join(markdown_output)

    except Exception as e:
        logger.error(f"Error extracting elements by text: {e}")
        return f"\n\n## Elements Containing Text: '{text}'\n\nError: {str(e)}\n"


def extract_input_elements(chrome_interface, uuid_mapping: Dict[str, str]) -> str:
    """
    Extract all input elements from the current webpage in a concise format.

    For each input element, extracts:
    - UUID: Short unique identifier for the element
    - Type: Input type (text, email, password, etc.)
    - Placeholder/Label: Placeholder text or associated label
    - Required: Whether the field is required
    - Disabled: Whether the field is disabled
    - Name: The name attribute of the element
    - Value: Current value of the element

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        uuid_mapping: Dictionary to store UUID to XPath mappings

    Returns:
        Concise markdown table with UUID, type, description, status, name, and value for each input element
    """
    try:
        # Load JavaScript code from external file
        js_code = js_loader.get_extract_input_elements_js()

        # Execute JavaScript to get input elements
        result = chrome_interface.Runtime.evaluate(
            expression=js_code, returnByValue=True
        )

        if isinstance(result, tuple) and len(result) >= 2:
            if isinstance(result[1], dict):
                elements_data = (
                    result[1].get("result", {}).get("result", {}).get("value", [])
                )
            elif isinstance(result[1], list) and len(result[1]) > 0:
                elements_data = (
                    result[1][0].get("result", {}).get("result", {}).get("value", [])
                )
            else:
                elements_data = []
        else:
            elements_data = []

        if not elements_data:
            return "\n\n## Input Elements\n\nNo input elements found on this page.\n"

        # Format input elements into concise markdown with UUID mapping
        markdown_output = []
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
            element_type = element.get("type", "")
            description = element.get("description", "").strip()
            required = "yes" if element.get("required", False) else "no"
            disabled = "yes" if element.get("disabled", False) else "no"
            name = element.get("name", "").strip()
            value = element.get("value", "").strip()

            # Generate UUID and store mapping
            element_uuid = str(uuid.uuid4())[:8]  # Use first 8 characters for brevity
            uuid_mapping[element_uuid] = xpath

            # Escape pipe characters for markdown table
            if description:
                description = description.replace("|", "\\|")
            else:
                description = "_no description_"

            element_type = element_type.replace("|", "\\|")
            name = name.replace("|", "\\|") if name else ""
            value = value.replace("|", "\\|") if value else ""

            # Truncate long values for better table readability
            if len(value) > 30:
                value = value[:27] + "..."

            markdown_output.append(
                f"| `{element_uuid}` | {element_type} | {description} | {required} | {disabled} | {name} | {value} |\n"
            )

        return "".join(markdown_output)

    except Exception as e:
        logger.error(f"Error extracting input elements: {e}")
        return f"\n\n## Input Elements\n\nError extracting input elements: {str(e)}\n"


def extract_scrollable_elements(chrome_interface, uuid_mapping: Dict[str, str]) -> str:
    """
    Extract all scrollable elements from the current webpage.

    Finds elements that have overflow: auto, scroll, or hidden and either:
    - scrollHeight > clientHeight (vertically scrollable)
    - scrollWidth > clientWidth (horizontally scrollable)

    For each scrollable element, extracts:
    - UUID: Short unique identifier for the element
    - Tag: HTML tag name
    - Scroll Info: Scrolling direction capabilities
    - Description: Element content or class/id info

    Args:
        chrome_interface: ChromeInterface object with enabled DOM
        uuid_mapping: Dictionary to store UUID to XPath mappings

    Returns:
        Markdown table with UUID and scrollable element details
    """
    try:
        # Load JavaScript code from external file
        js_code = js_loader.get_extract_scrollable_elements_js()

        # Execute JavaScript to get scrollable elements
        result = chrome_interface.Runtime.evaluate(
            expression=js_code, returnByValue=True
        )

        if isinstance(result, tuple) and len(result) >= 2:
            if isinstance(result[1], dict):
                elements_data = (
                    result[1].get("result", {}).get("result", {}).get("value", [])
                )
            elif isinstance(result[1], list) and len(result[1]) > 0:
                elements_data = (
                    result[1][0].get("result", {}).get("result", {}).get("value", [])
                )
            else:
                elements_data = []
        else:
            elements_data = []

        if not elements_data:
            return "\n\n## Scrollable Elements\n\nNo scrollable elements found on this page.\n"

        # Format scrollable elements into markdown with UUID mapping
        markdown_output = []
        markdown_output.append(
            "\n\n## Scrollable Elements\nUse browser_scroll with UUID and direction to scroll specific elements.\n"
        )
        markdown_output.append("| UUID | Tag | Scroll Direction | Description |\n")
        markdown_output.append("|------|-----|------------------|-------------|\n")

        for element in elements_data:
            xpath = element.get("xpath", "")
            tag_name = element.get("tagName", "")
            scroll_directions = element.get("scrollDirections", "")
            description = element.get("description", "").strip()

            # Skip elements without xpath
            if not xpath:
                continue

            # Generate UUID and store mapping
            element_uuid = str(uuid.uuid4())[:8]  # Use first 8 characters for brevity
            uuid_mapping[element_uuid] = xpath

            # Escape pipe characters for markdown table
            if description:
                description = description.replace("|", "\\|")
            else:
                description = "_no description_"

            tag_name = tag_name.replace("|", "\\|")
            scroll_directions = scroll_directions.replace("|", "\\|")

            markdown_output.append(
                f"| `{element_uuid}` | {tag_name} | {scroll_directions} | {description} |\n"
            )

        return "".join(markdown_output)

    except Exception as e:
        logger.error(f"Error extracting scrollable elements: {e}")
        return f"\n\n## Scrollable Elements\n\nError extracting scrollable elements: {str(e)}\n"
