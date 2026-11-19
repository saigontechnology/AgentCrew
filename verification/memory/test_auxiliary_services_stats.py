"""Auxiliary routing/setup and shared stats contracts using fake clients only."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.events import AppEvents
from AgentCrew.modules.llm.model_registry import ModelRegistry
from AgentCrew.modules.llm.model_selection import RuntimeModelInput
from AgentCrew.modules.llm.service_manager import ServiceManager
from AgentCrew.modules.llm.types import Model

MATRIX = [
    ("openai_codex", "gpt-6.1-sol", "openai_codex", True),
    ("openai", "chat", "openai", True),
    ("openai", "responses", "openai_response", False),
    ("opencode_go", "chat", "opencode_go", True),
    ("opencode_go", "messages", "opencode_anthropic", False),
    ("github_copilot", "chat", "github_copilot", True),
    ("github_copilot", "responses", "copilot_response", False),
    ("commandcode", "chat", "commandcode", True),
    ("commandcode", "messages", "commandcode_anthropic", False),
    ("claude", "haiku", "claude", True),
    ("google", "flash", "google", True),
]


class FakeService:
    def __init__(self, provider, family):
        self.provider_name = provider
        self.model = "factory-default"
        self._service_family = family
        self.auth_marker = "oauth" if provider == "openai_codex" else "api-key"
        self.api_key = "PRIVATE_KEY"
        self.base_url = "https://private:credential@host/secret"
        self.reasoning_effort = "constructor-default"
        self.thinking = []
        self.closed = False

    def set_think(self, value):
        self.thinking.append(value)
        self.reasoning_effort = value
        return True

    async def close(self):
        self.closed = True


class FakeRegistry:
    def __init__(self):
        self.models = {
            f"{provider}/{model_id}": Model(
                id=model_id,
                provider=provider,
                name="Fictional",
                description="Fake client matrix",
                capabilities=["thinking"],
                service_name=family,
                default=default,
                default_reasoning="high",
            )
            for provider, model_id, family, default in MATRIX
        }

    def get_model(self, key):
        return self.models.get(key)

    def get_models_by_provider(self, provider):
        return [model for model in self.models.values() if model.provider == provider]

    def set_current_model(self, key):
        self.current_model = self.models[key]


@pytest.fixture
def routing(monkeypatch):
    registry = FakeRegistry()
    manager = ServiceManager.__new__(ServiceManager)
    manager.services = {}
    manager.custom_provider_details = {}
    manager._pending_closes = set()
    manager._closed_services = set()
    manager.service_factories = {
        family: (lambda provider=provider, family=family: FakeService(provider, family))
        for provider, _id, family, _default in MATRIX
    }
    monkeypatch.setattr(ModelRegistry, "_instance", registry)
    monkeypatch.setattr(ModelRegistry, "get_instance", lambda: registry)
    monkeypatch.setattr(ServiceManager, "get_instance", lambda: manager)
    return manager, registry


def primary(manager, registry, key="openai_codex/gpt-6.1-sol"):
    model = registry.get_model(key)
    result = manager.initialize_standalone_service_for_model(model)
    result.model = model.id
    result.reasoning_effort = "high"
    return result


@pytest.mark.parametrize("provider,model_id,family,_default", MATRIX)
@pytest.mark.parametrize("override", [False, True])
def test_model_service_matrix_preserves_auth_selection_and_isolation(
    routing, provider, model_id, family, _default, override
):
    manager, registry = routing
    main = primary(manager, registry, f"{provider}/{model_id}")
    auxiliary = manager.initialize_auxiliary_service(
        main, provider if override else None
    )
    identity = manager.describe_service(auxiliary)
    assert auxiliary is not main
    assert identity["provider"] == provider and identity["model"] == model_id
    assert identity["service_family"] == family
    assert auxiliary.auth_marker == main.auth_marker
    assert main.reasoning_effort == "high"
    assert auxiliary.thinking[-1] in ("none", "0")
    assert "high" not in auxiliary.thinking


@pytest.mark.parametrize(
    "override",
    ["openai", "opencode_go", "github_copilot", "commandcode", "claude", "google"],
)
def test_different_provider_uses_registered_default_not_main_id(routing, override):
    manager, registry = routing
    main = primary(manager, registry)
    result = manager.initialize_auxiliary_service(main, override)
    expected = next(
        model for model in registry.get_models_by_provider(override) if model.default
    )
    assert (result.provider_name, result.model, result._service_family) == (
        expected.provider,
        expected.id,
        expected.resolved_service_name(),
    )
    assert result.model != main.model


def test_legacy_copilot_responses_override_is_model_aware(routing):
    manager, registry = routing
    result = manager.initialize_auxiliary_service(
        primary(manager, registry), "copilot_response"
    )
    assert (result.provider_name, result.model, result._service_family) == (
        "github_copilot",
        "responses",
        "copilot_response",
    )
    repeated = manager.initialize_auxiliary_service(result, "copilot_response")
    assert repeated.model == result.model


def test_raw_runtime_model_retains_recorded_family(routing):
    manager, _registry = routing
    main = manager.initialize_standalone_service("openai_response")
    main.model = "raw-unregistered"
    result = manager.initialize_auxiliary_service(main)
    assert (result.provider_name, result.model, result._service_family) == (
        "openai",
        "raw-unregistered",
        "openai_response",
    )


def test_unknown_override_fails_without_incompatible_client(routing):
    manager, registry = routing
    with pytest.raises(ValueError, match="no registered models"):
        manager.initialize_auxiliary_service(primary(manager, registry), "absent")


def test_actual_registry_definitions_represent_requested_families():
    from AgentCrew.modules.llm.model_registry import PROVIDER_MODEL_MODULES

    families = set()
    for provider in {row[0] for row in MATRIX}:
        module, variable = PROVIDER_MODEL_MODULES[provider]
        families.update(
            model.resolved_service_name()
            for model in getattr(importlib.import_module(module), variable)
        )
    assert {row[2] for row in MATRIX} - {"openai"} <= families


def test_stats_snapshot_codex_primary_and_memory(routing):
    manager, registry = routing
    main = primary(manager, registry)
    memory = SimpleNamespace(llm_service=manager.initialize_auxiliary_service(main))
    services = {"llm": main, "memory": memory, "tool_result_summary": None}
    report = UtilityCommands._format_llm_services(services, main, memory)
    assert report == (
        "LLM-backed service roles (instantiated local services):\n"
        "  Primary/current chat: provider=openai_codex | service=openai_codex (FakeService) | model=openai_codex/gpt-6.1-sol\n"
        "  Memory extraction: provider=openai_codex | service=openai_codex (FakeService) | model=openai_codex/gpt-6.1-sol\n"
        "  Tool-result summary: not configured"
    )
    assert "same as primary" not in report
    assert "PRIVATE" not in report and "https://" not in report


def test_stats_cross_provider_separate_summary_disabled_and_shared_roles(routing):
    manager, registry = routing
    main = primary(manager, registry)
    memory = SimpleNamespace(
        llm_service=manager.initialize_auxiliary_service(main, "claude")
    )
    summary = SimpleNamespace(
        llm_service=manager.initialize_auxiliary_service(main, "google")
    )
    services = {
        "llm": main,
        "memory": memory,
        "tool_result_summary": summary,
        "code_analysis": SimpleNamespace(llm_service=main),
    }
    rows = {
        row["role"]: row
        for row in manager.describe_service_roles(services, primary=main)
    }
    assert rows["Memory extraction"]["qualified_model"] == "claude/haiku"
    assert rows["Tool-result summary"]["qualified_model"] == "google/flash"
    assert rows["Code analysis"]["status"] == "same as primary"
    disabled = UtilityCommands._format_llm_services(
        {"memory": None, "llm_service_status": {"Tool-result summary": "disabled"}},
        main,
    )
    assert "Memory extraction: disabled" in disabled
    assert "Tool-result summary: disabled" in disabled
    assert "PRIVATE" not in disabled


def test_stats_uses_current_agent_not_stale_startup_and_emits_shared_event(routing):
    manager, registry = routing
    startup = primary(manager, registry)
    current = primary(manager, registry, "openai/responses")
    services = {"llm": startup, "memory": None}
    bus = SimpleNamespace(emit_sync=Mock())
    handler = SimpleNamespace(
        agent=SimpleNamespace(name="Current", llm=current, services=services),
        agent_manager=SimpleNamespace(agents={}),
        memory_service=None,
        bus=bus,
    )
    result = UtilityCommands(handler).handle_stats("/stats")
    assert result.handled
    (event,) = bus.emit_sync.call_args.args
    assert event == AppEvents.SYSTEM_MESSAGE
    report = bus.emit_sync.call_args.kwargs["message"]
    assert "Primary/current chat: provider=openai | service=openai_response" in report
    assert "Primary setup: provider=openai_codex" in report
    assert "Current conversation total:" in report
    assert "PRIVATE" not in report and "https://" not in report


def test_stats_no_local_chat_does_not_misidentify_startup_as_current(routing):
    manager, registry = routing
    main = primary(manager, registry)
    rows = manager.describe_service_roles({"llm": main}, primary=None)
    assert rows[0]["status"] == "not configured"
    assert (
        next(row for row in rows if row["role"] == "Primary setup")["qualified_model"]
        == "openai_codex/gpt-6.1-sol"
    )


@pytest.mark.parametrize(
    "need_memory,override,summary_enabled",
    [(True, None, True), (True, "claude", True), (False, None, False)],
)
def test_setup_calls_shared_auxiliary_resolver(
    monkeypatch, tmp_path, routing, need_memory, override, summary_enabled
):
    from AgentCrew import setup
    from AgentCrew.modules.agents.manager import AgentManager

    manager, _registry = routing
    resolve = Mock(wraps=manager.initialize_auxiliary_service)
    monkeypatch.setattr(manager, "initialize_auxiliary_service", resolve)
    settings = {"global_settings": {"tool_result_summary_enabled": summary_enabled}}
    monkeypatch.setattr(
        setup,
        "GlobalConfig",
        lambda: SimpleNamespace(
            read=lambda: settings,
            get_last_used_model=lambda: None,
            get_last_used_provider=lambda: None,
        ),
    )

    class Empty:
        def __init__(self, *args, **kwargs):
            self.llm_service = kwargs.get("llm_service")

        @staticmethod
        def get_instance():
            return Empty()

    def module(name, **attributes):
        result = ModuleType(name)
        result.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, result)

    for name, cls in (
        ("clipboard", "ClipboardService"),
        ("web_search", "TavilySearchService"),
        ("code_analysis", "CodeAnalysisService"),
        ("browser_automation", "BrowserAutomationService"),
        ("file_editing", "FileEditingService"),
        ("command_execution", "CommandExecutionService"),
        ("skills", "SkillsService"),
    ):
        module(f"AgentCrew.modules.{name}", **{cls: Empty})

    def no_image():
        raise ValueError("fictional disabled image service")

    module("AgentCrew.modules.image_generation", ImageGenerationService=no_image)
    module(
        "AgentCrew.modules.memory.context_persistent", ContextPersistenceService=Empty
    )
    module(
        "AgentCrew.modules.memory.engine_selection",
        resolve_memory_engine=lambda _settings: "sqlite",
        ensure_sqlite_store=lambda *_args, **_kw: tmp_path / "fake.db",
        sqlite_store_path=lambda: tmp_path / "fake.db",
        chroma_source_path=lambda: tmp_path,
        sqlite_migration_work_path=lambda: tmp_path,
    )
    module("AgentCrew.modules.memory.sqlite_service", SQLiteMemoryService=Empty)
    import AgentCrew.modules.agents.tool_result_summary as summaries

    monkeypatch.setattr(
        summaries,
        "ToolResultSummaryService",
        lambda llm, context: SimpleNamespace(llm_service=llm),
    )
    monkeypatch.setattr(AgentManager, "get_instance", lambda: SimpleNamespace())
    instance = setup.ApplicationSetup.__new__(setup.ApplicationSetup)
    instance._standalone_llm_services = []
    services = instance.setup_services(
        RuntimeModelInput("openai_codex", True, "gpt-6.1-sol", None),
        memory_llm=override,
        need_memory=need_memory,
    )
    assert resolve.call_count == int(need_memory) + int(summary_enabled)
    for call in resolve.call_args_list:
        assert call.args == (services["llm"], override)
    if need_memory:
        expected = "claude/haiku" if override else "openai_codex/gpt-6.1-sol"
        assert (
            manager.describe_service(services["memory"].llm_service)["qualified_model"]
            == expected
        )
    else:
        assert services["llm_service_status"]["Memory extraction"] == "disabled"
        assert services["llm_service_status"]["Tool-result summary"] == "disabled"


def test_memory_and_summary_constructors_do_not_replace_selected_model(
    monkeypatch, tmp_path, routing
):
    from AgentCrew.modules.agents.tool_result_summary import ToolResultSummaryService
    from AgentCrew.modules.memory.chroma_service import ChromaMemoryService
    from AgentCrew.modules.memory.memory_worker import MemoryWorker

    manager, registry = routing
    main = primary(manager, registry)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORYDB_PATH", str(tmp_path / "fictional-memory"))
    monkeypatch.setattr(MemoryWorker, "start", lambda _self: None)
    memory = ChromaMemoryService(llm_service=main)
    assert memory.llm_service.model == "gpt-6.1-sol"
    summary = ToolResultSummaryService(main)
    try:
        assert summary.llm_service.model == "gpt-6.1-sol"
    finally:
        assert summary.close()


def test_existing_command_dispatcher_routes_stats_to_shared_handler():
    import asyncio

    from AgentCrew.modules.chat.message.command_processor import CommandProcessor

    result = object()
    command = CommandProcessor.__new__(CommandProcessor)
    command.utility_commands = SimpleNamespace(handle_stats=Mock(return_value=result))
    assert asyncio.run(command.process_command("/stats")) is result
    command.utility_commands.handle_stats.assert_called_once_with("/stats")


def test_gui_routes_stats_without_creating_another_implementation(monkeypatch):
    package = ModuleType("AgentCrew.modules.gui")
    package.__path__ = [str(ROOT / "AgentCrew/modules/gui")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    module = importlib.import_module("AgentCrew.modules.gui.components.command_handler")
    command = module.CommandHandler.__new__(module.CommandHandler)
    emit = Mock()
    enabled = Mock()
    command.chat_window = SimpleNamespace(
        llm_worker=SimpleNamespace(process_request=SimpleNamespace(emit=emit)),
        ui_state_manager=SimpleNamespace(set_input_controls_enabled=enabled),
        display_error=Mock(),
    )
    assert command.process_command("/stats")
    emit.assert_called_once_with("/stats")
    enabled.assert_called_once_with(True)
    command.chat_window.display_error.assert_not_called()


def test_completers_and_help_advertise_existing_stats_command():
    import ast

    for relative in (
        "AgentCrew/modules/gui/components/completers.py",
        "AgentCrew/modules/console/completers.py",
    ):
        tree = ast.parse((ROOT / relative).read_text())
        assert (
            sum(
                isinstance(node, ast.Constant) and node.value == "/stats"
                for node in ast.walk(tree)
            )
            == 1
        )
    assert (ROOT / "AgentCrew/modules/console/constants.py").read_text().count(
        "Use '/stats'"
    ) == 1


def test_registered_nested_model_ids_remain_visible(routing):
    manager, registry = routing
    model = Model(
        id="vendor/registered-model",
        provider="google",
        name="Fictional",
        description="Registered hierarchical ID",
        capabilities=[],
    )
    registry.models["google/vendor/registered-model"] = model
    service = manager.initialize_standalone_service("google")
    service.model = model.id
    assert (
        manager.describe_service(service)["qualified_model"]
        == "google/vendor/registered-model"
    )


def test_provider_without_explicit_default_uses_first_registered_model(routing):
    manager, registry = routing
    models = registry.get_models_by_provider("commandcode")
    for model in models:
        model.default = False
    result = manager.initialize_auxiliary_service(
        primary(manager, registry), "commandcode"
    )
    assert result.model == models[0].id
    assert result._service_family == models[0].resolved_service_name()
