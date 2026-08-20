"""Typed model/provider selection value objects and the precedence resolver.

The startup precedence chain (highest first):

1. ``USER_SWITCH`` — runtime force switch via ``/model`` (assigned at runtime)
2. ``RUNTIME_ARGS`` — explicit provider/model runtime arguments
3. ``AGENT_CONFIG`` — ``model_id`` in the selected agent configuration
4. ``ENVIRONMENT`` — detected ``AGENTCREW_MODEL_ID``
5. ``LAST_USED`` — persisted last-used model/provider
6. ``DEFAULT`` — provider default model

``USER_SWITCH`` is never produced by the resolver: the ``/model`` command
assigns it at runtime. ``ENVIRONMENT`` is an internal distinction that keeps
detected values below explicit arguments and agent config while still above
last-used/default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelSelectionSource(str, Enum):
    """Where the effective model selection came from."""

    USER_SWITCH = "user_switch"
    RUNTIME_ARGS = "runtime_args"
    AGENT_CONFIG = "agent_config"
    ENVIRONMENT = "environment"
    LAST_USED = "last_used"
    DEFAULT = "default"


@dataclass(frozen=True)
class RuntimeModelInput:
    """Resolved runtime provider/model input for one startup run.

    ``explicit_*`` fields are only set when the caller truly supplied the
    value; detected values never masquerade as explicit arguments.
    """

    provider: str | None
    explicit_provider: bool
    explicit_model_id: str | None
    detected_model_id: str | None

    @property
    def model_id(self) -> str | None:
        """Effective provider-relative model id (explicit wins over detected)."""
        return self.explicit_model_id or self.detected_model_id


@dataclass(frozen=True)
class ModelSelection:
    """Effective model selection for a service or agent."""

    provider: str
    model_id: str | None
    source: ModelSelectionSource

    @property
    def is_forced(self) -> bool:
        """True when the selection survives a config reload."""
        return self.source in {
            ModelSelectionSource.USER_SWITCH,
            ModelSelectionSource.RUNTIME_ARGS,
        }

    @property
    def is_pinned(self) -> bool:
        """True when the agent keeps its service on global updates."""
        return self.source in {
            ModelSelectionSource.RUNTIME_ARGS,
            ModelSelectionSource.AGENT_CONFIG,
        }

    @property
    def relative_model_id(self) -> str | None:
        """Provider-relative model id, or None when there is no model."""
        if not self.model_id:
            return None
        prefix = f"{self.provider}/"
        if self.model_id.startswith(prefix):
            return self.model_id[len(prefix) :]
        return self.model_id

    @classmethod
    def from_model_id(
        cls, model_id: str, source: ModelSelectionSource
    ) -> ModelSelection:
        """Build a selection from a fully-qualified ``provider/model`` id."""
        provider = model_id.split("/", 1)[0] if "/" in model_id else model_id
        return cls(provider=provider, model_id=model_id, source=source)


def resolve_model_selection(
    runtime: RuntimeModelInput,
    *,
    agent_model_id: str | None,
    last_used_model: str | None,
    last_used_provider: str | None,
) -> ModelSelection:
    """Resolve the effective model for a service or agent exactly once.

    Precedence: explicit runtime args > agent config ``model_id`` > detected
    environment > persisted last-used (provider matching) > provider default.
    """
    if runtime.provider is None:
        raise ValueError("Provider must be resolved before model selection")

    if runtime.explicit_model_id:
        return ModelSelection(
            provider=runtime.provider,
            model_id=f"{runtime.provider}/{runtime.explicit_model_id}",
            source=ModelSelectionSource.RUNTIME_ARGS,
        )

    if agent_model_id:
        if not runtime.explicit_provider:
            return ModelSelection.from_model_id(
                agent_model_id, ModelSelectionSource.AGENT_CONFIG
            )
        from AgentCrew.modules.llm.model_registry import ModelRegistry

        agent_model = ModelRegistry.get_instance().get_model(agent_model_id)
        if agent_model and agent_model.provider == runtime.provider:
            return ModelSelection.from_model_id(
                agent_model_id, ModelSelectionSource.AGENT_CONFIG
            )

    if runtime.detected_model_id:
        return ModelSelection(
            provider=runtime.provider,
            model_id=f"{runtime.provider}/{runtime.detected_model_id}",
            source=ModelSelectionSource.ENVIRONMENT,
        )

    if (
        last_used_model
        and last_used_provider
        and last_used_provider == runtime.provider
    ):
        return ModelSelection(
            provider=runtime.provider,
            model_id=last_used_model,
            source=ModelSelectionSource.LAST_USED,
        )

    return ModelSelection(
        provider=runtime.provider,
        model_id=None,
        source=ModelSelectionSource.DEFAULT,
    )
