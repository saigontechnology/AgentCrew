import os
import mimetypes
import re
from typing import Dict, Any, List, Union
from anthropic import AsyncAnthropic
from anthropic.types import TextBlock
from dotenv import load_dotenv
from AgentCrew.modules.llm.base import BaseLLMService, read_binary_file, read_text_file
from AgentCrew.modules.llm.model_registry import ModelRegistry
from loguru import logger


class AnthropicService(BaseLLMService):
    """Anthropic-specific implementation of the LLM service."""

    def __init__(self):
        load_dotenv()
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment variables")
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = "claude-3-7-sonnet-latest"
        # self.model = "claude-3-5-haiku-latest"
        self.tools = []  # Initialize empty tools list
        self.tool_handlers = {}  # Map tool names to handler functions
        self.thinking_enabled = False
        self.thinking_budget = 0
        self.caching_blocks = 0
        self._provider_name = "claude"
        self.system_prompt = ""
        logger.info("Initialized Anthropic Service")

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        current_model = ModelRegistry.get_instance().get_model(
            f"{self._provider_name}/{self.model}"
        )
        if current_model:
            input_cost = (input_tokens / 1_000_000) * current_model.input_token_price_1m
            output_cost = (
                output_tokens / 1_000_000
            ) * current_model.output_token_price_1m
            return input_cost + output_cost
        return 0.0

    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        """Summarize the provided content using Claude."""
        message = await self.client.messages.create(
            model=self.model,
            temperature=temperature,
            max_tokens=3000,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        content_block = message.content[0]
        if not isinstance(content_block, TextBlock):
            raise ValueError(
                "Unexpected response type: message content is not a TextBlock"
            )

        # Calculate and log token usage and cost
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens)

        logger.info("\nToken Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return content_block.text

    def _process_file(self, file_path, for_command=False):
        """
        Process a file and return the appropriate message content.

        Args:
            file_path: Path to the file to process
            for_command: If True, format for /file command with different text prefix

        Returns:
            Content object for the file or None if processing failed
        """
        mime_type, _ = mimetypes.guess_type(file_path)

        if mime_type == "application/pdf":
            pdf_data = read_binary_file(file_path)
            if pdf_data:
                logger.info(f"📄 Including PDF document: {file_path}")
                return {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_data,
                    },
                }
        elif mime_type and mime_type.startswith("image/"):
            if "vision" not in ModelRegistry.get_model_capabilities(
                f"{self._provider_name}/{self.model}"
            ):
                return None
            image_data = read_binary_file(file_path)
            if image_data:
                logger.info(f"🖼️ Including image: {file_path}")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_data,
                    },
                }
        else:
            content = read_text_file(file_path)
            if content:
                logger.info(f"📄 Including text file: {file_path}")
                return {
                    "type": "text",
                    "text": f"Content of {file_path}:\n\n{content}",
                }

        return None

    def process_file_for_message(self, file_path):
        """Process a file and return the appropriate message content."""
        return self._process_file(file_path, for_command=False)

    def handle_file_command(self, file_path):
        """Handle the /file command and return message content."""
        content = self._process_file(file_path, for_command=True)
        if content:
            return [content]
        return None

    def register_tool(self, tool_definition, handler_function):
        """
        Register a tool with its handler function.

        Args:
            tool_definition (dict): The tool definition following Anthropic's schema
            handler_function (callable): Function to call when tool is used
        """
        self.tools.append(tool_definition)
        self.tool_handlers[tool_definition["name"]] = handler_function
        logger.info(f"🔧 Registered tool: {tool_definition['name']}")

    async def execute_tool(self, tool_name, tool_params):
        """
        Execute a registered tool with the given parameters.

        Args:
            tool_name (str): Name of the tool to execute
            tool_params (dict): Parameters to pass to the tool

        Returns:
            dict: Result of the tool execution
        """
        if tool_name not in self.tool_handlers:
            raise ValueError(f"Tool '{tool_name}' not found")

        handler = self.tool_handlers[tool_name]
        result = handler(**tool_params)
        return result

    def _convert_content_to_claude_format(
        self,
        content: Union[Dict[str, Any], List[Dict[str, Any]], str],
    ):
        new_content = None

        pattern = r"^data:([^;]+);base64,(.*)$"
        if isinstance(content, Dict):
            if content.get("type", "text") == "image_url":
                data_url = content.get("image_url", {}).get("url", "")
                match = re.match(pattern, data_url, re.DOTALL)

                if match:
                    mime_type = match.group(1)
                    base64_data = match.group(2)
                    new_content = {
                        "type": "image",
                        "source": {
                            "media_type": mime_type,
                            "data": base64_data,
                            "type": "base64",
                        },
                    }
                    return new_content
            else:
                return content
        elif isinstance(content, List):
            new_content = []
            for c in content:
                new_content.append(self._convert_content_to_claude_format(c))
            return new_content
        else:
            return content
        return content

    def _convert_internal_format(self, messages: List[Dict[str, Any]]) -> Any:
        claude_messages = []
        for msg in messages:
            claude_msg = {"role": msg.get("role", "")}
            if claude_msg["role"] == "tool":
                claude_msg["role"] = "user"
            elif claude_msg["role"] == "consolidated":
                claude_msg["role"] = "user"
            # Handle content
            if "content" in msg:
                if msg.get("role") == "assistant" and "tool_calls" in msg:
                    if isinstance(msg["content"], List):
                        claude_msg["content"] = list(msg["content"])
                    else:
                        if msg["content"] == "":
                            msg["content"] = " "
                        claude_msg["content"] = [
                            {"type": "text", "text": msg["content"]}
                        ]

                    # Add tool use blocks
                    for tool_call in msg.get("tool_calls", []):
                        tool_use = {
                            "type": "tool_use",
                            "id": tool_call.get("id", ""),
                            "name": tool_call.get("name", ""),
                            "input": tool_call.get("arguments", {}),
                        }

                        claude_msg["content"].append(tool_use)
                elif msg.get("role") == "tool":
                    tool_result = {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": self._convert_content_to_claude_format(
                            msg.get("content", "")
                        ),
                    }

                    if msg.get("is_error", False):
                        tool_result["is_error"] = True

                    if isinstance(tool_result["content"], list):
                        for content_item in tool_result["content"]:
                            if isinstance(content_item, dict):
                                if "annotations" in content_item:
                                    del content_item["annotations"]

                    claude_msg["content"] = [tool_result]
                else:
                    # Regular content
                    if msg["content"] is str:
                        claude_msg["content"] = [
                            {"type": "text", "text": msg["content"]}
                        ]
                    else:
                        claude_msg["content"] = self._convert_content_to_claude_format(
                            msg["content"]
                        )

            claude_messages.append(claude_msg)
        return claude_messages

    def process_stream_chunk(self, chunk, assistant_response, tool_uses):
        """
        Process a single chunk from the Anthropic streaming response.

        Args:
            chunk: The chunk from the stream
            assistant_response: Current accumulated assistant response
            tool_use: Current tool use information

        Returns:
            tuple: (
                updated_assistant_response (str),
                updated_tool_use (dict or None),
                input_tokens (int),
                output_tokens (int),
                chunk_text (str or None) - text to print for this chunk,
                thinking_data (tuple or None) - thinking content from this chunk
            )
        """
        chunk_text = None
        thinking_content = None
        thinking_signature = None
        input_tokens = 0
        output_tokens = 0

        if chunk.type == "content_block_delta" and hasattr(chunk.delta, "text"):
            chunk_text = chunk.delta.text
            assistant_response += chunk_text
        elif chunk.type == "content_block_delta" and hasattr(chunk.delta, "thinking"):
            # Process thinking content
            thinking_content = chunk.delta.thinking
        elif chunk.type == "content_block_delta" and hasattr(chunk.delta, "signature"):
            # Capture thinking signature
            thinking_signature = chunk.delta.signature
        elif (
            chunk.type == "message_start"
            and hasattr(chunk, "message")
            and hasattr(chunk.message, "usage")
        ):
            if hasattr(chunk.message.usage, "input_tokens"):
                input_tokens = chunk.message.usage.input_tokens
        elif chunk.type == "message_delta" and hasattr(chunk, "usage") and chunk.usage:
            if hasattr(chunk.usage, "output_tokens"):
                output_tokens = chunk.usage.output_tokens
        elif chunk.type == "message_stop" and hasattr(chunk, "message"):
            if (
                hasattr(chunk.message, "stop_reason")
                and chunk.message.stop_reason == "refusal"
            ):
                raise ValueError(
                    "Request has been refused. Please create new conversation or rollback to older message."
                )

            elif (
                hasattr(chunk.message, "stop_reason")
                and chunk.message.stop_reason == "tool_use"
                and hasattr(chunk.message, "content")
            ):
                # Extract tool use information
                logger.info(chunk.message.content)
                for content_block in chunk.message.content:
                    if (
                        hasattr(content_block, "type")
                        and content_block.type == "tool_use"
                    ):
                        if not tool_uses:
                            tool_uses = []
                        tool_uses.append(
                            {
                                "name": content_block.name,
                                "input": content_block.input,
                                "id": content_block.id,
                                "response": content_block,
                            }
                        )
                    # elif (
                    #     hasattr(content_block, "type")
                    #     and content_block.type == "thinking"
                    # ):
                    #     # Store thinking content and signature from final message
                    #     thinking_content = content_block.thinking
                    #     if hasattr(content_block, "signature"):
                    #         thinking_signature = content_block.signature

        # Return thinking_signature as part of the thinking_content
        # We'll use a tuple to return both thinking content and signature
        thinking_data = None
        if thinking_content is not None or thinking_signature is not None:
            thinking_data = (thinking_content, thinking_signature)

        return (
            assistant_response,
            tool_uses,
            input_tokens,
            output_tokens,
            chunk_text,
            thinking_data,
        )

    async def validate_spec(self, prompt: str) -> str:
        """
        Validate a specification prompt using Anthropic Claude.

        Args:
            prompt: The specification prompt to validate

        Returns:
            Validation result as a JSON string
        """

        message = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        content_block = message.content[0]
        if not isinstance(content_block, TextBlock):
            raise ValueError(
                "Unexpected response type: message content is not a TextBlock"
            )

        # Calculate and log token usage and cost
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens)

        logger.info("\nSpec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return content_block.text

    def set_system_prompt(self, system_prompt: str):
        """
        Set the system prompt for the LLM service.

        Args:
            system_prompt: The system prompt to use
        """
        self.system_prompt = system_prompt

    def clear_tools(self):
        """
        Clear all registered tools from the LLM service.
        """
        self.tools = []
        self.tool_handlers = {}

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.

        Args:
            budget_tokens (int): Token budget for thinking. 0 to disable thinking mode.

        Returns:
            bool: True if thinking mode is supported and successfully set, False otherwise.
        """
        budget_tokens = int(budget_tokens)
        if budget_tokens == 0:
            self.thinking_enabled = False
            self.thinking_budget = 0
            logger.info("Thinking mode disabled.")
            return True
        if "thinking" not in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            logger.warning("Thinking mode is disabled for this model.")
            return False

        # Ensure minimum budget is 1024 tokens
        if budget_tokens < 1024:
            logger.warning("Minimum thinking budget is 1024 tokens. Setting to 1024.")
            budget_tokens = 1024

        self.thinking_enabled = True
        self.thinking_budget = budget_tokens
        logger.info(f"Thinking mode enabled with budget of {budget_tokens} tokens.")
        return True

    async def stream_assistant_response(self, messages):
        """Stream the assistant's response with tool support."""
        # first cache for system prompt and tool
        if self.caching_blocks == 0:
            messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
            self.caching_blocks += 1
        stream_params = {
            "model": self.model,
            "max_tokens": 20000,
            "system": self.system_prompt,
            "messages": self._convert_internal_format(messages),
            "top_p": 0.95,
            "temperature": self.temperature / 2,  # agent temperature scales at 2,
        }

        # Add thinking configuration if enabled
        if self.thinking_enabled:
            stream_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }
            stream_params.pop("top_p", None)
            stream_params.pop("temperature", None)
        if self.model == "claude-sonnet-4-5":
            stream_params.pop("top_p", None)
        # else:
        #     stream_params["temperature"] = 0.7

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            stream_params["tools"] = self.tools
        return self.client.messages.stream(**stream_params)
