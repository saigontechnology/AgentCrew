"""Tool-round configuration precedence, validation, and per-turn snapshots."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest
from loguru import logger

from AgentCrew.modules.agents.message_metadata import is_tool_loop_finalization
from AgentCrew.modules.agents.tool_loop_guard import (
    DEFAULT_MAX_TOOL_ROUNDS,
    ToolLoopGuard,
)
from AgentCrew.modules.config.global_config import GlobalConfig


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("AGENTCREW_CONFIG_PATH", str(path))
    monkeypatch.delenv("AGENTCREW_MAX_TOOL_ROUNDS", raising=False)
    config = GlobalConfig()
    monkeypatch.setattr(config, "_cached_config", None)
    monkeypatch.setattr(config, "_cached_mtime", None)
    return path


@pytest.fixture
def warnings():
    messages = []
    sink = logger.add(
        lambda message: messages.append(message.record["message"]), level="WARNING"
    )
    try:
        yield messages
    finally:
        logger.remove(sink)


def write_config(path, value):
    path.write_text(json.dumps({"global_settings": {"max_tool_rounds": value}}))


def test_absent_environment_and_config_default_to_exactly_200(config_path, warnings):
    assert DEFAULT_MAX_TOOL_ROUNDS == 200
    assert ToolLoopGuard().max_tool_rounds == 200
    assert GlobalConfig().read()["global_settings"]["max_tool_rounds"] == 200
    assert not config_path.exists()
    assert not warnings


def test_default_budget_allows_200_rounds_before_finalization(config_path):
    guard = ToolLoopGuard()
    for index in range(200):
        decision = guard.check_round(
            [{"id": str(index), "name": str(index % 2), "input": {}}]
        )
        assert decision.errors == (None,)
        assert not decision.finalization_required
    decision = guard.check_round([{"id": "201", "name": "new", "input": {}}])
    assert decision.finalization_required
    assert decision.max_tool_rounds == 200
    assert (
        "tool budget of 200 model tool rounds"
        in decision.recovery_message([])["content"][0]["text"]
    )
    assert guard.start_request() is False


@pytest.mark.parametrize(
    "contents", [{}, {"global_settings": {}}, {"global_settings": {"theme": "light"}}]
)
def test_old_config_without_budget_uses_default_without_rewriting(
    config_path, contents, warnings
):
    config_path.write_text(json.dumps(contents))
    original = config_path.read_bytes()
    assert ToolLoopGuard().max_tool_rounds == 200
    assert config_path.read_bytes() == original
    assert not warnings


def test_explicit_override_wins_without_reading_environment_or_config(
    config_path, monkeypatch, warnings
):
    write_config(config_path, 7)
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", "9")
    resolver = MagicMock(
        side_effect=AssertionError("must not resolve config for explicit override")
    )
    monkeypatch.setattr(GlobalConfig, "get_max_tool_rounds", resolver)
    assert ToolLoopGuard(max_tool_rounds=3).max_tool_rounds == 3
    resolver.assert_not_called()
    assert not warnings


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.0, 2.5, "3"])
def test_invalid_explicit_overrides_raise(config_path, value):
    with pytest.raises(ValueError, match="max_tool_rounds must be a positive integer"):
        ToolLoopGuard(max_tool_rounds=value)


@pytest.mark.parametrize("value", ["11", "  11 \n", "+11"])
def test_valid_environment_overrides_config(config_path, monkeypatch, value, warnings):
    write_config(config_path, 7)
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", value)
    assert ToolLoopGuard().max_tool_rounds == 11
    assert not warnings


def test_valid_environment_does_not_need_to_read_config(config_path, monkeypatch):
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", "8")
    read = MagicMock(side_effect=AssertionError("environment must take precedence"))
    monkeypatch.setattr(GlobalConfig, "read", read)
    assert ToolLoopGuard().max_tool_rounds == 8
    read.assert_not_called()


@pytest.mark.parametrize("value", [1, 7, 300])
def test_config_is_used_when_environment_is_absent(config_path, value, warnings):
    write_config(config_path, value)
    assert ToolLoopGuard().max_tool_rounds == value
    assert not warnings


@pytest.mark.parametrize(
    "value", ["invalid", "", "  ", "0", "-1", "2.0", "1e2", "true"]
)
def test_invalid_environment_warns_and_falls_back_to_config(
    config_path, monkeypatch, value, warnings
):
    write_config(config_path, 7)
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", value)
    assert ToolLoopGuard().max_tool_rounds == 7
    assert len(warnings) == 1
    assert "AGENTCREW_MAX_TOOL_ROUNDS" in warnings[0]


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.0, 2.5, "3", None, [], {}])
def test_invalid_config_warns_and_falls_back_to_default(config_path, value, warnings):
    write_config(config_path, value)
    assert ToolLoopGuard().max_tool_rounds == 200
    assert len(warnings) == 1
    assert "global_settings.max_tool_rounds" in warnings[0]


@pytest.mark.parametrize("settings", [None, [], "invalid"])
def test_invalid_global_settings_falls_back_to_default(config_path, settings, warnings):
    config_path.write_text(json.dumps({"global_settings": settings}))
    assert ToolLoopGuard().max_tool_rounds == 200
    assert len(warnings) == 1


def test_invalid_environment_and_config_both_warn_and_use_default(
    config_path, monkeypatch, warnings
):
    write_config(config_path, False)
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", "-3")
    assert ToolLoopGuard().max_tool_rounds == 200
    assert len(warnings) == 2


def test_environment_changes_only_affect_new_guards(config_path, monkeypatch):
    write_config(config_path, 7)
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", "5")
    original = ToolLoopGuard()
    monkeypatch.setenv("AGENTCREW_MAX_TOOL_ROUNDS", "9")
    changed = ToolLoopGuard()
    monkeypatch.delenv("AGENTCREW_MAX_TOOL_ROUNDS")
    from_config = ToolLoopGuard()
    assert (
        original.max_tool_rounds,
        changed.max_tool_rounds,
        from_config.max_tool_rounds,
    ) == (5, 9, 7)


def test_config_mtime_changes_affect_new_guards_not_active_turn_or_prompt(config_path):
    write_config(config_path, 3)
    original = ToolLoopGuard()
    previous = config_path.stat()
    write_config(config_path, 9)
    os.utime(config_path, (previous.st_atime, previous.st_mtime + 2))
    changed = ToolLoopGuard()
    assert original.max_tool_rounds == 3
    assert changed.max_tool_rounds == 9
    for index in range(original.max_tool_rounds):
        decision = original.check_round(
            [{"id": str(index), "name": str(index % 2), "input": {}}]
        )
        assert not decision.finalization_required
    blocked = [{"id": "overflow", "name": "new", "input": {}}]
    decision = original.check_round(blocked)
    assert decision.finalization_required
    retained, decision = decision.omit_blocked_calls(blocked)
    assert retained == []
    assert decision.max_tool_rounds == 3
    recovery = decision.recovery_message([])
    assert is_tool_loop_finalization(recovery)
    assert "tool budget of 3 model tool rounds" in recovery["content"][0]["text"]
    assert original.start_request() is False


def test_filtering_repeat_blocked_calls_preserves_resolved_budget():
    guard = ToolLoopGuard(max_tool_rounds=9)
    tools = [{"id": "repeat", "name": "read_file", "input": {}}]
    guard.check_round(tools)
    guard.check_round(tools)
    retained, decision = guard.check_round(tools).omit_blocked_calls(tools)
    assert retained == []
    assert decision.needs_recovery
    assert decision.max_tool_rounds == 9
