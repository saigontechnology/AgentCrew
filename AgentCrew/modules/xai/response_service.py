"""Experimental direct xAI subscription OAuth Responses adapter."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx2

from AgentCrew.modules.agents.message_metadata import (
    get_responses_provider_state,
    strip_internal_metadata,
)
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.reasoning_selection import REASONING_LEVELS
from AgentCrew.modules.llm.token_usage import TokenUsage
from AgentCrew.modules.llm.types import Model
from AgentCrew.modules.openai.response_service import OpenAIResponseService
from AgentCrew.modules.openai.response_stream import ResponsesStreamGuard
from AgentCrew.modules.xai.oauth import XAIAuthenticationError, XAIOAuth

XAI_BASE_URL = "https://api.x.ai/v1"
XAI_PROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
XAI_USAGE_CLIENT_MARKER = "xai-grok-cli"
XAI_USER_URL = f"{XAI_PROXY_BASE_URL}/user?include=subscription"
XAI_BILLING_URL = f"{XAI_PROXY_BASE_URL}/billing?format=credits"
_NO_ALLOWANCE_MESSAGE = "No allowance was returned."
_CREDIT_KEYS = {
    "creditUsagePercent",
    "currentPeriod",
    "monthlyLimit",
    "used",
    "onDemandCap",
    "onDemandUsed",
    "prepaidBalance",
}
_PERIOD_NAMES = {
    "USAGE_PERIOD_TYPE_WEEKLY": "weekly",
    "USAGE_PERIOD_TYPE_MONTHLY": "monthly",
}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _present_val(field: Any) -> float | None:
    if not isinstance(field, dict) or "val" not in field:
        return None
    return _as_float(field.get("val"))


def _cent_amount(field: Any) -> int | None:
    if not isinstance(field, dict):
        return None
    if "val" not in field:
        return 0
    raw = field.get("val")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw)


def _parse_reset_at(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _period_name(period_type: Any) -> str:
    if not isinstance(period_type, str) or not period_type.strip():
        return "allowance"
    normalized = period_type.strip()
    mapped = _PERIOD_NAMES.get(normalized)
    if mapped:
        return mapped
    if normalized.startswith("USAGE_PERIOD_TYPE_"):
        normalized = normalized.removeprefix("USAGE_PERIOD_TYPE_")
    readable = normalized.replace("_", " ").strip().lower()
    return readable or "allowance"


def _limit(
    name: str,
    used_percent: float,
    *,
    window_seconds: int | None,
    reset_at: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "used_percent": used_percent,
        "remaining_percent": max(0.0, 100.0 - used_percent),
        "window_seconds": window_seconds,
        "reset_at": reset_at,
        "reset_after_seconds": None,
    }


def _plan_from_user(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    plan_type = _nonempty_str(payload.get("subscriptionTier")) or _nonempty_str(
        payload.get("subscription_tier")
    )
    user_id = _nonempty_str(payload.get("userId")) or _nonempty_str(
        payload.get("user_id")
    )
    return plan_type, user_id


def _billing_config(payload: Any) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(payload, dict):
        return None, False
    config = payload.get("config")
    if isinstance(config, dict):
        return config, True
    if _CREDIT_KEYS.intersection(payload):
        return payload, False
    return None, False


def _allowance_window(used: float, config: dict[str, Any]) -> dict[str, Any]:
    period = config.get("currentPeriod")
    period = period if isinstance(period, dict) else {}
    name = _period_name(period.get("type"))
    return _limit(
        name,
        used,
        window_seconds=604800 if name == "weekly" else None,
        reset_at=_parse_reset_at(period.get("end")),
    )


def _legacy_monthly_limit(config: dict[str, Any]) -> dict[str, Any] | None:
    limit = _present_val(config.get("monthlyLimit"))
    used = _present_val(config.get("used"))
    if limit is None or used is None or limit <= 0:
        return None
    period = config.get("currentPeriod")
    period = period if isinstance(period, dict) else {}
    reset_at = _parse_reset_at(period.get("end"))
    if reset_at is None:
        reset_at = _parse_reset_at(config.get("billingPeriodEnd"))
    return _limit(
        "monthly",
        (used / limit) * 100,
        window_seconds=None,
        reset_at=reset_at,
    )


def _included_limit(
    config: dict[str, Any], *, omitted_percent_is_zero: bool
) -> dict[str, Any] | None:
    if "creditUsagePercent" in config:
        used = _as_float(config.get("creditUsagePercent"))
        if used is not None:
            return _allowance_window(used, config)
    legacy = _legacy_monthly_limit(config)
    if legacy is not None:
        return legacy
    if omitted_percent_is_zero:
        return _allowance_window(0.0, config)
    return None


def _on_demand_reset(config: dict[str, Any]) -> Any:
    period = config.get("onDemandPeriod")
    if isinstance(period, dict) and "end" in period:
        return _parse_reset_at(period.get("end"))
    return None


def _on_demand_limit(config: dict[str, Any]) -> dict[str, Any] | None:
    cap = _present_val(config.get("onDemandCap"))
    if cap is None or cap <= 0:
        return None
    used = _present_val(config.get("onDemandUsed"))
    if used is None:
        used = 0.0
    return _limit(
        "on-demand",
        (used / cap) * 100,
        window_seconds=None,
        reset_at=_on_demand_reset(config),
    )


def _prepaid_credits(config: dict[str, Any]) -> dict[str, str] | None:
    if "prepaidBalance" not in config:
        return None
    cents = _cent_amount(config.get("prepaidBalance"))
    if cents is None:
        return None
    return {"balance": f"${cents / 100:.2f}"}


def _usage_from_billing(
    payload: Any,
    *,
    provider: str,
    model: str,
    plan_type: str | None,
) -> dict[str, Any]:
    config, nested = _billing_config(payload)
    limits: list[dict[str, Any]] = []
    credits = None
    message = None
    if config is None:
        message = _NO_ALLOWANCE_MESSAGE
    else:
        included = _included_limit(config, omitted_percent_is_zero=nested)
        if included is not None:
            limits.append(included)
        on_demand = _on_demand_limit(config)
        if on_demand is not None:
            limits.append(on_demand)
        credits = _prepaid_credits(config)
        if not limits:
            message = _NO_ALLOWANCE_MESSAGE
    return {
        "supported": True,
        "provider": provider,
        "model": model,
        "plan_type": plan_type,
        "message": message,
        "limits": limits,
        "credits": credits,
        "raw": payload,
    }


class XAISubscriptionResponseService(OpenAIResponseService):
    """Reuse Responses history/event orchestration, without OpenAI-only options."""

    strict_reasoning_selection = True

    async def close(self) -> None:
        await super().close()
        self._client_loop = None

    def __init__(self, token_path: str | None = None):
        self._oauth = XAIOAuth(token_path)
        token = self._oauth.get_valid_access_token()
        super().__init__(api_key=token, base_url=XAI_BASE_URL)
        self.client = self.client.with_options(max_retries=0)
        self._client_loop = None
        self._provider_name = "xai"
        self._models_ready = True
        self.model = "grok-4.7"

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        if not getattr(self, "_models_ready", False):
            self._model = value
            return
        model = self._model_definition(value)
        self._model = value
        self.reasoning_effort = model.default_reasoning

    @staticmethod
    def _model_definition(model_id: str) -> Model:
        model = ModelRegistry.get_instance().get_model(f"xai/{model_id}")
        if model is None or model.provider != "xai" or model.id != model_id:
            raise ValueError(
                f"Unsupported xAI subscription Responses model: {model_id}"
            )
        return model

    @property
    def allow_unconfigured_reasoning(self) -> bool:
        return "thinking" not in self._model_definition(self.model).capabilities

    async def _ensure_valid_token(self) -> None:
        self.client.api_key = await asyncio.to_thread(
            self._oauth.get_valid_access_token
        )

    def set_think(self, budget_tokens: str) -> bool:
        if budget_tokens not in REASONING_LEVELS:
            raise ValueError(
                f"Invalid reasoning effort '{budget_tokens}'. "
                f"Must be one of: {', '.join(REASONING_LEVELS)}"
            )
        if self.allow_unconfigured_reasoning:
            return False
        self.reasoning_effort = budget_tokens
        return True

    def _convert_internal_format(
        self, messages: list[dict[str, Any]], *, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        target_model = self.model if model_id is None else model_id
        model = self._model_definition(target_model)
        if "thinking" not in model.capabilities:
            return super()._convert_internal_format(
                strip_internal_metadata(messages), model_id=target_model
            )
        filtered = []
        for message in messages:
            state = get_responses_provider_state(message)
            if state and (
                state.get("provider") != self.provider_name
                or state.get("model") != target_model
            ):
                filtered.extend(strip_internal_metadata([message]))
            else:
                filtered.append(message)
        return super()._convert_internal_format(filtered, model_id=target_model)

    def _capture_reasoning_item(
        self, item: Any, stream_state: dict[str, Any] | None
    ) -> None:
        if (
            "thinking" in self._model_definition(self.model).capabilities
            and stream_state is not None
            and stream_state.get("provider") == self.provider_name
            and stream_state.get("model") == self.model
        ):
            super()._capture_reasoning_item(item, stream_state)

    async def _create_responses_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model_id: str,
        reasoning_effort: str | None,
        tools_enabled: bool = True,
    ) -> ResponsesStreamGuard:
        model = self._model_definition(model_id)
        loop = asyncio.get_running_loop()
        if self._client_loop is not None and self._client_loop is not loop:
            raise RuntimeError(
                "xAI Responses client is bound to a different asyncio event loop"
            )
        self._client_loop = loop
        await self._ensure_valid_token()
        params = {
            "model": model_id,
            "input": self._convert_internal_format(messages, model_id=model_id),
            "instructions": self.system_prompt or "You are a helpful assistant.",
            "stream": True,
            "store": False,
        }
        if "thinking" in model.capabilities:
            params["include"] = ["reasoning.encrypted_content"]
            if reasoning_effort is not None:
                params["reasoning"] = {"effort": reasoning_effort}
        if tools_enabled and self.tools:
            params["tools"] = [
                tool for tool in self.tools if tool.get("type") == "function"
            ]
        return ResponsesStreamGuard(await self.client.responses.create(**params), "xAI")

    async def stream_assistant_response(
        self, messages: list[dict[str, Any]], *, tools_enabled: bool = True
    ) -> Any:
        return await self._create_responses_stream(
            messages,
            model_id=self.model,
            reasoning_effort=self.reasoning_effort,
            tools_enabled=tools_enabled,
        )

    @staticmethod
    def _check_stream_event(event_type: str | None) -> None:
        if event_type in ("response.failed", "response.incomplete", "error"):
            raise RuntimeError(
                "xAI Responses stream failed or incomplete; request was not replayed"
            )

    def process_stream_chunk(
        self,
        chunk: Any,
        assistant_response: str,
        tool_uses: list[dict],
        stream_state: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict], TokenUsage, str | None, tuple | None]:
        event_type = getattr(chunk, "type", None)
        self._check_stream_event(event_type)
        if event_type == "response.completed" and stream_state is not None:
            response = getattr(chunk, "response", None)
            for item in getattr(response, "output", None) or []:
                if getattr(item, "type", None) == "reasoning" and not any(
                    existing.get("id") == getattr(item, "id", None)
                    for existing in stream_state.get("reasoning_items", [])
                ):
                    self._capture_reasoning_item(item, stream_state)
        return super().process_stream_chunk(
            chunk, assistant_response, tool_uses, stream_state
        )

    async def process_message(
        self, prompt: str | list, temperature: float = 0, model_id: str | None = None
    ) -> str:
        target_model = self.model if model_id is None else model_id
        model = self._model_definition(target_model)
        reasoning_effort = (
            self.reasoning_effort
            if target_model == self.model
            else model.default_reasoning
        )
        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": prompt}]
        )
        text = ""
        async with await self._create_responses_stream(
            messages, model_id=target_model, reasoning_effort=reasoning_effort
        ) as stream:
            async for event in stream:
                event_type = getattr(event, "type", None)
                self._check_stream_event(event_type)
                if event_type == "response.output_text.delta":
                    text += getattr(event, "delta", "") or ""
        return text

    async def validate_spec(self, prompt: str) -> str:
        raise NotImplementedError("xAI Subscription structured output is not validated")

    async def get_response(self, response_id: str) -> dict[str, Any]:
        raise NotImplementedError("xAI Subscription does not store Responses")

    async def cancel_response(self, response_id: str) -> bool:
        raise NotImplementedError("xAI Subscription does not use background Responses")

    async def delete_response(self, response_id: str) -> bool:
        raise NotImplementedError("xAI Subscription does not store Responses")

    async def get_usage(self) -> dict[str, Any]:
        """Return the signed-in xAI subscription allowance, without inference traffic."""
        await self._ensure_valid_token()
        access_token = self.client.api_key
        if not isinstance(access_token, str) or not access_token:
            raise XAIAuthenticationError(
                "xAI OAuth login required; run 'agentcrew xai-auth'"
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-XAI-Token-Auth": XAI_USAGE_CLIENT_MARKER,
            "Accept": "application/json",
        }
        plan_type = None
        user_id = None
        async with httpx2.AsyncClient() as client:
            try:
                user_response = await client.get(
                    XAI_USER_URL, headers=headers, timeout=10
                )
                if user_response.status_code < 400:
                    plan_type, user_id = _plan_from_user(user_response.json())
            except Exception:
                plan_type = None
                user_id = None

            billing_headers = dict(headers)
            if user_id:
                billing_headers["x-userid"] = user_id
            billing_response = await client.get(
                XAI_BILLING_URL, headers=billing_headers, timeout=15
            )
            if billing_response.status_code >= 400:
                raise RuntimeError(
                    "xAI billing request failed with status "
                    f"{billing_response.status_code}"
                )
            try:
                payload = billing_response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "xAI billing request returned an unreadable response"
                ) from exc

        return _usage_from_billing(
            payload,
            provider=self.provider_name,
            model=self.model,
            plan_type=plan_type,
        )
