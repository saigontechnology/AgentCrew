from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from AgentCrew.modules.agents.context_manager import AgentContextManager
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.message_metadata import (
    attach_context_usage,
    attach_stream_metadata,
    get_context_usage,
    get_responses_provider_state,
    strip_internal_metadata,
)
from AgentCrew.modules.llm.token_usage import TokenUsage


def _agent(tokens=340_000, *, provider="openai", model="gpt-5.4"):
    manager = SimpleNamespace(
        context_shrink_enabled=True, shrink_excluded_list=["keep"]
    )
    agent = LocalAgent("worker", "description", None, {"agent_manager": manager}, [])
    agent.llm = SimpleNamespace(_provider_name=provider, model=model)
    agent.token_usage = TokenUsage(total_input_tokens=tokens)
    agent.get_model = lambda: model
    return agent


def _snapshot(tokens, *, agent="worker", provider="openai", model="gpt-5.4"):
    message = {"role": "assistant", "agent": agent, "content": "done"}
    if tokens is not None:
        attach_context_usage(message, tokens, provider, model)
    return message


def _interaction(call_id, tool="read_file", *, rejected=False):
    return [
        {
            "role": "assistant",
            "agent": "worker",
            "tool_calls": [
                {"id": call_id, "name": tool, "arguments": {"path": call_id}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "tool_name": tool,
            "content": "large result",
            **({"is_rejected": True} if rejected else {}),
        },
    ]


def _history(tokens=(190_000, 245_000, 265_000, 300_000, 340_000)):
    messages = []
    for idx, token_count in enumerate(tokens):
        interaction = _interaction(str(idx))
        if token_count is not None:
            attach_context_usage(interaction[0], token_count, "openai", "gpt-5.4")
        messages.extend(interaction)
        messages.append(_snapshot(token_count))
    return messages


def _shrink(agent, messages, monkeypatch):
    monkeypatch.setenv("AGENTCREW_DEFAULT_MAX_CONTEXT", "400000")
    AgentContextManager(agent).shrink_tool_results(messages)


def test_floor_watermark_shrinks_only_prior_interactions(monkeypatch):
    agent = _agent()
    messages = _history()
    _shrink(agent, messages, monkeypatch)
    assert [messages[i]["role"] for i in (1, 4, 7, 10, 13)] == [
        "user",
        "user",
        "user",
        "tool",
        "tool",
    ]
    assert "tool_calls" not in messages[6]
    assert get_context_usage(messages[8])["prompt_tokens"] == 265_000


def test_equal_snapshots_can_establish_boundary(monkeypatch):
    messages = _history((190_000, 265_000, 265_000, 300_000, 340_000))
    _shrink(_agent(), messages, monkeypatch)
    assert messages[7]["role"] == "user"
    assert messages[8]["role"] == "assistant"
    assert messages[10]["role"] == "tool"


def test_boundary_does_not_truncate_tool_result_after_it(monkeypatch):
    messages = (
        [_snapshot(190_000)]
        + _interaction("old")
        + _interaction("split")
        + [_snapshot(265_000), _snapshot(340_000)]
    )
    split_result = messages.pop(4)
    messages.append(split_result)
    _shrink(_agent(), messages, monkeypatch)
    assert messages[2]["role"] == "user"
    assert split_result["role"] == "tool"
    assert split_result["content"] == "large result"
    assert any(
        tool["id"] == "split"
        for message in messages
        for tool in message.get("tool_calls", [])
    )


def test_below_trigger_leaves_history_intact(monkeypatch):
    messages = _history()
    original = copy.deepcopy(messages)
    _shrink(_agent(339_000), messages, monkeypatch)
    assert messages == original


def test_incompatible_snapshots_and_monotonic_break_fall_back(monkeypatch):
    for change, expected_boundary in (
        (lambda messages: messages[8].update(agent="other"), 6),
        (
            lambda messages: messages[8]["_metadata"]["context_usage"].update(
                model="other"
            ),
            None,
        ),
        (
            lambda messages: messages[8]["_metadata"]["context_usage"].update(
                provider="other"
            ),
            None,
        ),
        (
            lambda messages: messages[8]["_metadata"]["context_usage"].update(
                prompt_tokens=350_000
            ),
            None,
        ),
        (
            lambda messages: (
                messages[-1].pop("_metadata"),
                messages[-3].pop("_metadata"),
            ),
            None,
        ),
    ):
        messages = _history()
        change(messages)
        monkeypatch.setenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", "10")
        agent = _agent()
        assert (
            AgentContextManager(agent)._watermark_boundary(messages, 340_000, 340_000)
            == expected_boundary
        )
        _shrink(agent, messages, monkeypatch)
        assert messages[1]["role"] == "user"
        assert messages[4]["role"] == "user"
        assert messages[7]["role"] == "tool"


def test_legacy_fallback_and_emergency_pressure(monkeypatch):
    messages = _history((None,) * 5)
    monkeypatch.setenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", "10")
    _shrink(_agent(), messages, monkeypatch)
    assert messages[1]["role"] == "user"
    assert messages[4]["role"] == "user"
    assert messages[7]["role"] == "tool"

    agent = _agent(100)
    agent.input_tokens_usage = 400_000
    assert agent.token_usage.total_input_tokens == 400_000
    messages = _history()
    _shrink(agent, messages, monkeypatch)
    assert messages[1]["role"] == "user"
    assert messages[4]["role"] == "user"
    assert messages[7]["role"] == "tool"


def test_exclusions_rejected_summary_fallback_and_unique_stale(monkeypatch):
    messages = (
        [_snapshot(190_000)]
        + _interaction("excluded", "keep")
        + _interaction("skill", "activate_skill")
        + _interaction("memory", "search_memory")
        + _interaction("denied", rejected=True)
        + _interaction("ordinary")
        + [
            {"role": "tool", "content": "[UNIQUE]old"},
            {"role": "tool", "content": "[UNIQUE]new"},
        ]
        + [_snapshot(265_000), _snapshot(340_000)]
    )
    monkeypatch.setenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", "2")
    _shrink(_agent(), messages, monkeypatch)
    results = {
        msg.get("tool_call_id"): msg for msg in messages if msg.get("tool_call_id")
    }
    assert all(
        results[id]["content"] == "large result"
        for id in ("excluded", "skill", "memory")
    )
    assert "rejected by user" in results["denied"]["content"][0]["text"]
    assert "was truncated" in results["ordinary"]["content"][0]["text"]
    assert any(msg.get("content") == "[stale]" for msg in messages)
    assert any(msg.get("content") == "[UNIQUE]new" for msg in messages)


def test_metadata_merge_roundtrip_and_provider_safe():
    message = _snapshot(340_000)
    state = {
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_items": [{"type": "reasoning", "encrypted_content": "opaque"}],
    }
    attach_stream_metadata(message, state)
    assert get_context_usage(message)["prompt_tokens"] == 340_000
    assert (
        get_responses_provider_state(message)["output_items"]
        == state["reasoning_items"]
    )
    assert "_metadata" not in strip_internal_metadata([message])[0]
    assert get_context_usage(json.loads(json.dumps(message))) == get_context_usage(
        message
    )
    attach_context_usage(message, 341_000, "openai", "gpt-5.4")
    assert get_responses_provider_state(message) is not None
    assert get_context_usage(message)["prompt_tokens"] == 341_000


def test_snapshot_validation():
    for tokens in (0, -1, None, True, "100"):
        message = _snapshot(100)
        message["_metadata"]["context_usage"]["prompt_tokens"] = tokens
        assert get_context_usage(message) is None
