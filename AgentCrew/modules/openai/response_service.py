import os
import json
import mimetypes
from typing import Dict, Any, List, Optional, Tuple
from openai import AsyncOpenAI
from dotenv import load_dotenv
from AgentCrew.modules.llm.base import BaseLLMService, read_binary_file, read_text_file
from AgentCrew.modules.llm.model_registry import ModelRegistry
from loguru import logger


class OpenAIResponseService(BaseLLMService):
    """OpenAI Response API implementation - next generation stateful conversations."""

    def __init__(self, api_key=None, base_url=None):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=base_url)

        # Set default model
        self.model = "gpt-4.1"
        self.tools = []  # Initialize empty tools list
        self.tool_handlers = {}  # Map tool names to handler functions
        self._provider_name = "openai"
        self.system_prompt = ""
        self.reasoning_effort = None
        self._extra_headers = None

        # Response API specific state management
        self.conversation_state = {}

        logger.info("Initialized OpenAI Response Service")

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
            elif budget_tokens not in ["minimal", "low", "medium", "high"]:
                raise ValueError("budget_tokens must be minimal, low, medium or high")

            self.reasoning_effort = budget_tokens
            return True
        logger.info("Thinking mode is not supported for this OpenAI model.")
        return False

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate the cost based on token usage."""
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

    def _convert_internal_format(self, messages: List[Dict[str, Any]]):
        """
        Convert Chat Completions messages format to Response API input format.
        """
        tool_call_list = {}
        for i, msg in enumerate(messages):
            msg.pop("agent", None)
            role = msg.get("role", "user")
            if isinstance(msg.get("content", ""), List):
                for part in msg["content"]:
                    if part.get("type") == "text":
                        part["type"] = (
                            "output_text" if role == "assistant" else "input_text"
                        )
                    elif part.get("type") == "image_url":
                        part["type"] = (
                            "output_image" if role == "assistant" else "input_image"
                        )
            if "tool_calls" in msg:
                tool_call_list[i] = msg.pop("tool_calls")
            if role == "tool":
                msg.pop("role", None)
                msg.pop("tool_name", None)
                msg["type"] = "function_call_output"
                msg["call_id"] = msg.pop("tool_call_id", None)
                msg["output"] = json.dumps(msg.pop("content", []))
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

    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        """Process a single message using Response API."""
        request_params = {"model": self.model, "input": prompt, "stream": False}
        if self._extra_headers:
            request_params["extra_headers"] = self._extra_headers

        # Add reasoning configuration if supported
        if self.reasoning_effort and "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            request_params["reasoning"] = {"effort": self.reasoning_effort}

        response = await self.client.responses.create(**request_params)

        # Extract usage information from Response API format
        input_tokens = getattr(response, "input_tokens", 0)
        output_tokens = getattr(response, "output_tokens", 0)
        total_cost = self.calculate_cost(input_tokens, output_tokens)

        logger.info("\nResponse API Token Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")
        logger.info(f"Response ID: {response.id}")

        # Return the output_text helper
        return response.output_text or ""

    def _process_file(self, file_path):
        """Process file - same as original implementation."""
        mime_type, _ = mimetypes.guess_type(file_path)

        if mime_type and mime_type.startswith("image/"):
            if "vision" not in ModelRegistry.get_model_capabilities(
                f"{self._provider_name}/{self.model}"
            ):
                return None
            image_data = read_binary_file(file_path)
            if image_data:
                message_content = {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_data}",
                        "detail": "high",
                    },
                }
                return message_content
        else:
            content = read_text_file(file_path)
            if content:
                message_content = {
                    "type": "text",
                    "text": f"Content of {file_path}:\n\n{content}",
                }

                logger.info(f"📄 Including text file: {file_path}")
                return message_content
            else:
                return None

    def process_file_for_message(self, file_path):
        """Process a file and return the appropriate message content."""
        return self._process_file(file_path)

    def handle_file_command(self, file_path):
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

    async def execute_tool(self, tool_name, tool_params):
        """Execute a registered tool with the given parameters."""
        if tool_name not in self.tool_handlers:
            raise ValueError(f"Tool '{tool_name}' not found")

        handler = self.tool_handlers[tool_name]
        result = handler(**tool_params)
        return result

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

        return await self.client.responses.create(**stream_params)

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: List[Dict]
    ) -> Tuple[str, List[Dict], int, int, Optional[str], Optional[tuple]]:
        """
        Process a single chunk from Response API streaming.
        Response API uses structured event objects with semantic types.
        """
        chunk_text = None
        input_tokens = 0
        output_tokens = 0
        thinking_content = None

        try:
            # Parse the chunk - it's an event object with type and data
            event_type = getattr(chunk, "type", None)

            if event_type == "response.created":
                # Response created event - contains response metadata
                response = getattr(chunk, "response", None)
                if response:
                    response_id = getattr(response, "id", None)
                    if response_id:
                        logger.debug(
                            f"Response API: New response created with ID {response_id}"
                        )

            elif event_type == "response.in_progress":
                # Response in progress - no specific action needed
                logger.debug("Response API: Response in progress")

            elif event_type == "response.output_item.added":
                # New output item started (message, reasoning, function call, etc.)
                item = getattr(chunk, "item", None)
                if item:
                    item_type = getattr(item, "type", None)
                    if item_type == "message":
                        logger.debug("Response API: New message output started")
                    elif item_type == "reasoning":
                        logger.debug("Response API: New reasoning output started")
                    elif item_type == "function_call":
                        # Handle tool use
                        idx = getattr(chunk, "output_index", len(tool_uses) + 1)
                        while len(tool_uses) < idx:
                            tool_uses.append({})
                        tool_call = {
                            "id": getattr(item, "call_id", ""),
                            "type": "function",
                            "name": getattr(item, "name", ""),
                            "input": {},
                        }
                        tool_uses[idx - 1] = tool_call
                        logger.debug(
                            f"Response API: New function call started: {tool_call['name']}"
                        )

            elif event_type == "response.content_part.added":
                # New content part added to an output item
                part = getattr(chunk, "part", None)
                if part:
                    part_type = getattr(part, "type", None)
                    if part_type == "output_text":
                        # Regular text content
                        text = getattr(part, "text", "")
                        chunk_text = text
                        assistant_response += text
                        logger.debug(
                            f"Response API: Text content added: {text[:50]}..."
                        )

            elif event_type == "response.output_text.delta":
                # Streaming text delta (incremental text updates)
                delta = getattr(chunk, "delta", "")
                if delta:
                    chunk_text = delta
                    assistant_response += delta
                    logger.debug(f"Response API: Text delta: {delta}")

            elif event_type == "response.output_text.done":
                # Text output completed
                text = getattr(chunk, "text", "")
                if text and not assistant_response.endswith(text):
                    # Sometimes the final text is provided here
                    chunk_text = text
                    assistant_response = text  # Replace with final complete text
                logger.debug("Response API: Text output completed")

            elif event_type == "response.function_call_arguments.delta":
                # Function call arguments streaming
                delta = getattr(chunk, "delta", "")
                tool_index = getattr(chunk, "output_index", len(tool_uses))

                # Find the corresponding tool use and update its arguments
                if len(tool_uses) >= tool_index:
                    tool_use = tool_uses[tool_index - 1]
                    if "arguments" not in tool_use:
                        tool_use["arguments"] = ""
                    tool_use["arguments"] += delta
                    logger.debug(
                        f"Response API: Function arguments delta for {tool_use.get('name')}: {delta}"
                    )

            elif event_type == "response.function_call_arguments.done":
                # Function call arguments completed
                arguments = getattr(chunk, "arguments", "")
                tool_index = getattr(chunk, "output_index", len(tool_uses))

                if len(tool_uses) >= tool_index:
                    tool_use = tool_uses[tool_index - 1]

                    try:
                        tool_use["input"] = json.loads(arguments) if arguments else {}
                        tool_use["arguments"] = arguments
                        logger.debug(
                            f"Response API: Function arguments completed for {tool_use.get('name')}"
                        )
                    except json.JSONDecodeError:
                        tool_use["input"] = {}
                        tool_use["arguments"] = arguments
                        logger.warning(
                            f"Response API: Invalid JSON in function arguments: {arguments}"
                        )

            elif event_type == "response.output_item.done":
                # Output item completed - may contain usage info
                item = getattr(chunk, "item", None)
                if item:
                    item_type = getattr(item, "type", None)
                    if item_type == "reasoning":
                        # Reasoning completed - extract thinking content
                        content = getattr(item, "content", None)
                        if content:
                            reasoning_content = []
                            for part in content:
                                if getattr(part, "type", None) == "output_text":
                                    reasoning_content.append(getattr(part, "text", ""))
                            if reasoning_content:
                                thinking_content = ("\n".join(reasoning_content), None)
                                logger.debug(
                                    "Response API: Reasoning content extracted"
                                )

            elif event_type == "response.completed":
                # Entire response completed
                response = getattr(chunk, "response", None)
                if response:
                    # Extract final usage information
                    usage = getattr(response, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0)
                        output_tokens = getattr(usage, "output_tokens", 0)

                        # Handle detailed output tokens if available
                        output_tokens_details = getattr(
                            usage, "output_tokens_details", None
                        )
                        if output_tokens_details:
                            reasoning_tokens = getattr(
                                output_tokens_details, "reasoning_tokens", 0
                            )
                            if reasoning_tokens > 0:
                                logger.debug(
                                    f"Response API: Reasoning tokens used: {reasoning_tokens}"
                                )

                        logger.debug(
                            f"Response API: Usage - Input: {input_tokens}, Output: {output_tokens}"
                        )

            else:
                # Log unhandled event types for debugging
                logger.debug(f"Response API: Unhandled event type: {event_type}")

        except Exception as e:
            logger.warning(f"Error processing Response API stream chunk: {e}")
            # Fallback to basic text extraction if available
            if hasattr(chunk, "text"):
                chunk_text = getattr(chunk, "text", "")
                assistant_response += chunk_text
            elif hasattr(chunk, "delta"):
                chunk_text = getattr(chunk, "delta", "")
                assistant_response += chunk_text

        return (
            assistant_response or "",
            tool_uses,
            input_tokens,
            output_tokens,
            chunk_text,
            thinking_content,
        )

    # def format_tool_result(
    #     self, tool_use: Dict, tool_result: Any, is_error: bool = False
    # ) -> Dict[str, Any]:
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
    #     self, assistant_response: str, tool_uses: Optional[List[Dict]] = None
    # ) -> Dict[str, Any]:
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

    # def format_thinking_message(self, thinking_data) -> Optional[Dict[str, Any]]:
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
        total_cost = self.calculate_cost(input_tokens, output_tokens)

        logger.info("\nResponse API Spec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
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

    async def get_response(self, response_id: str) -> Dict[str, Any]:
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
            raise Exception(f"Failed to retrieve response {response_id}: {str(e)}")

    async def cancel_response(self, response_id: str) -> bool:
        """Cancel a background response by ID."""
        try:
            await self.client.responses.cancel(response_id)
            logger.info(f"Successfully cancelled response: {response_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel response {response_id}: {str(e)}")
            return False

    async def delete_response(self, response_id: str) -> bool:
        """Delete a stored response by ID."""
        try:
            await self.client.responses.delete(response_id)
            logger.info(f"Successfully deleted response: {response_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete response {response_id}: {str(e)}")
            return False

    def get_conversation_state(self) -> Dict[str, Any]:
        """Get current conversation state information."""
        return {
            "conversation_state": self.conversation_state.copy(),
            "active_tools": len(self.tools),
            "model": self.model,
        }
