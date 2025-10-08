from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai import OpenAIService
from AgentCrew.modules.llm.base import AsyncIterator
from typing import Dict, Any, List, Optional, Tuple
import json
from AgentCrew.modules import logger


class CustomLLMService(OpenAIService):
    """Custom LLM service that can connect to any OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        provider_name: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        """
        Initializes the CustomLLMService.

        Args:
            base_url (str): The base URL of the OpenAI-compatible API.
            api_key (str): The API key for the service.
            provider_name (str): The name of the custom provider.
            is_stream (bool): Whether to enable streaming responses by default.
            extra_headers (Optional[List[Dict[str, str]]]): Custom HTTP headers to include in API requests.
        """
        super().__init__(api_key=api_key, base_url=base_url)
        self._provider_name = provider_name
        logger.info(
            f"Initialized Custom LLM Service for provider: {provider_name} at {base_url}"
        )
        self.extra_headers = extra_headers

    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                timeout=60,
                max_tokens=3000,
                temperature=temperature,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                extra_headers=self.extra_headers,
            )

            # Calculate and log token usage and cost
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0
            total_cost = self.calculate_cost(input_tokens, output_tokens)

            logger.info("\nToken Usage Statistics:")
            logger.info(f"Input tokens: {input_tokens:,}")
            logger.info(f"Output tokens: {output_tokens:,}")
            logger.info(f"Total tokens: {input_tokens + output_tokens:,}")
            logger.info(f"Estimated cost: ${total_cost:.4f}")
            analyze_result = response.choices[0].message.content or ""
            if "thinking" in ModelRegistry.get_model_capabilities(
                f"{self._provider_name}/{self.model}"
            ):
                THINK_STARTED = "<think>"
                THINK_STOPED = "</think>"

                if (
                    analyze_result.find(THINK_STARTED) >= 0
                    and analyze_result.find(THINK_STOPED) >= 0
                ):
                    analyze_result = (
                        analyze_result[: analyze_result.find(THINK_STARTED)]
                        + analyze_result[
                            (analyze_result.find(THINK_STOPED) + len(THINK_STOPED)) :
                        ]
                    )

            return analyze_result
        except Exception as e:
            raise Exception(f"Failed to process content: {str(e)}")

    def _convert_internal_format(self, messages: List[Dict[str, Any]]):
        for msg in messages:
            msg.pop("agent", None)
            if "tool_calls" in msg and msg.get("tool_calls", []):
                for tool_call in msg["tool_calls"]:
                    tool_call["function"] = {}
                    tool_call["function"]["name"] = tool_call.pop("name", "")
                    tool_call["function"]["arguments"] = json.dumps(
                        tool_call.pop("arguments", {})
                    )
            if msg.get("role") == "tool":
                msg.pop("tool_name", None)
                cleaned_tool_content = []
                if isinstance(msg.get("content", ""), List):
                    for tool_content in msg["content"]:
                        if isinstance(tool_content, dict):
                            if tool_content.get("type", "text") == "text":
                                cleaned_tool_content.append(
                                    tool_content.get("text", "")
                                )
                msg["content"] = "\n".join(cleaned_tool_content)

        return messages

    async def stream_assistant_response(self, messages):
        """Stream the assistant's response with tool support."""

        stream_params = {
            "model": self.model,
            "parallel_tool_calls": False,
            "messages": messages,
            # "max_tokens": 16000,
        }
        stream_params["temperature"] = self.temperature
        stream_params["extra_body"] = {"min_p": 0.1}

        # Add system message if provided
        if self.system_prompt:
            stream_params["messages"] = self._convert_internal_format(
                [{"role": "system", "content": self.system_prompt}] + messages
            )

        # Add tools if available
        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            stream_params["tools"] = self.tools

        if (
            "thinking" in ModelRegistry.get_model_capabilities(self.model)
            and self.reasoning_effort is None
        ):
            stream_params["reasoning_effort"] = "none"

        try:
            if "stream" in ModelRegistry.get_model_capabilities(
                f"{self._provider_name}/{self.model}"
            ):
                self._is_thinking = False
                return await self.client.chat.completions.create(
                    **stream_params,
                    stream=True,
                    extra_headers=self.extra_headers,
                )

            else:
                response = await self.client.chat.completions.create(
                    **stream_params,
                    stream=False,
                    extra_headers=self.extra_headers,
                )

                if response.usage:
                    self.current_input_tokens = response.usage.prompt_tokens
                    self.current_output_tokens = response.usage.completion_tokens
                else:
                    self.current_input_tokens = 0
                    self.current_output_tokens = 0

                # Return an AsyncIterator wrapping response.choices
                return AsyncIterator(response.choices)
        except Exception as e:
            logger.error(f"Error in stream_assistant_response: {str(e)}")

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: List[Dict]
    ) -> Tuple[str, List[Dict], int, int, Optional[str], Optional[tuple]]:
        if "stream" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            return self._process_stream_chunk(chunk, assistant_response, tool_uses)
        else:
            return self._process_non_stream_chunk(chunk, assistant_response, tool_uses)

    def _process_non_stream_chunk(
        self, chunk, assistant_response, tool_uses
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
        # Check if this is a non-streaming response (for tool use)
        thinking_content = None

        input_tokens = self.current_input_tokens
        self.current_input_tokens = 0
        output_tokens = self.current_output_tokens
        self.current_output_tokens = 0
        if hasattr(chunk, "message"):
            # This is a complete response, not a streaming chunk
            message = chunk.message
            content = message.content or " "
            if hasattr(message, "reasoning") and message.reasoning:
                thinking_content = (message.reasoning, None)
            if "thinking" in ModelRegistry.get_model_capabilities(
                f"{self._provider_name}/{self.model}"
            ):
                THINK_STARTED = "<think>"
                THINK_STOPED = "</think>"
                think_start_idx = content.find(THINK_STARTED)
                think_stop_idx = content.find(THINK_STOPED)
                if think_start_idx >= 0 and think_stop_idx >= 0:
                    thinking_content = (content[think_start_idx:think_stop_idx], None)
                    content = (
                        content[:think_start_idx]
                        + content[think_stop_idx + len(THINK_STOPED) :]
                    )
            # Check for tool calls
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool_call in message.tool_calls:
                    function = tool_call.function

                    tool_uses.append(
                        {
                            "id": f"toolu_{function.name}_{len(tool_uses)}",
                            "name": function.name,
                            "input": json.loads(function.arguments),
                            "type": tool_call.type,
                            "response": "",
                        }
                    )

                # Return with tool use information and the full content
                return (
                    content,
                    tool_uses,
                    input_tokens,
                    output_tokens,
                    content,  # Return the full content to be printed
                    thinking_content,
                )

            # Check for tool call format in the response
            tool_call_start = "<tool_call>"
            tool_call_end = "<｜tool▁calls▁end｜>"

            if tool_call_start in content and tool_call_end in content:
                start_idx = content.find(tool_call_start)
                end_idx = content.find(tool_call_end) + len(tool_call_end)

                tool_call_content = content[
                    start_idx + len(tool_call_start) : end_idx - len(tool_call_end)
                ]

                try:
                    tool_data = json.loads(tool_call_content)
                    tool_uses.append(
                        {
                            "id": f"toolu_{len(tool_uses)}",  # Generate an ID
                            "name": tool_data.get("name", ""),
                            "input": tool_data.get("arguments", {}),
                            "type": "function",
                            "response": "",
                        }
                    )

                    # Remove the tool call from the response
                    content = content[:start_idx] + content[end_idx:]
                except json.JSONDecodeError:
                    # If we can't parse the JSON, just continue
                    pass

            # Regular response without tool calls
            return (
                content,
                tool_uses,
                input_tokens,
                output_tokens,
                content,  # Return the full content to be printed
                thinking_content,
            )

        # Handle regular streaming chunk
        chunk_text = chunk.choices[0].delta.content or ""
        updated_assistant_response = assistant_response + chunk_text

        return (
            updated_assistant_response,
            tool_uses,
            input_tokens,
            output_tokens,
            chunk_text,
            thinking_content,
        )

    def _process_stream_chunk(
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
        chunk_text = None
        input_tokens = 0
        output_tokens = 0
        thinking_content = None  # OpenAI doesn't support thinking mode

        # Handle regular content chunks
        if (
            chunk.choices
            and len(chunk.choices) > 0
            and hasattr(chunk.choices[0].delta, "content")
            and chunk.choices[0].delta.content is not None
        ):
            chunk_text = chunk.choices[0].delta.content
            if "<think>" in chunk_text:
                self._is_thinking = True

            if self._is_thinking:
                thinking_content = chunk_text
                if "<think>" in thinking_content:
                    thinking_content = thinking_content.replace("<think>", "")
                if "</think>" in thinking_content:
                    # Remove thinking end tag
                    thinking_content = thinking_content.replace("</think>", "")
            else:
                assistant_response += chunk_text

            if "</think>" in chunk_text:
                self._is_thinking = False
                chunk_text = None

            if self._is_thinking:
                chunk_text = None
            # Remove chunk_text if still in thinking mode

        # Handle final chunk with usage information
        if hasattr(chunk, "usage"):
            if hasattr(chunk.usage, "prompt_tokens"):
                input_tokens = chunk.usage.prompt_tokens
            if hasattr(chunk.usage, "completion_tokens"):
                output_tokens = chunk.usage.completion_tokens

        # Handle tool call chunks
        if (
            chunk.choices
            and len(chunk.choices) > 0
            and hasattr(chunk.choices[0].delta, "tool_calls")
        ):
            delta_tool_calls = chunk.choices[0].delta.tool_calls
            if delta_tool_calls:
                # Process each tool call in the delta
                for tool_call_delta in delta_tool_calls:
                    tool_call_index = tool_call_delta.index or 0

                    # Check if this is a new tool call
                    if tool_call_index >= len(tool_uses):
                        # Create a new tool call entry
                        tool_uses.append(
                            {
                                "id": getattr(tool_call_delta, "id")
                                if hasattr(tool_call_delta, "id")
                                else f"toolu_{len(tool_uses)}",
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
                    input_tokens,
                    output_tokens,
                    "",
                    (thinking_content, None) if thinking_content else None,
                )

        return (
            assistant_response or " ",
            tool_uses,
            input_tokens,
            output_tokens,
            chunk_text,
            (thinking_content, None) if thinking_content else None,
        )
