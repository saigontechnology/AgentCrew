"""The regular and Docker CLIs share trust overrides and local revocation."""

import json
from importlib import import_module
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from AgentCrew import app as app_code
from AgentCrew.modules.config import project_trust as trust


@pytest.fixture(params=["AgentCrew.main", "AgentCrew.main_docker"])
def cli_module(request, monkeypatch):
    from loguru import logger

    monkeypatch.setattr(logger, "remove", Mock())
    monkeypatch.setattr(logger, "add", Mock())
    return import_module(request.param)


@pytest.fixture(
    params=[
        ("chat", "run_console"),
        ("job", "run_job"),
        ("acp", "run_acp"),
        ("a2a-server", "run_server"),
    ]
)
def entrypoint(request):
    return request.param


def test_cli_exposes_revoke_but_no_grant_endpoint(cli_module):
    result = CliRunner().invoke(cli_module.cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "revoke-project-trust" in result.output
    assert "grant-project-trust" not in cli_module.cli.commands


def test_cli_revokes_without_starting_application(cli_module, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    store = trust.ProjectTrustStore(tmp_path / "user-state")
    store.grant(project)
    monkeypatch.setattr(trust, "ProjectTrustStore", lambda: store)
    application = Mock(side_effect=AssertionError("Application must not start"))
    monkeypatch.setattr(app_code, "AgentCrewApplication", application)
    result = CliRunner().invoke(cli_module.cli, ["revoke-project-trust", str(project)])
    assert result.exit_code == 0, result.output
    assert "Revoked trust" in result.output
    assert json.loads(store.path.read_text())["projects"] == []
    application.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], None),
        (["--trusted-project"], True),
        (["--no-trusted-project"], False),
        (["--trusted-project", "--no-trusted-project"], False),
        (["--no-trusted-project", "--trusted-project"], True),
    ],
)
def test_entrypoint_forwards_tristate_without_granting(
    cli_module, entrypoint, tmp_path, monkeypatch, arguments, expected
):
    monkeypatch.chdir(tmp_path)
    application = Mock()
    factory = Mock(return_value=application)
    monkeypatch.setattr(app_code, "AgentCrewApplication", factory)
    if hasattr(cli_module, "_start_version_check_background"):
        monkeypatch.setattr(
            cli_module,
            "_start_version_check_background",
            Mock(return_value=({}, Mock())),
        )
    if hasattr(cli_module, "_show_version_result"):
        monkeypatch.setattr(
            cli_module, "_show_version_result", Mock(return_value=False)
        )
    if hasattr(cli_module, "check_and_update"):
        monkeypatch.setattr(cli_module, "check_and_update", Mock())
    command_name, method_name = entrypoint
    command = [command_name]
    if command_name == "chat" and cli_module.__name__ == "AgentCrew.main":
        command.append("--console")
    if command_name == "job":
        command.append("Task")
    result = CliRunner().invoke(cli_module.cli, [*command, *arguments])
    assert result.exit_code == 0, result.output
    factory.assert_called_once_with(expected)
    getattr(application, method_name).assert_called_once()
    assert not (tmp_path / ".AgentCrew" / "project-trust.json").exists()


def test_entrypoint_help_describes_project_plugins_and_skills(cli_module, entrypoint):
    command_name, _ = entrypoint
    result = CliRunner().invoke(cli_module.cli, [command_name, "--help"])
    assert result.exit_code == 0, result.output
    assert "--trusted-project / --no-trusted-project" in result.output
    assert "project plugins and skills" in " ".join(result.output.split())
    option = next(
        option
        for option in cli_module.cli.commands[command_name].params
        if option.name == "trusted_project_plugins"
    )
    assert option.opts == ["--trusted-project"]
    assert option.secondary_opts == ["--no-trusted-project"]
    assert option.default is None


@pytest.mark.parametrize(
    "old_option", ["--trusted-project-plugins", "--no-trusted-project-plugins"]
)
def test_entrypoint_rejects_old_trust_options(
    cli_module, entrypoint, monkeypatch, old_option
):
    command_name, _ = entrypoint
    factory = Mock(side_effect=AssertionError("Application must not start"))
    monkeypatch.setattr(app_code, "AgentCrewApplication", factory)
    result = CliRunner().invoke(cli_module.cli, [command_name, old_option])
    assert result.exit_code == 2, result.output
    assert "No such option" in result.output
    assert old_option in result.output
    factory.assert_not_called()
