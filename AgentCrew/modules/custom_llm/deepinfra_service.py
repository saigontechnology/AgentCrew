import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from AgentCrew.modules.llm.token_usage import TokenUsage

from .service import CustomLLMService


class DeepInfraService(CustomLLMService):
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("DEEPINFRA_API_KEY")
        if not api_key:
            logger.error("DEEPINFRA_API_KEY not found in environment variables")
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

    def _build_stream_params(self) -> tuple[dict[str, Any], bool]:
        stream_params, is_streamable = super()._build_stream_params()
        stream_params["max_tokens"] = 81920
        return stream_params, is_streamable

    def _process_stream_chunk(
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
                input_tokens,
                output_tokens,
                chunk_text,
                thinking_data
            )
        """
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None  # OpenAI doesn't support thinking mode

        # Handle final chunk with usage information
        if hasattr(chunk, "usage"):
            if hasattr(chunk.usage, "prompt_tokens"):
                input_tokens = chunk.usage.prompt_tokens
            if hasattr(chunk.usage, "completion_tokens"):
                output_tokens = chunk.usage.completion_tokens
            if (
                hasattr(chunk.usage, "prompt_tokens_details")
                and chunk.usage.prompt_tokens_details
            ):
                if hasattr(chunk.usage.prompt_tokens_details, "cached_tokens"):
                    cached_tokens = chunk.usage.prompt_tokens_details.cached_tokens or 0

        if (not chunk.choices) or (len(chunk.choices) == 0):
            return (
                assistant_response or " ",
                tool_uses,
                TokenUsage(
                    input_tokens=input_tokens - cached_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                ),
                "",
                (thinking_content, None) if thinking_content else None,
            )

        delta_chunk = chunk.choices[0].delta
        # Handle regular content chunks
        #
        if (
            hasattr(delta_chunk, "reasoning_content")
            and delta_chunk.reasoning_content is not None
        ):
            thinking_content = delta_chunk.reasoning_content

        if hasattr(delta_chunk, "content") and delta_chunk.content is not None:
            chunk_text = delta_chunk.content
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

        # Handle tool call chunks
        if hasattr(delta_chunk, "tool_calls"):
            delta_tool_calls = chunk.choices[0].delta.tool_calls
            if delta_tool_calls:
                for tool_call_delta in delta_tool_calls:
                    tool_call_index = self._merge_stream_tool_call_delta(
                        tool_uses, tool_call_delta
                    )
                    if tool_call_index is None:
                        continue

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            (thinking_content, None) if thinking_content else None,
        )
