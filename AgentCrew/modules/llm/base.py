from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
import re
import json
import base64
from loguru import logger

if TYPE_CHECKING:
    from typing import List, Dict, Any, Optional, Tuple


def read_text_file(file_path):
    """Read and return the contents of a text file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        # try again with cp1252 encoding
        try:
            with open(file_path, "r", encoding="cp1252") as f:
                return f.read()
        except Exception as e:
            logger.error(f"❌ Error reading file {file_path}: {str(e)}")
            return None


def read_binary_file(file_path):
    """Read a binary file and return base64 encoded content."""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
        return base64.b64encode(content).decode("utf-8")
    except Exception as e:
        logger.error(f"❌ Error reading file {file_path}: {str(e)}")
        return None


def base64_to_bytes(base64_str: str):
    """Convert a base64 string to bytes."""
    try:
        return base64.b64decode(base64_str)
    except Exception as e:
        logger.error(f"❌ Error decoding base64: {str(e)}")
        return None


class AsyncIterator:
    def __init__(self, seq):
        self.iter = iter(seq)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.iter)
        except StopIteration:
            raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # No specific cleanup is needed for this simple iterator wrapper
        pass


class BaseLLMService(ABC):
    """Base interface for LLM services."""

    @property
    def provider_name(self) -> str:
        """Get the provider name for this service."""
        return getattr(self, "_provider_name", "unknown")

    @provider_name.setter
    def provider_name(self, value: str):
        """Set the provider name for this service."""
        self._provider_name = value

    @property
    def model(self) -> str:
        """Get the model for this service."""
        return getattr(self, "_model", "unknown")

    @model.setter
    def model(self, value: str):
        """Set the model for this service."""
        self._model = value

    @property
    def is_stream(self) -> bool:
        """Get the provider name for this service."""
        return getattr(self, "_is_stream", True)

    @property
    def temperature(self) -> float:
        """Get the temperature for this service."""
        return getattr(self, "_temperature", 0.4)

    @temperature.setter
    def temperature(self, value: float):
        self._temperature = value

    def _extract_tool_name(self, tool_def):
        """Extract tool name from definition regardless of format"""
        if "name" in tool_def:
            return tool_def["name"]
        elif "function" in tool_def and "name" in tool_def["function"]:
            return tool_def["function"]["name"]
        else:
            raise ValueError("Could not extract tool name from definition")

    def parse_user_context_summary(
        self,
        assistant_response: str,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Parses the <user_context_summary> JSON block from the beginning of a string.

        Args:
            raw_response: The raw string potentially containing the summary block
                        at the beginning.

        Returns:
            A tuple containing:
            - The parsed dictionary from the JSON block (or None if not found or invalid).
            - The rest of the string after the summary block (or the original
            string if the block wasn't found).
        """
        summary_data: Optional[Dict[str, Any]] = None
        cleaned_response: str = (
            assistant_response
            if not assistant_response.startswith("<user_context_summary>")
            and not assistant_response.startswith("```user_context_summary")
            else "Updating user context..."  # Default to original if no block found
        )

        # Regex explanation:
        # \s*                  - Match optional leading whitespace
        # <user_context_summary> - Match the opening tag literally (case-insensitive due to re.IGNORECASE)
        # (.*?)                - Match any character (non-greedy) inside the tags (Group 1: the JSON content)
        # </user_context_summary> - Match the closing tag literally (case-insensitive)
        # \s*                  - Match optional trailing whitespace after the block
        # (.*)                 - Match the rest of the string (Group 2: the cleaned response)
        # re.DOTALL            - Makes '.' match newline characters as well
        # re.IGNORECASE        - Makes the tag matching case-insensitive
        match = re.match(
            r"^(?:```json|```)?\s*<user_context_summary>(.*?)</user_context_summary>\s*(?:```)?(.*)",
            assistant_response,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            match = re.match(
                r"^```user_context_summary\n(.*?)\n```",
                assistant_response,
                re.DOTALL | re.IGNORECASE,
            )

        if match:
            summary_json_str = match.group(1).strip()
            # Potential optimization: If group 2 is empty, maybe assign original string minus matched part?
            # But group(2) correctly captures the rest, even if empty.
            cleaned_response = match.group(2).strip()

            try:
                summary_data = json.loads(summary_json_str)
                # Optional: Add validation here to check if the loaded data
                # has the expected keys (explicit_preferences, etc.)
                if not isinstance(summary_data, dict):
                    logger.warning(
                        f"WARNING: Parsed user context summary is not a dictionary: {type(summary_data)}"
                    )
                    summary_data = (
                        None  # Treat non-dict JSON as invalid for this purpose
                    )
                    # Revert cleaned_response if parsing fails? Or keep it cleaned?
                    # Let's keep it cleaned, assuming the block was intended but malformed.

            except json.JSONDecodeError as json_err:
                logger.error(
                    f"ERROR: Failed to parse user context JSON: {json_err}\nContent: <<< {summary_json_str} >>>"
                )
                summary_data = None  # Parsing failed
                # Keep cleaned_response as the block was likely intended but invalid.
            except Exception as e:
                logger.error(f"ERROR: Unexpected error parsing user context JSON: {e}")
                summary_data = None
                # Consider if unexpected errors should revert cleaned_response
                # Sticking with keeping it cleaned for now.

        # else: No match found, summary_data remains None, cleaned_response remains raw_response

        return summary_data, cleaned_response

    @abstractmethod
    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate the cost of a request based on token usage."""
        pass

    @abstractmethod
    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        """
        Process a user message and return the LLM's response.

        Args:
            prompt (str): The user's input message to be processed

        Returns:
            str: The processed response from the LLM
        """
        pass

    @abstractmethod
    def process_file_for_message(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Process a file and return the appropriate message content."""
        pass

    @abstractmethod
    def handle_file_command(self, file_path: str) -> Optional[List[Dict[str, Any]]]:
        """Handle the /file command and return message content."""
        pass

    @abstractmethod
    async def stream_assistant_response(self, messages: List[Dict[str, Any]]) -> Any:
        """Stream the assistant's response."""
        pass

    @abstractmethod
    def register_tool(self, tool_definition, handler_function):
        """
        Register a tool with its handler function.

        Args:
            tool_definition (dict): The tool definition following Anthropic's schema
            handler_function (callable): Function to call when tool is used
        """
        pass

    @abstractmethod
    async def execute_tool(self, tool_name, tool_params) -> Any:
        """
        Execute a registered tool with the given parameters.

        Args:
            tool_name (str): Name of the tool to execute
            tool_params (dict): Parameters to pass to the tool

        Returns:
            dict: Result of the tool execution
        """
        pass

    @abstractmethod
    def _convert_internal_format(self, messages: List[Dict[str, Any]]) -> Any:
        """
        Convert agent message format to the provider-specific format.
        """
        pass

    @abstractmethod
    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.

        Args:
            budget_tokens (int): Token budget for thinking. 0 to disable thinking mode.

        Returns:
            bool: True if thinking mode is supported and successfully set, False otherwise.
        """
        pass

    @abstractmethod
    def process_stream_chunk(
        self, chunk, assistant_response, tool_uses
    ) -> tuple[str, list[Dict] | None, int, int, str | None, tuple | None]:
        """
        Process a single chunk from the streaming response.

        Args:
            chunk: The chunk from the stream
            assistant_response: Current accumulated assistant response
            tool_uses: Current tool use information

        Returns:
            tuple: (
                updated_assistant_response (str),
                updated_tool_uses (List of dict or empty),
                input_tokens (int),
                output_tokens (int),
                chunk_text (str or None) - text to print for this chunk,
                thinking_content (tuple or None) - thinking content from this chunk
            )
        """
        pass

    @abstractmethod
    async def validate_spec(self, prompt: str) -> str:
        """
        Validate a specification prompt using the LLM.

        Args:
            prompt: The specification prompt to validate

        Returns:
            Validation result as a string (typically JSON)
        """
        pass

    @abstractmethod
    def set_system_prompt(self, system_prompt: str):
        """
        Set the system prompt for the LLM service.

        Args:
            system_prompt: The system prompt to use
        """
        pass

    @abstractmethod
    def clear_tools(self):
        """
        Clear all registered tools from the LLM service.
        """
        pass
