"""Tests for model/provider selection precedence.

Precedence under test (highest first):

1. runtime force switch via ``/model``
2. explicit model/provider supplied through command/runtime arguments
3. ``model_id`` in the selected agent configuration
4. detected ``AGENTCREW_MODEL_ID`` (internal ``ENVIRONMENT`` source)
5. persisted last-used model/provider
6. provider/model default

External services (registry, service manager, global config) are mocked.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from AgentCrew.app import AgentCrewApplication
from AgentCrew.modules.agents import LocalAgent
from AgentCrew.modules.agents.manager import AgentManager
from AgentCrew.modules.chat.message.commands.model_commands import ModelCommands
from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.agents_config import AgentsConfig, _reload_model_selection
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.model_selection import (
    ModelSelection,
    ModelSelectionSource,
    RuntimeModelInput,
    resolve_model_selection,
)
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import Model
from AgentCrew.setup import ApplicationSetup

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _StubLLM:
    """Minimal stub satisfying what LocalAgent/setup code expects from an LLM."""

    provider_name = "stub"
    model = ""

    def __init__(self, model: str = "", provider_name: str = "stub"):
        self.model = model
        self.provider_name = provider_name
        self.close_calls = 0

    def set_system_prompt(self, prompt):
        pass

    def clear_tools(self):
        pass

    def set_tools(self, tools):
        pass

    def calculate_cost(self, *args, **kwargs):
        return 0.0

    def set_think(self, val):
        return True

    def close(self):
        self.close_calls += 1

    temperature = 0.4


OPENAI_DEFAULT = Model(
    id="gpt-4o",
    provider="openai",
    name="GPT-4o",
    description="",
    capabilities=["tool_use"],
    default=True,
)
OPENAI_LAST = Model(
    id="gpt-4o-mini",
    provider="openai",
    name="GPT-4o mini",
    description="",
    capabilities=["tool_use"],
)
OPENAI_EXPLICIT = Model(
    id="gpt-5",
    provider="openai",
    name="GPT-5",
    description="",
    capabilities=["tool_use"],
)
CLAUDE_DEFAULT = Model(
    id="claude-3-5-sonnet",
    provider="claude",
    name="Claude 3.5 Sonnet",
    description="",
    capabilities=["tool_use"],
    default=True,
)
GITHUB_FAMILY_A = Model(
    id="claude-sonnet-4.5",
    provider="github_copilot",
    name="Claude Sonnet 4.5",
    description="",
    capabilities=["tool_use"],
    service_name="github_copilot",
    default=True,
)
GITHUB_FAMILY_B = Model(
    id="copilot-response-gpt-5",
    provider="github_copilot",
    name="Copilot Response",
    description="",
    capabilities=["tool_use"],
    service_name="copilot_response",
)


def _runtime(
    provider: str = "openai",
    explicit_provider: bool = False,
    explicit_model_id: str | None = None,
    detected_model_id: str | None = None,
) -> RuntimeModelInput:
    return RuntimeModelInput(
        provider=provider,
        explicit_provider=explicit_provider,
        explicit_model_id=explicit_model_id,
        detected_model_id=detected_model_id,
    )


class _FakeRegistry:
    def __init__(self, models, last_used_model=None, last_used_provider=None):
        self.models = {f"{m.provider}/{m.id}": m for m in models}
        self.current_model = None
        self.last_used_model = last_used_model
        self.last_used_provider = last_used_provider

    def get_model(self, model_id):
        return self.models.get(model_id)

    def get_models_by_provider(self, provider):
        return [m for m in self.models.values() if m.provider == provider]

    def set_current_model(self, model_id):
        model = self.models.get(model_id)
        if model:
            self.current_model = model
            return True
        return False

    def get_current_model(self):
        return self.current_model


class _FakeLlmManager:
    def __init__(self, registry):
        self.registry = registry
        self.services = {}

    def _service_for(self, model):
        key = model.resolved_service_name()
        if key not in self.services:
            self.services[key] = _StubLLM(model=model.id, provider_name=model.provider)
        return self.services[key]

    def get_service_for_model(self, model):
        return self._service_for(model)

    def get_service_for_provider(self, provider):
        models = self.registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            return self._service_for(default_model)
        return _StubLLM(provider_name=provider)

    def set_model_for_llm(self, model):
        service = self.get_service_for_model(model)
        service.model = model.id

    def apply_model_defaults(self, service, model):
        service.model = model.id

    def initialize_standalone_service(self, name):
        return _StubLLM(provider_name=name)

    def initialize_standalone_service_for_model(self, model):
        return _StubLLM(model=model.id, provider_name=model.provider)

    def clone_service(self, service):
        model_id = f"{service.provider_name}/{service.model}"
        model = self.registry.get_model(model_id)
        if model:
            new_service = self.initialize_standalone_service_for_model(model)
            self.apply_model_defaults(new_service, model)
            return new_service
        new_service = self.initialize_standalone_service(service.provider_name)
        new_service.model = service.model
        return new_service

    def get_service_for_selection(self, selection, *, standalone=False):
        registry = self.registry
        provider = selection.provider
        model_id = selection.model_id

        if model_id:
            model = registry.get_model(model_id)
            if model:
                registry.set_current_model(model_id)
                if standalone:
                    service = self.initialize_standalone_service_for_model(model)
                else:
                    service = self.get_service_for_model(model)
                self.apply_model_defaults(service, model)
                return service
            if selection.source in (
                ModelSelectionSource.RUNTIME_ARGS,
                ModelSelectionSource.ENVIRONMENT,
            ):
                if standalone:
                    service = self.initialize_standalone_service(provider)
                else:
                    service = self.get_service_for_provider(provider)
                service.model = selection.relative_model_id
                return service

        if standalone:
            service = self.initialize_standalone_service(provider)
            models = registry.get_models_by_provider(provider)
            if models:
                default_model = next((m for m in models if m.default), models[0])
                self.apply_model_defaults(service, default_model)
            return service
        models = registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            registry.set_current_model(f"{default_model.provider}/{default_model.id}")
            service = self.get_service_for_model(default_model)
            self.apply_model_defaults(service, default_model)
            return service
        return _StubLLM(provider_name=provider)

    def close_service(self, service):
        if service is None:
            return
        if service in self.services.values():
            return
        close = getattr(service, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: S110 - mirrors real ServiceManager
                pass

    async def drain_pending_closes(self):
        return None


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _failing_service_fakes():
    """Fake service modules so setup_services stays hermetic and fast.

    Mirrors the try/except guards in ApplicationSetup.setup_services: any
    dependency that fails to construct is logged and skipped.
    """

    class _Broken:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("not available in tests")

    return {
        "AgentCrew.modules.clipboard": SimpleNamespace(
            ClipboardService=lambda: SimpleNamespace()
        ),
        "AgentCrew.modules.web_search": SimpleNamespace(TavilySearchService=_Broken),
        "AgentCrew.modules.code_analysis": SimpleNamespace(CodeAnalysisService=_Broken),
        "AgentCrew.modules.browser_automation": SimpleNamespace(
            BrowserAutomationService=_Broken
        ),
        "AgentCrew.modules.file_editing": SimpleNamespace(FileEditingService=_Broken),
        "AgentCrew.modules.command_execution": SimpleNamespace(
            CommandExecutionService=SimpleNamespace(
                get_instance=staticmethod(lambda: (_ for _ in ()).throw(RuntimeError()))
            )
        ),
        "AgentCrew.modules.skills": SimpleNamespace(SkillsService=_Broken),
        "AgentCrew.modules.image_generation": SimpleNamespace(
            ImageGenerationService=_Broken
        ),
    }


def _install_fake_modules(monkeypatch, fake_modules):
    for name, module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def app_setup(monkeypatch):
    """ApplicationSetup with mocked registry/llm-manager/global-config."""
    AgentManager._instance = None
    registry = _FakeRegistry(
        [OPENAI_DEFAULT, OPENAI_LAST, OPENAI_EXPLICIT, CLAUDE_DEFAULT]
    )
    llm_manager = _FakeLlmManager(registry)

    monkeypatch.setattr(ModelRegistry, "get_instance", staticmethod(lambda: registry))
    monkeypatch.setattr(
        ServiceManager, "get_instance", staticmethod(lambda: llm_manager)
    )
    monkeypatch.setattr(
        GlobalConfig,
        "get_last_used_model",
        lambda self=None: registry.last_used_model,
    )
    monkeypatch.setattr(
        GlobalConfig,
        "get_last_used_provider",
        lambda self=None: registry.last_used_provider,
    )
    monkeypatch.setattr(
        GlobalConfig, "set_last_used_model", lambda self, model_id, provider: None
    )
    _install_fake_modules(monkeypatch, _failing_service_fakes())

    setup = ApplicationSetup(ConfigManagement(), trusted_project_plugins=False)
    yield setup, registry, llm_manager
    AgentManager._instance = None


AGENT_DEFS = [
    {
        "name": "coder",
        "description": "Coder agent",
        "system_prompt": "you write code",
        "tools": [],
        "model_id": "openai/gpt-4o",
    },
    {
        "name": "plain",
        "description": "Plain agent",
        "system_prompt": "you help",
        "tools": [],
    },
]


@pytest.fixture
def agents_env(app_setup, monkeypatch):
    setup, registry, llm_manager = app_setup
    AgentManager._instance = None
    monkeypatch.setattr(AgentManager, "load_agents_from_config", lambda uri: AGENT_DEFS)
    _install_fake_modules(
        monkeypatch,
        {
            "AgentCrew.modules.mcpclient.tool": SimpleNamespace(
                register=lambda *a, **k: None
            )
        },
    )
    yield setup, registry, llm_manager
    AgentManager._instance = None


# ---------------------------------------------------------------------------
# typed value objects
# ---------------------------------------------------------------------------


class TestTypedModelSelection:
    def test_runtime_input_model_id_prefers_explicit(self):
        runtime = _runtime(explicit_model_id="gpt-5", detected_model_id="gpt-4o-mini")
        assert runtime.model_id == "gpt-5"

    def test_runtime_input_model_id_falls_back_to_detected(self):
        runtime = _runtime(detected_model_id="gpt-4o-mini")
        assert runtime.model_id == "gpt-4o-mini"

    def test_is_forced_only_for_user_switch_and_runtime_args(self):
        forced = {ModelSelectionSource.USER_SWITCH, ModelSelectionSource.RUNTIME_ARGS}
        for source in ModelSelectionSource:
            selection = ModelSelection(
                provider="openai", model_id="openai/gpt-5", source=source
            )
            assert selection.is_forced is (source in forced)

    def test_is_pinned_for_runtime_args_and_agent_config(self):
        pinned = {ModelSelectionSource.RUNTIME_ARGS, ModelSelectionSource.AGENT_CONFIG}
        for source in ModelSelectionSource:
            selection = ModelSelection(
                provider="openai", model_id="openai/gpt-5", source=source
            )
            assert selection.is_pinned is (source in pinned)

    def test_relative_model_id_strips_provider_prefix(self):
        selection = ModelSelection.from_model_id(
            "openai/gpt-5", ModelSelectionSource.RUNTIME_ARGS
        )
        assert selection.provider == "openai"
        assert selection.model_id == "openai/gpt-5"
        assert selection.relative_model_id == "gpt-5"

    def test_from_model_id_without_provider_prefix(self):
        selection = ModelSelection.from_model_id(
            "gpt-5", ModelSelectionSource.RUNTIME_ARGS
        )
        assert selection.provider == "gpt-5"
        assert selection.relative_model_id == "gpt-5"


# ---------------------------------------------------------------------------
# resolve_model_selection: the shared precedence resolver
# ---------------------------------------------------------------------------


class TestResolveModelSelection:
    def test_explicit_model_beats_config_last_used_default(self):
        selection = resolve_model_selection(
            _runtime("openai", explicit_provider=True, explicit_model_id="gpt-5"),
            agent_model_id="openai/gpt-4o",
            last_used_model="openai/gpt-4o-mini",
            last_used_provider="openai",
        )
        assert selection.model_id == "openai/gpt-5"
        assert selection.source is ModelSelectionSource.RUNTIME_ARGS

    def test_explicit_model_is_provider_relative(self):
        selection = resolve_model_selection(
            _runtime(explicit_model_id="gpt-5"),
            agent_model_id=None,
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.model_id == "openai/gpt-5"
        assert selection.source is ModelSelectionSource.RUNTIME_ARGS

    def test_unregistered_explicit_model_keeps_runtime_source(self):
        selection = resolve_model_selection(
            _runtime("openai", explicit_provider=True, explicit_model_id="custom-raw"),
            agent_model_id="openai/gpt-4o",
            last_used_model="openai/gpt-4o-mini",
            last_used_provider="openai",
        )
        assert selection.model_id == "openai/custom-raw"
        assert selection.source is ModelSelectionSource.RUNTIME_ARGS

    def test_agent_config_beats_last_used_and_default(self, app_setup):
        selection = resolve_model_selection(
            _runtime("openai"),
            agent_model_id="openai/gpt-4o",
            last_used_model="openai/gpt-4o-mini",
            last_used_provider="openai",
        )
        assert selection.model_id == "openai/gpt-4o"
        assert selection.source is ModelSelectionSource.AGENT_CONFIG

    def test_agent_config_skipped_for_other_explicit_provider(self, app_setup):
        selection = resolve_model_selection(
            _runtime("claude", explicit_provider=True),
            agent_model_id="openai/gpt-4o",
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.model_id is None
        assert selection.source is ModelSelectionSource.DEFAULT

    def test_agent_config_kept_for_matching_explicit_provider(self, app_setup):
        selection = resolve_model_selection(
            _runtime("openai", explicit_provider=True),
            agent_model_id="openai/gpt-4o",
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.model_id == "openai/gpt-4o"
        assert selection.source is ModelSelectionSource.AGENT_CONFIG

    def test_environment_beats_last_used_and_default(self):
        selection = resolve_model_selection(
            _runtime("openai", detected_model_id="gpt-5"),
            agent_model_id=None,
            last_used_model="openai/gpt-4o-mini",
            last_used_provider="openai",
        )
        assert selection.model_id == "openai/gpt-5"
        assert selection.source is ModelSelectionSource.ENVIRONMENT

    def test_environment_below_agent_config(self):
        selection = resolve_model_selection(
            _runtime("openai", detected_model_id="gpt-5"),
            agent_model_id="openai/gpt-4o",
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.model_id == "openai/gpt-4o"
        assert selection.source is ModelSelectionSource.AGENT_CONFIG

    def test_environment_never_runtime_args(self):
        selection = resolve_model_selection(
            _runtime("openai", detected_model_id="gpt-5"),
            agent_model_id=None,
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.source is ModelSelectionSource.ENVIRONMENT

    def test_last_used_beats_default(self):
        selection = resolve_model_selection(
            _runtime("openai"),
            agent_model_id=None,
            last_used_model="openai/gpt-4o-mini",
            last_used_provider="openai",
        )
        assert selection.model_id == "openai/gpt-4o-mini"
        assert selection.source is ModelSelectionSource.LAST_USED

    def test_last_used_from_another_provider_skipped(self):
        selection = resolve_model_selection(
            _runtime("openai", explicit_provider=True),
            agent_model_id=None,
            last_used_model="claude/claude-3-5-sonnet",
            last_used_provider="claude",
        )
        assert selection.model_id is None
        assert selection.source is ModelSelectionSource.DEFAULT

    def test_default_when_no_higher_source(self):
        selection = resolve_model_selection(
            _runtime("openai"),
            agent_model_id=None,
            last_used_model=None,
            last_used_provider=None,
        )
        assert selection.model_id is None
        assert selection.source is ModelSelectionSource.DEFAULT


# ---------------------------------------------------------------------------
# setup_services: base LLM service selection
# ---------------------------------------------------------------------------


class TestSetupServicesPrecedence:
    def test_explicit_model_beats_last_used_and_default(self, app_setup):
        setup, registry, _ = app_setup
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        services = setup.setup_services(
            _runtime("openai", explicit_provider=True, explicit_model_id="gpt-5"),
            need_memory=False,
        )
        assert services["llm"].model == "gpt-5"
        assert registry.current_model.id == "gpt-5"

    def test_last_used_beats_default(self, app_setup):
        setup, registry, _ = app_setup
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        services = setup.setup_services(_runtime("openai"), need_memory=False)
        assert services["llm"].model == "gpt-4o-mini"

    def test_default_when_no_higher_source(self, app_setup):
        setup, _, _ = app_setup
        services = setup.setup_services(_runtime("openai"), need_memory=False)
        assert services["llm"].model == "gpt-4o"

    def test_last_used_from_other_provider_not_restored(self, app_setup):
        setup, registry, _ = app_setup
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        services = setup.setup_services(_runtime("claude"), need_memory=False)
        assert services["llm"].model == "claude-3-5-sonnet"

    def test_detected_env_model_beats_last_used_at_base_level(self, app_setup):
        setup, registry, _ = app_setup
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        services = setup.setup_services(
            _runtime("openai", detected_model_id="gpt-5"), need_memory=False
        )
        assert services["llm"].model == "gpt-5"

    def test_unregistered_detected_env_model_raw_fallback(self, app_setup):
        setup, _, _ = app_setup
        services = setup.setup_services(
            _runtime("openai", detected_model_id="custom-env"), need_memory=False
        )
        assert services["llm"].model == "custom-env"

    def test_detected_env_model_rebinds_service_family(self, app_setup):
        setup, registry, llm_manager = app_setup
        registry.models["github_copilot/claude-sonnet-4.5"] = GITHUB_FAMILY_A
        registry.models["github_copilot/copilot-response-gpt-5"] = GITHUB_FAMILY_B
        registry.last_used_model = "github_copilot/claude-sonnet-4.5"
        registry.last_used_provider = "github_copilot"
        services = setup.setup_services(
            _runtime("github_copilot", detected_model_id="copilot-response-gpt-5"),
            need_memory=False,
        )
        family_b_service = llm_manager.services["copilot_response"]
        assert services["llm"] is family_b_service
        assert services["llm"].model == "copilot-response-gpt-5"
        assert registry.current_model.id == "copilot-response-gpt-5"

    def test_last_used_updates_registry_current_model(self, app_setup):
        setup, registry, _ = app_setup
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        services = setup.setup_services(_runtime("openai"), need_memory=False)
        assert services["llm"].model == "gpt-4o-mini"
        assert registry.current_model is not None
        assert registry.current_model.id == "gpt-4o-mini"

    def test_unregistered_explicit_model_uses_raw_fallback(self, app_setup):
        setup, registry, _ = app_setup
        services = setup.setup_services(
            _runtime("openai", explicit_provider=True, explicit_model_id="custom-raw"),
            need_memory=False,
        )
        assert services["llm"].model == "custom-raw"
        assert registry.current_model is None

    def test_binder_standalone_returns_uncached_service(self, app_setup):
        _, _, llm_manager = app_setup
        service = llm_manager.get_service_for_selection(
            ModelSelection(
                provider="openai",
                model_id="openai/gpt-5",
                source=ModelSelectionSource.RUNTIME_ARGS,
            ),
            standalone=True,
        )
        assert service.model == "gpt-5"
        assert "openai" not in llm_manager.services


# ---------------------------------------------------------------------------
# setup_agents: per-agent precedence
# ---------------------------------------------------------------------------


class TestSetupAgentsPrecedence:
    def test_explicit_model_beats_config_last_used_default(self, agents_env):
        setup, registry, _ = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai", explicit_provider=True, explicit_model_id="gpt-5")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.llm.model == "gpt-5"
        assert coder.model_selection.source is ModelSelectionSource.RUNTIME_ARGS
        assert coder.model_selection.model_id == "openai/gpt-5"
        assert coder.model_selection.is_forced is True
        assert plain.llm.model == "gpt-5"
        assert plain.model_selection.source is ModelSelectionSource.RUNTIME_ARGS

    def test_config_model_beats_last_used_and_default(self, agents_env):
        setup, registry, _ = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.llm.model == "gpt-4o"
        assert coder.model_selection.source is ModelSelectionSource.AGENT_CONFIG
        assert coder.model_selection.model_id == "openai/gpt-4o"
        assert coder.model_selection.is_forced is False
        assert plain.llm.model == "gpt-4o-mini"
        assert plain.model_selection.source is ModelSelectionSource.LAST_USED

    def test_agent_without_config_model_inherits_last_used(self, agents_env):
        setup, registry, _ = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        assert manager.agents["plain"].llm.model == "gpt-4o-mini"
        assert (
            manager.agents["plain"].model_selection.source
            is ModelSelectionSource.LAST_USED
        )

    def test_default_selected_when_no_higher_source(self, agents_env):
        setup, _, _ = agents_env
        runtime = _runtime("openai")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        assert manager.agents["coder"].llm.model == "gpt-4o"
        assert manager.agents["plain"].llm.model == "gpt-4o"
        assert (
            manager.agents["coder"].model_selection.source
            is ModelSelectionSource.AGENT_CONFIG
        )
        assert (
            manager.agents["plain"].model_selection.source
            is ModelSelectionSource.DEFAULT
        )
        assert manager.agents["plain"].model_selection.model_id is None

    def test_explicit_provider_only_skips_other_provider_config(self, agents_env):
        setup, _, _ = agents_env
        runtime = _runtime("claude", explicit_provider=True)
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        assert coder.llm.model == "claude-3-5-sonnet"
        assert coder.model_selection.source is ModelSelectionSource.DEFAULT

    def test_explicit_provider_only_keeps_matching_config(self, agents_env):
        setup, _, _ = agents_env
        runtime = _runtime("openai", explicit_provider=True)
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.llm.model == "gpt-4o"
        assert coder.model_selection.source is ModelSelectionSource.AGENT_CONFIG
        assert plain.llm.model == "gpt-4o"
        assert plain.model_selection.source is ModelSelectionSource.DEFAULT

    def test_detected_env_model_does_not_override_config(self, agents_env):
        setup, _, _ = agents_env
        runtime = _runtime("openai", detected_model_id="gpt-5")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        assert manager.agents["coder"].llm.model == "gpt-4o"
        assert (
            manager.agents["coder"].model_selection.source
            is ModelSelectionSource.AGENT_CONFIG
        )
        assert manager.agents["plain"].llm.model == "gpt-5"
        assert (
            manager.agents["plain"].model_selection.source
            is ModelSelectionSource.ENVIRONMENT
        )

    def test_detected_env_model_family_flows_to_unconfigured_agent(self, agents_env):
        setup, registry, llm_manager = agents_env
        registry.models["github_copilot/claude-sonnet-4.5"] = GITHUB_FAMILY_A
        registry.models["github_copilot/copilot-response-gpt-5"] = GITHUB_FAMILY_B
        registry.last_used_model = "github_copilot/claude-sonnet-4.5"
        registry.last_used_provider = "github_copilot"
        runtime = _runtime("github_copilot", detected_model_id="copilot-response-gpt-5")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        assert manager.agents["coder"].llm.model == "gpt-4o"
        assert (
            manager.agents["coder"].model_selection.source
            is ModelSelectionSource.AGENT_CONFIG
        )
        assert (
            manager.agents["plain"].llm is not llm_manager.services["copilot_response"]
        )
        assert manager.agents["plain"].llm.model == "copilot-response-gpt-5"
        assert (
            manager.agents["plain"].model_selection.source
            is ModelSelectionSource.ENVIRONMENT
        )

    def test_unregistered_explicit_model_raw_fallback(self, agents_env):
        setup, _, _ = agents_env
        runtime = _runtime(
            "openai", explicit_provider=True, explicit_model_id="custom-raw"
        )
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        assert coder.llm.model == "custom-raw"
        assert coder.model_selection.source is ModelSelectionSource.RUNTIME_ARGS
        assert coder.model_selection.model_id == "openai/custom-raw"
        assert coder.model_selection.is_pinned is True

    def test_server_standalone_mode_applies_explicit_model(
        self, agents_env, monkeypatch
    ):
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
            lambda: SimpleNamespace(initialized=False),
        )
        setup, registry, _ = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai", explicit_provider=True, explicit_model_id="gpt-5")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", "openai", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.is_remoting_mode is True
        assert coder.llm.model == "gpt-5"
        assert coder.model_selection.source is ModelSelectionSource.RUNTIME_ARGS
        assert coder.model_selection.model_id == "openai/gpt-5"
        assert plain.llm.model == "gpt-5"
        assert plain.model_selection.source is ModelSelectionSource.RUNTIME_ARGS

    def test_server_standalone_mode_restores_last_used_for_unconfigured(
        self, agents_env, monkeypatch
    ):
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
            lambda: SimpleNamespace(initialized=False),
        )
        setup, registry, _ = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", "openai", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.llm.model == "gpt-4o"
        assert plain.llm.model == "gpt-4o-mini"
        assert coder.model_selection.source is ModelSelectionSource.AGENT_CONFIG
        assert plain.model_selection.source is ModelSelectionSource.LAST_USED

    def test_server_standalone_mode_with_detected_provider(
        self, agents_env, monkeypatch
    ):
        monkeypatch.setattr(
            "AgentCrew.modules.mcpclient.MCPSessionManager.get_instance",
            lambda: SimpleNamespace(initialized=False),
        )
        setup, registry, llm_manager = agents_env
        registry.last_used_model = "openai/gpt-4o-mini"
        registry.last_used_provider = "openai"
        runtime = _runtime("openai", detected_model_id="gpt-5")
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(
            services,
            "agents.toml",
            use_standalone_provider=runtime.provider,
            runtime_model=runtime,
        )
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        plain = manager.agents["plain"]
        assert coder.is_remoting_mode is True
        assert plain.is_remoting_mode is True
        assert coder.is_active is True
        assert plain.is_active is True
        assert coder.llm.model == "gpt-4o"
        assert coder.model_selection.source is ModelSelectionSource.AGENT_CONFIG
        assert plain.llm.model == "gpt-5"
        assert plain.model_selection.source is ModelSelectionSource.ENVIRONMENT
        cached = llm_manager.services.get("openai")
        assert plain.llm is not cached


# ---------------------------------------------------------------------------
# /model force switch
# ---------------------------------------------------------------------------


class TestModelCommandForceSwitch:
    @pytest.mark.asyncio
    async def test_model_command_overrides_pinned_agent_and_persists(
        self, app_setup, monkeypatch
    ):
        _, _, llm_manager = app_setup
        saved = {}
        monkeypatch.setattr(
            GlobalConfig,
            "set_last_used_model",
            lambda self, model_id, provider: saved.update(
                model=model_id, provider=provider
            ),
        )

        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="opus", provider_name="claude")
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_a.model_selection = ModelSelection.from_model_id(
            "claude/opus", ModelSelectionSource.AGENT_CONFIG
        )
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai")
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        svc_c = _StubLLM(model="gpt-4o", provider_name="openai")
        agent_c = LocalAgent("c", "C", svc_c, {}, [])
        agent_c.model_selection = ModelSelection.from_model_id(
            "openai/gpt-4o", ModelSelectionSource.AGENT_CONFIG
        )
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.register_agent(agent_c)
        manager.current_agent = agent_a

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model("/model openai/gpt-5")

        new_service = llm_manager.services["openai"]
        assert agent_a.llm is not new_service
        assert agent_a.llm.model == "gpt-5"
        assert agent_a.model_selection.source is ModelSelectionSource.USER_SWITCH
        assert agent_a.model_selection.model_id == "openai/gpt-5"
        assert agent_b.llm is not new_service
        assert agent_b.llm is not agent_a.llm
        assert agent_b.llm.model == "gpt-5"
        assert agent_c.llm is svc_c
        assert agent_c.model_selection.source is ModelSelectionSource.AGENT_CONFIG
        assert saved == {"model": "openai/gpt-5", "provider": "openai"}

    @pytest.mark.asyncio
    async def test_config_reload_does_not_demote_model_switch(
        self, app_setup, monkeypatch
    ):
        _, _, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="opus", provider_name="claude")
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_a.model_selection = ModelSelection.from_model_id(
            "claude/opus", ModelSelectionSource.AGENT_CONFIG
        )
        manager.register_agent(agent_a)
        manager.current_agent = agent_a

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model("/model openai/gpt-5")
        switched_service = llm_manager.services["openai"]
        assert agent_a.llm is not switched_service
        assert agent_a.llm.model == "gpt-5"

        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            staticmethod(
                lambda uri: [
                    {
                        "name": "a",
                        "description": "A",
                        "system_prompt": "sys",
                        "tools": [],
                        "model_id": "claude/opus",
                    }
                ]
            ),
        )
        AgentsConfig().reload()

        assert agent_a.llm is not switched_service
        assert agent_a.llm.model == "gpt-5"
        assert agent_a.model_selection.source is ModelSelectionSource.USER_SWITCH
        assert agent_a.model_selection.model_id == "openai/gpt-5"


# ---------------------------------------------------------------------------
# reload selection helper
# ---------------------------------------------------------------------------


class TestReloadModelSelection:
    def test_force_switch_kept(self):
        forced_selection = ModelSelection(
            provider="openai",
            model_id="openai/gpt-5",
            source=ModelSelectionSource.USER_SWITCH,
        )
        agent = SimpleNamespace(model_selection=forced_selection)
        new_svc, selection, _reasoning = _reload_model_selection(
            agent, {"name": "a", "model_id": "claude/opus"}
        )
        assert new_svc is None
        assert selection is forced_selection

    def test_config_reapplied_without_force(self, app_setup):
        agent = SimpleNamespace(model_selection=None, reasoning_selection=None)
        new_svc, selection, _reasoning = _reload_model_selection(
            agent, {"name": "a", "model_id": "openai/gpt-4o"}
        )
        assert new_svc is not None
        assert new_svc.model == "gpt-4o"
        assert selection.source is ModelSelectionSource.AGENT_CONFIG
        assert selection.model_id == "openai/gpt-4o"

    def test_no_config_model_no_force(self):
        agent = SimpleNamespace(model_selection=None, reasoning_selection=None)
        new_svc, selection, _reasoning = _reload_model_selection(agent, {"name": "a"})
        assert new_svc is None
        assert selection is None


# ---------------------------------------------------------------------------
# startup route wiring
# ---------------------------------------------------------------------------


def _install_route_fakes(monkeypatch, mode):
    mcp_module = SimpleNamespace(
        MCPSessionManager=SimpleNamespace(
            get_instance=lambda: SimpleNamespace(
                initialized=False, cleanup=lambda: None
            )
        )
    )
    monkeypatch.setitem(sys.modules, "AgentCrew.modules.mcpclient", mcp_module)
    if mode == "console":
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.chat",
            SimpleNamespace(MessageHandler=lambda *a, **k: SimpleNamespace()),
        )
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.console",
            SimpleNamespace(
                ConsoleUI=lambda *a, **k: SimpleNamespace(start=lambda: None)
            ),
        )
    elif mode == "gui":
        monkeypatch.setitem(sys.modules, "PySide6", SimpleNamespace())
        monkeypatch.setitem(
            sys.modules,
            "PySide6.QtCore",
            SimpleNamespace(
                QCoreApplication=SimpleNamespace(setAttribute=lambda *a, **k: None),
                Qt=SimpleNamespace(
                    ApplicationAttribute=SimpleNamespace(AA_UseOpenGLES=0)
                ),
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "PySide6.QtWidgets",
            SimpleNamespace(
                QApplication=lambda *a, **k: SimpleNamespace(exec=lambda: 0)
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.gui",
            SimpleNamespace(
                ChatWindow=lambda *a, **k: SimpleNamespace(show=lambda: None)
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.chat",
            SimpleNamespace(MessageHandler=lambda *a, **k: SimpleNamespace()),
        )
        monkeypatch.setattr(sys, "exit", lambda code=None: None)
    elif mode == "server":
        monkeypatch.setitem(sys.modules, "AgentCrew.modules.a2a", SimpleNamespace())
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.a2a.server",
            SimpleNamespace(A2AServer=lambda **k: SimpleNamespace(start=lambda: None)),
        )
    elif mode == "acp":
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.acp",
            SimpleNamespace(run_acp_agent=AsyncMock()),
        )
    elif mode == "job":
        import AgentCrew.modules.agents as agents_module

        async def _fake_run_agent_loop(agent, history):
            return "ok", None

        monkeypatch.setattr(agents_module, "run_agent_loop", _fake_run_agent_loop)
    return mcp_module


def _run_mode(monkeypatch, mode, provider, model_id):
    _install_route_fakes(monkeypatch, mode)
    monkeypatch.setattr(
        ApplicationSetup, "load_api_keys_from_config", lambda self: None
    )

    app = AgentCrewApplication(trusted_project_plugins=False)
    setup = app.setup

    monkeypatch.setattr(setup, "detect_provider", lambda: "openai")
    monkeypatch.setattr(setup, "detect_model_id", lambda: "gpt-4o-mini")

    setup_services_calls = _Recorder()
    setup_agents_calls = _Recorder()
    monkeypatch.setattr(setup, "setup_services", setup_services_calls)
    monkeypatch.setattr(setup, "setup_agents", setup_agents_calls)
    monkeypatch.setattr(setup, "restore_last_agent", lambda: None)
    monkeypatch.setattr(setup, "initialize_plugins", AsyncMock())
    monkeypatch.setattr(setup, "shutdown", AsyncMock())

    llm_service = _StubLLM(model="gpt-4o", provider_name="openai")
    setup.agent_manager = SimpleNamespace(
        enforce_transfer=True,
        one_turn_process=False,
        agents={"a": None},
        select_agent=lambda name: True,
        register_agent=lambda agent: None,
        get_local_agent=lambda name: LocalAgent(name, "Coder", llm_service, {}, []),
    )

    if mode == "console":
        app.run_console(provider=provider, model_id=model_id)
    elif mode == "gui":
        app.run_gui(provider=provider, model_id=model_id)
    elif mode == "server":
        app.run_server(provider=provider, model_id=model_id)
    elif mode == "acp":
        app.run_acp(provider=provider, model_id=model_id, agent="test")
    elif mode == "job":
        app.run_job(task="do it", agent="coder", provider=provider, model_id=model_id)
    else:
        raise AssertionError(f"unknown mode {mode}")

    return setup_services_calls, setup_agents_calls


ROUTE_MODES = ["console", "gui", "server", "acp", "job"]


def _runtime_from_setup_services(svc_calls):
    args, kwargs = svc_calls.calls[-1]
    return kwargs.get("runtime_model") or args[0]


def _runtime_from_setup_agents(agents_calls):
    args, kwargs = agents_calls.calls[-1]
    runtime_model = kwargs.get("runtime_model")
    if runtime_model is not None:
        return runtime_model
    return args[3] if len(args) > 3 else None


class TestStartupRoutesPrecedence:
    @pytest.mark.parametrize("mode", ROUTE_MODES)
    def test_explicit_args_flow_to_setup(self, monkeypatch, mode):
        svc_calls, agents_calls = _run_mode(
            monkeypatch, mode, provider="openai", model_id="gpt-5"
        )
        svc_runtime = _runtime_from_setup_services(svc_calls)
        assert svc_runtime is not None
        assert svc_runtime.provider == "openai"
        assert svc_runtime.explicit_provider is True
        assert svc_runtime.explicit_model_id == "gpt-5"
        assert svc_runtime.detected_model_id is None
        agents_runtime = _runtime_from_setup_agents(agents_calls)
        assert agents_runtime is not None
        assert agents_runtime.provider == "openai"
        assert agents_runtime.explicit_provider is True
        assert agents_runtime.explicit_model_id == "gpt-5"
        assert agents_runtime.detected_model_id is None
        if mode == "server":
            _, agents_kwargs = agents_calls.calls[-1]
            assert agents_kwargs.get("use_standalone_provider") == "openai"

    @pytest.mark.parametrize("mode", ROUTE_MODES)
    def test_detected_values_are_not_elevated_to_arguments(self, monkeypatch, mode):
        svc_calls, agents_calls = _run_mode(
            monkeypatch, mode, provider=None, model_id=None
        )
        svc_runtime = _runtime_from_setup_services(svc_calls)
        assert svc_runtime is not None
        assert svc_runtime.provider == "openai"
        assert svc_runtime.explicit_provider is False
        assert svc_runtime.explicit_model_id is None
        assert svc_runtime.detected_model_id == "gpt-4o-mini"
        agents_runtime = _runtime_from_setup_agents(agents_calls)
        assert agents_runtime is not None
        assert agents_runtime.provider == "openai"
        assert agents_runtime.explicit_provider is False
        assert agents_runtime.explicit_model_id is None
        assert agents_runtime.detected_model_id == "gpt-4o-mini"

    def test_server_detected_provider_uses_resolved_standalone_provider(
        self, monkeypatch
    ):
        _, agents_calls = _run_mode(monkeypatch, "server", provider=None, model_id=None)
        _, agents_kwargs = agents_calls.calls[-1]
        assert agents_kwargs.get("use_standalone_provider") == "openai"
        agents_runtime = _runtime_from_setup_agents(agents_calls)
        assert agents_runtime is not None
        assert agents_runtime.provider == "openai"
        assert agents_runtime.explicit_provider is False
        assert agents_runtime.detected_model_id == "gpt-4o-mini"
