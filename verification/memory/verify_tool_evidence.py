"""Fictional Slice 3 checks; no network, private memories or deprecated tests."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.agents.memory_coordinator import (
    MAX_TOOL_EVIDENCE_CHARACTERS,
    MAX_TOOL_EVIDENCE_ITEMS,
    AgentMemoryCoordinator,
)
from AgentCrew.modules.agents.tool_result_summary import (
    ToolResultSummaryService,
    bind_inference_scope,
    reset_inference_scope,
)
from AgentCrew.modules.memory.context_persistent import ContextPersistenceService
from AgentCrew.modules.memory.memory_record_builder import MemoryRecordBuilder
from AgentCrew.modules.prompts.constants import PRE_ANALYZE_PROMPT
from verification.memory.verify_phase4 import LegacyService, require
from verification.memory.verify_phase5 import FictionalHooks


class FictionalLLM:
    def __init__(self, reply="Checked /safe/file.py with 12 validations."):
        self.reply = reply
        self.prompts = []
        self.fail = False

    async def process_message(self, prompt, temperature=0):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("fictional failure")
        return self.reply


def record(
    service, summary="Checked /safe/file.py with 12 validations.", tool="run_command"
):
    return {
        "version": service.SUMMARY_VERSION,
        "tool_name": tool,
        "summary": summary,
        "source_hash": service.source_hash(
            tool, {"command": "RAW_ARGUMENT"}, "RAW_OUTPUT", False
        ),
        "created_at": "2026-11-16T00:00:00+00:00",
    }


def tool_message(call_id, tool="run_command", **kwargs):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": tool,
        "content": "RAW_OUTPUT",
        **kwargs,
    }


def coordinator(service=None, **extra):
    services = {"tool_result_summary": service, **extra}
    return AgentMemoryCoordinator(
        SimpleNamespace(name="FictionalAgent", services=services)
    )


def extract(owner, messages, scope="chat:fictional", response=""):
    token = bind_inference_scope(scope)
    try:
        return owner.extract_assistant_messages_for_memory(messages, response)
    finally:
        reset_inference_scope(token)


def process_queued(service):
    payload = service._queue.get_nowait()
    try:
        asyncio.run(service._summarize_and_store(payload))
    finally:
        service._clear_pending(payload)
        service._queue.task_done()


def checks_scope_readiness_and_source_validation():
    service = ToolResultSummaryService(None)
    scopes = ("chat:fictional", "job:fictional", "a2a:fictional", "acp:fictional")
    for scope in scopes:
        service._cache_record(
            scope, "same-call", record(service, f"Completed {scope} safely.")
        )
    for scope in scopes:
        require(
            service.get_ready_summary(scope, "same-call", "run_command")
            == f"Completed {scope} safely.",
            "Cross-scope evidence leak",
        )
        require(
            service.get_ready_summary(scope, "same-call", "read_file") is None,
            "Tool-name mismatch accepted",
        )
    require(
        service.get_ready_summary("chat:other", "same-call", "run_command") is None,
        "Unknown scope accepted",
    )
    require(
        service.get_ready_summary("job:fictional", "missing", "run_command") is None,
        "Missing summary accepted",
    )
    service._pending.add(("job:fictional", "same-call", "new-hash"))
    require(
        service.get_ready_summary("job:fictional", "same-call", "run_command") is None,
        "Pending summary was consumed",
    )
    service._pending.clear()
    for changes in (
        {"version": 1},
        {"version": 2.0},
        {"source_hash": "invalid"},
        {"tool_name": "wrong"},
        {"summary": ""},
    ):
        service._cache[("job:invalid", "call")] = {**record(service), **changes}
        require(
            service.get_ready_summary("job:invalid", "call", "run_command") is None,
            "Invalid or stale data accepted",
        )
    require(
        service.get_summary(
            "chat:fictional",
            "same-call",
            "run_command",
            {"command": "RAW_ARGUMENT"},
            "RAW_OUTPUT",
            False,
        )
        is not None,
        "Existing source-validated API broke",
    )
    require(
        service.get_summary(
            "chat:fictional",
            "same-call",
            "run_command",
            {"command": "CHANGED"},
            "RAW_OUTPUT",
            False,
        )
        is None,
        "Source validation weakened",
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: service.get_ready_summary(
                    "acp:fictional", "same-call", "run_command"
                ),
                range(40),
            )
        )
    require(
        all(value in (None, "Completed acp:fictional safely.") for value in results)
        and any(value is not None for value in results),
        "Concurrent reads diverged",
    )
    with ThreadPoolExecutor(max_workers=1) as executor, service._lock:
        value = executor.submit(
            service.get_ready_summary, "acp:fictional", "same-call", "run_command"
        ).result(timeout=0.5)
    require(value is None, "Ready lookup waited on a contended cache lock")
    service.invalidate_scope("acp:fictional")
    require(
        service.get_ready_summary("acp:fictional", "same-call", "run_command") is None,
        "Invalidated scope reused",
    )
    service.close()


def checks_pending_failed_and_superseded_sources():
    service = ToolResultSummaryService(None)
    llm = FictionalLLM()
    service._llm_service = llm
    service._cache_record("chat:fictional", "call", record(service))
    require(
        service.submit("chat:fictional", "call", "run_command", {}, "changed", False),
        "Replacement not submitted",
    )
    require(
        service.get_ready_summary("chat:fictional", "call", "run_command") is None,
        "Pending replacement reused old summary",
    )
    llm.fail = True
    process_queued(service)
    require(
        service.get_ready_summary("chat:fictional", "call", "run_command") is None,
        "Failed replacement exposed stale source",
    )
    llm.fail = False
    require(
        service.submit("chat:fictional", "call", "run_command", {}, "older", False),
        "Older work not queued",
    )
    require(
        service.submit("chat:fictional", "call", "run_command", {}, "latest", False),
        "Latest work not queued",
    )
    process_queued(service)
    require(
        service._cache[("chat:fictional", "call")]["source_hash"]
        != service.source_hash("run_command", {}, "older", False),
        "Obsolete completion entered cache",
    )
    process_queued(service)
    require(
        service.get_ready_summary("chat:fictional", "call", "run_command") == llm.reply,
        "Latest completion missing",
    )
    calls = len(llm.prompts)
    for _ in range(10):
        service.get_ready_summary("chat:fictional", "call", "run_command")
        service.get_ready_summary("chat:missing", "call", "run_command")
    require(len(llm.prompts) == calls, "Read API generated summaries")
    service.clear_scope("chat:fictional")
    require(not service._latest_sources, "Scope revision state leaked after clearing")
    service.close()


def checks_pending_capture_and_worker_lifecycle():
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    class HeldLLM(FictionalLLM):
        provider_name = "fictional"
        closed = False

        async def process_message(self, prompt, temperature=0):
            self.prompts.append(prompt)
            started.set()
            await asyncio.to_thread(release.wait, 2)
            return self.reply

        async def close(self):
            self.closed = True

    llm = HeldLLM()
    service = ToolResultSummaryService(llm)
    memory = LegacyService("")
    memory.store_conversation = Mock()
    owner = coordinator(service, memory=memory)
    messages = [{"role": "user", "content": "Current request"}, tool_message("held")]
    clear_pending = service._clear_pending

    def complete(payload):
        clear_pending(payload)
        completed.set()

    def store_pending():
        token = bind_inference_scope("job:held")
        try:
            owner.store_memory_if_available("Current request", messages, "Final")
        finally:
            reset_inference_scope(token)

    try:
        with patch.object(service, "_clear_pending", side_effect=complete):
            require(
                service.submit(
                    "job:held", "held", "run_command", {}, "RAW_OUTPUT", False
                ),
                "Worker submission failed",
            )
            require(started.wait(1), "Fictional worker did not start")
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(store_pending).result(timeout=0.5)
            require(
                memory.store_conversation.call_args.args[1] == ["Final"],
                "Pending summary delayed or contaminated storage",
            )
            require(len(llm.prompts) == 1, "Memory capture generated another summary")
            release.set()
            require(completed.wait(2), "Fictional worker did not complete")
            require(
                service.get_ready_summary("job:held", "held", "run_command")
                == llm.reply,
                "Worker completion not readable",
            )
    finally:
        release.set()
        require(service.close(), "Summary worker failed to close")
    require(llm.closed and service._worker_done.is_set(), "Worker lifecycle leaked")


def checks_ready_cache_miss_has_no_persistence_io():
    persistence = SimpleNamespace(get_tool_result_summaries=Mock())
    service = ToolResultSummaryService(None, persistence)
    stored = record(service)
    persistence.get_tool_result_summaries.return_value = {"persisted": stored}
    try:
        for scope in (
            "chat:fictional",
            "job:fictional",
            "a2a:fictional",
            "acp:fictional",
        ):
            require(
                service.get_ready_summary(scope, "persisted", "run_command") is None,
                "Ready cache miss performed a persistence lookup",
            )
        require(
            persistence.get_tool_result_summaries.call_count == 0,
            "Cache-only ready reads accessed persistence",
        )
        service.preload_chat_scope("fictional")
        require(
            persistence.get_tool_result_summaries.call_count == 1,
            "Explicit chat preload did not load persisted summaries",
        )
        persistence.get_tool_result_summaries.reset_mock()
        require(
            service.get_ready_summary("chat:fictional", "persisted", "run_command")
            == stored["summary"],
            "Preloaded summary was not ready",
        )
        require(
            persistence.get_tool_result_summaries.call_count == 0,
            "Ready cache hit accessed persistence",
        )
    finally:
        service.close()


def checks_blocked_persistence_does_not_block_state():
    for invalidate in (False, True):
        started = threading.Event()
        release = threading.Event()
        persisted = {}

        def write(
            conversation_id,
            tool_call_id,
            summary,
            *,
            started=started,
            release=release,
            persisted=persisted,
        ):
            started.set()
            require(release.wait(3), "Fictional persistence write was not released")
            persisted[tool_call_id] = dict(summary)
            return True

        persistence = SimpleNamespace(
            upsert_tool_result_summary=Mock(side_effect=write),
            get_tool_result_summaries=Mock(return_value=persisted),
        )
        service = ToolResultSummaryService(None, persistence)
        llm = FictionalLLM()
        service._llm_service = llm
        scope = "chat:blocked"
        service._cache_record(scope, "stable", record(service))
        require(
            service.submit(scope, "writing", "run_command", {}, "original", False),
            "Initial summary was not queued",
        )
        executor = ThreadPoolExecutor(max_workers=3)
        try:
            writer = executor.submit(process_queued, service)
            require(started.wait(1), "Fictional persistence write did not start")
            submitted = executor.submit(
                service.submit,
                scope,
                "writing",
                "run_command",
                {},
                "replacement",
                False,
            ).result(timeout=0.5)
            require(submitted, "Later submit blocked or failed during persistence")
            ready = executor.submit(
                service.get_ready_summary, scope, "stable", "run_command"
            ).result(timeout=0.5)
            require(
                ready == llm.reply, "Blocked write interfered with ready cache read"
            )
            require(
                executor.submit(service._scope_is_writable, scope).result(timeout=0.5),
                "Blocked write interfered with a state read",
            )
            require(
                service.get_ready_summary(scope, "writing", "run_command") is None,
                "Pending replacement exposed the older summary",
            )
            if invalidate:
                executor.submit(service.invalidate_scope, scope).result(timeout=0.5)
            require(
                not release.is_set(), "Persistence was released before state checks"
            )
            release.set()
            writer.result(timeout=1)
            require("writing" in persisted, "Fictional write did not finish")
            require(
                service.get_ready_summary(scope, "writing", "run_command") is None,
                "Late persistence completion resurrected stale evidence",
            )
            llm.fail = True
            process_queued(service)
            require(
                service.get_ready_summary(scope, "writing", "run_command") is None,
                "Failed or invalidated replacement exposed stale persistence",
            )
            require(
                persistence.get_tool_result_summaries.call_count == 0,
                "Ready read consulted persistence during or after a blocked write",
            )
        finally:
            release.set()
            executor.shutdown(wait=True)
            service.close()


def checks_redaction_prompt_cache_and_persistence():
    keys = (
        "PaSsWoRd",
        "SECRET",
        "api_key",
        "API-KEY",
        "access_token",
        "REFRESH_TOKEN",
        "Authorization",
        "Cookie",
    )
    secrets = [f"FIXTURE_CREDENTIAL_{index}" for index in range(len(keys))]
    source = dict(zip(keys, secrets))
    source.update(
        {"path": "/safe/file.py", "id": "safe:id#1", "command": "uv run check.py"}
    )
    raw_text = 'curl -H "Cookie: session=FIXTURE_COOKIE; other=FIXTURE_OTHER" /safe/file.py\nAuthorization: Bearer FIXTURE_HEADER\nBearer FIXTURE_BEARER'
    secret_values = [
        *secrets,
        "FIXTURE_COOKIE",
        "FIXTURE_OTHER",
        "FIXTURE_HEADER",
        "FIXTURE_BEARER",
    ]
    original = copy.deepcopy(source)
    for key in keys:
        for assignment in (
            f'{key}="FIXTURE_MATRIX"',
            f"'{key}': 'FIXTURE_MATRIX'",
            f"{key}=FIXTURE_MATRIX",
            f"--{key}=FIXTURE_MATRIX",
        ):
            redacted = ToolResultSummaryService._redact_text(assignment)
            require(
                "FIXTURE_MATRIX" not in redacted,
                "Labeled credential escaped sanitization",
            )
            require(
                ToolResultSummaryService._redact_text(redacted) == redacted,
                "Redaction matrix is not idempotent",
            )
    with TemporaryDirectory(prefix="agentcrew-evidence-") as directory:
        persistence = ContextPersistenceService(directory)
        service = ToolResultSummaryService(None, persistence)
        llm = FictionalLLM(
            'Checked /safe/file.py; api_key="FIXTURE_ACCEPTED" and Bearer FIXTURE_OUTPUT successfully.'
        )
        service._llm_service = llm
        arguments = {"nested": [source], "json": json.dumps(source)}
        result = {"headers": source, "text": raw_text}
        full_hash = service.source_hash("run_command", arguments, result, False)
        require(
            service.submit(
                "chat:fictional", "call", "run_command", arguments, result, False
            ),
            "Submission failed",
        )
        process_queued(service)
        prompt = llm.prompts[0]
        require(
            all(secret not in prompt for secret in secret_values),
            "Credential reached summary-model prompt",
        )
        require(
            all(
                value in prompt
                for value in ("/safe/file.py", "safe:id#1", "uv run check.py")
            ),
            "Nonsecret facts lost",
        )
        require(source == original, "Redaction mutated source data")
        changed = copy.deepcopy(arguments)
        changed["nested"][0]["SECRET"] = "CHANGED_FIXTURE_CREDENTIAL"
        require(
            full_hash != service.source_hash("run_command", changed, result, False),
            "Hash ignores full-source secret changes",
        )
        cached = service._cache[("chat:fictional", "call")]
        require(cached["source_hash"] == full_hash, "Hash was computed after redaction")
        accepted = service.get_ready_summary("chat:fictional", "call", "run_command")
        require(
            accepted
            and "FIXTURE_ACCEPTED" not in accepted
            and "FIXTURE_OUTPUT" not in accepted,
            "Accepted summary not sanitized",
        )
        metadata = persistence.get_tool_result_summaries("fictional")
        serialized = json.dumps(metadata)
        require(
            all(
                secret not in serialized
                for secret in [*secret_values, "FIXTURE_ACCEPTED", "FIXTURE_OUTPUT"]
            ),
            "Credentials persisted",
        )
        require(
            metadata["call"]["summary"] == accepted,
            "Cache/persistence sanitization differs",
        )
        require(
            all(
                secret not in path.read_text()
                for path in Path(directory).rglob("*.json")
                for secret in [*secret_values, "FIXTURE_ACCEPTED", "FIXTURE_OUTPUT"]
            ),
            "Credential reached persistence files",
        )
        require(
            service._redact_text(accepted) == accepted, "Sanitizer is not idempotent"
        )
        loaded = ToolResultSummaryService(None, persistence)
        loaded.preload_chat_scope("fictional")
        require(
            loaded.get_ready_summary("chat:fictional", "call", "run_command")
            == accepted,
            "Persisted ready summary not reused",
        )
        persistence.upsert_tool_result_summary(
            "fictional",
            "legacy",
            record(service, 'api-key="FIXTURE_LEGACY"; checked /safe/file.py.'),
        )
        require(
            loaded.get_ready_summary("chat:fictional", "legacy", "run_command") is None,
            "New persisted record was read without explicit preload",
        )
        loaded.preload_chat_scope("fictional")
        legacy = loaded.get_ready_summary("chat:fictional", "legacy", "run_command")
        require(
            legacy and "FIXTURE_LEGACY" not in legacy, "Loaded summary not sanitized"
        )
        require(
            loaded.get_ready_summary("job:fictional", "call", "run_command") is None,
            "Chat persistence escaped scope",
        )
        service.close()
        loaded.close()


def checks_memory_capture_order_exclusions_and_raw_safety():
    service = ToolResultSummaryService(None)
    for call in ("before", "one", "two", "search", "skill", "excluded"):
        tool = {
            "search": "search_memory",
            "skill": "activate_skill",
            "excluded": "private_tool",
        }.get(call, "run_command")
        service._cache_record(
            "chat:fictional",
            call,
            record(service, f"Completed {call} validation.", tool),
        )
    manager = SimpleNamespace(
        context_shrink_enabled=True, shrink_excluded_list=["private_tool"]
    )
    owner = coordinator(service, agent_manager=manager)
    messages = [
        tool_message("before"),
        {"role": "user", "content": "Current request"},
        {
            "role": "assistant",
            "content": "First",
            "tool_calls": [
                {
                    "id": "one",
                    "name": "run_command",
                    "arguments": {"command": "RAW_ARGUMENT"},
                }
            ],
        },
        tool_message("one"),
        tool_message("search", "search_memory"),
        tool_message("skill", "activate_skill"),
        tool_message("excluded", "private_tool"),
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "ask", "name": "ask", "arguments": '{"question":"Continue?"}'}
            ],
        },
        tool_message("ask", "ask", content="Yes"),
        tool_message(
            "rejected",
            "write_file",
            is_rejected=True,
            content="Please preserve the file",
        ),
        {"role": "assistant", "content": "Middle"},
        tool_message("two"),
    ]
    result = extract(owner, messages, response="Final")
    require(
        result
        == [
            "First",
            "[Tool evidence: run_command] Completed one validation.",
            "[User answered: Yes | Question was: Continue?]",
            "[Tool rejected: write_file] Please preserve the file",
            "Middle",
            "[Tool evidence: run_command] Completed two validation.",
            "Final",
        ],
        "Boundary, chronology or ask/rejected behavior changed",
    )
    require(
        "RAW_ARGUMENT" not in str(result) and "RAW_OUTPUT" not in str(result),
        "Raw payload reached memory",
    )
    require(
        not any(
            "Tool evidence" in value
            for value in extract(owner, messages, scope="job:other")
        ),
        "Cross-scope capture occurred",
    )
    require(
        owner.extract_assistant_messages_for_memory(messages)[0] == "First",
        "Missing scope broke ordinary text",
    )
    manager.context_shrink_enabled = False
    require(
        not any("Tool evidence" in value for value in extract(owner, messages)),
        "Disabled summaries captured",
    )
    service.close()


def checks_bounds_latest_policy_and_duplicates():
    service = ToolResultSummaryService(None)
    owner = coordinator(service)
    messages = [{"role": "user", "content": "Current request"}]
    for index in range(12):
        service._cache_record(
            "chat:fictional", str(index), record(service, f"Completed step {index}.")
        )
        messages.append(tool_message(str(index)))
    messages.append({"role": "assistant", "content": "Final text"})
    result = extract(owner, messages)
    evidence = [value for value in result if value.startswith("[Tool evidence:")]
    require(len(evidence) == MAX_TOOL_EVIDENCE_ITEMS, "Item cap not applied")
    require(
        evidence[0].endswith("step 4.") and evidence[-1].endswith("step 11."),
        "Latest suffix policy not applied",
    )
    messages.extend([tool_message("10"), tool_message("11")])
    duplicate_result = extract(owner, messages)
    require(
        sum("step 10." in value for value in duplicate_result) == 1,
        "Duplicate call captured twice",
    )
    require(
        duplicate_result[-2:]
        == [
            "[Tool evidence: run_command] Completed step 10.",
            "[Tool evidence: run_command] Completed step 11.",
        ],
        "Latest duplicate placement lost",
    )
    for index in range(12):
        service._cache_record(
            "chat:fictional",
            str(index),
            record(service, f"Step {index}: " + "x" * 1100 + "."),
        )
    result = extract(owner, messages[:-2])
    evidence = [value for value in result if value.startswith("[Tool evidence:")]
    require(
        sum(map(len, evidence)) <= MAX_TOOL_EVIDENCE_CHARACTERS,
        "Character cap exceeded",
    )
    require(
        len(evidence) == 5 and "Step 7:" in evidence[0] and "Step 11:" in evidence[-1],
        "Latest bounded character suffix changed",
    )
    require(result[-1] == "Final text", "Evidence cap dropped ordinary assistant text")
    service.close()


def checks_fail_open_storage_and_fallback_notes():
    memory = LegacyService("")
    memory.store_conversation = Mock()
    service = ToolResultSummaryService(None)
    service._cache_record("chat:fictional", "ready", record(service))
    messages = [
        {"role": "user", "content": "Current request"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "missing",
                    "name": "run_command",
                    "arguments": {"command": "RAW_ARGUMENT"},
                }
            ],
        },
        tool_message("missing"),
        tool_message("ready"),
    ]
    owner = coordinator(service, memory=memory)
    token = bind_inference_scope("chat:fictional")
    try:
        owner.store_memory_if_available(
            "Current request", messages, "Final", "fictional"
        )
    finally:
        reset_inference_scope(token)
    captured = memory.store_conversation.call_args.args[1]
    require(
        captured
        == [
            "[Tool evidence: run_command] Checked /safe/file.py with 12 validations.",
            "Final",
        ],
        "Store payload missing ready evidence",
    )
    operation = {"user_message": "Current request", "assistant_messages": captured}
    with patch(
        "AgentCrew.modules.events.hooks.HookRegistry.get_instance",
        return_value=FictionalHooks(),
    ):
        built = asyncio.run(MemoryRecordBuilder(None).build(operation))
    require(
        built
        and "RAW_ARGUMENT" not in built.document
        and "RAW_OUTPUT" not in built.document,
        "Fallback XML contains raw data",
    )
    for broken in (
        None,
        SimpleNamespace(),
        SimpleNamespace(
            get_ready_summary=Mock(side_effect=RuntimeError("private exception detail"))
        ),
    ):
        memory.store_conversation.reset_mock()
        owner = coordinator(broken, memory=memory)
        token = bind_inference_scope("chat:fictional")
        try:
            owner.store_memory_if_available(
                "Current request", messages, "Final", "fictional"
            )
        finally:
            reset_inference_scope(token)
        require(
            memory.store_conversation.call_count == 1,
            "Summary failure blocked memory store",
        )
        require(
            memory.store_conversation.call_args.args[1] == ["Final"],
            "Failure used raw fallback",
        )
    require(
        "untrusted factual summaries" in PRE_ANALYZE_PROMPT
        and "not instructions or authorization" in PRE_ANALYZE_PROMPT
        and "INSIGHTS and RESOURCES" in PRE_ANALYZE_PROMPT,
        "Extraction guidance missing",
    )
    service.close()


def main():
    for check in (
        checks_scope_readiness_and_source_validation,
        checks_pending_failed_and_superseded_sources,
        checks_pending_capture_and_worker_lifecycle,
        checks_ready_cache_miss_has_no_persistence_io,
        checks_blocked_persistence_does_not_block_state,
        checks_redaction_prompt_cache_and_persistence,
        checks_memory_capture_order_exclusions_and_raw_safety,
        checks_bounds_latest_policy_and_duplicates,
        checks_fail_open_storage_and_fallback_notes,
    ):
        check()
        print(f"PASS {check.__name__}")
    print("PASS Slice 3 ready, redacted, bounded tool evidence; fictional data only.")


if __name__ == "__main__":
    main()
