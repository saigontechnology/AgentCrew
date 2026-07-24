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


class OpenAIResponseService(BaseLLMService):
    """OpenAI Response API implementation - next generation stateful conversations."""

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
        self.system_prompt = ""
        self.reasoning_effort = None
        self._extra_headers = None

        # Response API specific state management
        self.conversation_state = {}

        logger.info("Initialized OpenAI Response Service")

    async def close(self):
        await self.client.close()

    def clear_conversation_state(self):
        """Clear conversation state and start fresh."""
        self.conversation_state = {}
        logger.info("Cleared conversation state")

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.
        """
        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            if budget_tokens == "0" or budget_tokens == "none":
                self.reasoning_effort = None
                return True
            if budget_tokens not in ["minimal", "low", "medium", "high", "xhigh"]:
                raise ValueError(
                    "budget_tokens must be minimal, low, medium, high or xhigh"
                )

            self.reasoning_effort = budget_tokens
            return True
        logger.info("Thinking mode is not supported for this OpenAI model.")
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
        """
        Convert Chat Completions messages format to Response API input format.
        """
        tool_call_list = {}
        for i, msg in enumerate(messages):
            msg.pop("agent", None)
            role = msg.get("role", "user")
            if role == "consolidated":
                msg["role"] = "user"
                msg.pop("metadata", None)
            elif role == "tool":
                msg.pop("role", None)
                msg.pop("tool_name", None)
                msg.pop("is_rejected", None)
                msg["type"] = "function_call_output"
                msg["call_id"] = msg.pop("tool_call_id", None)
                msg["output"] = json.dumps(msg.pop("content", []))
            elif role == "user":
                msg.pop("tool_call_id", None)

            if isinstance(msg.get("content", ""), list):
                for part in msg["content"]:
                    if part.get("type") == "text":
                        part["type"] = (
                            "output_text" if role == "assistant" else "input_text"
                        )
                    elif part.get("type") == "thinking":
                        part["type"] = "output_text"
                        part["text"] = f"<think>{part['thinking']}</think>"
                        part.pop("signature", None)
                    elif part.get("type") == "image_url":
                        image_url_value = part.get("image_url", {})
                        if isinstance(image_url_value, dict):
                            image_url_value = image_url_value.get("url", "")
                        part["type"] = (
                            "output_image" if role == "assistant" else "input_image"
                        )
                        part["image_url"] = image_url_value
                        part.pop("content", None)
            if "tool_calls" in msg:
                tool_call_list[i] = msg.pop("tool_calls")
        for idx, tool_calls in tool_call_list.items():
            for i, tool_call in enumerate(tool_calls):
                messages.insert(
                    idx + i + 1,
                    {
                        "type": "function_call",
                        "call_id": tool_call.get("id", ""),
                        "name": tool_call.get("name", ""),
                        "arguments": json.dumps(tool_call.get("arguments", "")),
                    },
                )
        return messages

    async def process_message(
        self,
        prompt: str | list,
        temperature: float = 0,
        model_id: str | None = None,
    ) -> str:
        """Process a single message using Response API with streaming."""
        request_params = {
            "model": model_id or self.model,
            "input": self._convert_internal_format(prompt)
            if isinstance(prompt, list)
            else prompt,
            "stream": True,
            "instructions": self.system_prompt or None,
            "service_tier": "default",
        }
        if self._extra_headers:
            request_params["extra_headers"] = self._extra_headers

        if self.reasoning_effort and "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            request_params["reasoning"] = {"effort": self.reasoning_effort}

        result_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        async for event in await self.client.responses.create(**request_params):
            if event.type == "response.output_text.delta":
                result_text += event.delta
            elif event.type == "response.completed":
                usage = getattr(event.response, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "input_tokens", 0)
                    output_tokens = getattr(usage, "output_tokens", 0)
                    input_tokens_details = getattr(usage, "input_tokens_details", None)
                    if input_tokens_details:
                        cached_tokens = getattr(
                            input_tokens_details, "cached_tokens", 0
                        )

        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nResponse API Token Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return result_text

    def _process_file(self, file_path):
        """Process file - same as original implementation."""
        return

    def process_file_for_message(self, file_path):
        """Process a file and return the appropriate message content."""
        return self._process_file(file_path)

    def handle_file_command(self, file_path: str) -> list[dict[str, Any]] | None:
        """Handle the /file command and return message content."""
        content = self._process_file(file_path)
        if content:
            return [content]
        return None

    def register_tool(self, tool_definition, handler_function):
        """
        Register a tool with its handler function.
        Response API uses flattened tool structure.
        """
        # Convert Chat Completions format to Response API format
        if "function" in tool_definition:
            # Convert from Chat Completions nested format
            converted_tool = {
                "type": "function",
                "name": tool_definition["function"]["name"],
                "description": tool_definition["function"].get("description", ""),
                "parameters": tool_definition["function"].get("parameters", {}),
            }
        else:
            # Already in Response API format
            converted_tool = tool_definition

        self.tools.append(converted_tool)

        tool_name = converted_tool["name"]
        self.tool_handlers[tool_name] = handler_function
        logger.info(f"🔧 Registered tool for Response API: {tool_name}")

    async def stream_assistant_response(self, messages) -> Any:
        """Stream the assistant's response using Response API."""

        # Convert messages to Response API input format
        input_data = self._convert_internal_format(messages)
        full_model_id = f"{self._provider_name}/{self.model}"

        stream_params = {
            "model": self.model,
            "input": input_data,
            "stream": True,
            "instructions": self.system_prompt or None,
        }

        forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
        if forced_sample_params:
            if forced_sample_params.temperature is not None:
                stream_params["temperature"] = forced_sample_params.temperature
            if forced_sample_params.top_p is not None:
                stream_params["top_p"] = forced_sample_params.top_p

        # Add reasoning configuration for thinking models
        if "thinking" in ModelRegistry.get_model_capabilities(full_model_id):
            if self.reasoning_effort:
                stream_params["reasoning"] = {"effort": self.reasoning_effort}

        if self._extra_headers:
            stream_params["extra_headers"] = self._extra_headers

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            full_model_id
        ):
            # Include both custom tools and built-in tools
            all_tools = self.tools.copy()

            # Add built-in tools if needed
            # all_tools.extend([
            #     {"type": "web_search"},
            #     {"type": "file_search"},
            #     {"type": "code_interpreter"}
            # ])

            stream_params["tools"] = all_tools

        if (
            "structured_output" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.structured_output
        ):
            stream_params["text"] = {
                "format": {
                    "name": "default",
                    "type": "json_schema",
                    "json_schema": self.structured_output,
                }
            }

        return await self.client.responses.create(**stream_params)

    @staticmethod
    def _get_or_create_tool_use(tool_uses: list[dict], output_index) -> dict:
        tool_use = next(
            (t for t in tool_uses if t.get("_output_index") == output_index), None
        )
        if tool_use is None:
            tool_use = {
                "id": "",
                "type": "function",
                "name": "",
                "input": {},
                "arguments": "",
                "_output_index": output_index,
                "_saw_args_delta": False,
            }
            tool_uses.append(tool_use)
        return tool_use

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        """
        Process a single chunk from Response API streaming.
        Response API uses structured event objects with semantic types.

        All per-stream parsing state lives inside ``tool_uses`` entries
        (via temporary ``_output_index`` / ``_saw_args_delta`` fields)
        so that concurrent streams remain isolated.
        """
        chunk_text = None
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None

        try:
            event_type = getattr(chunk, "type", None)

            if event_type == "response.created" or event_type == "response.in_progress":
                pass

            elif event_type == "response.output_item.added":
                item = getattr(chunk, "item", None)
                output_index = getattr(chunk, "output_index", None)
                if item:
                    item_type = getattr(item, "type", None)
                    if item_type == "function_call":
                        idx = (
                            output_index if output_index is not None else len(tool_uses)
                        )
                        tool_use = self._get_or_create_tool_use(tool_uses, idx)
                        if not tool_use["id"]:
                            tool_use["id"] = getattr(item, "call_id", "")
                        if not tool_use["name"]:
                            tool_use["name"] = getattr(item, "name", "")
                        logger.debug(
                            f"Response API: function_call registered: {tool_use['name']} (output_index={idx})"
                        )

            elif event_type == "response.content_part.added":
                part = getattr(chunk, "part", None)
                if part and getattr(part, "type", None) == "output_text":
                    text = getattr(part, "text", "")
                    chunk_text = text
                    assistant_response += text

            elif event_type == "response.output_text.delta":
                delta = getattr(chunk, "delta", "")
                if delta:
                    chunk_text = delta
                    assistant_response += delta

            elif event_type == "response.output_text.done":
                text = getattr(chunk, "text", "")
                if text and not assistant_response:
                    assistant_response = text
                    chunk_text = text

            elif event_type == "response.function_call_arguments.delta":
                delta = getattr(chunk, "delta", "")
                tool_index = getattr(chunk, "output_index", None)
                tool_use = self._get_or_create_tool_use(tool_uses, tool_index)
                tool_use["arguments"] += delta
                tool_use["_saw_args_delta"] = True
                try:
                    tool_use["input"] = json.loads(tool_use["arguments"])
                except (json.JSONDecodeError, ValueError):
                    pass

            elif event_type == "response.function_call_arguments.done":
                arguments = getattr(chunk, "arguments", "")
                tool_index = getattr(chunk, "output_index", None)
                tool_use = self._get_or_create_tool_use(tool_uses, tool_index)
                if not tool_use.get("_saw_args_delta") or not tool_use.get("input"):
                    tool_use["arguments"] = arguments
                    try:
                        tool_use["input"] = json.loads(arguments) if arguments else {}
                    except json.JSONDecodeError:
                        tool_use["input"] = {}
                        logger.warning(
                            f"Response API: invalid JSON arguments for tool output_index={tool_index}"
                        )

            elif event_type == "response.output_item.done":
                item = getattr(chunk, "item", None)
                output_index = getattr(chunk, "output_index", None)
                if item:
                    item_type = getattr(item, "type", None)
                    if item_type == "function_call":
                        tool_use = self._get_or_create_tool_use(tool_uses, output_index)
                        if not tool_use["id"]:
                            tool_use["id"] = getattr(item, "call_id", "")
                        if not tool_use["name"]:
                            tool_use["name"] = getattr(item, "name", "")
                        authoritative_args = getattr(item, "arguments", "")
                        if not tool_use.get("input") and authoritative_args:
                            try:
                                tool_use["input"] = json.loads(authoritative_args)
                                tool_use["arguments"] = authoritative_args
                            except json.JSONDecodeError:
                                logger.warning(
                                    f"Response API: fallback JSON error for '{tool_use.get('name')}'"
                                )
                    elif item_type == "reasoning":
                        content = getattr(item, "content", None)
                        if content:
                            reasoning_content = [
                                getattr(part, "text", "")
                                for part in content
                                if getattr(part, "type", None) == "output_text"
                            ]
                            if reasoning_content:
                                thinking_content = ("\n".join(reasoning_content), None)

            elif event_type == "response.completed":
                response = getattr(chunk, "response", None)
                if response:
                    usage = getattr(response, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0)
                        output_tokens = getattr(usage, "output_tokens", 0)
                        input_tokens_details = getattr(
                            usage, "input_tokens_details", None
                        )
                        if input_tokens_details:
                            cached_tokens = getattr(
                                input_tokens_details, "cached_tokens", 0
                            )
                        output_tokens_details = getattr(
                            usage, "output_tokens_details", None
                        )
                        if output_tokens_details:
                            reasoning_tokens = getattr(
                                output_tokens_details, "reasoning_tokens", 0
                            )
                            if reasoning_tokens > 0:
                                logger.debug(
                                    f"Response API: reasoning_tokens={reasoning_tokens}"
                                )
                        logger.info(
                            f"Response API: input_tokens={input_tokens} output_tokens={output_tokens}"
                        )

                clean_tool_uses = []
                for t in tool_uses:
                    if not t.get("name") and not t.get("arguments"):
                        continue
                    if not t.get("id"):
                        t["id"] = str(t.get("_output_index", ""))
                    if t.get("arguments") and not t.get("input"):
                        try:
                            t["input"] = json.loads(t["arguments"])
                        except (json.JSONDecodeError, ValueError):
                            pass
                    clean = {
                        k: v
                        for k, v in t.items()
                        if k not in ("_output_index", "_saw_args_delta")
                    }
                    clean_tool_uses.append(clean)
                tool_uses[:] = clean_tool_uses

            else:
                logger.debug(f"Response API: unhandled event_type={event_type}")

        except Exception as e:
            logger.warning(f"Response API stream chunk error: {e}")
            if hasattr(chunk, "text"):
                chunk_text = getattr(chunk, "text", "")
                assistant_response += chunk_text
            elif hasattr(chunk, "delta"):
                chunk_text = getattr(chunk, "delta", "")
                assistant_response += chunk_text

        return (
            assistant_response or "",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            thinking_content,
        )

    # def format_tool_result(
    #     self, tool_use: dict, tool_result: Any, is_error: bool = False
    # ) -> dict[str, Any]:
    #     """Format a tool result for Response API."""
    #     # Response API tool result format
    #     message = {
    #         "role": "tool",
    #         "tool_call_id": tool_use["id"],
    #         "content": tool_result,
    #     }
    #
    #     if is_error:
    #         message["content"] = f"ERROR: {str(message['content'])}"
    #
    #     return message

    # def format_assistant_message(
    #     self, assistant_response: str, tool_uses: list[dict] | None = None
    # ) -> dict[str, Any]:
    #     """Format the assistant's response for Response API."""
    #     if tool_uses and any(tu.get("id") for tu in tool_uses):
    #         return {
    #             "role": "assistant",
    #             "content": assistant_response,
    #             "tool_calls": [
    #                 {
    #                     "id": tool_use["id"],
    #                     "function": {
    #                         "name": tool_use["name"],
    #                         "arguments": json.dumps(tool_use["input"]),
    #                     },
    #                     "type": tool_use["type"],
    #                 }
    #                 for tool_use in tool_uses
    #                 if tool_use.get("id")
    #             ],
    #         }
    #     else:
    #         return {
    #             "role": "assistant",
    #             "content": assistant_response,
    #         }

    # def format_thinking_message(self, thinking_data) -> dict[str, Any] | None:
    #     """
    #     Format thinking content for Response API.
    #     Response API has native reasoning support.
    #     """
    #     if thinking_data:
    #         thinking_content, thinking_signature = thinking_data
    #         return {
    #             "role": "reasoning",
    #             "content": thinking_content,
    #             "reasoning_signature": thinking_signature,
    #         }
    #     return None

    async def validate_spec(self, prompt: str) -> str:
        """Validate a specification prompt using Response API."""
        request_params = {
            "model": self.model,
            "input": prompt,
            "text": {"format": "json_object"},  # Response API structured output
        }

        response = await self.client.responses.create(**request_params)

        # Calculate usage and cost
        input_tokens = getattr(response, "input_tokens", 0)
        output_tokens = getattr(response, "output_tokens", 0)
        cached_tokens = 0
        input_tokens_details = getattr(response, "input_tokens_details", None)
        if input_tokens_details:
            cached_tokens = getattr(input_tokens_details, "cached_tokens", 0)
        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nResponse API Spec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return response.output_text or ""

    def set_system_prompt(self, system_prompt: str):
        """Set the system prompt for the LLM service."""
        self.system_prompt = system_prompt

    def clear_tools(self):
        """Clear all registered tools from the LLM service."""
        self.tools = []
        self.tool_handlers = {}

    # Response API specific methods

    async def get_response(self, response_id: str) -> dict[str, Any]:
        """Retrieve a stored response by ID."""
        try:
            response = await self.client.responses.retrieve(response_id)
            return {
                "id": response.id,
                "output_text": response.output_text,
                "created_at": getattr(response, "created_at", None),
                "model": getattr(response, "model", self.model),
            }
        except Exception as e:
            raise Exception(f"Failed to retrieve response {response_id}: {e!s}")

    async def cancel_response(self, response_id: str) -> bool:
        """Cancel a background response by ID."""
        try:
            await self.client.responses.cancel(response_id)
            logger.info(f"Successfully cancelled response: {response_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel response {response_id}: {e!s}")
            return False

    async def delete_response(self, response_id: str) -> bool:
        """Delete a stored response by ID."""
        try:
            await self.client.responses.delete(response_id)
            logger.info(f"Successfully deleted response: {response_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete response {response_id}: {e!s}")
            return False

    def get_conversation_state(self) -> dict[str, Any]:
        """Get current conversation state information."""
        return {
            "conversation_state": self.conversation_state.copy(),
            "active_tools": len(self.tools),
            "model": self.model,
        }
