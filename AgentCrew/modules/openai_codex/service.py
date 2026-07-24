import os
from typing import Any

import httpx
from loguru import logger

from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.openai import OpenAIResponseService
from AgentCrew.modules.openai_codex.oauth import OpenAICodexOAuth

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_CODEX_INSTRUCTIONS = "You are a helpful assistant."


class OpenAICodexService(OpenAIResponseService):
    def __init__(self, token_path: str | None = None):
        self._oauth = OpenAICodexOAuth(token_path=token_path)

        access_token = self._oauth.get_valid_access_token()
        if not access_token:
            logger.error(
                "No valid OpenAI Codex OAuth token found. "
                "Run 'agentcrew chatgpt-auth' to authenticate with your ChatGPT subscription."
            )

        super().__init__(
            api_key=access_token,
            base_url=CODEX_BASE_URL,
        )
        self._provider_name = "openai_codex"
        self.model = "gpt-5.4"

        # Match Codex CLI's header behavior:
        # Authorization: Bearer <token> done by the client via api_key
        # ChatGPT-Account-ID: <account_id> for subscription routing
        if self._oauth.account_id:
            self._extra_headers = {
                "ChatGPT-Account-ID": self._oauth.account_id,
            }

        logger.info("Initialized OpenAI Codex Service (ChatGPT subscription)")

    def _ensure_valid_token(self):
        new_token = self._oauth.get_valid_access_token()
        if new_token and new_token != self.client.api_key:
            self.client.api_key = new_token
            logger.debug("Refreshed OAuth access token for Codex service")
        elif not new_token:
            logger.warning(
                "OAuth token expired and could not be refreshed. "
                "Re-run 'agentcrew chatgpt-auth' to re-authenticate."
            )

    def _usage_url(self) -> str:
        base_url = str(self.base_url or CODEX_BASE_URL).rstrip("/")
        if "/backend-api/codex" in base_url:
            return base_url.replace("/backend-api/codex", "/backend-api/wham/usage")
        return f"{base_url}/api/codex/usage"

    @staticmethod
    def _window_label(window_seconds: Any, fallback: str) -> str:
        if window_seconds == 18000:
            return "5h"
        if window_seconds == 604800:
            return "weekly"
        if isinstance(window_seconds, (int, float)) and window_seconds > 0:
            minutes = int((window_seconds + 59) // 60)
            if minutes % 1440 == 0:
                days = minutes // 1440
                return f"{days}d"
            if minutes % 60 == 0:
                hours = minutes // 60
                return f"{hours}h"
            return f"{minutes}m"
        return fallback

    @classmethod
    def _normalize_window(cls, name: str, window: Any) -> dict[str, Any] | None:
        if not isinstance(window, dict):
            return None
        used_percent = window.get("used_percent")
        if used_percent is None:
            used_percent = window.get("usedPercent")
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
            "name": cls._window_label(window_seconds, name),
            "used_percent": used_percent_value,
            "remaining_percent": max(0.0, 100.0 - used_percent_value)
            if used_percent_value is not None
            else None,
            "window_seconds": window_seconds,
            "reset_at": window.get("reset_at") or window.get("resetAt"),
            "reset_after_seconds": window.get("reset_after_seconds")
            or window.get("resetAfterSeconds"),
        }

    @staticmethod
    def _select_codex_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            additional = payload.get("additional_rate_limits") or payload.get(
                "additionalRateLimits"
            )
            candidates = [payload]
            if isinstance(additional, list):
                candidates.extend(item for item in additional if isinstance(item, dict))
            for candidate in candidates:
                if (
                    candidate.get("limit_id") == "codex"
                    or candidate.get("limitId") == "codex"
                ):
                    return candidate
            return payload
        if isinstance(payload, list) and payload:
            dict_items = [item for item in payload if isinstance(item, dict)]
            for item in dict_items:
                if item.get("limit_id") == "codex" or item.get("limitId") == "codex":
                    return item
            if dict_items:
                return dict_items[0]
        return {}

    @staticmethod
    def _codex_service_tier() -> str:
        if os.getenv("AGENTCREW_FAST_CODEX") == "1":
            return "priority"
        return "default"

    async def get_usage(self) -> dict[str, Any]:
        self._ensure_valid_token()
        access_token = self._oauth.get_valid_access_token()
        if not access_token:
            raise ValueError(
                "No valid OpenAI Codex OAuth token found. Run 'agentcrew chatgpt-auth' to authenticate."
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        if self._oauth.account_id:
            headers["ChatGPT-Account-ID"] = self._oauth.account_id

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self._usage_url(),
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        selected_payload = self._select_codex_payload(payload)
        rate_limit = selected_payload.get("rate_limit") or selected_payload.get(
            "rateLimit"
        )
        limits = []
        if isinstance(rate_limit, dict):
            primary = self._normalize_window(
                "5h",
                rate_limit.get("primary_window") or rate_limit.get("primaryWindow"),
            )
            secondary = self._normalize_window(
                "weekly",
                rate_limit.get("secondary_window") or rate_limit.get("secondaryWindow"),
            )
            limits = [window for window in (primary, secondary) if window]

        return {
            "supported": True,
            "provider": self.provider_name,
            "model": self.model,
            "plan_type": selected_payload.get("plan_type")
            or selected_payload.get("planType"),
            "message": None,
            "limits": limits,
            "credits": selected_payload.get("credits"),
            "raw": payload,
        }

    async def process_message(
        self,
        prompt: str | list,
        temperature: float = 0,
        model_id: str | None = None,
    ) -> str:
        self._ensure_valid_token()
        request_params = {
            "model": model_id or self.model,
            "input": self._convert_internal_format(prompt)
            if isinstance(prompt, list)
            else [{"role": "user", "content": prompt}],
            "stream": True,
            "store": False,
            "instructions": self.system_prompt or DEFAULT_CODEX_INSTRUCTIONS,
            "service_tier": self._codex_service_tier(),
        }
        if self._extra_headers:
            request_params["extra_headers"] = self._extra_headers

        if "thinking" in ModelRegistry.get_model_capabilities(
            f"{self._provider_name}/{self.model}"
        ):
            request_params["reasoning"] = {"effort": "none"}

        result_text = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        async for event in await self.client.responses.create(**request_params):
            if event.type == "response.output_text.delta":
                result_text += event.delta
            elif event.type == "response.completed":
                usage = getattr(event.response, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "input_tokens", 0)
                    output_tokens = getattr(usage, "output_tokens", 0)
                    input_tokens_details = getattr(usage, "input_tokens_details", None)
                    if input_tokens_details:
                        cached_tokens = getattr(
                            input_tokens_details, "cached_tokens", 0
                        )

        if cached_tokens:
            input_tokens = input_tokens - cached_tokens
        total_cost = self.calculate_cost(input_tokens, output_tokens, cached_tokens)
        logger.info("\nCodex Response API Token Usage Statistics:")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        if cached_tokens:
            logger.info(f"Cached tokens: {cached_tokens:,}")
        logger.info(f"Total tokens: {input_tokens + output_tokens + cached_tokens:,}")
        logger.info(f"Estimated cost: ${total_cost:.4f}")

        return result_text

    async def _ensure_extra_headers(self):
        """Ensure ChatGPT-Account-ID header is set after token refresh."""
        if self._oauth.account_id:
            base = dict(self._extra_headers or {})
            base["ChatGPT-Account-ID"] = self._oauth.account_id
            self._extra_headers = base

    async def stream_assistant_response(self, messages) -> Any:
        self._ensure_valid_token()
        await self._ensure_extra_headers()

        input_data = self._convert_internal_format(messages)
        full_model_id = f"{self._provider_name}/{self.model}"

        stream_params = {
            "model": self.model,
            "input": input_data,
            "stream": True,
            "instructions": self.system_prompt or DEFAULT_CODEX_INSTRUCTIONS,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "service_tier": self._codex_service_tier(),
        }

        forced_sample_params = ModelRegistry.get_model_sample_params(full_model_id)
        if forced_sample_params:
            if forced_sample_params.temperature is not None:
                stream_params["temperature"] = forced_sample_params.temperature
            if forced_sample_params.top_p is not None:
                stream_params["top_p"] = forced_sample_params.top_p

        if (
            "thinking" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.reasoning_effort
        ):
            stream_params["reasoning"] = {"effort": self.reasoning_effort}

        if self._extra_headers:
            stream_params["extra_headers"] = self._extra_headers

        if self.tools and "tool_use" in ModelRegistry.get_model_capabilities(
            full_model_id
        ):
            stream_params["tools"] = self.tools.copy()

        if (
            "structured_output" in ModelRegistry.get_model_capabilities(full_model_id)
            and self.structured_output
        ):
            stream_params["text"] = {
                "format": {
                    "name": "default",
                    "type": "json_schema",
                    "json_schema": self.structured_output,
                }
            }

        return await self.client.responses.create(**stream_params)
