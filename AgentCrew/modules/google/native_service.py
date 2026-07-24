import os
import re
import json
from typing import Any, Tuple
from dotenv import load_dotenv
from google import genai
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage
from google.genai import types
from AgentCrew.modules.llm.base import BaseLLMService, base64_to_bytes
from loguru import logger
import traceback


class GoogleStreamAdapter:
    """
    Adapter class that wraps Google GenAI streaming response to support async context manager protocol.
    """

    def __init__(self, stream_generator):
        """
        Initialize the adapter with a Google GenAI stream generator.

        Args:
            stream_generator: The generator returned by generate_content_stream
        """
        self.stream_generator = stream_generator

    async def __aenter__(self):
        """Enter the async context manager, returning self as the async iterable."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager, handling cleanup if needed."""
        # No specific cleanup needed for Google GenAI stream
        pass

    def __aiter__(self):
        """Return an async iterator for the stream."""
        return self

    async def __anext__(self):
        """Get the next chunk from the stream generator."""
        try:
            return await self.stream_generator.__anext__()
        except StopAsyncIteration:
            raise
        except StopIteration:
            raise StopAsyncIteration
        except Exception as e:
            # Handle any Google GenAI specific exceptions
            logger.error(f"Error in Google GenAI stream: {str(e)}")
            traceback.print_exc()
            raise StopAsyncIteration


