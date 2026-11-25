"""Consent precedes project-sensitive startup without changing headless policy."""

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from AgentCrew import app as app_module
from AgentCrew import setup as setup_code
from AgentCrew.modules.config.project_trust import ProjectTrustStore
from AgentCrew.setup import ApplicationSetup


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def make_setup(project, tmp_path, monkeypatch):
    settings = Mock()
    settings.read.return_value = {"global_settings": {"trusted_project_plugins": True}}
    monkeypatch.setattr(setup_code, "GlobalConfig", lambda: settings)
    manager_type = Mock()
    monkeypatch.setattr("AgentCrew.modules.events.PluginManager", manager_type)

    def make(override=None):
        setup = ApplicationSetup(Mock(), override)
        setup._project_trust_resolver.store = ProjectTrustStore(tmp_path / "user-state")
        return setup, manager_type

    return make


@pytest.mark.parametrize(
    "override, expected", [(None, False), (True, True), (False, False)]
)
def test_repository_config_cannot_grant_and_cli_override_is_initial(
    make_setup, project, override, expected
):
    setup, managers = make_setup(override)
    assert setup._trusted_project is expected
    assert setup._project_root == project.resolve()
    assert managers.call_args.kwargs == {
        "project_plugins_dir": str(project / ".agentcrew" / "plugins"),
        "trusted_project_plugins": expected,
    }
    assert not setup._project_trust_resolver.store.path.exists()


def test_root_does_not_drift_with_later_cwd(make_setup, project, tmp_path, monkeypatch):
    setup, managers = make_setup()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    prompt = Mock(return_value=True)
    assert setup.resolve_interactive_project_trust(
        interactive=True, prompt=prompt, diagnostic=Mock()
    )
    prompt.assert_called_once_with(project.resolve())
    assert setup._project_root == project.resolve()
    assert managers.call_args.kwargs["project_plugins_dir"] == str(
        project / ".agentcrew" / "plugins"
    )
    assert setup._project_trust_resolver.store.is_trusted(project)
    assert not setup._project_trust_resolver.store.is_trusted(other)


def test_headless_setup_does_not_inherit_stored_interactive_consent(
    make_setup, project
):
    setup, _ = make_setup()
    setup._project_trust_resolver.store.grant(project)
    assert not setup._trusted_project
    with patch.object(
        setup._project_trust_resolver.store,
        "is_trusted",
        side_effect=AssertionError("headless read"),
    ):
        assert not setup.resolve_interactive_project_trust(
            interactive=False, prompt=Mock(), diagnostic=Mock()
        )


def test_prompt_cannot_run_after_services_or_plugins_started(make_setup):
    setup, _ = make_setup()
    setup.services = {}
    prompt = Mock(return_value=True)
    with pytest.raises(RuntimeError, match="before"):
        setup.resolve_interactive_project_trust(
            interactive=True, prompt=prompt, diagnostic=Mock()
        )
    prompt.assert_not_called()


def build_app(setup, events, monkeypatch):
    from AgentCrew.modules.skills import SkillsService

    manager = Mock()
    services = {"agent_manager": manager, "memory": None, "context_persistent": None}

    def services_start(*args, **kwargs):
        events.append("skills")
        services["skills"] = SkillsService(
            project_root=setup._project_root,
            user_root=setup._project_root.parent / "user-skills",
            trusted_project=setup._trusted_project,
        )
        setup.services = services
        return services

    setup.resolve_runtime_model = Mock(
        return_value=SimpleNamespace(provider="mock", model=None)
    )
    setup.setup_services = services_start
    setup.setup_agents = Mock(
        side_effect=lambda *args, **kwargs: events.append("agents") or services
    )
    setup.restore_last_agent = Mock()
    setup.shutdown = AsyncMock()

    async def plugins_start():
        events.append("plugins")

    setup.initialize_plugins = plugins_start
    application = app_module.AgentCrewApplication.__new__(
        app_module.AgentCrewApplication
    )
    application.setup = setup
    application.config_manager = Mock()
    application.config_manager.read_config.return_value = {"user_prompts": []}
    application.interactive_runtime = Mock()
    application.interactive_runtime.start.side_effect = lambda: events.append("runtime")
    application.interactive_runtime.run.side_effect = asyncio.run
    application.interactive_runtime.shutdown.side_effect = asyncio.run
    monkeypatch.setattr(
        app_module,
        "InteractiveAsyncRuntime",
        Mock(return_value=application.interactive_runtime),
    )
    application._run_async_cleanup = asyncio.run
    application._shutdown_started = False
    application._shutdown_complete = False
    application._console_ui = None
    console = Mock()
    console.interactive_chat.side_effect = lambda: events.append("ui")
    monkeypatch.setattr(
        "AgentCrew.modules.console.ConsoleUI", Mock(return_value=console)
    )
    monkeypatch.setattr("AgentCrew.modules.chat.MessageHandler", Mock())
    return application, services


