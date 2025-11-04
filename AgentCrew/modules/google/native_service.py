import os
import re
import json
import mimetypes
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv
from google import genai
from AgentCrew.modules.llm.model_registry import ModelRegistry
from google.genai import types
from AgentCrew.modules.llm.base import (
    BaseLLMService,
    read_binary_file,
    read_text_file,
    base64_to_bytes,
)
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
            return self.stream_generator.__next__()
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

    def __init__(self):
        """Initialize the Google GenAI service."""
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables")

        # Initialize the Google GenAI client
        self.client = genai.Client(api_key=api_key)

        # Default model
        self.model = "gemini-2.5-flash-preview-05-20"

        # Initialize tools and handlers
        self.tools = []
        self.tool_handlers = {}
        self.tool_definitions = []  # Keep original definitions for reference

        self.thinking_enabled = False
        self.thinking_budget = 0

        # Provider name and system prompt
        self._provider_name = "google"
        self.system_prompt = ""
        logger.info("Initialized Google Service")

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.
        Currently not supported in Google GenAI.

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
            logger.warning(
                "Warning: Minimum thinking budget is 1024 tokens. Setting to 1024."
            )
            budget_tokens = 1024

        self.thinking_enabled = True
        self.thinking_budget = budget_tokens
        logger.info(f"Thinking mode enabled with budget of {budget_tokens} tokens.")
        return True

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate the cost based on token usage.

        Args:
            input_tokens (int): Number of input tokens
            output_tokens (int): Number of output tokens

        Returns:
            float: Estimated cost in USD
        """
        # Current Gemini pricing as of March 2025
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
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=3000, temperature=temperature
            ),
        )

        # Get token usage if available
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            if hasattr(response.usage_metadata, "prompt_token_count"):
                input_tokens = response.usage_metadata.prompt_token_count or 0
            if hasattr(response.usage_metadata, "candidates_token_count"):
                output_tokens = response.usage_metadata.candidates_token_count or 0

        # Calculate and log cost
        total_cost = self.calculate_cost(input_tokens, output_tokens)
        logger.info("\nToken Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return response.text or ""

    def process_file_for_message(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Process a file and return the appropriate message content.

        Args:
            file_path (str): Path to the file

        Returns:
            Optional[Dict[str, Any]]: The message content or None if processing failed
        """
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

    def handle_file_command(self, file_path: str) -> Optional[List[Dict[str, Any]]]:
        """
        Handle the /file command and return message content.

        Args:
            file_path (str): Path to the file

        Returns:
            Optional[List[Dict[str, Any]]]: Message content or None if processing failed
        """
        result = self.process_file_for_message(file_path)
        if result:
            if "type" in result and result["type"] == "text":
                return [
                    {
                        "type": "text",
                        "text": f"{result['text']}",
                    }
                ]
            else:
                # For now, we'll just use text for file content
                return [
                    {
                        "type": "text",
                        "text": f"I'm sharing this file with you: {os.path.basename(file_path)}",
                    }
                ]
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

    async def execute_tool(self, tool_name, tool_params) -> Any:
        """
        Execute a registered tool with the given parameters.

        Args:
            tool_name (str): Name of the tool to execute
            tool_params (dict): Parameters to pass to the tool

        Returns:
            Any: Result of the tool execution
        """
        if tool_name not in self.tool_handlers:
            raise ValueError(f"Tool '{tool_name}' not found")

        try:
            handler = self.tool_handlers[tool_name]
            result = handler(**tool_params)
            return result
        except Exception as e:
            return f"Error executing tool {tool_name}: {str(e)}"

    async def stream_assistant_response(self, messages: List[Dict[str, Any]]) -> Any:
        """
        Stream the assistant's response with tool support.
        Returns a context manager compatible adapter around the Google GenAI stream.

        Args:
            messages (List[Dict[str, Any]]): The conversation messages

        Returns:
            GoogleStreamAdapter: A context manager compatible adapter
        """
        # Convert messages to Google GenAI format
        google_messages = self._convert_internal_format(messages)

        # Create configuration with tools
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=65536,
            top_p=0.95,
        )

        # Add system instruction if available
        if self.system_prompt:
            config.system_instruction = self.system_prompt

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            config.tools = self.tools

        if self.thinking_enabled and self.thinking_budget > 0:
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.thinking_budget
            )

        # Get the stream generator
        stream_generator = self.client.models.generate_content_stream(
            model=self.model, contents=google_messages, config=config
        )

        # Wrap in adapter that supports context manager protocol
        return GoogleStreamAdapter(stream_generator)

    def _convert_internal_format(self, messages: List[Dict[str, Any]]):
        """
        Convert standard messages to Google GenAI format.

        Args:
            messages (List[Dict[str, Any]]): Standard message format

        Returns:
            List: Messages in Google GenAI format as Content or Part objects
        """
        from google.genai.types import Content, Part

        # Create a conversation in Google format
        google_messages = []

        # print(messages)
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
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
                parts = [Part.from_text(text=content)]

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
        self, chunk, assistant_response: str, tool_uses: List[Dict]
    ) -> Tuple[str, List[Dict], int, int, Optional[str], Optional[tuple]]:
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
                input_tokens,
                output_tokens,
                chunk_text,
                thinking_data
            )
        """
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        thinking_content = ""

        if hasattr(chunk, "candidates") and chunk.candidates:
            for candidate in chunk.candidates:
                if (
                    hasattr(candidate, "content")
                    and candidate.content
                    and candidate.content.parts is not None
                ):
                    # get chunk text
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text is not None:
                            chunk_text += part.text

                        # get the thinking data
                        if hasattr(part, "thought") and part.thought is not None:
                            thinking_content += part.thought
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
        if hasattr(chunk, "usage_metadata"):
            if hasattr(chunk.usage_metadata, "prompt_token_count"):
                input_tokens = chunk.usage_metadata.prompt_token_count or 0
            if hasattr(chunk.usage_metadata, "candidates_token_count"):
                output_tokens = chunk.usage_metadata.candidates_token_count or 0

        return (
            assistant_response or " ",
            tool_uses,
            input_tokens,
            output_tokens,
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
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            if hasattr(response.usage_metadata, "prompt_token_count"):
                input_tokens = response.usage_metadata.prompt_token_count or 0
            if hasattr(response.usage_metadata, "candidates_token_count"):
                output_tokens = response.usage_metadata.candidates_token_count or 0

        # Calculate cost
        total_cost = self.calculate_cost(input_tokens, output_tokens)

        logger.info("\nSpec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
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
