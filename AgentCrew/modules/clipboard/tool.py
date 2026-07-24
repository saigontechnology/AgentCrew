from collections.abc import Callable
from typing import Any

from AgentCrew.modules.utils.file_handler import optimize_image_data_uri

from .service import ClipboardService


def get_clipboard_read_tool_definition() -> dict[str, Any]:
    """
    Get the tool definition for reading from clipboard based on provider.

    Args:
        provider: The LLM provider ("claude" or another OpenAI-compatible provider)

    Returns:
        dict containing the tool definition
    """
    tool_description = "Reads the current content from the system clipboard. Automatically detects whether the content is text or an image. Use this to access data the user may have copied from another application. Useful when the user refers to content they've copied from another source without explicitly providing it. If the user seems to be referencing external content without providing it, consider using this tool."
    tool_arguments = {}
    tool_required = []
    return {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_clipboard_write_tool_definition() -> dict[str, Any]:
    """
    Get the tool definition for writing to clipboard based on provider.

    Args:
        provider: The LLM provider ("claude" or another OpenAI-compatible provider)

    Returns:
        dict containing the tool definition
    """
    tool_description = "Writes content to the system clipboard, making it accessible for pasting into other applications. Use this to provide information to the user in a readily accessible format. Always explain *why* you are writing to the clipboard and what the user should do with the copied content. For example, 'I have written the generated code snippet to your clipboard. You can now paste it into your code editor."
    tool_arguments = {
        "content": {
            "type": "string",
            "description": "The text or data to write to the clipboard. Ensure the content is properly formatted for the intended use and is clear and concise.",
        },
    }
    tool_required = ["content"]
    return {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_clipboard_read_tool_handler(clipboard_service: ClipboardService) -> Callable:
    """
    Get the handler function for the clipboard read tool.

    Args:
        clipboard_service: The clipboard service instance

    Returns:
        Function that handles clipboard read requests
    """

    async def handle_clipboard_read() -> str | list[dict[str, Any]]:
        result = clipboard_service.read()
        if result["type"] == "image":
            image_data = result["content"]
            media_type = "image/png"
            data_uri = optimize_image_data_uri(f"data:{media_type};base64,{image_data}")
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                }
            ]
        else:
            return result["content"]

    return handle_clipboard_read


def get_clipboard_write_tool_handler(clipboard_service: ClipboardService) -> Callable:
    """
    Get the handler function for the clipboard write tool.

    Args:
        clipboard_service: The clipboard service instance

    Returns:
        Function that handles clipboard write requests
    """

    async def handle_clipboard_write(**params) -> str | list[dict[str, Any]]:
        content = params.get("content")
        if not content:
            raise ValueError("Invalid Argument")

        result = clipboard_service.write(content)
        if result["success"]:
            return result["message"]
        else:
            raise RuntimeError("Cannot write to clipboard")

    return handle_clipboard_write


def register(service_instance=None, agent=None):
    """
    Register this tool with the central registry or directly with an agent

    Args:
        service_instance: The clipboard service instance
        agent: Agent instance to register with directly (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_clipboard_read_tool_definition,
        get_clipboard_read_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_clipboard_write_tool_definition,
        get_clipboard_write_tool_handler,
        service_instance,
        agent,
    )
