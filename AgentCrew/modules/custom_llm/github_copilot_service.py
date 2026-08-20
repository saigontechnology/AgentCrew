import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx2
from dotenv import load_dotenv
from loguru import logger

from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.token_usage import TokenUsage

from .service import CustomLLMService


class GithubCopilotService(CustomLLMService):
    def __init__(
        self, api_key: str | None = None, provider_name: str = "github_copilot"
    ):
        if api_key is None:
            load_dotenv()
            api_key = os.getenv("GITHUB_COPILOT_API_KEY")
            if not api_key:
                logger.error(
                    "GITHUB_COPILOT_API_KEY not found in environment variables"
                )
        super().__init__(
            api_key=api_key,
            base_url="https://api.githubcopilot.com",
            provider_name=provider_name,
            extra_headers={
                "Copilot-Integration-Id": "vscode-chat",
                "Editor-Plugin-Version": "CopilotChat.nvim/*",
                "Editor-Version": "Neovim/0.9.0",
            },
        )
        self.model = "claude-sonnet-4.6"
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        self._is_thinking = False
        # self._interaction_id = None
        logger.info("Initialized Github Copilot Service")

    def set_think(self, budget_tokens) -> bool:
        """
        Enable or disable thinking mode with the specified token budget.

        Args:
            budget_tokens (int): Token budget for thinking. 0 to disable thinking mode.

        Returns:
            bool: True if thinking mode is supported and successfully set, False otherwise.
        """
        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            if budget_tokens == "0" or budget_tokens == "none":
                self.reasoning_effort = None
                return True
            elif budget_tokens not in ["low", "medium", "high", "max"]:
                raise ValueError("budget_tokens must be low, medium, high or max")

            self.reasoning_effort = budget_tokens
            return True
        logger.info("Thinking mode is not supported for OpenAI models.")
        return False

    def _github_copilot_token_to_open_ai_key(self, copilot_api_key):
        """
        Convert GitHub Copilot token to OpenAI key format.

        Args:
            copilot_api_key: The GitHub Copilot token

        Returns:
            Updated OpenAI compatible token
        """
        openai_api_key = self.client.api_key

        if openai_api_key.startswith("ghu") or int(
            dict(x.split("=") for x in openai_api_key.split(";"))["exp"]
        ) < int(datetime.now(UTC).timestamp()):
            import requests

            headers = {
                "Authorization": f"Bearer {copilot_api_key}",
                "Content-Type": "application/json",
            }
            if self.extra_headers:
                headers.update(self.extra_headers)
            res = requests.get(
                "https://api.github.com/copilot_internal/v2/token", headers=headers
            )
            self.client.api_key = res.json()["token"]

    def _is_github_provider(self):
        if self.base_url:
            from urllib.parse import urlparse

            parsed_url = urlparse(self.base_url)
            host = parsed_url.hostname
            if host and host.endswith(".githubcopilot.com"):
                return True
        return False

    @staticmethod
    def _window_label(name: str, window_seconds: Any = None) -> str:
        if window_seconds == 18000:
            return "5h"
        if window_seconds == 604800:
            return "weekly"
        normalized = name.replace("_limit", "").replace("-limit", "")
        if normalized in {"session", "weekly", "monthly"}:
            return normalized
        if isinstance(window_seconds, (int, float)) and window_seconds > 0:
            minutes = int((window_seconds + 59) // 60)
            if minutes % 1440 == 0:
                return f"{minutes // 1440}d"
            if minutes % 60 == 0:
                return f"{minutes // 60}h"
            return f"{minutes}m"
        return normalized or "unknown"

    @classmethod
    def _normalize_window(cls, name: str, window: Any) -> dict[str, Any] | None:
        if not isinstance(window, dict):
            return None
        used_percent = (
            window.get("used_percent")
            or window.get("usedPercent")
            or window.get("percentage")
        )
        if (
            used_percent is None
            and window.get("used") is not None
            and window.get("total")
        ):
            try:
                used_percent = (float(window["used"]) / float(window["total"])) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                used_percent = None
        try:
            used_percent_value = (
                float(used_percent) if used_percent is not None else None
            )
        except (TypeError, ValueError):
            used_percent_value = None
        window_seconds = window.get("limit_window_seconds") or window.get(
            "limitWindowSeconds"
        )
        return {
            "name": cls._window_label(name, window_seconds),
            "used_percent": used_percent_value,
            "remaining_percent": max(0.0, 100.0 - used_percent_value)
            if used_percent_value is not None
            else None,
            "window_seconds": window_seconds,
            "reset_at": window.get("reset_at")
            or window.get("resetAt")
            or window.get("next_reset_at")
            or window.get("nextResetAt"),
            "reset_after_seconds": window.get("reset_after_seconds")
            or window.get("resetAfterSeconds"),
        }

    @classmethod
    def _extract_usage_limits(cls, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        candidates = [
            "session_limit",
            "session",
            "weekly_limit",
            "weekly",
            "monthly_limit",
            "monthly",
            "usage",
            "rate_limit",
            "rateLimit",
        ]
        limits = []
        for key in candidates:
            window = cls._normalize_window(key, payload.get(key))
            if window:
                limits.append(window)
        nested_limits = payload.get("limits")
        if isinstance(nested_limits, list):
            for index, item in enumerate(nested_limits):
                window = cls._normalize_window(
                    item.get("name") or item.get("type") or f"limit_{index + 1}", item
                )
                if window:
                    limits.append(window)
        quota_snapshots = payload.get("quota_snapshots")
        if isinstance(quota_snapshots, dict):
            premium_interactions = cls._normalize_quota_snapshot(
                "premium_interactions", quota_snapshots.get("premium_interactions")
            )
            if premium_interactions:
                limits.append(premium_interactions)
        return limits

    @staticmethod
    def _parse_reset_at(value: Any) -> Any:
        if value in (None, 0, "0"):
            return None
        return value

    @classmethod
    def _normalize_quota_snapshot(
        cls, name: str, snapshot: Any
    ) -> dict[str, Any] | None:
        if not isinstance(snapshot, dict):
            return None
        percent_remaining = snapshot.get("percent_remaining")
        try:
            remaining_percent = (
                float(percent_remaining) if percent_remaining is not None else None
            )
        except (TypeError, ValueError):
            remaining_percent = None
        used_percent = (
            max(0.0, 100.0 - remaining_percent)
            if remaining_percent is not None
            else None
        )
        return {
            "name": name.replace("_", " "),
            "used_percent": used_percent,
            "remaining_percent": remaining_percent,
            "window_seconds": None,
            "reset_at": cls._parse_reset_at(snapshot.get("quota_reset_at")),
            "reset_after_seconds": None,
            "remaining": snapshot.get("remaining"),
            "entitlement": snapshot.get("entitlement"),
            "unlimited": snapshot.get("unlimited"),
        }

    async def get_usage(self) -> dict[str, Any]:
        if not self.api_key or not str(self.api_key).startswith("gh"):
            return {
                "supported": False,
                "provider": self.provider_name,
                "model": self.model,
                "message": "Usage not supported for this provider",
                "limits": [],
            }

        async with httpx2.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://api.github.com/copilot_internal/user",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()

        limits = self._extract_usage_limits(payload)
        message = None
        if not limits:
            message = "Usage data returned but no known limit windows could be parsed. Raw provider payload is available in logs."
            logger.debug(f"Unparsed GitHub Copilot usage payload: {payload}")

        return {
            "supported": True,
            "provider": self.provider_name,
            "model": self.model,
            "plan_type": payload.get("plan_type") or payload.get("planType"),
            "message": message,
            "limits": limits,
            "credits": payload.get("premium_requests") or payload.get("credits"),
            "raw": payload,
        }

    def _convert_internal_format(self, messages: list[dict[str, Any]]):
        for msg in messages:
            msg.pop("agent", None)

            if msg.get("role") == "consolidated":
                msg["role"] = "user"
                msg.pop("metadata", None)
            elif msg.get("role") == "assistant":
                if isinstance(msg.get("content", ""), list):
                    thinking_block = next(
                        (
                            block
                            for block in msg.get("content", [])
                            if block.get("type", "text") == "thinking"
                        ),
                        None,
                    )
                    msg["content"] = [
                        block
                        for block in msg["content"]
                        if block.get("type", "text") != "thinking"
                    ] or " "
                    if thinking_block:
                        msg["reasoning_text"] = thinking_block.get("thinking", "")
                        msg["reasoning_opaque"] = thinking_block.get("signature", "")

            elif msg.get("role") == "tool":
                msg.pop("tool_name", None)
                msg.pop("is_rejected", None)
                if isinstance(msg.get("content", ""), list):
                    # OpenAI format for tool responses
                    parsed_tool_result = []
                    for tool_content in msg["content"]:
                        if tool_content.get("type", "text") == "image_url":
                            if "vision" in ModelRegistry.get_model_capabilities(
                                f"{self._provider_name}/{self.model}"
                            ):
                                parsed_tool_result.append(tool_content)
                        else:
                            parsed_tool_result.append(tool_content)
                    msg["content"] = parsed_tool_result
                elif isinstance(msg.get("content", ""), str):
                    msg["content"] = [{"type": "text", "text": msg["content"]}]
            elif msg.get("role") == "user":
                msg.pop("tool_call_id", None)

            if "tool_calls" in msg and msg.get("tool_calls", []):
                normalized_tool_calls = []
                for raw_tool_call in msg["tool_calls"]:
                    normalized_tool_call = self._normalize_tool_call_for_request(
                        raw_tool_call
                    )
                    if normalized_tool_call:
                        normalized_tool_calls.append(normalized_tool_call)
                msg["tool_calls"] = normalized_tool_calls

        return messages

    async def process_message(
        self,
        prompt: str | list,
        temperature: float = 0,
        model_id: str | None = None,
    ) -> str:
        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            if self.extra_headers:
                self.extra_headers["X-Initiator"] = "user"
                self.extra_headers["X-Request-Id"] = str(uuid4())
        return await super().process_message(prompt, temperature, model_id)

    def _process_stream_chunk(
        self, chunk, assistant_response: str, tool_uses: list[dict]
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        chunk_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        thinking_content = None
        thinking_signature = None

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

        # Handle thinking content
        if (
            hasattr(delta_chunk, "reasoning_text")
            and delta_chunk.reasoning_text is not None
        ):
            thinking_content = delta_chunk.reasoning_text

        if (
            hasattr(delta_chunk, "reasoning_opaque")
            and delta_chunk.reasoning_opaque is not None
        ):
            thinking_signature = delta_chunk.reasoning_opaque
        # Handle regular content chunks
        if hasattr(delta_chunk, "content") and delta_chunk.content is not None:
            chunk_text = chunk.choices[0].delta.content
            assistant_response += chunk_text

        # Handle tool call chunks
        if hasattr(delta_chunk, "tool_calls"):
            delta_tool_calls = chunk.choices[0].delta.tool_calls
            if delta_tool_calls:
                for tool_call_delta in delta_tool_calls:
                    self._merge_stream_tool_call_delta(tool_uses, tool_call_delta)

        return (
            assistant_response or " ",
            tool_uses,
            TokenUsage(
                input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            chunk_text,
            (thinking_content, thinking_signature)
            if thinking_content or thinking_signature
            else None,
        )

    async def stream_assistant_response(self, messages):
        """Stream the assistant's response with tool support."""

        if self._is_github_provider():
            self.base_url = self.base_url.rstrip("/")
            self._github_copilot_token_to_open_ai_key(self.api_key)
            # if len([m for m in messages if m.get("role") == "assistant"]) == 0:
            #     self._interaction_id = str(uuid4())
            if self.extra_headers:
                self.extra_headers["X-Initiator"] = (
                    "user"
                    if messages[-1].get("role", "assistant") == "user"
                    else "agent"
                )
                self.extra_headers["X-Request-Id"] = str(uuid4())
                if (
                    len(
                        [
                            m
                            for m in messages
                            if isinstance(m.get("content", ""), list)
                            and len(
                                [
                                    n
                                    for n in m.get("content", [])
                                    if n.get("type", "text") == "image_url"
                                ]
                            )
                            > 0
                        ]
                    )
                    > 0
                ) and "vision" in ModelRegistry.get_model_capabilities(
                    f"{self._provider_name}/{self.model}"
                ):
                    self.extra_headers["Copilot-Vision-Request"] = "true"

        return await super().stream_assistant_response(messages)
