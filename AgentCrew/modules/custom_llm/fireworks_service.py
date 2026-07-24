import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage

from .service import CustomLLMService


class FireworksService(CustomLLMService):
    """Fireworks AI implementation using the OpenAI-compatible API."""

    def __init__(self):
        load_dotenv()
        api_key = os.getenv("FIREWORKS_API_KEY")
        if not api_key:
            logger.error("FIREWORKS_API_KEY not found in environment variables")
        super().__init__(
            api_key=api_key,
            base_url="https://api.fireworks.ai/inference/v1",
            provider_name="fireworks",
        )
        self.model = "accounts/fireworks/models/deepseek-v4-pro"
        self._is_thinking = False
        logger.info("Initialized Fireworks AI Service")

    def _build_stream_params(self) -> tuple[dict[str, Any], bool]:
        stream_params, is_streamable = super()._build_stream_params()
        full_model_id = f"{self._provider_name}/{self.model}"
        forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
        model_caps = ModelRegistry.get_model_capabilities(full_model_id)

        if "thinking" in model_caps and self.reasoning_effort:
            if isinstance(self.reasoning_effort, str) and self.reasoning_effort in [
                "low",
                "medium",
                "high",
                "max",
            ]:
                stream_params["reasoning_effort"] = self.reasoning_effort
                stream_params.pop("max_tokens", None)
            else:
                extra_body = stream_params.setdefault("extra_body", {})
                extra_body["reasoning"] = {"enabled": True}
                stream_params.pop("max_tokens", None)
        else:
            if forced_sample_params:
                if forced_sample_params.top_k is not None:
                    extra_body = stream_params.setdefault("extra_body", {})
                    extra_body["top_k"] = forced_sample_params.top_k
                if forced_sample_params.min_p is not None:
                    extra_body = stream_params.setdefault("extra_body", {})
                    extra_body["min_p"] = forced_sample_params.min_p
                if forced_sample_params.repetition_penalty is not None:
                    extra_body = stream_params.setdefault("extra_body", {})
                    extra_body["repetition_penalty"] = (
                        forced_sample_params.repetition_penalty
                    )

        return stream_params, is_streamable

    def _process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None

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

        if hasattr(delta_chunk, "tool_calls"):
            delta_tool_calls = delta_chunk.tool_calls
            if delta_tool_calls:
                for tool_call_delta in delta_tool_calls:
                    tool_call_index = self._resolve_stream_tool_call_index(
                        tool_uses, tool_call_delta
                    )
                    if tool_call_index is None:
                        continue

                    self._merge_stream_tool_call_delta(tool_uses, tool_call_delta)

                if not chunk_text:
                    chunk_text = ""

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
