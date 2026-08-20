"""Typed reasoning-effort selection value objects and shared provider adapter.

Reasoning precedence (highest first):

1. ``USER_SWITCH`` — runtime force switch via ``/think`` (assigned at runtime)
2. ``RUNTIME_ARGS`` — explicit ``--reason-effort`` runtime argument
3. ``AGENT_CONFIG`` — ``reason_effort`` in the selected agent configuration
4. ``MODEL_DEFAULT`` — the selected model's ``default_reasoning``

There is deliberately no environment or last-used reasoning layer: reasoning
is a per-run, per-agent control and is never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

REASONING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")

#: Canonical level -> Anthropic-family token budget (shared with ACP).
ANTHROPIC_REASONING_BUDGETS = {
    "none": "0",
    "minimal": "1024",
    "low": "2048",
    "medium": "4096",
    "high": "8192",
    "xhigh": "16384",
}

#: Providers/services that expect a numeric Anthropic-style thinking budget.
ANTHROPIC_PROVIDER_NAMES = ("claude", "opencode_anthropic", "commandcode_anthropic")


class ReasoningSource(str, Enum):
    """Where the effective reasoning selection came from."""

    USER_SWITCH = "user_switch"
    RUNTIME_ARGS = "runtime_args"
    AGENT_CONFIG = "agent_config"
    MODEL_DEFAULT = "model_default"


@dataclass(frozen=True)
class ReasoningSelection:
    """Effective reasoning selection for a service or agent."""

    level: str | None
    source: ReasoningSource

    @property
    def is_forced(self) -> bool:
        """True when the selection survives a config reload."""
        return self.source in {
            ReasoningSource.USER_SWITCH,
            ReasoningSource.RUNTIME_ARGS,
        }

    @property
    def is_explicit(self) -> bool:
        """True when a user/config level (not a model default) is requested."""
        return self.source is not ReasoningSource.MODEL_DEFAULT


def validate_reason_effort(value: str | None) -> str | None:
    """Validate a canonical reasoning level, returning None for absent values."""
    if value is None:
        return None
    if value not in REASONING_LEVELS:
        raise ValueError(
            f"Invalid reason_effort '{value}'. Must be one of: "
            + ", ".join(REASONING_LEVELS)
        )
    return value


def resolve_reasoning_selection(
    explicit_reason_effort: str | None,
    agent_reason_effort: str | None,
    model_default_reasoning: str | None,
) -> ReasoningSelection:
    """Resolve the effective reasoning level exactly once.

    Precedence: explicit CLI effort > agent config effort > model default.
    """
    if explicit_reason_effort:
        return ReasoningSelection(explicit_reason_effort, ReasoningSource.RUNTIME_ARGS)
    if agent_reason_effort:
        return ReasoningSelection(agent_reason_effort, ReasoningSource.AGENT_CONFIG)
    if model_default_reasoning:
        return ReasoningSelection(
            model_default_reasoning, ReasoningSource.MODEL_DEFAULT
        )
    return ReasoningSelection(None, ReasoningSource.MODEL_DEFAULT)


def adapt_reason_effort(service: BaseLLMService, level: str) -> str:
    """Map a canonical reasoning level to a provider-specific value.

    Anthropic-family services expect a numeric token budget; all other
    providers receive the canonical name and may reject unsupported values
    through their existing ``set_think`` behavior.
    """
    provider_name = getattr(service, "provider_name", "")
    class_name = service.__class__.__name__.lower()
    if level in ANTHROPIC_REASONING_BUDGETS and (
        provider_name in ANTHROPIC_PROVIDER_NAMES or "anthropic" in class_name
    ):
        return ANTHROPIC_REASONING_BUDGETS[level]
    return level


def default_reasoning_for_service(service: BaseLLMService) -> str | None:
    """Return the selected model's default reasoning, or None when unset/raw."""
    from AgentCrew.modules.llm.model_registry import ModelRegistry

    model_id = f"{service.provider_name}/{service.model}"
    model = ModelRegistry.get_instance().get_model(model_id)
    if model is None:
        return None
    return model.default_reasoning


def apply_reasoning_to_service(
    service: BaseLLMService | None,
    level: str | None,
    *,
    explicit: bool = False,
) -> bool:
    """Apply a canonical reasoning level to a service via ``set_think``.

    Returns True when applied (or already disabled). When ``explicit`` and
    the service rejects the level (e.g. a non-thinking model), raises
    ``ValueError`` so callers can surface a clear failure.
    """
    if service is None:
        return False
    value = adapt_reason_effort(service, level or "none")
    try:
        applied = bool(service.set_think(value))
    except ValueError:
        if explicit:
            raise
        return False
    if not applied and explicit:
        raise ValueError(
            f"Reasoning effort '{level}' is not supported by the selected model."
        )
    return True
