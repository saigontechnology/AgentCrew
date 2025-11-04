from .service import CustomLLMService
import os
from dotenv import load_dotenv
from loguru import logger
from typing import Dict, List, Optional, Tuple
import ast
import json


class DeepInfraService(CustomLLMService):
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("DEEPINFRA_API_KEY")
        if not api_key:
            raise ValueError("DEEPINFRA_API_KEY not found in environment variables")
        super().__init__(
            api_key=api_key,
            base_url="https://api.deepinfra.com/v1/openai",
            provider_name="deepinfra",
        )
        self.model = "Qwen/Qwen3-235B-A22B"
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        self._is_thinking = False
        logger.info("Initialized DeepInfra Service")

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
                    # Check if this is a new tool call
                    if getattr(tool_call_delta, "id"):
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

                    tool_call_index = len(tool_uses) - 1

                    # # Update existing tool call with new data
                    # if hasattr(tool_call_delta, "id") and tool_call_delta.id:
                    #     tool_uses[tool_call_index]["id"] = tool_call_delta.id

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
                                # this piece of code is to fix the isuse of deepinfra cannot load all of arguments structure
                                for key, value in tool_uses[tool_call_index][
                                    "input"
                                ].items():
                                    if isinstance(value, str):
                                        try:
                                            tool_uses[tool_call_index]["input"][key] = (
                                                ast.literal_eval(value)
                                            )
                                        except Exception:
                                            # just skip if literal_eval fails
                                            pass
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
