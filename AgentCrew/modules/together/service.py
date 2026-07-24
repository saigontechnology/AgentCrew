import json
import os
from typing import Any, Tuple

from dotenv import load_dotenv
from loguru import logger
from together import AsyncTogether

from AgentCrew.modules.llm.base import (
    BaseLLMService,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage


class TogetherAIService(BaseLLMService):
    """Together AI implementation aligned with the official together-py client."""

    def __init__(self, api_key: str | None = None):
        load_dotenv()
        self.api_key = api_key or os.getenv("TOGETHER_API_KEY")
        if not self.api_key:
            logger.error("TOGETHER_API_KEY not found in environment variables")
        self.base_url = os.getenv("TOGETHER_BASE_URL")
        self.client = AsyncTogether(api_key=self.api_key)
        self.model = "deepseek-ai/DeepSeek-V3.1"
        self.tools = []
        self.tool_handlers = {}
        self._provider_name = "together"
        self.system_prompt = ""
        self.reasoning_effort = None
        logger.info("Initialized Together AI Service")

    async def close(self):
        await self.client.close()

    def set_think(self, budget_tokens) -> bool:
        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            if budget_tokens == "0" or budget_tokens == "none":
                self.reasoning_effort = None
                return True
            self.reasoning_effort = budget_tokens
            return True
        logger.info("Thinking mode is not supported for Together models.")
        return False

    def calculate_cost(
        self, input_tokens: int, output_tokens: int, cached_tokens: int = 0
    ) -> float:
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
        converted_messages = []
        defered_vision_messages = []
        for raw_msg in messages:
            msg = dict(raw_msg)
            msg.pop("agent", None)
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "consolidated":
                msg["role"] = "user"
                msg.pop("metadata", None)
            elif role == "tool":
                msg.pop("tool_name", None)
                msg.pop("is_rejected", None)
                if isinstance(content, list):
                    cleaned_content = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                cleaned_content.append(item.get("text", ""))
                            elif (
                                item.get("type") == "image_url"
                                and "vision"
                                in ModelRegistry.get_model_capabilities(
                                    f"{self._provider_name}/{self.model}"
                                )
                            ):
                                defered_vision_messages.append(
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": f"Image from tool id: {msg.get('tool_call_id', '')}",
                                            },
                                            item,
                                        ],
                                    }
                                )

                        elif item is not None:
                            cleaned_content.append(str(item))
                    msg["content"] = "\n".join(c for c in cleaned_content if c)
                elif not isinstance(content, str):
                    msg["content"] = str(content) if content is not None else ""

            elif role == "assistant":
                # Handle assistant message content arrays
                # Together doesn't support 'thinking' type - convert to text
                if isinstance(content, list):
                    cleaned_content = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "thinking":
                                # Convert thinking blocks to text
                                thinking_text = item.get("thinking", "") or item.get(
                                    "text", ""
                                )
                                if thinking_text:
                                    msg["reasoning_content"] = thinking_text

                            elif item.get("type") == "text":
                                cleaned_content.append(item.get("text", ""))
                            elif "text" in item:
                                cleaned_content.append(item["text"])
                            else:
                                cleaned_content.append(str(item))
                        elif item is not None:
                            cleaned_content.append(str(item))
                    msg["content"] = "\n".join(c for c in cleaned_content if c) or " "
                elif not isinstance(content, str):
                    msg["content"] = str(content) if content is not None else " "

            elif role == "user":
                msg.pop("tool_call_id", None)
                if len(defered_vision_messages) > 0:
                    converted_messages.extend(defered_vision_messages)
                    defered_vision_messages = []

            if "tool_calls" in msg and msg.get("tool_calls", []):
                converted_tool_calls = []
                for raw_tool_call in msg["tool_calls"]:
                    tool_call = dict(raw_tool_call)
                    converted_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "type": tool_call.get("type", "function"),
                            "function": {
                                "name": tool_call.get("name", ""),
                                "arguments": json.dumps(tool_call.get("arguments", {})),
                            },
                        }
                    )

                # set default empty reasoning_content if message has tool_calls
                if "thinking" in ModelRegistry.get_model_capabilities(
                    f"{self._provider_name}/{self.model}"
                ):
                    msg["reasoning_content"] = (
                        msg["reasoning_content"]
                        if "reasoning_content" in msg and msg["reasoning_content"]
                        else " "
                    )
                msg["tool_calls"] = converted_tool_calls

            converted_messages.append(msg)

        if len(defered_vision_messages) > 0:
            converted_messages.extend(defered_vision_messages)
            defered_vision_messages = []
        return converted_messages

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

        request_params = {
            "model": model_id or self.model,
            "max_tokens": 3000,
            "temperature": temperature,
            "stream": True,
            "messages": (
                [
                    {"role": "system", "content": self.system_prompt},
                    *prompt,
                ]
                if isinstance(prompt, list)
                else [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ]
            ),
            "reasoning_effort": None,
        }

        stream = await self.client.chat.completions.create(**request_params)

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
                # if (
                #     hasattr(chunk.usage, "prompt_tokens_details")
                #     and chunk.usage.prompt_tokens_details
                # ):
                #     if hasattr(chunk.usage.prompt_tokens_details, "cached_tokens"):
                #         cached_tokens = chunk.usage.prompt_tokens_details.cached_tokens

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
            think_start = result_text.find("<think>")
            think_end = result_text.find("</think>")
            if think_start >= 0 and think_end >= 0:
                result_text = (
                    result_text[:think_start]
                    + result_text[think_end + len("</think>") :]
                )
        return result_text

    def _process_file(self, file_path: str):
        return None

    def process_file_for_message(self, file_path: str) -> dict[str, Any] | None:
        return self._process_file(file_path)

    def handle_file_command(self, file_path: str) -> list[dict[str, Any]] | None:
        content = self._process_file(file_path)
        if content:
            return [content]
        return None

    def register_tool(self, tool_definition, handler_function):
        self.tools.append(tool_definition)
        tool_name = self._extract_tool_name(tool_definition)
        if not tool_name:
            raise ValueError("Tool definition must contain a name")
        self.tool_handlers[tool_name] = handler_function
        logger.info(f"🔧 Registered tool: {tool_name}")

    async def stream_assistant_response(self, messages: list[dict[str, Any]]) -> Any:
        full_model_id = f"{self._provider_name}/{self.model}"
        stream_params: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert_internal_format(messages),
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": 20000,
        }

        forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
        if forced_sample_params:
            if forced_sample_params.temperature is not None:
                stream_params["temperature"] = forced_sample_params.temperature
            if forced_sample_params.top_p is not None:
                stream_params["top_p"] = forced_sample_params.top_p
            extra_body = stream_params.setdefault("extra_body", {})
            if forced_sample_params.top_k is not None:
                extra_body["top_k"] = forced_sample_params.top_k
            if forced_sample_params.min_p is not None:
                extra_body["min_p"] = forced_sample_params.min_p
            if forced_sample_params.repetition_penalty is not None:
                extra_body["repetition_penalty"] = (
                    forced_sample_params.repetition_penalty
                )
            if forced_sample_params.frequency_penalty is not None:
                stream_params["frequency_penalty"] = (
                    forced_sample_params.frequency_penalty
                )
            if forced_sample_params.presence_penalty is not None:
                stream_params["presence_penalty"] = (
                    forced_sample_params.presence_penalty
                )

        if self.system_prompt:
            stream_params["messages"] = [
                {"role": "system", "content": self.system_prompt}
            ] + stream_params["messages"]

        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            full_model_id
        ):
            stream_params["tools"] = self.tools

        # Handle reasoning/thinking mode for Together AI models
        # Different models have different reasoning parameter patterns:
        # - GPT-OSS models: use reasoning_effort ("low", "medium", "high")
        # - Hybrid models (DeepSeek V3.1, Kimi K2.5, GLM-5, Qwen3.5): use reasoning={"enabled": True/False}
        model_caps = ModelRegistry.get_model_capabilities(full_model_id)
        if "thinking" in model_caps:
            if self.reasoning_effort:
                # GPT-OSS style: reasoning_effort parameter
                if self.reasoning_effort in ["low", "medium", "high"]:
                    stream_params["reasoning_effort"] = self.reasoning_effort
                else:
                    # Hybrid models: reasoning.enabled parameter
                    stream_params["reasoning"] = {"enabled": True}
            else:
                # For hybrid models with thinking on by default, we can disable it
                # by passing reasoning={"enabled": False}
                pass

            # For GLM-5 preserved thinking, use chat_template_kwargs
            # This would be set via reasoning_content in message history
            # and chat_template_kwargs={"clear_thinking": False}

        if (
            "structured_output" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.structured_output
        ):
            stream_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "default",
                    "schema": self.structured_output,
                },
            }

        return await self.client.chat.completions.create(**stream_params)

    def process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> Tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        chunk_text = None
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None

        usage = getattr(chunk, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(usage, "completion_tokens", 0) or 0
            prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
            if prompt_tokens_details is not None:
                cached_tokens = getattr(prompt_tokens_details, "cached_tokens", 0) or 0

        choices = getattr(chunk, "choices", None)
        if not choices:
            return (
                assistant_response or " ",
                tool_uses,
                TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                ),
                chunk_text,
                None,
            )

        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return (
                assistant_response or " ",
                tool_uses,
                TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                ),
                chunk_text,
                None,
            )

        delta_reasoning = getattr(delta, "reasoning", None)
        if delta_reasoning:
            thinking_content = (delta_reasoning, None)

        delta_content = getattr(delta, "content", None)
        if delta_content:
            chunk_text = delta_content
            assistant_response += delta_content

        delta_tool_calls = getattr(delta, "tool_calls", None)
        if delta_tool_calls:
            for tool_call_delta in delta_tool_calls:
                raw_index = getattr(tool_call_delta, "index", len(tool_uses))
                try:
                    tool_call_index = int(raw_index)
                except (TypeError, ValueError):
                    tool_call_index = len(tool_uses)

                while tool_call_index >= len(tool_uses):
                    tool_uses.append(
                        {
                            "id": None,
                            "name": "",
                            "input": {},
                            "type": "function",
                            "response": "",
                        }
                    )

                current_tool = tool_uses[tool_call_index]

                tool_id = getattr(tool_call_delta, "id", None)
                if tool_id:
                    current_tool["id"] = tool_id

                tool_type = getattr(tool_call_delta, "type", None)
                if tool_type:
                    current_tool["type"] = tool_type

                function_data = getattr(tool_call_delta, "function", None)
                if function_data is not None:
                    function_name = getattr(function_data, "name", None)
                    if function_name:
                        current_tool["name"] = function_name

                    function_arguments = getattr(function_data, "arguments", None)
                    if function_arguments:
                        current_args = current_tool.get("args_json", "")
                        current_tool["args_json"] = current_args + function_arguments
                        try:
                            current_tool["input"] = json.loads(
                                current_tool["args_json"]
                            )
                        except json.JSONDecodeError:
                            pass

            if not chunk_text:
                chunk_text = ""

            return (
                assistant_response or " ",
                tool_uses,
                TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                ),
                chunk_text,
                thinking_content,
            )

        delta_function_call = getattr(delta, "function_call", None)
        if delta_function_call is not None:
            if not tool_uses:
                tool_uses.append(
                    {
                        "id": None,
                        "name": "",
                        "input": {},
                        "type": "function",
                        "response": "",
                    }
                )

            current_tool = tool_uses[-1]
            function_name = getattr(delta_function_call, "name", None)
            if function_name:
                current_tool["name"] = function_name

            function_arguments = getattr(delta_function_call, "arguments", None)
            if function_arguments:
                current_args = current_tool.get("args_json", "")
                current_tool["args_json"] = current_args + function_arguments
                try:
                    current_tool["input"] = json.loads(current_tool["args_json"])
                except json.JSONDecodeError:
                    pass

            if not chunk_text:
                chunk_text = ""

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            thinking_content,
        )

    async def validate_spec(self, prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cached_tokens = 0
        # if (
        #     response.usage
        #     and hasattr(response.usage, "prompt_tokens_details")
        #     and response.usage.prompt_tokens_details
        # ):
        #     if hasattr(response.usage.prompt_tokens_details, "cached_tokens"):
        #         cached_tokens = response.usage.prompt_tokens_details.cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)

        logger.info("\nSpec Validation Token Usage:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return (
            response.choices[0].message.content or ""
            if response.choices[0].message
            else ""
        )

    def set_system_prompt(self, system_prompt: str):
        self.system_prompt = system_prompt

    def clear_tools(self):
        self.tools = []
        self.tool_handlers = {}
