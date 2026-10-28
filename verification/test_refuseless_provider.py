"""Focused, offline Refuseless provider integration checks."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from AgentCrew.modules.custom_llm import RefuselessService
from AgentCrew.modules.llm.model_definitions.refuseless import REFUSELESS_MODELS
from AgentCrew.modules.llm.model_registry import (
    PROVIDER_API_KEY_MAP,
    PROVIDER_MODEL_MODULES,
    ModelRegistry,
)
from AgentCrew.modules.llm.model_selection import (
    ModelSelection,
    ModelSelectionSource,
)
from AgentCrew.modules.llm.reasoning_selection import apply_reasoning_to_service
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import SampleParam
from AgentCrew.setup import PROVIDER_LIST, ApplicationSetup, common_options

MODEL_IDS = {
    "glm-5.3-abliterated",
    "glm-5.3-flash-abliterated",
}


def test_catalog_and_qualified_ids(monkeypatch):
    monkeypatch.setenv("REFUSELESS_API_KEY", "dummy-offline-key")
    monkeypatch.setattr(ModelRegistry, "_instance", None)
    registry = ModelRegistry.get_instance()
    assert PROVIDER_API_KEY_MAP["refuseless"] == "REFUSELESS_API_KEY"
    assert PROVIDER_MODEL_MODULES["refuseless"][1] == "REFUSELESS_MODELS"
    assert "refuseless" in registry.get_providers()
    assert {
        model.id for model in registry.get_models_by_provider("refuseless")
    } == MODEL_IDS
    assert all(
        registry.get_model(f"refuseless/{model_id}") is not None
        for model_id in MODEL_IDS
    )
    assert [model.id for model in REFUSELESS_MODELS if model.default] == [
        "glm-5.3-abliterated"
    ]
    assert all(model.provider == "refuseless" for model in REFUSELESS_MODELS)
    assert all(
        model.resolved_service_name() == "refuseless" for model in REFUSELESS_MODELS
    )
    assert all(model.input_token_price_1m == 0 for model in REFUSELESS_MODELS)
    assert all(model.output_token_price_1m == 0 for model in REFUSELESS_MODELS)
    assert all(model.cached_token_price_1m == 0 for model in REFUSELESS_MODELS)
    assert all(model.max_context_token == 128_000 for model in REFUSELESS_MODELS)
    assert all(
        set(model.capabilities) == {"tool_use", "thinking", "vision", "stream"}
        for model in REFUSELESS_MODELS
    )


def test_key_load_detection_and_cli(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"api_keys": {"REFUSELESS_API_KEY": "  dummy-offline-key  "}})
    )
    monkeypatch.setenv("AGENTCREW_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("REFUSELESS_API_KEY", raising=False)
    monkeypatch.delenv("AGENTCREW_PROVIDER", raising=False)
    for key in set(PROVIDER_API_KEY_MAP.values()) - {"REFUSELESS_API_KEY"}:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "AgentCrew.modules.config.global_config.GlobalConfig.get_last_used_provider",
        lambda self: None,
    )
    setup = object.__new__(ApplicationSetup)
    setup.load_api_keys_from_config()
    assert "refuseless" in PROVIDER_LIST
    assert setup.detect_provider() == "refuseless"
    monkeypatch.setenv("AGENTCREW_PROVIDER", "refuseless")
    assert setup.detect_provider() == "refuseless"
    monkeypatch.delenv("AGENTCREW_PROVIDER")
    monkeypatch.setattr(
        "AgentCrew.modules.config.global_config.GlobalConfig.get_last_used_provider",
        lambda self: "refuseless",
    )
    assert setup.detect_provider() == "refuseless"
    assert any(
        "refuseless" in getattr(parameter.type, "choices", ())
        for parameter in common_options(lambda: None).__click_params__
    )
    assert setup.resolve_runtime_model("refuseless", None).provider == "refuseless"


def test_missing_refuseless_key_never_falls_back_to_openai(monkeypatch):
    monkeypatch.delenv("REFUSELESS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider-offline-key")
    monkeypatch.setattr(ServiceManager, "_instance", None)
    manager = ServiceManager.get_instance()
    with pytest.raises(RuntimeError, match="REFUSELESS_API_KEY"):
        manager.get_service_for_model(REFUSELESS_MODELS[0])
    assert "refuseless" not in manager.services
    monkeypatch.setattr(ServiceManager, "_instance", None)


def test_service_routing(monkeypatch):
    monkeypatch.setenv("REFUSELESS_API_KEY", "dummy-offline-key")
    monkeypatch.setattr(ServiceManager, "_instance", None)
    manager = ServiceManager.get_instance()
    model = REFUSELESS_MODELS[0]
    service = manager.get_service_for_model(model)
    assert isinstance(service, RefuselessService)
    assert service.provider_name == "refuseless"
    assert str(service.client.base_url) == "https://api.refuseless.com/v1/"
    assert service.api_key == "dummy-offline-key"
    assert manager.get_service_for_model(REFUSELESS_MODELS[1]) is service
    default_selection = ModelSelection("refuseless", None, ModelSelectionSource.DEFAULT)
    assert manager.get_service_for_selection(default_selection).model == model.id
    selection = ModelSelection(
        "refuseless", f"refuseless/{model.id}", ModelSelectionSource.RUNTIME_ARGS
    )
    assert selection.relative_model_id == model.id
    for model_id in MODEL_IDS:
        qualified = f"refuseless/{model_id}"
        selected = ModelSelection(
            "refuseless", qualified, ModelSelectionSource.RUNTIME_ARGS
        )
        assert manager.get_service_for_selection(selected).model == model_id
    raw_selection = ModelSelection(
        "refuseless", "refuseless/unlisted-model", ModelSelectionSource.RUNTIME_ARGS
    )
    assert manager.get_service_for_selection(raw_selection).model == "unlisted-model"
    standalone = manager.initialize_standalone_service_for_model(model)
    assert isinstance(standalone, RefuselessService)
    assert standalone is not service
    asyncio.run(standalone.close())
    asyncio.run(service.close())
    monkeypatch.setattr(ServiceManager, "_instance", None)


@pytest.mark.parametrize(
    ("requested", "wire_value"),
    [
        ("none", "off"),
        ("0", "off"),
        (0, "off"),
        ("off", "off"),
        ("minimal", "low"),
        ("low", "low"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "max"),
        ("max", "max"),
    ],
)
def test_reasoning_mapping(monkeypatch, requested, wire_value):
    monkeypatch.setenv("REFUSELESS_API_KEY", "dummy-offline-key")
    service = RefuselessService(api_key="dummy-offline-key")
    service.model = REFUSELESS_MODELS[0].id
    assert service.set_think(requested)
    params, streamable = service._build_stream_params()
    assert streamable
    assert params["reasoning_effort"] == wire_value
    assert "extra_body" not in params
    asyncio.run(service.close())


def test_unconfigured_reasoning_and_explicit_sampling(monkeypatch):
    monkeypatch.setenv("REFUSELESS_API_KEY", "dummy-offline-key")
    service = RefuselessService(api_key="dummy-offline-key")
    service.model = REFUSELESS_MODELS[0].id
    assert apply_reasoning_to_service(service, None)
    params, _ = service._build_stream_params()
    assert "reasoning_effort" not in params
    assert "extra_body" not in params
    assert apply_reasoning_to_service(service, "high", explicit=True)
    assert service._build_stream_params()[0]["reasoning_effort"] == "high"
    assert apply_reasoning_to_service(service, None)
    assert service.reasoning_effort is None
    assert "reasoning_effort" not in service._build_stream_params()[0]
    assert apply_reasoning_to_service(service, "none", explicit=True)
    assert service._build_stream_params()[0]["reasoning_effort"] == "off"
    with pytest.raises(ValueError, match="Unsupported Refuseless reasoning level"):
        service.set_think("unsupported")

    monkeypatch.setattr(
        ModelRegistry,
        "get_model_sample_params",
        classmethod(lambda cls, model_id: SampleParam(top_k=5)),
    )
    params, _ = service._build_stream_params()
    assert params["extra_body"] == {"top_k": 5}
    monkeypatch.setattr(
        ModelRegistry,
        "get_model_sample_params",
        classmethod(lambda cls, model_id: SampleParam(min_p=0.6, top_k=5)),
    )
    params, _ = service._build_stream_params()
    assert params["extra_body"] == {"min_p": 0.6, "top_k": 5}
    asyncio.run(service.close())


def test_non_opt_in_unconfigured_reasoning_uses_existing_path():
    calls = []
    service = SimpleNamespace(set_think=lambda level: calls.append(level) or True)
    assert apply_reasoning_to_service(service, None)
    assert calls == ["none"]


def test_process_message_uses_off_not_none(monkeypatch):
    monkeypatch.setenv("REFUSELESS_API_KEY", "dummy-offline-key")
    service = RefuselessService(api_key="dummy-offline-key")
    service.model = REFUSELESS_MODELS[0].id

    async def chunks():
        yield SimpleNamespace(choices=[], usage=None)

    create = AsyncMock(return_value=chunks())
    original_client = service.client
    service.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    assert asyncio.run(service.process_message("test")) == ""
    assert create.await_args.kwargs["reasoning_effort"] == "off"
    asyncio.run(original_client.close())