@pytest.mark.parametrize("accept", [True, False])
def test_console_entry_decides_before_discovery_and_plugins(
    make_setup, project, monkeypatch, accept
):
    from AgentCrew.modules.config import project_trust

    setup, _ = make_setup()
    events = []
    skill = project / ".agents" / "skills" / "local"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: local\ndescription: Local task\n---\nInstructions"
    )
    application, services = build_app(setup, events, monkeypatch)
    monkeypatch.setattr(project_trust, "console_can_prompt", lambda: True)
    prompt = Mock(side_effect=lambda _: events.append("prompt") or accept)
    monkeypatch.setattr(project_trust, "confirm_project_trust_console", prompt)
    application.run_console("mock")
    assert events.index("prompt") < events.index("skills") < events.index("plugins")
    assert setup._trusted_project is accept
    assert services["skills"].project_root == project
    assert services["skills"].get_skill_names() == (["local"] if accept else [])
    assert setup._project_trust_resolver.store.path.exists() is accept


def test_nonterminal_console_never_prompts_or_reads_grants(
    make_setup, project, monkeypatch
):
    from AgentCrew.modules.config import project_trust

    setup, _ = make_setup()
    setup._project_trust_resolver.store.grant(project)
    events = []
    application, services = build_app(setup, events, monkeypatch)
    monkeypatch.setattr(project_trust, "console_can_prompt", lambda: False)
    prompt = Mock(side_effect=AssertionError("no stdin prompt"))
    monkeypatch.setattr(project_trust, "confirm_project_trust_console", prompt)
    application.run_console("mock")
    prompt.assert_not_called()
    assert not services["skills"].trusted_project


@pytest.mark.parametrize("accept", [True, False])
def test_gui_entry_decides_with_one_qapp_and_no_stdin(make_setup, monkeypatch, accept):
    from AgentCrew.modules.config import project_trust

    setup, _ = make_setup()
    events = []
    application, services = build_app(setup, events, monkeypatch)
    qt_app = Mock()
    qt_app.exec.return_value = 0
    create_app = Mock(side_effect=lambda: events.append("qapp") or qt_app)
    monkeypatch.setattr(app_module, "_gui_application", create_app)
    prompt = Mock(side_effect=lambda _: events.append("prompt") or accept)
    monkeypatch.setattr(project_trust, "confirm_project_trust_qt", prompt)
    monkeypatch.setattr(
        project_trust,
        "confirm_project_trust_console",
        Mock(side_effect=AssertionError("no terminal")),
    )
    monkeypatch.setitem(
        sys.modules, "AgentCrew.modules.gui", SimpleNamespace(ChatWindow=Mock())
    )
    with pytest.raises(SystemExit) as exited:
        application.run_gui("mock")
    assert exited.value.code == 0
    assert (
        events.index("qapp")
        < events.index("prompt")
        < events.index("skills")
        < events.index("plugins")
    )
    assert services["skills"].trusted_project is accept
    create_app.assert_called_once()
    qt_app.exec.assert_called_once()


def test_headless_entry_points_do_not_resolve_interactive_consent():
    tree = ast.parse(Path(app_module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith(("run_job", "run_acp", "run_server")):
            assert not any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "resolve_interactive_project_trust"
                for call in ast.walk(node)
            ), node.name


def test_checkout_config_cannot_self_grant(tmp_path, monkeypatch):
    from AgentCrew.modules.skills.service import SkillsService

    project = tmp_path / "project"
    project.mkdir()
    config = project / "config.json"
    config.write_text('{"global_settings": {"trusted_project_plugins": true}}')
    skill = project / ".agents" / "skills" / "local"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: local\ndescription: local\n---\nPrivate body"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("AGENTCREW_CONFIG_PATH", str(config))
    plugins = Mock()
    monkeypatch.setattr("AgentCrew.modules.events.PluginManager", plugins)
    setup = ApplicationSetup(Mock())
    assert not setup._trusted_project
    assert plugins.call_args.kwargs["trusted_project_plugins"] is False
    service = SkillsService(
        project_root=setup._project_root,
        user_root=tmp_path / "home",
        trusted_project=setup._trusted_project,
    )
    assert service.get_skill_names() == []


def test_redirected_project_during_prompt_aborts_startup(make_setup, tmp_path):
    setup, plugins = make_setup()
    store = setup._project_trust_resolver.store
    sibling = tmp_path / "sibling"
    sibling.mkdir()

    def redirect(root):
        root.rename(tmp_path / "moved")
        root.symlink_to(sibling, target_is_directory=True)
        return True

    with pytest.raises(RuntimeError, match="Launch project path changed"):
        setup.resolve_interactive_project_trust(
            interactive=True, prompt=redirect, diagnostic=Mock()
        )
    assert not setup._trusted_project
    assert not store.path.exists()
    assert plugins.call_count == 1
