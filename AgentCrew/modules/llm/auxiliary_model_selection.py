"""Lightweight model policy for memory, tool summaries, and code analysis.

Preferences use registered IDs, including explicit endpoint-family aliases.
xAI has no small/flash tier or latency/pricing comparison in its catalog;
Grok 4.3 is the low-reasoning general model rather than the high-reasoning default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_registry import ModelRegistry
    from .types import Model


AUXILIARY_MODEL_PREFERENCES: dict[str, str] = {
    "claude": "claude/claude-haiku-4-5",
    "openai": "openai/gpt-4.1-mini",
    "openai_codex": "openai_codex/gpt-6.1-luna",
    "xai": "xai/grok-4.3",
    "google": "google/gemini-3.5-flash-lite",
    "deepinfra": "deepinfra/google/gemma-4-31B-it",
    "together": "together/google/gemma-4-31B-it",
    "opencode_go": "opencode_go/glm-5.3-flash",
    "fireworks": "fireworks/accounts/fireworks/models/deepseek-v4p1-flash-1017",
    "refuseless": "refuseless/glm-5.3-flash-abliterated",
    "github_copilot": "github_copilot/claude-haiku-4.5",
    "commandcode": "commandcode/google/gemini-3.5-flash-lite",
    "openai_response": "openai/gpt-4.1-mini",
    "copilot_response": "github_copilot/gpt-5.4-nano",
    "opencode_anthropic": "opencode_go/qwen3.8-flash",
    "commandcode_anthropic": "commandcode/claude-haiku-4-5-20251001",
}


def resolve_auxiliary_model(
    registry: ModelRegistry,
    provider: str,
    *,
    primary_provider: str,
) -> Model | None:
    """Select a registered background model, or allow a same-provider raw clone.

    Built-ins must have their preferred model registered. Custom providers use
    their explicit default or first configured model. Only the primary provider
    may lack models; the caller then retains its raw model and service family.
    This does not change the registry's current/interactive model selection.
    """
    preferred_id = AUXILIARY_MODEL_PREFERENCES.get(provider)
    catalog_provider = preferred_id.split("/", 1)[0] if preferred_id else provider
    models = registry.get_models_by_provider(catalog_provider)
    if preferred_id:
        model = registry.get_model(preferred_id)
        if model is None:
            raise ValueError(f"Auxiliary model is not registered: {preferred_id}")
        return model
    if models:
        return next((model for model in models if model.default), models[0])
    if provider == primary_provider:
        return None
    raise ValueError(f"Auxiliary provider '{provider}' has no registered models")