class GoogleAINativeService(BaseLLMService):
    """
    Google GenAI service implementation using the native Python SDK.
    This service connects to Google's Gemini models using the official Google GenAI SDK.
    """

    def __init__(self, api_key=None, base_url=None):
        """Initialize the Google GenAI service."""
        load_dotenv()
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        base_url = base_url or os.getenv("GEMINI_BASE_URL")
        if not api_key:
            logger.error("GEMINI_API_KEY not found in environment variables")

        # Initialize the Google GenAI client
        http_options = types.HttpOptions(base_url=base_url) if base_url else None
        self.client = genai.Client(api_key=api_key, http_options=http_options)

        # Default model
        self.model = "gemini-3.6-flash"

        # Initialize tools and handlers
        self.tools = []
        self.tool_handlers = {}
        self.tool_definitions = []  # Keep original definitions for reference

        self.thinking_enabled = True
        self.reasoning_effort: types.ThinkingLevel = types.ThinkingLevel.HIGH

        # Provider name and system prompt
        self._provider_name = "google"
        self.system_prompt = ""
        logger.info("Initialized Google Service")

    async def close(self):
        self.client.close()

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.
        Currently not supported in Google GenAI.

        Args:
            budget_tokens (int): Token budget for thinking. 0 to disable thinking mode.

        Returns:
            bool: True if thinking mode is supported and successfully set, False otherwise.
        """
        if "thinking" not in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            logger.warning("Thinking mode is disabled for this model.")
            return False

        if budget_tokens == "0" or budget_tokens == "none":
            self.thinking_enabled = False
            self.reasoning_effort = types.ThinkingLevel.THINKING_LEVEL_UNSPECIFIED
            logger.info("Thinking mode disabled.")
            return True
        if budget_tokens not in ["minimal", "low", "medium", "high"]:
            raise ValueError("budget_tokens must be minimal, low, medium or high")

        self.thinking_enabled = True
        self.reasoning_effort = types.ThinkingLevel(budget_tokens.upper())
        logger.info(f"Thinking mode enabled with budget of {budget_tokens} tokens.")
        return True

    def calculate_cost(
        self, input_tokens: int, output_tokens: int, cached_tokens: int = 0
    ) -> float:
        """
        Calculate the cost based on token usage.

        Args:
            input_tokens (int): Number of input tokens
            output_tokens (int): Number of output tokens
            cached_tokens (int): Number of cached input tokens

        Returns:
            float: Estimated cost in USD
        """
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

        stream_generator = await self.client.aio.models.generate_content_stream(
            model=model_id or self.model,
            contents=self._convert_internal_format(prompt)
            if isinstance(prompt, list)
            else prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=3000,
                temperature=temperature,
                system_instruction=self.system_prompt,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        async for chunk in stream_generator:
            if hasattr(chunk, "text") and chunk.text:
                result_text += chunk.text
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                if hasattr(chunk.usage_metadata, "prompt_token_count"):
                    input_tokens = chunk.usage_metadata.prompt_token_count or 0
                if hasattr(chunk.usage_metadata, "candidates_token_count"):
                    output_tokens = chunk.usage_metadata.candidates_token_count or 0
                if hasattr(chunk.usage_metadata, "cached_content_token_count"):
                    cached_tokens = chunk.usage_metadata.cached_content_token_count or 0

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

    def process_file_for_message(self, file_path: str) -> dict[str, Any] | None:
        """
        Process a file and return the appropriate message content.

        Args:
            file_path (str): Path to the file

        Returns:
            dict[str, Any] | None: The message content or None if processing failed
        """
        return None

    def handle_file_command(self, file_path: str) -> list[dict[str, Any]] | None:
        """
        Handle the /file command and return message content.

        Args:
            file_path (str): Path to the file

        Returns:
            list[dict[str, Any]] | None: Message content or None if processing failed
        """
        return None

    def _build_schema(self, param_def):
        param_type = param_def.get("type", "STRING").upper()
        if param_type == "INTEGER":
            param_type = "NUMBER"

        schema = types.Schema(
            type=types.Type(param_type),
            description=param_def.get("description", None),
        )
        if "const" in param_def.keys():
            schema.default = param_def.get("const", None)

        if "enum" in param_def.keys():
            schema.enum = param_def.get("enum", [])

        if "anyOf" in param_def.keys():
            schema.any_of = [
                self._build_schema(item) for item in param_def.get("anyOf", [])
            ]
        if param_type == "OBJECT":
            schema.properties = {}
            if "properties" in param_def.keys():
                for key in param_def.get("properties", {}):
                    prop = param_def.get("properties").get(key, {})
                    schema.properties[key] = self._build_schema(prop)
        elif param_type == "ARRAY":
            itemsSchema = self._build_schema(param_def.get("items"))
            schema.items = itemsSchema

        return schema

    def register_tool(self, tool_definition, handler_function):
        """
        Register a tool with its handler function.

        Args:
            tool_definition (dict): The tool definition following OpenAI's function schema
            handler_function (callable): Function to call when tool is used
        """
        # Store original tool definition for reference
        self.tool_definitions.append(tool_definition)

        # Extract tool name from definition
        tool_name = self._extract_tool_name(tool_definition)

        # Extract parameters schema
        parameters = {}
        required = []

        if "function" in tool_definition:
            parameters = (
                tool_definition["function"].get("parameters", {}).get("properties", {})
            )
            required = (
                tool_definition["function"].get("parameters", {}).get("required", [])
            )
            description = tool_definition["function"].get("description", "")
            defs = tool_definition["function"].get("parameters", {}).get("$defs", {})
        else:
            parameters = tool_definition.get("parameters", {}).get("properties", {})
            required = tool_definition.get("parameters", {}).get("required", [])
            description = tool_definition.get("description", "")
            defs = tool_definition.get("parameters", {}).get("$defs", {})

        # Create a function declaration for Google GenAI
        function_declaration = types.FunctionDeclaration(
            name=tool_name,
            description=description,
        )

        # Convert parameters to Google GenAI format
        for param_name, param_def in parameters.items():
            if not function_declaration.parameters:
                function_declaration.parameters = types.Schema(
                    type=types.Type.OBJECT, properties={}
                )

            if (
                function_declaration.parameters is not None
                and function_declaration.parameters.properties is not None
            ):
                if param_def.get("$ref", ""):
                    ref_key = param_def["$ref"].removeprefix("#/$defs/")
                    if ref_key in defs.keys():
                        function_declaration.parameters.properties[param_name] = (
                            self._build_schema(defs[ref_key])
                        )
                        continue

                function_declaration.parameters.properties[param_name] = (
                    self._build_schema(param_def)
                )
        # Add required parameters
        if required and function_declaration.parameters:
            function_declaration.parameters.required = required

        # Create a Tool object with the function declaration
        self.tools.append(types.Tool(function_declarations=[function_declaration]))

        # Store the handler function
        self.tool_handlers[tool_name] = handler_function

        logger.info(f"🔧 Registered tool: {tool_name}")

    async def stream_assistant_response(self, messages: list[dict[str, Any]]) -> Any:
        """
        Stream the assistant's response with tool support.
        Returns a context manager compatible adapter around the Google GenAI stream.

        Args:
            messages (list[dict[str, Any]]): The conversation messages

        Returns:
            GoogleStreamAdapter: A context manager compatible adapter
        """
        # Convert messages to Google GenAI format
        google_messages = self._convert_internal_format(messages)
        full_model_id = f"{self._provider_name}/{self.model}"

        # Create configuration with tools
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=32768,
            top_p=0.95,
        )

        forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
        if forced_sample_params:
            if forced_sample_params.temperature is not None:
                config.temperature = forced_sample_params.temperature
            if forced_sample_params.top_p is not None:
                config.top_p = forced_sample_params.top_p
            if forced_sample_params.top_k is not None:
                config.top_k = forced_sample_params.top_k
            if forced_sample_params.frequency_penalty is not None:
                config.frequency_penalty = forced_sample_params.frequency_penalty
            if forced_sample_params.presence_penalty is not None:
                config.presence_penalty = forced_sample_params.presence_penalty

        # Add system instruction if available
        if self.system_prompt:
            config.system_instruction = self.system_prompt

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            full_model_id
        ):
            config.tools = self.tools
            # config.tools.append(types.Tool(google_search=types.GoogleSearch()))
            # config.tool_config = types.ToolConfig(
            #     include_server_side_tool_invocations=True
            # )

        if self.thinking_enabled:
            config.thinking_config = types.ThinkingConfig(
                include_thoughts=True, thinking_level=self.reasoning_effort
            )

        if (
            "structured_output" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.structured_output
        ):
            config.response_mime_type = "application/json"
            config.response_json_schema = self.structured_output
            config.tools = None

        # Get the stream generator
        stream_generator = await self.client.aio.models.generate_content_stream(
            model=self.model, contents=google_messages, config=config
        )

        # Wrap in adapter that supports context manager protocol
        return GoogleStreamAdapter(stream_generator)

    def _convert_internal_format(self, messages: list[dict[str, Any]]):
        """
        Convert standard messages to Google GenAI format.

        Args:
            messages (list[dict[str, Any]]): Standard message format

        Returns:
            list: Messages in Google GenAI format as Content or Part objects
        """
        from google.genai.types import Content, Part

        # Create a conversation in Google format
        google_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" or role == "consolidated":
                google_content = Content(role="user", parts=[])
                if isinstance(content, list):
                    for c in content:
                        if google_content.parts is not None:
                            if isinstance(c, dict):
                                if c.get("type", "") == "image_url":
                                    pattern = r"^data:([^;]+);base64,(.*)$"
                                    data_url = c.get("image_url", {}).get("url", "")
                                    match = re.match(pattern, data_url, re.DOTALL)
                                    if match:
                                        mime_type = match.group(1)
                                        base64_data = match.group(2)
                                        data = base64_to_bytes(base64_data)
                                        if data:
                                            google_content.parts.append(
                                                Part.from_bytes(
                                                    data=data,
                                                    mime_type=mime_type,
                                                )
                                            )
                                else:
                                    google_content.parts.append(
                                        Part.from_text(text=c.get("text", ""))
                                    )
                            else:
                                google_content.parts.append(Part.from_text(text=c))
                else:
                    if google_content.parts is not None:
                        google_content.parts.append(Part.from_text(text=content))
                # Create a user message
                google_messages.append(google_content)

            elif role == "assistant":
                # Create an assistant message
                parts = []
                if isinstance(content, list):
                    for c in content:
                        if c.get("type", "text") == "thinking":
                            parts.append(
                                Part(
                                    thought=c.get("thinking", None),
                                    thought_signature=c.get("signature", None),
                                )
                            )
                        else:
                            parts.append(Part.from_text(text=c.get("text", "")))
                else:
                    parts.append(Part.from_text(text=content))

                # Add tool calls if present
                if "tool_calls" in msg:
                    for tool_call in msg["tool_calls"]:
                        parts.append(
                            Part.from_function_call(
                                name=tool_call.get("name", ""),
                                args=tool_call.get("arguments", {}),
                            )
                        )

                google_messages.append(Content(role="model", parts=parts))

            elif role == "tool":
                # Tool responses need to be sent as user messages
                tool_content = f"Tool result: {content}"
                google_messages.append(
                    Content(role="user", parts=[Part.from_text(text=tool_content)])
                )

        return google_messages

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> Tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
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
                token_usage,
                chunk_text,
                thinking_data
            )
        """
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = ""
        stop_chunk = False

        if hasattr(chunk, "candidates") and chunk.candidates:
            for candidate in chunk.candidates:
                if (
                    hasattr(candidate, "content")
                    and candidate.content
                    and candidate.content.parts is not None
                ):
                    # get chunk text
                    for part in candidate.content.parts:
                        # get the thinking data
                        if hasattr(part, "thought") and isinstance(part.thought, str):
                            thinking_content += part.thought

                        if hasattr(part, "text") and isinstance(part.text, str):
                            chunk_text += part.text

                        # Check if this part has a function call
                        if hasattr(part, "function_call") and part.function_call:
                            function_call = part.function_call

                            # Create a unique ID for this tool call
                            tool_id = f"{function_call.name}_{len(tool_uses)}"

                            # Check if this function is already in tool_uses
                            existing_tool = next(
                                (
                                    t
                                    for t in tool_uses
                                    if t.get("name") == function_call.name
                                ),
                                None,
                            )

                            if existing_tool:
                                # Update the existing tool
                                if (
                                    hasattr(function_call, "args")
                                    and function_call.args
                                ):
                                    existing_tool["input"] = function_call.args
                            else:
                                # Create a new tool use entry
                                tool_uses.append(
                                    {
                                        "id": tool_id,
                                        "name": function_call.name,
                                        "input": function_call.args
                                        if hasattr(function_call, "args")
                                        else {},
                                        "type": "function",
                                        "response": "",
                                    }
                                )
                if (
                    hasattr(candidate, "finish_reason")
                    and candidate.finish_reason is not None
                ):
                    stop_chunk = True

        assistant_response += chunk_text
        # Process tool usage information from text if present
        if assistant_response.rfind("Using tool") > -1:
            tool_pattern = r"Using tool: (\w+)\s*(?:\n)?Arguments: (\{[\s\S]*\})"
            tool_matches = re.findall(tool_pattern, assistant_response, re.M)

            if assistant_response.count("{") == assistant_response.count("}"):
                ## ignore if curly brackets not close
                for tool_name, tool_args_str in tool_matches:
                    try:
                        # Parse the JSON arguments
                        tool_args = json.loads(tool_args_str)

                        # Create a unique ID for this tool call
                        tool_id = f"{tool_name}_{len(tool_uses)}"

                        # Check if this tool is already in tool_uses
                        existing_tool = next(
                            (t for t in tool_uses if t.get("name") == tool_name),
                            None,
                        )

                        if existing_tool:
                            # Update the existing tool
                            existing_tool["input"] = tool_args
                        else:
                            # Create a new tool use entry
                            tool_uses.append(
                                {
                                    "id": tool_id,
                                    "name": tool_name,
                                    "input": tool_args,
                                    "type": "function",
                                    "response": "",
                                }
                            )
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse tool arguments: {tool_args_str}")

                assistant_response = re.sub(tool_pattern, "", assistant_response)
        # Process usage information if available
        if stop_chunk and hasattr(chunk, "usage_metadata"):
            if hasattr(chunk.usage_metadata, "prompt_token_count"):
                input_tokens = chunk.usage_metadata.prompt_token_count or 0
            if hasattr(chunk.usage_metadata, "candidates_token_count"):
                output_tokens = chunk.usage_metadata.candidates_token_count or 0
            if hasattr(chunk.usage_metadata, "cached_content_token_count"):
                cached_tokens = chunk.usage_metadata.cached_content_token_count or 0

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            (thinking_content, None) if thinking_content.strip() else None,
        )

    async def validate_spec(self, prompt: str) -> str:
        """
        Validate a specification prompt using Google GenAI.

        Args:
            prompt: The specification prompt to validate

        Returns:
            Validation result as a JSON string
        """
        # Request JSON response
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )

        # Calculate and log token usage
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            if hasattr(response.usage_metadata, "prompt_token_count"):
                input_tokens = response.usage_metadata.prompt_token_count or 0
            if hasattr(response.usage_metadata, "candidates_token_count"):
                output_tokens = response.usage_metadata.candidates_token_count or 0
            if hasattr(response.usage_metadata, "cached_content_token_count"):
                cached_tokens = response.usage_metadata.cached_content_token_count or 0

        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        # Calculate cost
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nSpec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        # Return the response text (should be JSON)
        return response.text or ""

    def set_system_prompt(self, system_prompt: str):
        """
        Set the system prompt for the Google GenAI service.

        Args:
            system_prompt: The system prompt to use
        """
        self.system_prompt = system_prompt

    def clear_tools(self):
        """
        Clear all registered tools from the Google GenAI service.
        """
        self.tools = []
        self.tool_handlers = {}
        self.tool_definitions = []
