"""Registry defaults, service routing, and custom model precedence checks."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.model_selection import (
    ModelSelectionSource,
    RuntimeModelInput,
    resolve_model_selection,
)
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.onboarding.service import OnboardingService


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(ModelRegistry, "_instance", None)
    monkeypatch.setattr(ModelRegistry, "_initialize_models", lambda self: None)
    return ModelRegistry.get_instance()


@pytest.mark.parametrize(
    "provider,model_id,family",
    [
        ("claude", "claude-sonnet-5-5", "claude"),
        ("openai", "gpt-6.2-sol", "openai_response"),
        ("openai_codex", "gpt-6.1-sol", "openai_codex"),
        ("github_copilot", "gpt-6.1-sol", "copilot_response"),
        ("together", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "together"),
        (
            "fireworks",
            "accounts/fireworks/models/deepseek-v4p1-flash-1017",
            "fireworks",
        ),
        ("opencode_go", "kimi-k2.5", "opencode_go"),
    ],
)
def test_provider_only_selection_uses_default_service_family(
    registry, provider, model_id, family
):
    manager = object.__new__(ServiceManager)
    manager.get_service = MagicMock(return_value=SimpleNamespace(model=""))
    service = manager.get_service_for_provider(provider)
    manager.get_service.assert_called_once_with(family, provider)
    default_model = next(
        m for m in registry.get_models_by_provider(provider) if m.default
    )
    assert default_model.id == model_id
    manager.apply_model_defaults(service, default_model)
    assert service.model == model_id


@pytest.mark.parametrize(
    "provider", ["together", "fireworks", "openai_codex", "my-private-provider"]
)
def test_explicit_raw_model_is_not_overridden_by_new_catalog_defaults(provider):
    selection = resolve_model_selection(
        RuntimeModelInput(
            provider=provider,
            explicit_provider=True,
            explicit_model_id="raw/private-model",
            detected_model_id=None,
        ),
        agent_model_id=None,
        last_used_model=None,
        last_used_provider=None,
    )
    assert selection.source == ModelSelectionSource.RUNTIME_ARGS
    assert selection.relative_model_id == "raw/private-model"
    assert selection.model_id == f"{provider}/raw/private-model"


def test_cloning_unknown_raw_model_preserves_provider_and_id(registry):
    manager = object.__new__(ServiceManager)
    manager.initialize_standalone_service = MagicMock(
        return_value=SimpleNamespace(model="")
    )
    original = SimpleNamespace(
        provider_name="private-provider", model="namespace/private-id"
    )
    clone = manager.clone_service(original)
    manager.initialize_standalone_service.assert_called_once_with("private-provider")
    assert clone.model == "namespace/private-id"


@pytest.mark.parametrize(
    "provider,family,expected_model",
    [
        ("github_copilot", "copilot_response", "gpt-6.1-sol"),
        ("openai_codex", "openai_codex", "gpt-6.1-sol"),
        ("github_copilot", "github_copilot", "claude-sonnet-4.6"),
    ],
)
def test_onboarding_model_matches_service_manager_endpoint_family(
    registry, provider, family, expected_model
):
    manager = object.__new__(ServiceManager)
    manager.services = {}
    manager.custom_provider_details = {}
    manager.service_factories = {
        family: lambda: SimpleNamespace(provider_name=provider, model="previous-model")
    }
    if family == "github_copilot":
        service = manager.get_service(family, provider)
    else:
        service = manager.get_service_for_provider(provider)
    assert service._service_family == family
    assert service.provider_name == provider

    OnboardingService(service, agents_config=SimpleNamespace())

    assert service.model == expected_model
    registry.get_models_by_provider(provider)
    model = registry.get_model(f"{provider}/{service.model}")
    assert model is not None
    assert model.resolved_service_name() == family


@pytest.mark.parametrize(
    "provider,expected_model",
    [
        ("google", "gemini-3.1-pro-preview"),
        ("commandcode", "zai-org/GLM-5.2"),
        ("claude", "claude-sonnet-4-6"),
        ("openai", "gpt-5.5"),
        ("deepinfra", "zai-org/GLM-5.2"),
        ("together", "zai-org/GLM-5.2"),
        ("opencode_go", "kimi-k2.7-code"),
        ("github_copilot", "previous-model"),
        ("private-provider", "previous-model"),
    ],
)
def test_onboarding_preserves_unrelated_provider_choices(provider, expected_model):
    service = SimpleNamespace(provider_name=provider, model="previous-model")
    OnboardingService(service, agents_config=SimpleNamespace())
    assert service.model == expected_model
