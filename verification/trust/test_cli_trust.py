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
        (["--trusted-project-plugins"], True),
        (["--no-trusted-project-plugins"], False),
    ],
)
def test_chat_forwards_tristate_without_granting(
    cli_module, tmp_path, monkeypatch, arguments, expected
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
    command = ["chat"]
    if cli_module.__name__ == "AgentCrew.main":
        command.append("--console")
    result = CliRunner().invoke(cli_module.cli, [*command, *arguments])
    assert result.exit_code == 0, result.output
    factory.assert_called_once_with(expected)
    application.run_console.assert_called_once()
    assert not (tmp_path / ".AgentCrew" / "project-trust.json").exists()
