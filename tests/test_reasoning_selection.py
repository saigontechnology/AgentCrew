"""Tests for reasoning-effort selection precedence and provider application.

Reasoning precedence under test (highest first):

1. runtime force switch via ``/think`` (USER_SWITCH)
2. explicit ``--reason-effort`` runtime argument (RUNTIME_ARGS)
3. agent config ``reason_effort`` (AGENT_CONFIG)
4. selected model's ``default_reasoning`` (MODEL_DEFAULT)

There is deliberately no environment or last-used reasoning layer.
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
from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.config import ConfigManagement
from AgentCrew.modules.config.agents_config import AgentsConfig, _reload_model_selection
from AgentCrew.modules.config.global_config import GlobalConfig
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.model_selection import (
    ModelSelection,
    ModelSelectionSource,
    RuntimeModelInput,
)
from AgentCrew.modules.llm.reasoning_selection import (
    REASONING_LEVELS,
    ReasoningSelection,
    ReasoningSource,
    adapt_reason_effort,
    apply_reasoning_to_service,
    default_reasoning_for_service,
    resolve_reasoning_selection,
    validate_reason_effort,
)
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import Model
from AgentCrew.setup import ApplicationSetup

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _StubLLM:
    """Minimal stub that records set_think calls and models thinking support."""

    def __init__(
        self,
        model: str = "",
        provider_name: str = "stub",
        registry=None,
        class_name: str = "StubLLM",
    ):
        self.model = model
        self.provider_name = provider_name
        self.registry = registry
        self.__class__.__name__ = class_name
        self.set_think_calls: list = []
        self.reasoning_effort = None
        self.thinking_budget = None
        self.close_calls = 0

    def set_system_prompt(self, prompt):
        pass

    def clear_tools(self):
        pass

    def set_tools(self, tools):
        pass

    def calculate_cost(self, *args, **kwargs):
        return 0.0

    def close(self):
        self.close_calls += 1

    def set_think(self, value):
        self.set_think_calls.append(value)
        if value in ("0", "none") or value == 0:
            self.reasoning_effort = None
            self.thinking_budget = 0
            return True
        if self.registry:
            model = self.registry.get_model(f"{self.provider_name}/{self.model}")
            if model and "thinking" not in model.capabilities:
                return False
        self.reasoning_effort = value
        try:
            self.thinking_budget = int(value)
        except (TypeError, ValueError):
            self.thinking_budget = None
        return True

    temperature = 0.4


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
            self.services[key] = _StubLLM(
                model=model.id, provider_name=model.provider, registry=self.registry
            )
        return self.services[key]

    def get_service_for_model(self, model):
        return self._service_for(model)

    def get_service_for_provider(self, provider):
        models = self.registry.get_models_by_provider(provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            return self._service_for(default_model)
        return _StubLLM(provider_name=provider, registry=self.registry)

    def set_model_for_llm(self, model):
        service = self.get_service_for_model(model)
        service.model = model.id

    def apply_model_defaults(self, service, model):
        service.model = model.id

    def initialize_standalone_service(self, name):
        return _StubLLM(provider_name=name, registry=self.registry)

    def initialize_standalone_service_for_model(self, model):
        return _StubLLM(
            model=model.id, provider_name=model.provider, registry=self.registry
        )

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
        if standalone:
            service = self.initialize_standalone_service(selection.provider)
            models = registry.get_models_by_provider(selection.provider)
            if models:
                default_model = next((m for m in models if m.default), models[0])
                self.apply_model_defaults(service, default_model)
            return service
        models = registry.get_models_by_provider(selection.provider)
        if models:
            default_model = next((m for m in models if m.default), models[0])
            registry.set_current_model(f"{default_model.provider}/{default_model.id}")
            service = self.get_service_for_model(default_model)
            self.apply_model_defaults(service, default_model)
            return service
        return _StubLLM(provider_name=selection.provider, registry=self.registry)

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


def _failing_service_fakes():
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


OPENAI_THINKING = Model(
    id="gpt-5",
    provider="openai",
    name="GPT-5",
    description="",
    capabilities=["tool_use", "thinking"],
    default_reasoning="high",
    default=True,
)
OPENAI_NOTHINK = Model(
    id="gpt-4o",
    provider="openai",
    name="GPT-4o",
    description="",
    capabilities=["tool_use"],
    default_reasoning=None,
)
CLAUDE_THINKING = Model(
    id="claude-sonnet-4",
    provider="claude",
    name="Claude Sonnet 4",
    description="",
    capabilities=["tool_use", "thinking"],
    default_reasoning="medium",
    default=True,
)
GITHUB_FAMILY_A = Model(
    id="claude-sonnet-4.5",
    provider="github_copilot",
    name="Claude Sonnet 4.5",
    description="",
    capabilities=["tool_use", "thinking"],
    service_name="github_copilot",
    default_reasoning="low",
    default=True,
)
GITHUB_FAMILY_B = Model(
    id="copilot-response-gpt-5",
    provider="github_copilot",
    name="Copilot Response",
    description="",
    capabilities=["tool_use", "thinking"],
    service_name="copilot_response",
    default_reasoning="high",
)


@pytest.fixture
def app_setup(monkeypatch):
    AgentManager._instance = None
    registry = _FakeRegistry(
        [
            OPENAI_THINKING,
            OPENAI_NOTHINK,
            CLAUDE_THINKING,
            GITHUB_FAMILY_A,
            GITHUB_FAMILY_B,
        ]
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
        "model_id": "openai/gpt-5",
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
# pure precedence matrix
# ---------------------------------------------------------------------------


class TestResolveReasoningSelection:
    def test_user_switch_beats_all(self):
        selection = ReasoningSelection("low", ReasoningSource.USER_SWITCH)
        assert selection.is_forced is True
        assert selection.is_explicit is True

    def test_cli_beats_config_and_default(self):
        selection = resolve_reasoning_selection("high", "low", "medium")
        assert selection.level == "high"
        assert selection.source is ReasoningSource.RUNTIME_ARGS
        assert selection.is_forced is True
        assert selection.is_explicit is True

    def test_config_beats_default(self):
        selection = resolve_reasoning_selection(None, "low", "medium")
        assert selection.level == "low"
        assert selection.source is ReasoningSource.AGENT_CONFIG
        assert selection.is_forced is False
        assert selection.is_explicit is True

    def test_model_default_when_no_override(self):
        selection = resolve_reasoning_selection(None, None, "medium")
        assert selection.level == "medium"
        assert selection.source is ReasoningSource.MODEL_DEFAULT
        assert selection.is_forced is False
        assert selection.is_explicit is False

    def test_disabled_when_nothing_available(self):
        selection = resolve_reasoning_selection(None, None, None)
        assert selection.level is None
        assert selection.source is ReasoningSource.MODEL_DEFAULT

    def test_raw_model_no_default_disabled(self):
        selection = resolve_reasoning_selection(None, None, None)
        assert selection.level is None


class TestValidateReasonEffort:
    def test_valid_levels_accepted(self):
        for level in REASONING_LEVELS:
            assert validate_reason_effort(level) == level

    def test_none_accepted(self):
        assert validate_reason_effort(None) is None

    def test_invalid_rejected(self):
        with pytest.raises(ValueError):
            validate_reason_effort("ultra")


# ---------------------------------------------------------------------------
# provider application
# ---------------------------------------------------------------------------


class TestAdaptReasonEffort:
    def test_anthropic_maps_to_budget(self):
        service = _StubLLM(model="claude-sonnet-4", provider_name="claude")
        for level, budget in {
            "none": "0",
            "minimal": "1024",
            "low": "2048",
            "medium": "4096",
            "high": "8192",
        }.items():
            assert adapt_reason_effort(service, level) == budget

    def test_anthropic_class_name_maps_to_budget(self):
        service = _StubLLM(
            model="x", provider_name="custom", class_name="AnthropicService"
        )
        assert adapt_reason_effort(service, "high") == "8192"

    def test_named_level_provider_passes_through(self):
        service = _StubLLM(model="gpt-5", provider_name="openai")
        assert adapt_reason_effort(service, "high") == "high"


class TestApplyReasoningToService:
    def test_anthropic_medium_uses_budget(self):
        service = _StubLLM(model="claude-sonnet-4", provider_name="claude")
        apply_reasoning_to_service(service, "medium", explicit=True)
        assert service.set_think_calls == ["4096"]
        assert service.thinking_budget == 4096

    def test_none_disables(self):
        service = _StubLLM(model="gpt-5", provider_name="openai")
        apply_reasoning_to_service(service, "none", explicit=True)
        assert service.set_think_calls == ["none"]
        assert service.reasoning_effort is None

    def test_explicit_on_non_thinking_model_raises(self):
        service = _StubLLM(
            model="gpt-4o",
            provider_name="openai",
            registry=_FakeRegistry([OPENAI_NOTHINK]),
        )
        with pytest.raises(ValueError):
            apply_reasoning_to_service(service, "high", explicit=True)

    def test_model_default_on_non_thinking_model_is_lenient(self):
        service = _StubLLM(
            model="gpt-4o",
            provider_name="openai",
            registry=_FakeRegistry([OPENAI_NOTHINK]),
        )
        applied = apply_reasoning_to_service(service, "high", explicit=False)
        assert applied is True
        assert service.reasoning_effort is None

    def test_default_reasoning_for_service(self, app_setup):
        _, registry, _ = app_setup
        service = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        assert default_reasoning_for_service(service) == "high"

    def test_default_reasoning_none_for_raw_model(self, app_setup):
        _, registry, _ = app_setup
        service = _StubLLM(
            model="unknown-model", provider_name="openai", registry=registry
        )
        assert default_reasoning_for_service(service) is None


# ---------------------------------------------------------------------------
# setup_services base reasoning
# ---------------------------------------------------------------------------


class TestSetupServicesReasoning:
    def test_cli_reasoning_applied_to_base_service(self, app_setup):
        setup, _, _ = app_setup
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, reason_effort="low", need_memory=False)
        assert services["llm"].reasoning_effort == "low"

    def test_model_default_applied_when_no_cli(self, app_setup):
        setup, _, _ = app_setup
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        assert services["llm"].reasoning_effort == "high"

    def test_absent_reasoning_disabled(self, app_setup):
        setup, _, _ = app_setup
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id="gpt-4o",
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        assert services["llm"].reasoning_effort is None


# ---------------------------------------------------------------------------
# per-agent reasoning
# ---------------------------------------------------------------------------


class TestSetupAgentsReasoning:
    def test_cli_beats_config_and_default(self, agents_env):
        setup, _, _ = agents_env
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, reason_effort="low", need_memory=False)
        setup.setup_agents(
            services, "agents.toml", runtime_model=runtime, reason_effort="low"
        )
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        assert coder.reasoning_selection.source is ReasoningSource.RUNTIME_ARGS
        assert coder.reasoning_selection.level == "low"
        assert coder.llm.reasoning_effort == "low"

    def test_config_beats_default(self, agents_env, monkeypatch):
        setup, _, _ = agents_env
        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            lambda uri: [
                {
                    "name": "coder",
                    "description": "Coder",
                    "system_prompt": "s",
                    "tools": [],
                    "model_id": "openai/gpt-5",
                    "reason_effort": "minimal",
                }
            ],
        )
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        assert coder.reasoning_selection.source is ReasoningSource.AGENT_CONFIG
        assert coder.reasoning_selection.level == "minimal"
        assert coder.llm.reasoning_effort == "minimal"

    def test_model_default_when_no_override(self, agents_env):
        setup, _, _ = agents_env
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        coder = manager.agents["coder"]
        assert coder.reasoning_selection.source is ReasoningSource.MODEL_DEFAULT
        assert coder.reasoning_selection.level == "high"
        assert coder.llm.reasoning_effort == "high"

    def test_distinct_agents_distinct_config_efforts(self, agents_env, monkeypatch):
        setup, _, _ = agents_env
        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            lambda uri: [
                {
                    "name": "a",
                    "description": "A",
                    "system_prompt": "s",
                    "tools": [],
                    "reason_effort": "low",
                },
                {
                    "name": "b",
                    "description": "B",
                    "system_prompt": "s",
                    "tools": [],
                    "reason_effort": "high",
                },
            ],
        )
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(services, "agents.toml", runtime_model=runtime)
        manager = AgentManager.get_instance()
        agent_a = manager.agents["a"]
        agent_b = manager.agents["b"]
        assert agent_a.reasoning_selection.level == "low"
        assert agent_b.reasoning_selection.level == "high"
        assert agent_a.llm is not agent_b.llm

    def test_non_thinking_config_effort_raises(self, agents_env, monkeypatch):
        setup, _, _ = agents_env
        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            lambda uri: [
                {
                    "name": "a",
                    "description": "A",
                    "system_prompt": "s",
                    "tools": [],
                    "model_id": "openai/gpt-4o",
                    "reason_effort": "high",
                }
            ],
        )
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        with pytest.raises(ValueError):
            setup.setup_agents(services, "agents.toml", runtime_model=runtime)

    def test_a2a_standalone_applies_per_agent_reasoning(self, agents_env, monkeypatch):
        setup, _, llm_manager = agents_env
        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            lambda uri: [
                {
                    "name": "a",
                    "description": "A",
                    "system_prompt": "s",
                    "tools": [],
                    "model_id": "openai/gpt-5",
                    "reason_effort": "low",
                }
            ],
        )
        runtime = RuntimeModelInput(
            provider="openai",
            explicit_provider=True,
            explicit_model_id=None,
            detected_model_id=None,
        )
        services = setup.setup_services(runtime, need_memory=False)
        setup.setup_agents(
            services,
            "agents.toml",
            use_standalone_provider=runtime.provider,
            runtime_model=runtime,
        )
        manager = AgentManager.get_instance()
        agent_a = manager.agents["a"]
        assert agent_a.is_remoting_mode is True
        assert agent_a.reasoning_selection.source is ReasoningSource.AGENT_CONFIG
        assert agent_a.llm.reasoning_effort == "low"
        assert agent_a.llm is not llm_manager.services.get("openai")


# ---------------------------------------------------------------------------
# runtime /think and /model
# ---------------------------------------------------------------------------


class TestThinkCommand:
    def _make_handler(self, agent):
        return SimpleNamespace(
            agent=agent,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )

    def test_think_sets_user_switch(self, app_setup):
        _, registry, _ = app_setup
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        handler = self._make_handler(agent)
        UtilityCommands(handler).handle_think("/think low")
        assert agent.reasoning_selection.source is ReasoningSource.USER_SWITCH
        assert agent.reasoning_selection.level == "low"
        assert agent.reasoning_selection.is_forced is True

    def test_think_none_disables(self, app_setup):
        _, registry, _ = app_setup
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        handler = self._make_handler(agent)
        UtilityCommands(handler).handle_think("/think none")
        assert agent.reasoning_selection.source is ReasoningSource.USER_SWITCH
        assert agent.reasoning_selection.level == "none"

    def test_think_replaces_previous(self, app_setup):
        _, registry, _ = app_setup
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        handler = self._make_handler(agent)
        UtilityCommands(handler).handle_think("/think low")
        UtilityCommands(handler).handle_think("/think high")
        assert agent.reasoning_selection.level == "high"

    def test_think_rejected_on_non_thinking_model(self, app_setup):
        _, registry, _ = app_setup
        svc = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        handler = self._make_handler(agent)
        UtilityCommands(handler).handle_think("/think high")
        assert agent.reasoning_selection is None


class TestModelSwitchReasoning:
    @pytest.mark.asyncio
    async def test_model_switch_preserves_think_override(self, app_setup, monkeypatch):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        agent.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.USER_SWITCH
        )
        manager.register_agent(agent)
        manager.current_agent = agent

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model("/model openai/gpt-5")
        assert agent.reasoning_selection.level == "low"
        assert agent.llm.reasoning_effort == "low"

    @pytest.mark.asyncio
    async def test_model_switch_recomputes_default_without_think(self, app_setup):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        agent.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent)
        manager.current_agent = agent

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model("/model openai/gpt-5")
        assert agent.reasoning_selection.source is ReasoningSource.MODEL_DEFAULT
        assert agent.reasoning_selection.level == "high"

    @pytest.mark.asyncio
    async def test_model_switch_to_non_thinking_clears_default(self, app_setup):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        agent.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent)
        manager.current_agent = agent

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model("/model openai/gpt-4o")
        assert agent.llm.reasoning_effort is None

    @pytest.mark.asyncio
    async def test_model_switch_service_family_no_stale_reasoning(self, app_setup):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(
            model="claude-sonnet-4.5", provider_name="github_copilot", registry=registry
        )
        agent = LocalAgent("a", "A", svc_a, {}, [])
        agent.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent)
        manager.current_agent = agent

        handler = SimpleNamespace(
            agent_manager=manager,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        await ModelCommands(handler).handle_model(
            "/model github_copilot/copilot-response-gpt-5"
        )
        assert agent.llm.reasoning_effort == "high"
        assert agent.reasoning_selection.level == "high"


# ---------------------------------------------------------------------------
# config reload
# ---------------------------------------------------------------------------


class TestReloadReasoning:
    def test_forced_reasoning_preserved(self):
        agent = SimpleNamespace(
            model_selection=None,
            reasoning_selection=ReasoningSelection("low", ReasoningSource.USER_SWITCH),
        )
        _new_svc, _model_selection, reasoning = _reload_model_selection(
            agent, {"name": "a", "reason_effort": "high"}
        )
        assert reasoning is agent.reasoning_selection
        assert reasoning.level == "low"

    def test_config_reasoning_reapplied_without_force(self):
        agent = SimpleNamespace(model_selection=None, reasoning_selection=None)
        _new_svc, _model_selection, reasoning = _reload_model_selection(
            agent, {"name": "a", "reason_effort": "medium"}
        )
        assert reasoning.source is ReasoningSource.AGENT_CONFIG
        assert reasoning.level == "medium"

    def test_no_config_no_force_falls_back_to_default(self):
        agent = SimpleNamespace(model_selection=None, reasoning_selection=None)
        _new_svc, _model_selection, reasoning = _reload_model_selection(
            agent, {"name": "a"}
        )
        assert reasoning.source is ReasoningSource.MODEL_DEFAULT
        assert reasoning.level is None


# ---------------------------------------------------------------------------
# config schema
# ---------------------------------------------------------------------------


class TestAgentConfigSchema:
    def test_reason_effort_round_trip(self):
        from AgentCrew.modules.config.agents_config import LocalAgentConfig

        cfg = LocalAgentConfig.from_dict(
            {"name": "a", "description": "A", "reason_effort": "high"}
        )
        assert cfg.reason_effort == "high"
        assert cfg.to_dict()["reason_effort"] == "high"

    def test_reason_effort_absent_omitted(self):
        from AgentCrew.modules.config.agents_config import LocalAgentConfig

        cfg = LocalAgentConfig.from_dict({"name": "a", "description": "A"})
        assert cfg.reason_effort is None
        assert "reason_effort" not in cfg.to_dict()


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
            "AgentCrew.modules.console",
            SimpleNamespace(
                ConsoleUI=lambda *a, **k: SimpleNamespace(start=lambda: None)
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "AgentCrew.modules.chat",
            SimpleNamespace(MessageHandler=lambda *a, **k: SimpleNamespace()),
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


def _run_mode(monkeypatch, mode, provider, model_id, reason_effort):
    _install_route_fakes(monkeypatch, mode)
    monkeypatch.setattr(
        ApplicationSetup, "load_api_keys_from_config", lambda self: None
    )

    app = AgentCrewApplication(trusted_project_plugins=False)
    setup = app.setup

    monkeypatch.setattr(setup, "detect_provider", lambda: "openai")
    monkeypatch.setattr(setup, "detect_model_id", lambda: "gpt-4o-mini")

    from tests.test_model_selection_precedence import _Recorder

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
        app.run_console(
            provider=provider, model_id=model_id, reason_effort=reason_effort
        )
    elif mode == "gui":
        app.run_gui(provider=provider, model_id=model_id, reason_effort=reason_effort)
    elif mode == "server":
        app.run_server(
            provider=provider, model_id=model_id, reason_effort=reason_effort
        )
    elif mode == "acp":
        app.run_acp(
            provider=provider,
            model_id=model_id,
            agent="test",
            reason_effort=reason_effort,
        )
    elif mode == "job":
        app.run_job(
            task="do it",
            agent="coder",
            provider=provider,
            model_id=model_id,
            reason_effort=reason_effort,
        )
    else:
        raise AssertionError(f"unknown mode {mode}")

    return setup_services_calls, setup_agents_calls


ROUTE_MODES = ["console", "gui", "server", "acp", "job"]


class TestStartupRoutesReasoning:
    def test_invalid_reason_effort_rejected_by_cli(self):
        from click.testing import CliRunner

        from AgentCrew.main import cli

        result = CliRunner().invoke(cli, ["job", "--reason-effort", "ultra", "do it"])
        assert result.exit_code == 2
        assert "Invalid value" in result.output

    @pytest.mark.parametrize("mode", ROUTE_MODES)
    def test_explicit_reason_effort_flows_to_setup(self, monkeypatch, mode):
        svc_calls, agents_calls = _run_mode(
            monkeypatch, mode, provider="openai", model_id=None, reason_effort="high"
        )
        _, svc_kwargs = svc_calls.calls[-1]
        assert svc_kwargs.get("reason_effort") == "high"
        _, agents_kwargs = agents_calls.calls[-1]
        assert agents_kwargs.get("reason_effort") == "high"

    @pytest.mark.parametrize("mode", ROUTE_MODES)
    def test_absent_reason_effort_not_elevated(self, monkeypatch, mode):
        svc_calls, agents_calls = _run_mode(
            monkeypatch, mode, provider="openai", model_id=None, reason_effort=None
        )
        _, svc_kwargs = svc_calls.calls[-1]
        assert svc_kwargs.get("reason_effort") is None
        _, agents_kwargs = agents_calls.calls[-1]
        assert agents_kwargs.get("reason_effort") is None


# ---------------------------------------------------------------------------
# per-agent LLM isolation (shared mutable service leakage)
# ---------------------------------------------------------------------------


class TestReasoningIsolation:
    def test_think_isolates_shared_llm(self, app_setup):
        _, registry, llm_manager = app_setup
        shared = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        # Simulate a cached shared service with B's model-default reasoning applied.
        llm_manager.services["openai"] = shared
        shared.reasoning_effort = "high"
        agent_b = LocalAgent("b", "B", shared, {}, [])
        agent_b.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        agent_a = LocalAgent("a", "A", shared, {}, [])

        AgentManager._instance = None
        manager = AgentManager()
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        handler = SimpleNamespace(
            agent=agent_a,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        UtilityCommands(handler).handle_think("/think low")

        assert agent_a.reasoning_selection.source is ReasoningSource.USER_SWITCH
        assert agent_a.reasoning_selection.level == "low"
        assert agent_a.llm is not agent_b.llm
        assert agent_a.llm is not shared
        assert agent_a.llm.reasoning_effort == "low"
        # B's shared service is untouched
        assert agent_b.llm is shared
        assert agent_b.llm.reasoning_effort == "high"

    def test_manager_update_isolates_reasoning_sync(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_a.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.USER_SWITCH
        )
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        agent_b.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        model = registry.get_model("openai/gpt-5")
        new_service = llm_manager.get_service_for_model(model)
        manager.update_llm_service(new_service)

        assert agent_a.llm is not agent_b.llm
        assert agent_a.llm is not new_service
        assert agent_b.llm is not new_service
        assert agent_a.llm.model == "gpt-5"
        assert agent_b.llm.model == "gpt-5"
        assert agent_a.llm.reasoning_effort == "low"
        assert agent_b.llm.reasoning_effort == "high"

    @pytest.mark.asyncio
    async def test_manager_update_isolates_reasoning_async(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_a.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.USER_SWITCH
        )
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        agent_b.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        model = registry.get_model("openai/gpt-5")
        new_service = llm_manager.get_service_for_model(model)
        await manager.update_llm_service_async(new_service)

        assert agent_a.llm is not agent_b.llm
        assert agent_a.llm is not new_service
        assert agent_b.llm is not new_service
        assert agent_a.llm.reasoning_effort == "low"
        assert agent_b.llm.reasoning_effort == "high"

    @pytest.mark.asyncio
    async def test_reload_preserves_distinct_reasoning_identity(
        self, app_setup, monkeypatch
    ):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_a.model_selection = ModelSelection.from_model_id(
            "openai/gpt-5", ModelSelectionSource.USER_SWITCH
        )
        agent_a.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.USER_SWITCH
        )
        svc_b = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        agent_b.model_selection = ModelSelection.from_model_id(
            "openai/gpt-5", ModelSelectionSource.ENVIRONMENT
        )
        agent_b.reasoning_selection = ReasoningSelection(
            "high", ReasoningSource.MODEL_DEFAULT
        )
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            staticmethod(
                lambda uri: [
                    {
                        "name": "a",
                        "description": "A",
                        "system_prompt": "s",
                        "tools": [],
                        "model_id": "openai/gpt-5",
                    },
                    {
                        "name": "b",
                        "description": "B",
                        "system_prompt": "s",
                        "tools": [],
                        "model_id": "openai/gpt-5",
                    },
                ]
            ),
        )
        AgentsConfig().reload()

        # A forced USER_SWITCH preserved; B recomputed to model default.
        assert agent_a.reasoning_selection.source is ReasoningSource.USER_SWITCH
        assert agent_a.reasoning_selection.level == "low"
        assert agent_b.reasoning_selection.source is ReasoningSource.MODEL_DEFAULT
        assert agent_b.reasoning_selection.level == "high"
        assert agent_a.llm is not agent_b.llm
        assert agent_a.llm.reasoning_effort == "low"
        assert agent_b.llm.reasoning_effort == "high"

    @pytest.mark.asyncio
    async def test_reload_clone_gets_dedicated_llm(self, app_setup, monkeypatch):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        agent.reasoning_selection = ReasoningSelection(
            "low", ReasoningSource.USER_SWITCH
        )
        manager.register_agent(agent)
        manager.current_agent = agent

        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            staticmethod(
                lambda uri: [
                    {
                        "name": "a",
                        "description": "A",
                        "system_prompt": "s",
                        "tools": [],
                        "model_id": "openai/gpt-5",
                    },
                    {
                        "name": "new",
                        "description": "New",
                        "system_prompt": "s",
                        "tools": [],
                        "model_id": "openai/gpt-5",
                    },
                ]
            ),
        )
        AgentsConfig().reload()

        new_agent = manager.agents["new"]
        assert new_agent.llm is not agent.llm
        assert new_agent.llm.model == "gpt-5"
        # Mutating the source agent's reasoning must not affect the clone.
        agent.llm.reasoning_effort = "medium"
        assert new_agent.llm.reasoning_effort != "medium"


# ---------------------------------------------------------------------------
# GUI editor reason_effort round-trip
# ---------------------------------------------------------------------------


class TestLocalAgentEditorReasonEffort:
    @pytest.fixture
    def qapp(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        return app

    def _make_editor(self, qapp):
        from AgentCrew.modules.gui.widgets.configs.agent_config.local_agent_editor import (
            LocalAgentEditor,
        )

        return LocalAgentEditor(available_tools=[], persistence_service=None)

    def test_populate_collect_round_trip_high(self, qapp):
        editor = self._make_editor(qapp)
        editor.populate({"name": "a", "description": "A", "reason_effort": "high"})
        assert editor.reason_effort_combo.currentData() == "high"
        assert editor.collect()["reason_effort"] == "high"

    def test_populate_collect_round_trip_absent(self, qapp):
        editor = self._make_editor(qapp)
        editor.populate({"name": "a", "description": "A"})
        assert editor.reason_effort_combo.currentData() is None
        assert editor.collect()["reason_effort"] is None

    def test_unknown_reason_effort_degrades_to_default(self, qapp):
        editor = self._make_editor(qapp)
        editor.populate({"name": "a", "description": "A", "reason_effort": "bogus"})
        assert editor.reason_effort_combo.currentData() is None
        assert editor.collect()["reason_effort"] is None

    def test_clear_resets_reason_effort(self, qapp):
        editor = self._make_editor(qapp)
        editor.populate({"name": "a", "description": "A", "reason_effort": "high"})
        editor.clear()
        assert editor.reason_effort_combo.currentData() is None
        assert editor.collect()["reason_effort"] is None

    def test_set_enabled_toggles_reason_effort(self, qapp):
        editor = self._make_editor(qapp)
        editor.setEnabled(False)
        assert editor.reason_effort_combo.isEnabled() is False
        editor.setEnabled(True)
        assert editor.reason_effort_combo.isEnabled() is True


class TestDeregisterLLMCleanup:
    """Deregistered LocalAgents release their dedicated LLM exactly once.

    Agent removal (config reload) routes through ``AgentManager.deregister_agent``,
    which calls ``LocalAgent.release_llm`` before deletion. Cached services and
    services still referenced by a remaining agent are never closed.
    """

    def test_reload_removal_closes_dedicated_service_once(self, app_setup, monkeypatch):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            staticmethod(
                lambda uri: [
                    {"name": "a", "description": "A", "system_prompt": "s", "tools": []}
                ]
            ),
        )
        AgentsConfig().reload()

        assert "b" not in manager.agents
        assert svc_b.close_calls == 1  # removed agent's dedicated service closed once
        assert svc_a.close_calls == 0  # remaining agent's service stays open

    @pytest.mark.asyncio
    async def test_reload_async_removal_closes_dedicated_service_once(
        self, app_setup, monkeypatch
    ):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc_a = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        monkeypatch.setattr(
            AgentManager,
            "load_agents_from_config",
            staticmethod(
                lambda uri: [
                    {"name": "a", "description": "A", "system_prompt": "s", "tools": []}
                ]
            ),
        )
        await AgentsConfig().reload_async()

        assert "b" not in manager.agents
        assert svc_b.close_calls == 1
        assert svc_a.close_calls == 0

    def test_deregister_does_not_close_cached_service(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        cached = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        llm_manager.services["openai"] = cached
        agent = LocalAgent("a", "A", cached, {}, [])
        manager.register_agent(agent)

        manager.deregister_agent("a")

        assert "a" not in manager.agents
        assert cached.close_calls == 0  # ServiceManager-cached service never closed

    def test_deregister_does_not_close_service_referenced_by_remaining_agent(
        self, app_setup
    ):
        _, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        shared = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", shared, {}, [])
        agent_b = LocalAgent("b", "B", shared, {}, [])
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)

        manager.deregister_agent("a")

        assert shared.close_calls == 0  # still referenced by B
        assert agent_b.llm is shared

    @pytest.mark.asyncio
    async def test_deregistered_service_not_double_closed_at_shutdown(self, app_setup):
        setup, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc, {}, [])
        manager.register_agent(agent)
        manager.current_agent = agent

        manager.deregister_agent("a")
        assert svc.close_calls == 1

        setup.agent_manager = manager

        async def mock_shutdown_plugins():
            pass

        async def mock_close_remote():
            pass

        setup.shutdown_plugins = mock_shutdown_plugins
        manager.close_all_remote_agents = mock_close_remote
        await setup.shutdown()

        assert svc.close_calls == 1  # not double-closed at shutdown

    def test_deregister_remote_agent_no_local_cleanup(self, app_setup):
        from AgentCrew.modules.agents.remote_agent import RemoteAgent

        _, _, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        remote = RemoteAgent("remote", "http://localhost:9999")
        manager.register_agent(remote)

        # Must not raise and must not enter LocalAgent LLM cleanup.
        manager.deregister_agent("remote")

        assert "remote" not in manager.agents


class _FailCloseLLM(_StubLLM):
    """Stub whose close() records the call and then raises."""

    def close(self):
        self.close_calls += 1
        raise RuntimeError("close failed")


class TestLLMLifecycleCleanup:
    """Ownership-safe LLM lifecycle: superseded dedicated services are closed
    exactly once; cached/shared/current services are never closed; shutdown
    drains all dedicated services and continues after individual failures."""

    def test_sync_replacement_closes_old_dedicated_once(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        old_svc = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", old_svc, {}, [])
        manager.register_agent(agent)
        manager.current_agent = agent

        model = registry.get_model("openai/gpt-5")
        new_service = llm_manager.get_service_for_model(model)
        agent.update_llm_service(new_service)

        assert old_svc.close_calls == 1
        assert agent.llm is not old_svc
        assert agent.llm.close_calls == 0

    @pytest.mark.asyncio
    async def test_async_replacement_closes_old_dedicated_once(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        old_svc = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", old_svc, {}, [])
        manager.register_agent(agent)
        manager.current_agent = agent

        model = registry.get_model("openai/gpt-5")
        new_service = llm_manager.get_service_for_model(model)
        await agent.update_llm_service_async(new_service)

        assert old_svc.close_calls == 1
        assert agent.llm is not old_svc
        assert agent.llm.close_calls == 0

    def test_repeated_replacements_close_each_superseded(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        svc1 = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", svc1, {}, [])
        manager.register_agent(agent)
        manager.current_agent = agent

        agent.update_llm_service(
            llm_manager.get_service_for_model(registry.get_model("openai/gpt-5"))
        )
        first_current = agent.llm
        assert svc1.close_calls == 1

        agent.update_llm_service(
            llm_manager.get_service_for_model(registry.get_model("openai/gpt-4o"))
        )
        second_current = agent.llm

        assert first_current.close_calls == 1
        assert second_current.close_calls == 0
        assert agent.llm is second_current

    def test_shared_cached_service_not_closed_by_think(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        shared = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        llm_manager.services["openai"] = shared  # cached by ServiceManager
        agent_a = LocalAgent("a", "A", shared, {}, [])
        manager.register_agent(agent_a)
        manager.current_agent = agent_a

        handler = SimpleNamespace(
            agent=agent_a,
            bus=SimpleNamespace(emit_sync=lambda *args, **kwargs: None),
        )
        UtilityCommands(handler).handle_think("/think low")

        assert shared.close_calls == 0  # cached service never closed
        assert agent_a.llm is not shared

    def test_another_agents_current_service_not_closed(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        shared = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent_a = LocalAgent("a", "A", shared, {}, [])
        agent_b = LocalAgent("b", "B", shared, {}, [])
        manager.register_agent(agent_a)
        manager.register_agent(agent_b)
        manager.current_agent = agent_a

        model = registry.get_model("openai/gpt-4o")
        new_service = llm_manager.get_service_for_model(model)
        agent_a.update_llm_service(new_service)

        assert shared.close_calls == 0  # still referenced by B
        assert agent_b.llm is shared

    def test_failed_reactivation_closes_superseded_not_current(
        self, app_setup, monkeypatch
    ):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        manager = AgentManager()
        old_svc = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        agent = LocalAgent("a", "A", old_svc, {}, [])
        agent.is_active = True
        manager.register_agent(agent)
        manager.current_agent = agent

        def boom():
            raise RuntimeError("activation failed")

        monkeypatch.setattr(agent, "activate", boom)
        model = registry.get_model("openai/gpt-5")
        new_service = llm_manager.get_service_for_model(model)

        with pytest.raises(RuntimeError, match="activation failed"):
            agent.update_llm_service(new_service)

        assert old_svc.close_calls == 1  # superseded closed exactly once
        assert agent.llm is not old_svc
        assert agent.llm.close_calls == 0  # current service stays open

    @pytest.mark.asyncio
    async def test_shutdown_closes_dedicated_once_dedup_continues(self, app_setup):
        setup, registry, _ = app_setup
        AgentManager._instance = None
        manager = AgentManager()

        svc_a = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        svc_b = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        failing = _FailCloseLLM(
            model="gpt-4o", provider_name="openai", registry=registry
        )
        agent_a = LocalAgent("a", "A", svc_a, {}, [])
        agent_b = LocalAgent("b", "B", svc_b, {}, [])
        agent_c = LocalAgent("c", "C", svc_b, {}, [])  # shares svc_b with B
        agent_d = LocalAgent("d", "D", failing, {}, [])
        for ag in (agent_a, agent_b, agent_c, agent_d):
            manager.register_agent(ag)
        manager.current_agent = agent_a

        setup.agent_manager = manager
        calls = []

        async def mock_shutdown_plugins():
            calls.append("plugins")

        async def mock_close_remote():
            calls.append("remote")

        setup.shutdown_plugins = mock_shutdown_plugins
        manager.close_all_remote_agents = mock_close_remote

        await setup.shutdown()

        assert calls == ["plugins", "remote"]
        assert svc_a.close_calls == 1
        assert svc_b.close_calls == 1  # deduplicated by identity
        assert failing.close_calls == 1  # failure swallowed, others continue


class TestAgentLLMLifecycleDelegation:
    """LocalAgent LLM lifecycle methods must delegate to the collaborator.

    These tests pin the extraction boundary introduced when the LLM
    ownership/reasoning lifecycle moved from ``LocalAgent`` into
    ``AgentLLMLifecycle``: all public/semi-public methods keep working as
    thin wrappers so existing callers (setup, AgentManager, AgentsConfig,
    slash commands, ACP, plugins, tests) are unaffected.
    """

    def test_wrapper_sync_service_update_delegates(self, app_setup):
        _, registry, llm_manager = app_setup
        AgentManager._instance = None
        old = _StubLLM(model="gpt-5", provider_name="openai", registry=registry)
        agent = LocalAgent(
            name="a",
            description="d",
            llm_service=old,
            services={"llm_manager": llm_manager},
            tools=[],
        )
        assert agent._llm_lifecycle.agent is agent

        sentinel = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)
        assert agent.update_llm_service(sentinel) is True
        assert agent.llm is sentinel
        # The superseded service is not cached and not shared, so the
        # collaborator closes it exactly once.
        assert old.close_calls == 1

    def test_wrappers_noop_paths_delegate(self, app_setup):
        _, _, llm_manager = app_setup
        agent = LocalAgent(
            name="a",
            description="d",
            llm_service=None,
            services={"llm_manager": llm_manager},
            tools=[],
        )
        # No-op paths still route through the collaborator without raising.
        agent.reapply_reasoning()
        agent.ensure_reasoning_isolated()
        agent.release_llm()
        assert agent._llm_lifecycle.dedicated_llm() is None
        assert agent._llm_lifecycle.is_service_owned(None) is False

    def test_wrapper_async_update_delegates(self, app_setup):
        _, registry, llm_manager = app_setup
        agent = LocalAgent(
            name="a",
            description="d",
            llm_service=_StubLLM(
                model="gpt-5", provider_name="openai", registry=registry
            ),
            services={"llm_manager": llm_manager},
            tools=[],
        )
        sentinel = _StubLLM(model="gpt-4o", provider_name="openai", registry=registry)

        async def run():
            return await agent.update_llm_service_async(sentinel)

        import asyncio

        assert asyncio.run(run()) is True
        assert agent.llm is sentinel
