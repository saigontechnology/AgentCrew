from typing import Literal

from pydantic import BaseModel


class SampleParam(BaseModel):
    temperature: float | None = None
    top_p: float | None = None
    min_p: float | None = None
    top_k: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None


class Model(BaseModel):
    """Model metadata class."""

    id: str
    provider: str
    name: str
    description: str
    capabilities: list[
        Literal[
            "tool_use",
            "stream",
            "thinking",
            "vision",
            "structured_output",
        ]
    ]
    default: bool = False
    default_reasoning: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        | None
    ) = None
    force_sample_params: SampleParam | None = None
    max_context_token: int = 80_000
    input_token_price_1m: float = 0.0
    output_token_price_1m: float = 0.0
    cached_token_price_1m: float = 0.0
    service_name: str | None = None
    vision_model: str | None = None

    def resolved_service_name(self) -> str:
        """Return the service name to use for this model, falling back to provider."""
        return self.service_name or self.provider
