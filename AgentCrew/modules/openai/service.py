import json
import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI

from AgentCrew.modules.llm.base import (
    BaseLLMService,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage


class OpenAIService(BaseLLMService):
    """OpenAI-specific implementation of the LLM service."""

    def __init__(self, api_key=None, base_url=None):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        if not self.api_key:
            logger.error("OPENAI_API_KEY not found in environment variables")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        # Set default model
        self.model = "gpt-5.4"
        self.tools = []  # Initialize empty tools list
        self.tool_handlers = {}  # Map tool names to handler functions
        self._provider_name = "openai"
        self.system_prompt = "You are a helpful assistant"
        self.reasoning_effort = None
        logger.info("Initialized OpenAI Service")

    async def close(self):
        await self.client.close()

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.

        Args:
            budget_tokens (int): Token budget for thinking. 0 to disable thinking mode.

        Returns:
            bool: True if thinking mode is supported and successfully set, False otherwise.
        """
        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            if budget_tokens == "0" or budget_tokens == "none":
                self.reasoning_effort = None
                return True
            elif budget_tokens not in ["minimal", "low", "medium", "high"]:
                raise ValueError("budget_tokens must be low, medium or high")

            self.reasoning_effort = budget_tokens
            return True
        logger.info("Thinking mode is not supported for OpenAI models.")
        return False

    def calculate_cost(
        self, input_tokens: int, output_tokens: int, cached_tokens: int = 0
    ) -> float:
        """Calculate the cost based on token usage."""
        current_model = ModelRegistry.get_instance().get_model(
            f"{self._provider_name}/{self.model}"
        )
        if current_model:
            input_cost = (input_tokens / 1_000_000) * current_model.input_token_price_1m
            output_cost = (
                output_tokens / 1_000_000
            ) * current_model.output_token_price_1m
            cached_cost = (
                cached_tokens / 1_000_000
            ) * current_model.cached_token_price_1m
            return input_cost + output_cost + cached_cost
        return 0.0

    def _convert_internal_format(self, messages: list[dict[str, Any]]):
        for msg in messages:
            msg.pop("agent", None)
            msg.pop("tool_name", None)
            if msg.get("role") == "consolidated":
                msg["role"] = "user"
                msg.pop("metadata", None)
            if msg.get("role") == "assistant":
                if isinstance(msg.get("content", ""), list):
                    thinking_block = next(
                        (
                            block
                            for block in msg.get("content", [])
                            if block.get("type", "text") == "thinking"
                        ),
                        None,
                    )
                    msg["content"] = (
                        "\n".join(
                            [
                                block.get("text", "")
                                for block in msg["content"]
                                if block.get("type", "text") != "thinking"
                            ]
                        )
                        or " "
                    )
                    if thinking_block:
                        msg["reasoning_text"] = thinking_block.get("thinking", "")
            elif msg.get("role") == "user":
                msg.pop("tool_call_id", None)

            if "tool_calls" in msg and msg.get("tool_calls", []):
                for tool_call in msg["tool_calls"]:
                    tool_call["function"] = {}
                    tool_call["function"]["name"] = tool_call.pop("name", "")
                    tool_call["function"]["arguments"] = json.dumps(
                        tool_call.pop("arguments", {})
                    )

        return messages

    async def process_message(
        self,
        prompt: str | list,
        temperature: float = 0,
        model_id: str | None = None,
    ) -> str:
        result_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        request_params = {
            "model": model_id or self.model,
            "max_tokens": 3000,
            "temperature": temperature,
            "stream": True,
            "messages": (
                [
                    {"role": "system", "content": self.system_prompt},
                    *prompt,
                ]
                if isinstance(prompt, list)
                else [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ]
            ),
            "stream_options": {"include_usage": True},
        }

        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            request_params["reasoning_effort"] = "none"

        stream = await self.client.chat.completions.create(**request_params)

        async for chunk in stream:
            if (
                chunk.choices
                and hasattr(chunk.choices[0].delta, "content")
                and chunk.choices[0].delta.content is not None
            ):
                result_text += chunk.choices[0].delta.content
            if hasattr(chunk, "usage") and chunk.usage:
                if hasattr(chunk.usage, "prompt_tokens"):
                    input_tokens = chunk.usage.prompt_tokens
                if hasattr(chunk.usage, "completion_tokens"):
                    output_tokens = chunk.usage.completion_tokens
                if (
                    hasattr(chunk.usage, "prompt_tokens_details")
                    and chunk.usage.prompt_tokens_details
                ) and hasattr(chunk.usage.prompt_tokens_details, "cached_tokens"):
                    cached_tokens = chunk.usage.prompt_tokens_details.cached_tokens or 0

        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nToken Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return result_text

    def _process_file(self, file_path):
        return None

    def process_file_for_message(self, file_path):
        """Process a file and return the appropriate message content."""

        return self._process_file(file_path)

    def handle_file_command(self, file_path):
        """Handle the /file command and return message content."""
        return

    def register_tool(self, tool_definition, handler_function):
        """
        Register a tool with its handler function.

        Args:
            tool_definition (dict): The tool definition following OpenAI's function schema
            handler_function (callable): Function to call when tool is used
        """
        self.tools.append(tool_definition)

        # Extract the name based on tool structure
        if "function" in tool_definition:
            tool_name = tool_definition["function"]["name"]
        elif "name" in tool_definition:
            tool_name = tool_definition["name"]
        else:
            raise ValueError("Tool definition must contain a name")

        self.tool_handlers[tool_name] = handler_function
        logger.info(f"🔧 Registered tool: {tool_name}")

    async def stream_assistant_response(self, messages) -> Any:
        """Stream the assistant's response with tool support."""
        full_model_id = f"{self._provider_name}/{self.model}"
        stream_params = {
            "model": self.model,
            "messages": self._convert_internal_format(messages),
            "parallel_tool_calls": False,
            "stream_options": {"include_usage": True},
            "max_tokens": 20000,
        }

        if "thinking" in ModelRegistry.get_model_capabilities(full_model_id):
            stream_params.pop("max_tokens", None)
            if self.reasoning_effort:
                stream_params["reasoning_effort"] = self.reasoning_effort
        else:
            stream_params["temperature"] = self.temperature
            stream_params["top_p"] = 0.95
            forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
            if forced_sample_params:
                if forced_sample_params.temperature is not None:
                    stream_params["temperature"] = forced_sample_params.temperature
                if forced_sample_params.top_p is not None:
                    stream_params["top_p"] = forced_sample_params.top_p
                if forced_sample_params.frequency_penalty is not None:
                    stream_params["frequency_penalty"] = (
                        forced_sample_params.frequency_penalty
                    )
                if forced_sample_params.presence_penalty is not None:
                    stream_params["presence_penalty"] = (
                        forced_sample_params.presence_penalty
                    )

        # Add system message if provided
        if self.system_prompt:
            stream_params["messages"] = [
                {"role": "system", "content": self.system_prompt}
            ] + messages

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            full_model_id
        ):
            stream_params["tools"] = self.tools

        if (
            "structured_output" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.structured_output
        ):
            stream_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "default",
                    "schema": self.structured_output,
                },
            }

        return await self.client.chat.completions.create(**stream_params, stream=True)

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        """
        Process a single chunk from the streaming response.

        Args:
            chunk: The chunk from the stream
            assistant_response: Current accumulated assistant response
            tool_uses: Current tool use information

        Returns:
            tuple: (
                updated_assistant_response,
                updated_tool_uses,
                token_usage (TokenUsage),
                chunk_text,
                thinking_data
            )
        """
        chunk_text = None
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None

        if (
            len(chunk.choices) > 0
            and hasattr(chunk.choices[0].delta, "content")
            and chunk.choices[0].delta.content is not None
        ):
            chunk_text = chunk.choices[0].delta.content
            assistant_response += chunk_text

        if hasattr(chunk, "usage"):
            if hasattr(chunk.usage, "prompt_tokens"):
                input_tokens = chunk.usage.prompt_tokens
            if hasattr(chunk.usage, "completion_tokens"):
                output_tokens = chunk.usage.completion_tokens
            if (
                hasattr(chunk.usage, "prompt_tokens_details")
                and chunk.usage.prompt_tokens_details
            ) and hasattr(chunk.usage.prompt_tokens_details, "cached_tokens"):
                cached_tokens = chunk.usage.prompt_tokens_details.cached_tokens or 0

        # Handle tool call chunks
        if len(chunk.choices) > 0 and hasattr(chunk.choices[0].delta, "tool_calls"):
            delta_tool_calls = chunk.choices[0].delta.tool_calls
            if delta_tool_calls:
                # Process each tool call in the delta
                for tool_call_delta in delta_tool_calls:
                    tool_call_index = tool_call_delta.index

                    # Check if this is a new tool call
                    if tool_call_index >= len(tool_uses):
                        # Create a new tool call entry
                        tool_uses.append(
                            {
                                "id": tool_call_delta.id
                                if hasattr(tool_call_delta, "id")
                                else None,
                                "name": getattr(tool_call_delta.function, "name", "")
                                if hasattr(tool_call_delta, "function")
                                else "",
                                "input": {},
                                "type": "function",
                                "response": "",
                            }
                        )

                    # Update existing tool call with new data
                    if hasattr(tool_call_delta, "id") and tool_call_delta.id:
                        tool_uses[tool_call_index]["id"] = tool_call_delta.id

                    if hasattr(tool_call_delta, "function"):
                        if (
                            hasattr(tool_call_delta.function, "name")
                            and tool_call_delta.function.name
                        ):
                            tool_uses[tool_call_index]["name"] = (
                                tool_call_delta.function.name
                            )

                        if (
                            hasattr(tool_call_delta.function, "arguments")
                            and tool_call_delta.function.arguments
                        ):
                            # Accumulate arguments as they come in chunks
                            current_args = tool_uses[tool_call_index].get(
                                "args_json", ""
                            )
                            tool_uses[tool_call_index]["args_json"] = (
                                current_args + tool_call_delta.function.arguments
                            )

                            # Try to parse JSON if it seems complete
                            try:
                                args_json = tool_uses[tool_call_index]["args_json"]
                                tool_uses[tool_call_index]["input"] = json.loads(
                                    args_json
                                )
                                # Keep args_json for accumulation but use input for execution
                            except json.JSONDecodeError:
                                # Arguments JSON is still incomplete, keep accumulating
                                pass

                return (
                    assistant_response or " ",
                    tool_uses,
                    TokenUsage(
                        input_tokens=input_tokens - cached_tokens,
                        output_tokens=output_tokens,
                        cached_tokens=cached_tokens,
                    ),
                    "",
                    thinking_content,
                )

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            thinking_content,
        )

    async def validate_spec(self, prompt: str) -> str:
        """
        Validate a specification prompt using OpenAI.

        Args:
            prompt: The specification prompt to validate

        Returns:
            Validation result as a JSON string
        """

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            response_format={"type": "json_object"},
        )

        # Calculate and log token usage and cost
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cached_tokens = 0
        if (
            response.usage
            and hasattr(response.usage, "prompt_tokens_details")
            and response.usage.prompt_tokens_details
        ) and hasattr(response.usage.prompt_tokens_details, "cached_tokens"):
            cached_tokens = response.usage.prompt_tokens_details.cached_tokens or 0
        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nSpec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return response.choices[0].message.content or ""

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
