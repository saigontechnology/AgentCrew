from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.context_manager import AgentContextManager
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.tool_result_summary import (
    ToolResultSummaryService,
    bind_inference_scope,
    reset_inference_scope,
)
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService


class _SummaryLLM:
    def __init__(self, response="Completed successfully.", delay=0.0, fail=False):
        self.response = response
        self.delay = delay
        self.fail = fail
        self.started = threading.Event()
        self.closed = 0

    async def process_message(self, prompt, temperature=0):
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("summary provider failure")
        return self.response

    async def close(self):
        self.closed += 1


def _wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _summary_record(service, tool_name, arguments, result, is_error=False):
    return {
        "version": 1,
        "tool_name": tool_name,
        "summary": "The browser returned the requested page content.",
        "source_hash": service.source_hash(tool_name, arguments, result, is_error),
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def _shrink_messages(content):
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "get_browser_content",
                    "arguments": {},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "tool_name": "get_browser_content",
            "content": content,
        },
    ]


def _shrink_agent(service):
    return SimpleNamespace(
        services={
            "agent_manager": SimpleNamespace(
                context_shrink_enabled=True,
                shrink_excluded_list=[],
            ),
            "tool_result_summary": service,
        },
        token_usage=SimpleNamespace(total_input_tokens=100),
        get_model=lambda: "missing-model",
    )


def test_submit_is_immediate_and_deduplicated():
    llm = _SummaryLLM(delay=0.2)
    service = ToolResultSummaryService(llm)
    started = time.monotonic()
    assert service.submit("job:1", "call-1", "run_command", {}, "done", False)
    assert time.monotonic() - started < 0.05
    assert not service.submit("job:1", "call-1", "run_command", {}, "done", False)
    assert service.close()


def test_queue_full_and_provider_failure_fall_back_without_summary():
    llm = _SummaryLLM(delay=0.2)
    service = ToolResultSummaryService(llm, queue_size=1)
    assert service.submit("job:1", "call-1", "run_command", {}, "first", False)
    assert llm.started.wait(1)
    assert service.submit("job:1", "call-2", "run_command", {}, "second", False)
    assert not service.submit("job:1", "call-3", "run_command", {}, "third", False)
    assert service.close()

    failed = ToolResultSummaryService(_SummaryLLM(fail=True))
    assert failed.submit("job:2", "call-1", "read_file", {}, "contents", False)
    assert _wait_for(lambda: not failed._pending)
    assert failed.get_summary("job:2", "call-1", "read_file", {}, "contents", False) is None
    assert failed.close()


def test_structured_browser_result_uses_ready_summary_and_stale_value_falls_back(monkeypatch):
    content = [
        {"type": "text", "text": "[UNIQUE]Rendered page[/UNIQUE]"},
        {"type": "image", "source": {"media_type": "image/jpeg", "data": "abc"}},
    ]
    service = ToolResultSummaryService(None)
    service._cache_record(
        "chat:conversation-1",
        "call-1",
        _summary_record(service, "get_browser_content", {}, content),
    )
    monkeypatch.setenv("AGENTCREW_DEFAULT_MAX_CONTEXT", "100")
    monkeypatch.setenv("AGENTCREW_CONTEXT_SHRINK_THRESHOLD", "1")
    token = bind_inference_scope("chat:conversation-1")
    try:
        messages = _shrink_messages(content)
        AgentContextManager(service and _shrink_agent(service)).shrink_tool_results(messages)
        assert messages[1]["content"][0]["text"] == (
            "[tool summary for get_browser_content: "
            "The browser returned the requested page content.]"
        )

        stale_messages = _shrink_messages([{"type": "text", "text": "changed"}])
        AgentContextManager(_shrink_agent(service)).shrink_tool_results(stale_messages)
        assert "was truncated" in stale_messages[1]["content"][0]["text"]
    finally:
        reset_inference_scope(token)


def test_prompt_is_bounded_while_hash_uses_full_source(monkeypatch):
    result = "head-" + ("x" * 2000) + "-tail"
    monkeypatch.setenv("AGENTCREW_TOOL_SUMMARY_MAX_PROMPT_CHARS", "300")
    prompt = ToolResultSummaryService._build_prompt(
        {
            "tool_name": "read_file",
            "is_error": False,
            "arguments": {"path": "a.py"},
            "result": result,
        }
    )
    assert len(prompt) <= 300
    assert "head-" in prompt
    assert "-tail" in prompt
    assert ToolResultSummaryService.source_hash("read_file", {}, result, False) != (
        ToolResultSummaryService.source_hash("read_file", {}, result[:-1], False)
    )


