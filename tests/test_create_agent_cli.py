"""CLI-level regression tests for the create-agent command.

These tests exercise the command wiring only: model/provider resolution,
typed runtime input plumbing, onboarding construction, and exit codes.
All external services (setup services, onboarding) are faked.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from AgentCrew.main import create_agent_command
from AgentCrew.modules.llm.model_selection import RuntimeModelInput


class _FakeOnboarding:
    """Records constructor and create_agent calls; honors a mutable success flag."""

    def __init__(self, llm_service, agents_config=None, services=None):
        self.llm_service = llm_service
        self.services = services
        state = _FakeOnboarding.state
        state.onboarding_instances.append(self)

    def create_agent(self, name=None, description=None):
        state = _FakeOnboarding.state
        state.create_agent_calls.append((name, description))
        return state.onboarding_success


class _CliState:
    def __init__(self):
        self.detect_provider_value = "openai"
        self.detect_model_id_value = "gpt-4o-mini"
        self.setup_services_calls = []
        self.onboarding_instances = []
        self.create_agent_calls = []
        self.onboarding_success = True


@pytest.fixture
def cli_env(monkeypatch):
    from AgentCrew.modules import onboarding as onboarding_module
    from AgentCrew.setup import ApplicationSetup

    state = _CliState()
    _FakeOnboarding.state = state

    def _detect_provider(self):
        return state.detect_provider_value

    def _detect_model_id(self):
        return state.detect_model_id_value

    def _setup_services(self, runtime_model, **kwargs):
        state.setup_services_calls.append((runtime_model, kwargs))
        return {
            "llm": SimpleNamespace(provider_name="openai", model="gpt-4o"),
            "memory": None,
            "context_persistent": None,
            "voice": None,
        }

    monkeypatch.setattr(
        ApplicationSetup, "load_api_keys_from_config", lambda self: None
    )
    monkeypatch.setattr(ApplicationSetup, "detect_provider", _detect_provider)
    monkeypatch.setattr(ApplicationSetup, "detect_model_id", _detect_model_id)
    monkeypatch.setattr(ApplicationSetup, "setup_services", _setup_services)
    monkeypatch.setattr(onboarding_module, "OnboardingService", _FakeOnboarding)
    return state


@pytest.fixture
def runner():
    return CliRunner()


def _last_runtime(state):
    runtime_model, _ = state.setup_services_calls[-1]
    return runtime_model


def test_explicit_provider_and_model_flow_to_setup_services(cli_env, runner):
    result = runner.invoke(
        create_agent_command,
        [
            "--provider",
            "openai",
            "--model-id",
            "gpt-5",
            "--name",
            "A",
            "--description",
            "B",
        ],
    )
    assert result.exit_code == 0
    runtime_model = _last_runtime(cli_env)
    assert isinstance(runtime_model, RuntimeModelInput)
    assert runtime_model.provider == "openai"
    assert runtime_model.explicit_provider is True
    assert runtime_model.explicit_model_id == "gpt-5"
    assert runtime_model.detected_model_id is None
    _, kwargs = cli_env.setup_services_calls[-1]
    assert kwargs.get("need_memory") is False
    assert kwargs.get("with_voice") is False


def test_detected_values_are_not_elevated_to_arguments(cli_env, runner):
    result = runner.invoke(create_agent_command, ["--name", "A", "--description", "B"])
    assert result.exit_code == 0
    runtime_model = _last_runtime(cli_env)
    assert isinstance(runtime_model, RuntimeModelInput)
    assert runtime_model.provider == "openai"
    assert runtime_model.explicit_provider is False
    assert runtime_model.explicit_model_id is None
    assert runtime_model.detected_model_id == "gpt-4o-mini"


def test_unresolved_provider_exits_one_without_setup(cli_env, runner):
    cli_env.detect_provider_value = None
    result = runner.invoke(create_agent_command, ["--name", "A"])
    assert result.exit_code == 1
    assert "No LLM provider configured" in result.output
    assert cli_env.setup_services_calls == []
    assert cli_env.onboarding_instances == []
    assert cli_env.create_agent_calls == []


def test_successful_setup_constructs_onboarding_and_forwards_fields(cli_env, runner):
    result = runner.invoke(
        create_agent_command,
        ["--provider", "openai", "--name", "Engineer", "--description", "Codes"],
    )
    assert result.exit_code == 0
    assert len(cli_env.onboarding_instances) == 1
    onboarding = cli_env.onboarding_instances[0]
    assert onboarding.services["llm"] is not None
    assert cli_env.create_agent_calls == [("Engineer", "Codes")]


def test_onboarding_failure_exits_one(cli_env, runner):
    cli_env.onboarding_success = False
    result = runner.invoke(
        create_agent_command,
        ["--provider", "openai", "--name", "A", "--description", "B"],
    )
    assert result.exit_code == 1
    assert cli_env.setup_services_calls
    assert cli_env.create_agent_calls == [("A", "B")]


def test_agent_config_sets_target_env_without_leaking(cli_env, runner):
    previous = os.environ.get("SW_AGENTS_CONFIG")
    try:
        result = runner.invoke(
            create_agent_command,
            [
                "--provider",
                "openai",
                "--agent-config",
                "/tmp/agentcrew-create-agent-test.toml",
                "--name",
                "A",
                "--description",
                "B",
            ],
        )
        assert result.exit_code == 0
        assert os.environ.get("SW_AGENTS_CONFIG") == (
            "/tmp/agentcrew-create-agent-test.toml"
        )
    finally:
        if previous is None:
            os.environ.pop("SW_AGENTS_CONFIG", None)
        else:
            os.environ["SW_AGENTS_CONFIG"] = previous
