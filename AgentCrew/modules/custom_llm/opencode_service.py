from typing import Any

from AgentCrew.modules.custom_llm.service import CustomLLMService
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage


class OpenCodeService(CustomLLMService):
    def _stringify_content(self, content: Any) -> str:
        if isinstance(content, list):
            content_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        content_parts.append(item.get("text", ""))
                    elif "text" in item:
                        content_parts.append(item["text"])
                    elif "content" in item:
                        content_parts.append(str(item["content"]))
                    else:
                        content_parts.append(str(item))
                elif item is not None:
                    content_parts.append(str(item))
            return "\n".join(part for part in content_parts if part)
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return str(content)

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        if "stream" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            return self._process_stream_chunk(chunk, assistant_response, tool_uses)
        return self._process_non_stream_chunk(chunk, assistant_response, tool_uses)

    def _process_non_stream_chunk(
        self, chunk, assistant_response, tool_uses
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        input_tokens = self.current_input_tokens
        self.current_input_tokens = 0
        output_tokens = self.current_output_tokens
        self.current_output_tokens = 0
        thinking_data = (" ", None) if self.model.startswith("deepseek-v4") else None

        if hasattr(chunk, "message"):
            message = chunk.message
            content = self._stringify_content(getattr(message, "content", " ") or " ")
            reasoning_content = getattr(message, "reasoning_content", None) or getattr(
                message, "reasoning", None
            )
            if reasoning_content:
                thinking_data = (reasoning_content, None)

            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool_call in message.tool_calls:
                    self._append_non_stream_tool_call(tool_uses, tool_call)
                return (
                    content,
                    tool_uses,
                    TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                    content,
                    thinking_data,
                )

            return (
                content,
                tool_uses,
                TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                content,
                thinking_data,
            )

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            None,
            thinking_data,
        )

    def _process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = " " if self.model.startswith("deepseek-v4") else None

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

        if chunk.choices and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if (
                hasattr(delta, "reasoning_content")
                and delta.reasoning_content is not None
            ):
                thinking_content = delta.reasoning_content
            elif hasattr(delta, "reasoning") and delta.reasoning is not None:
                thinking_content = delta.reasoning

            if hasattr(delta, "content") and delta.content is not None:
                chunk_text = delta.content
                assistant_response += chunk_text

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    self._merge_stream_tool_call_delta(tool_uses, tool_call_delta)

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text or None,
            (thinking_content, None) if thinking_content else None,
        )