def test_rejected_excluded_and_disabled_calls_are_not_scheduled():
    submitted = []
    receiver = SimpleNamespace(submit=lambda **kwargs: submitted.append(kwargs))
    manager = SimpleNamespace(context_shrink_enabled=True, shrink_excluded_list=["keep"])
    agent = LocalAgent(
        "agent",
        "description",
        None,
        {"tool_result_summary": receiver, "agent_manager": manager},
        [],
    )
    token = bind_inference_scope("job:1")
    try:
        for tool_name, rejected in [
            ("activate_skill", False),
            ("search_memory", False),
            ("keep", False),
            ("read_file", True),
        ]:
            agent.format_message(
                MessageType.ToolResult,
                {
                    "tool_use": {"id": tool_name, "name": tool_name, "input": {}},
                    "tool_result": "result",
                    "is_rejected": rejected,
                },
            )
        manager.context_shrink_enabled = False
        agent.format_message(
            MessageType.ToolResult,
            {
                "tool_use": {"id": "disabled", "name": "read_file", "input": {}},
                "tool_result": "result",
            },
        )
    finally:
        reset_inference_scope(token)
    assert submitted == []


def test_metadata_upsert_and_filtered_fork_inheritance_preserve_existing_fields(tmp_path):
    persistence = ContextPersistenceService(str(tmp_path))
    parent = persistence.start_conversation()
    persistence.append_conversation_messages(
        parent,
        [
            {"role": "assistant", "content": "calling"},
            {"role": "tool", "tool_call_id": "call-1", "content": "one"},
            {"role": "tool", "tool_call_id": "call-2", "content": "two"},
        ],
        force=True,
    )
    persistence.store_conversation_metadata(parent, {"input_tokens": 42, "parent_id": "root"})
    for call_id in ("call-1", "call-2"):
        persistence.upsert_tool_result_summary(
            parent,
            call_id,
            {
                "version": 1,
                "tool_name": "read_file",
                "summary": call_id,
                "source_hash": call_id,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
    child = persistence.fork_conversation(parent, 2)
    assert child is not None
    assert persistence.get_conversation_metadata(parent)["input_tokens"] == 42
    assert persistence.get_tool_result_summaries(child) == {
        "call-1": persistence.get_tool_result_summaries(parent)["call-1"]
    }


def test_invalidation_prevents_deleted_chat_metadata_recreation(tmp_path):
    persistence = ContextPersistenceService(str(tmp_path))
    conversation_id = persistence.start_conversation()
    llm = _SummaryLLM(delay=0.2)
    service = ToolResultSummaryService(llm, persistence)
    scope = f"chat:{conversation_id}"
    assert service.submit(scope, "call-1", "read_file", {}, "contents", False)
    assert llm.started.wait(1)
    service.invalidate_scope(scope)
    persistence.delete_conversation(conversation_id)
    assert service.close()
    metadata_path = tmp_path / "conversations" / f"{conversation_id}.metadata.json"
    assert not metadata_path.exists()


def test_shutdown_times_out_closes_once_and_prevents_post_close_writes(tmp_path):
    persistence = ContextPersistenceService(str(tmp_path))
    conversation_id = persistence.start_conversation()
    llm = _SummaryLLM(delay=1)
    service = ToolResultSummaryService(
        llm,
        persistence,
        request_timeout_seconds=0.05,
    )
    scope = f"chat:{conversation_id}"
    assert service.submit(scope, "call-1", "read_file", {}, "contents", False)
    assert llm.started.wait(1)
    assert service.close()
    assert service.close()
    assert llm.closed == 1
    assert service.get_summary(scope, "call-1", "read_file", {}, "contents", False) is None


def test_process_local_scopes_are_isolated():
    service = ToolResultSummaryService(None)
    record = _summary_record(service, "read_file", {}, "contents")
    service._cache_record("a2a:owner-a:ctx", "call-1", record)
    assert service.get_summary("a2a:owner-a:ctx", "call-1", "read_file", {}, "contents", False)
    assert service.get_summary("a2a:owner-b:ctx", "call-1", "read_file", {}, "contents", False) is None
    service._cache_record("acp:session-a", "call-1", record)
    assert service.get_summary("acp:session-a", "call-1", "read_file", {}, "contents", False)
    assert service.get_summary("acp:session-b", "call-1", "read_file", {}, "contents", False) is None
