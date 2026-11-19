"""Credential-free checks against real catalogs and isolated background clients."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from AgentCrew.modules.llm.auxiliary_model_selection import (
    AUXILIARY_MODEL_PREFERENCES,
    resolve_auxiliary_model,
)
from AgentCrew.modules.llm.model_registry import PROVIDER_MODEL_MODULES, ModelRegistry
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import Model


@pytest.fixture
def registry(monkeypatch):
    result = ModelRegistry.__new__(ModelRegistry)
    result.models = {}
    result._loaded_providers = set()
    result.current_model = None
    monkeypatch.setattr(ModelRegistry, "_instance", result)
    return result


class Client:
    def __init__(self, family):
        self.provider_name = "factory"
        self.model = "factory-default"
        self._service_family = family
        self.thinking = []
        self.closed = False

    def set_think(self, level):
        self.thinking.append(level)
        return True

    async def close(self):
        self.closed = True


@pytest.fixture
def manager(registry):
    result = ServiceManager.__new__(ServiceManager)
    result.services = {}
    result.custom_provider_details = {}
    result._pending_closes = set()
    result._closed_services = set()
    families = set(PROVIDER_MODEL_MODULES) | {
        "openai_response",
        "copilot_response",
        "opencode_anthropic",
        "commandcode_anthropic",
        "custom",
        "custom_response",
    }
    result.service_factories = {
        family: lambda family=family: Client(family) for family in families
    }
    return result


def primary(provider="openai", model="gpt-5", family="openai_response"):
    return SimpleNamespace(provider_name=provider, model=model, _service_family=family)


def test_preferences_cover_all_builtin_catalogs_and_only_valid_aliases():
    aliases = {
        "openai_response",
        "copilot_response",
        "opencode_anthropic",
        "commandcode_anthropic",
    }
    assert set(AUXILIARY_MODEL_PREFERENCES) == set(PROVIDER_MODEL_MODULES) | aliases


@pytest.mark.parametrize("provider", AUXILIARY_MODEL_PREFERENCES)
def test_every_preference_exists_in_actual_definitions(registry, provider):
    expected = AUXILIARY_MODEL_PREFERENCES[provider]
    catalog_provider, model_id = expected.split("/", 1)
    module, attribute = PROVIDER_MODEL_MODULES[catalog_provider]
    definitions = getattr(importlib.import_module(module), attribute)
    model = resolve_auxiliary_model(registry, provider, primary_provider="unrelated")
    assert any(item.id == model_id for item in definitions)
    assert model.id == model_id and model.provider == catalog_provider
    if provider not in PROVIDER_MODEL_MODULES:
        assert model.resolved_service_name() == provider
    assert registry.current_model is None


@pytest.mark.parametrize("provider", AUXILIARY_MODEL_PREFERENCES)
def test_all_preferences_bind_to_the_selected_model_family(manager, registry, provider):
    main = primary()
    service = manager.initialize_auxiliary_service(main, provider)
    model = registry.get_model(AUXILIARY_MODEL_PREFERENCES[provider])
    assert service._service_family == model.resolved_service_name()
    assert (service.provider_name, service.model) == (model.provider, model.id)
    assert service.thinking == (["0"] if model.provider == "claude" else ["none"])
    assert main.model == "gpt-5"
    assert not manager.services


@pytest.mark.parametrize("override", [None, "openai", "google", "xai", "refuseless"])
def test_lightweight_selection_does_not_inherit_primary_or_catalog_default(
    manager, registry, override
):
    service = manager.initialize_auxiliary_service(primary(), override)
    provider = override or "openai"
    selected = registry.get_model(AUXILIARY_MODEL_PREFERENCES[provider])
    default = next(
        model for model in registry.get_models_by_provider(provider) if model.default
    )
    assert service.model == selected.id
    assert service.model != default.id
    assert service.model != "gpt-5"


@pytest.mark.parametrize("override", [None, "custom"])
@pytest.mark.parametrize("has_default", [False, True])
@pytest.mark.parametrize("primary_provider", ["custom", "openai"])
def test_custom_models_use_default_or_first_without_guessing(
    manager, registry, override, has_default, primary_provider
):
    first = Model(
        id="first", provider="custom", name="First", description="", capabilities=[]
    )
    default = Model(
        id="default",
        provider="custom",
        name="Default",
        description="",
        capabilities=[],
        default=has_default,
        service_name="custom_response",
    )
    registry.register_model(first)
    registry.register_model(default)
    service = manager.initialize_auxiliary_service(
        primary(primary_provider, "raw", "custom"),
        "custom" if primary_provider == "openai" else override,
    )
    expected = default if has_default else first
    assert (service.provider_name, service.model, service._service_family) == (
        expected.provider,
        expected.id,
        expected.resolved_service_name(),
    )


@pytest.mark.parametrize("override", [None, "custom"])
@pytest.mark.parametrize("family", ["custom_response", None])
def test_custom_raw_model_retains_recorded_family(manager, override, family):
    main = primary("custom", "raw/model", family)
    result = manager.initialize_auxiliary_service(main, override)
    assert result is not main
    assert (result.provider_name, result.model, result._service_family) == (
        "custom",
        "raw/model",
        family or "custom",
    )


def test_unknown_override_is_rejected_before_client_construction(manager, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(manager, "initialize_standalone_service", factory)
    with pytest.raises(ValueError, match="no registered models"):
        manager.initialize_auxiliary_service(primary(), "unknown")
    factory.assert_not_called()


def test_missing_builtin_preference_is_not_silently_replaced(registry):
    registry._loaded_providers.add("openai")
    with pytest.raises(ValueError, match="Auxiliary model is not registered"):
        resolve_auxiliary_model(registry, "openai", primary_provider="openai")


def test_each_role_gets_a_fresh_client_without_mutating_the_primary(manager):
    main = primary()
    clients = [manager.initialize_auxiliary_service(main) for _ in range(3)]
    assert len({id(client) for client in clients}) == 3
    assert all(client.model == "gpt-4.1-mini" for client in clients)
    assert main.model == "gpt-5"


@pytest.mark.parametrize("stage", ["creation", "model", "reasoning"])
def test_failed_configuration_closes_new_client(manager, monkeypatch, stage):
    service = Client("openai_response")
    if stage == "creation":
        factory = Mock(side_effect=RuntimeError("creation failed"))
    else:
        factory = Mock(return_value=service)
        if stage == "model":

            class BrokenClient(Client):
                def __setattr__(self, name, value):
                    if name == "model" and value == "gpt-4.1-mini":
                        raise RuntimeError("model failed")
                    super().__setattr__(name, value)

            service = BrokenClient("openai_response")
            factory.return_value = service
        else:
            service.set_think = Mock(side_effect=RuntimeError("reasoning failed"))
    monkeypatch.setattr(manager, "initialize_standalone_service_for_model", factory)
    with pytest.raises(RuntimeError, match=f"{stage} failed"):
        manager.initialize_auxiliary_service(primary())
    assert service.closed is (stage != "creation")
    assert not manager.services


def test_lenient_reasoning_rejection_is_preserved(manager, monkeypatch):
    service = Client("openai_response")
    service.set_think = Mock(side_effect=ValueError("unsupported"))
    monkeypatch.setattr(
        manager, "initialize_standalone_service_for_model", lambda _model: service
    )
    assert manager.initialize_auxiliary_service(primary()) is service
    service.set_think.assert_called_once_with("none")
    assert not service.closed


@pytest.mark.parametrize("provider", AUXILIARY_MODEL_PREFERENCES)
def test_code_analysis_consumes_selected_client_without_model_mutation(
    manager, provider
):
    from AgentCrew.modules.code_analysis.service import CodeAnalysisService

    client = manager.initialize_auxiliary_service(primary(), provider)
    identity = (client.provider_name, client.model, client._service_family)
    analysis = CodeAnalysisService(client)
    assert analysis.llm_service is client
    assert (client.provider_name, client.model, client._service_family) == identity


@pytest.mark.asyncio
async def test_configuration_failure_drains_close_on_running_loop(manager, monkeypatch):
    client = Client("openai_response")
    client.set_think = Mock(side_effect=RuntimeError("failed"))
    monkeypatch.setattr(
        manager, "initialize_standalone_service_for_model", lambda _model: client
    )
    with pytest.raises(RuntimeError, match="failed"):
        manager.initialize_auxiliary_service(primary())
    assert len(manager._pending_closes) == 1
    await manager.drain_pending_closes()
    assert client.closed
    assert not manager._pending_closes


def test_cli_background_provider_choices_cover_every_builtin():
    import click

    from AgentCrew.setup import common_options

    command = click.command()(common_options(lambda **_kwargs: None))
    option = next(option for option in command.params if option.name == "memory_llm")
    assert set(PROVIDER_MODEL_MODULES) <= set(option.type.choices)
    assert "code analysis" in option.help
