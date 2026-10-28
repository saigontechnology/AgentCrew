"""OpenAI-compatible Refuseless chat adapter with provider-specific parameters."""

from typing import Any

from AgentCrew.modules.llm.model_registry import ModelRegistry

from .service import CustomLLMService


class RefuselessService(CustomLLMService):
    """Use Refuseless's documented reasoning vocabulary and sampling defaults."""

    allow_unconfigured_reasoning = True
    _non_stream_reasoning_effort = "off"

    def __init__(self, api_key: str):
        super().__init__(
            base_url="https://api.refuseless.com/v1",
            api_key=api_key,
            provider_name="refuseless",
        )

    def set_think(self, budget_tokens: str | int) -> bool:
        """Map AgentCrew reasoning selections to Refuseless effort levels."""
        levels = {
            "none": "off",
            "0": "off",
            0: "off",
            "off": "off",
            "minimal": "low",
            "low": "low",
            "medium": "high",
            "high": "high",
            "xhigh": "max",
            "max": "max",
        }
        if budget_tokens not in levels:
            raise ValueError(
                f"Unsupported Refuseless reasoning level: {budget_tokens!r}"
            )
        if "thinking" not in ModelRegistry.get_model_capabilities(
            f"{self.provider_name}/{self.model}"
        ):
            return False
        self.reasoning_effort = levels[budget_tokens]
        return True

    def _build_stream_params(self) -> tuple[dict[str, Any], bool]:
        """Omit the generic min_p default without dropping explicit sampling."""
        params, is_streamable = super()._build_stream_params()
        extra_body = params.get("extra_body")
        if extra_body is not None:
            forced = ModelRegistry.get_model_sample_params(
                f"{self.provider_name}/{self.model}"
            )
            if forced is None or forced.min_p is None:
                extra_body.pop("min_p", None)
            if not extra_body:
                params.pop("extra_body")
        return params, is_streamable
