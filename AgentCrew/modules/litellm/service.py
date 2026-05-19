from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.llm.base import AsyncIterator
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage

if TYPE_CHECKING:
    from typing import Tuple


class LiteLLMService(CustomLLMService):
    """LiteLLM integration providing access to 100+ LLM providers.

    Uses provider-prefixed model strings (e.g. ``openai/gpt-4o``,
    ``anthropic/claude-sonnet-4-6``, ``groq/llama-3.3-70b-versatile``).
    API keys are read from environment variables automatically by LiteLLM.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        provider_name: str = "litellm",
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = "openai/gpt-4o-mini"
        self.tools: list[dict] = []
        self.tool_handlers: dict[str, Any] = {}
        self._provider_name = provider_name
        self.system_prompt = "You are a helpful assistant"
        self.reasoning_effort = None
        self.extra_headers = None
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        self._is_thinking = False
        self._structured_output = None
        logger.info(f"Initialized LiteLLM Service (provider: {provider_name})")

    async def close(self):
        pass

    async def process_message(self, prompt: str, temperature: float = 0) -> str:
        import litellm

        result_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 3000,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "drop_params": True,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        stream = await litellm.acompletion(**kwargs)

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
                ):
                    if hasattr(chunk.usage.prompt_tokens_details, "cached_tokens"):
                        cached_tokens = (
                            chunk.usage.prompt_tokens_details.cached_tokens or 0
                        )

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

        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            THINK_STARTED = "<think>"
            THINK_STOPED = "</think>"
            if (
                result_text.find(THINK_STARTED) >= 0
                and result_text.find(THINK_STOPED) >= 0
            ):
                result_text = (
                    result_text[: result_text.find(THINK_STARTED)]
                    + result_text[
                        (result_text.find(THINK_STOPED) + len(THINK_STOPED)) :
                    ]
                )

        return result_text

    async def stream_assistant_response(self, messages) -> Any:
        import litellm

        stream_params, is_streamable = self._build_stream_params()
        stream_params["messages"] = self._convert_internal_format(messages)
        stream_params["drop_params"] = True

        if self.system_prompt:
            stream_params["messages"] = [
                {"role": "system", "content": self.system_prompt}
            ] + stream_params["messages"]

        if self.api_key:
            stream_params["api_key"] = self.api_key
        if self.api_base:
            stream_params["api_base"] = self.api_base

        if is_streamable:
            self._is_thinking = False
            return await litellm.acompletion(**stream_params, stream=True)
        else:
            response = await litellm.acompletion(**stream_params, stream=False)

            if response.usage:
                self.current_input_tokens = response.usage.prompt_tokens
                self.current_output_tokens = response.usage.completion_tokens
            else:
                self.current_input_tokens = 0
                self.current_output_tokens = 0

            return AsyncIterator(response.choices)

    async def validate_spec(self, prompt: str) -> str:
        import litellm

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "drop_params": True,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        response = await litellm.acompletion(**kwargs)

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cached_tokens = 0
        if (
            response.usage
            and hasattr(response.usage, "prompt_tokens_details")
            and response.usage.prompt_tokens_details
        ):
            if hasattr(response.usage.prompt_tokens_details, "cached_tokens"):
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
