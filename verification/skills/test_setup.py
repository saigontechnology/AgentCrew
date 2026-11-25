"""Verify the skill service shares the existing project-plugin trust decision."""

import sys
from unittest.mock import Mock, patch

import pytest

from AgentCrew.modules.llm.model_selection import RuntimeModelInput
from AgentCrew.setup import ApplicationSetup
from verification.memory import test_auxiliary_services_stats as auxiliary


@pytest.fixture
def configured_setup(monkeypatch, tmp_path):
    routing = auxiliary.routing.__wrapped__(monkeypatch)
    return auxiliary.configured_setup.__wrapped__(monkeypatch, tmp_path, routing)


@pytest.mark.parametrize(
    "config_value,override,expected",
    [
        (False, None, False),
        (True, None, False),
        (True, False, False),
        (False, True, True),
    ],
)
def test_trust_flag_is_shared(config_value, override, expected):
    with (
        patch("AgentCrew.setup.GlobalConfig") as config,
        patch("AgentCrew.modules.events.PluginManager") as plugins,
    ):
        config.return_value.read.return_value = {
            "global_settings": {"trusted_project_plugins": config_value}
        }
        instance = ApplicationSetup(
            config_manager=Mock(), trusted_project_plugins=override
        )
    assert instance._trusted_project is expected
    plugins.assert_called_once_with(
        project_plugins_dir=str(instance._project_root / ".agentcrew/plugins"),
        trusted_project_plugins=expected,
    )


@pytest.mark.parametrize("trusted", [False, True])
def test_service_setup_receives_the_resolved_trust(
    configured_setup, monkeypatch, tmp_path, trusted
):
    instance, _, _, settings = configured_setup
    instance._trusted_project = trusted
    instance._project_root = tmp_path
    settings["global_settings"]["tool_result_summary_enabled"] = False
    factory = Mock()
    monkeypatch.setattr(
        sys.modules["AgentCrew.modules.skills"], "SkillsService", factory
    )
    services = instance.setup_services(
        RuntimeModelInput("openai_codex", True, "gpt-6.1-sol", None),
        need_memory=False,
    )
    factory.assert_called_once_with(project_root=tmp_path, trusted_project=trusted)
    assert services["skills"] is factory.return_value
