"""Offline ActionTrace checks: fictional evidence, temporary SQLite, no provider calls."""

from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from AgentCrew.modules.agents import tool_result_summary
from AgentCrew.modules.agents.base import MessageType
from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.agents.memory_coordinator import AgentMemoryCoordinator
from AgentCrew.modules.agents.tool_result_summary import (
    bind_inference_scope,
    reset_inference_scope,
)
from AgentCrew.modules.events.hooks import (
    CancelOperation,
    Hook,
    HookPhase,
    HookPoints,
    HookRegistry,
)
from AgentCrew.modules.memory.action_trace import MAX_TRACE_EVENTS, ActionTraceCollector
from AgentCrew.modules.memory.base_service import BaseMemoryService
from AgentCrew.modules.memory.memory_worker import MemoryWorker
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator
from AgentCrew.modules.memory.sqlite_repository import (
    MEMOPAD_SCHEMA,
    RESOURCE_SCHEMA,
    SCHEMA_V1,
    SQLiteMemoryError,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService
from AgentCrew.modules.memory.tool import (
    MemoryToolContext,
    get_memory_retrieve_tool_handler,
)
from AgentCrew.modules.memory.types import MemoryQuery, MemoryRecord
from AgentCrew.modules.tools import parallel_executor

DOCUMENT = "<MEMORY><HEAD>Fictional trace widget</HEAD><CONVERSATION_NOTES><NOTE>Fictional widget saved.</NOTE></CONVERSATION_NOTES></MEMORY>"


@pytest.fixture(autouse=True)
def clean_runtime():
    HookRegistry.reset_instance()
    tool_result_summary._trace_collector = None
    parallel_executor._trace_collector = None
    yield
    tool_result_summary._trace_collector = None
    parallel_executor._trace_collector = None
    HookRegistry.reset_instance()
    assert tool_result_summary.current_inference_scope() is None


@pytest.fixture
def service(tmp_path):
    with patch.object(MemoryWorker, "start"):
        service = SQLiteMemoryService(tmp_path / "memory.db")
    service.enable_action_trace(HookRegistry.get_instance())
    yield service
    service.shutdown()


def make_agent(service, executor=None, name="Engineer"):
    agent = LocalAgent.__new__(LocalAgent)
    agent.name = name
    agent.services = {"memory": service}
    agent.llm = SimpleNamespace(execute_tool=executor or AsyncMock(return_value="done"))
    agent._memory_coordinator = AgentMemoryCoordinator(agent)
    agent._tool_result_coordinator = SimpleNamespace(
        format_message=Mock(return_value={})
    )
    return agent


def context(call="c1", agent="Engineer"):
    return {"agent_name": agent, "tool_id": call, "tool_name": "fictional_tool"}


def record(key="memory", session="previous", timestamp=1):
    return MemoryRecord(key, "Engineer", session, DOCUMENT, timestamp, timestamp)


def trace(collector, calls=1, name="Engineer", complete=True):
    token = collector.bind("job:fictional")
    try:
        for index in range(calls):
            ctx = context(f"call-{index}", name)
            collector.before_tool(ctx)
            collector.after_tool(ctx, object())
        if complete:
            collector.complete(name)
        return collector.snapshot(name)
    finally:
        collector.reset(token)


def counts(repository):
    with repository._transaction() as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("memory_record", "memory_episode", "memory_action")
        )


def test_immutable_bounded_snapshot_and_owner_cleanup(service):
    collector = service.action_trace_collector
    snapshot = trace(collector)
    assert snapshot.stored_count == snapshot.observed_count == 3
    assert snapshot.events[-1].kind == "response"
    assert snapshot.incomplete_reasons == ()
    with pytest.raises(FrozenInstanceError):
        snapshot.turn_id = "changed"
    assert collector.snapshot("Engineer") is None
    owner = collector._owner
    assert (
        len(
            [
                hook
                for hook in HookRegistry.get_instance().get_hooks()
                if hook.owner == owner
            ]
        )
        == 3
    )
    service.shutdown()
    assert not any(
        hook.owner == owner for hook in HookRegistry.get_instance().get_hooks()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["chat", "job", "a2a", "acp"])
async def test_scope_isolation_and_stable_turn_identity(service, surface):
    collector = service.action_trace_collector

    async def turn(index):
        token = bind_inference_scope(f"{surface}:same-session")
        try:
            assert bind_inference_scope("nested") is None
            ctx = context(f"call-{index}")
            collector.before_tool(ctx)
            await asyncio.sleep(0)
            collector.after_tool(ctx, None)
            collector.complete("Engineer")
            first = collector.snapshot("Engineer")
            assert first.turn_id == collector.snapshot("Engineer").turn_id
            assert {
                event.tool_call_id for event in first.events if event.kind == "tool"
            } == {f"call-{index}"}
            return first.turn_id
        finally:
            reset_inference_scope(token)

    ids = await asyncio.gather(*(turn(index) for index in range(8)))
    assert len(set(ids)) == 8
    assert collector.snapshot("Engineer") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeded", "failed", "rejected", "canceled"])
async def test_real_local_tool_lifecycle(service, outcome):
    hooks = HookRegistry.get_instance()
    legacy_after = Mock(side_effect=lambda ctx, result: result)
    hooks.register(Hook(HookPoints.TOOL_EXECUTE, HookPhase.AFTER, legacy_after))
    if outcome == "rejected":
        hooks.register(
            Hook(HookPoints.TOOL_EXECUTE, HookPhase.BEFORE, lambda ctx: None)
        )
    executor = AsyncMock(return_value="done")
    if outcome == "failed":
        executor.side_effect = ValueError("fictional failure")
    if outcome == "canceled":
        executor.side_effect = asyncio.CancelledError()
    agent = make_agent(service, executor)
    token = bind_inference_scope("job:local")
    try:
        tool = {"id": "call-1", "name": "fictional_tool", "input": {}}
        if outcome == "succeeded":
            assert await agent.execute_tool_call(tool) == "done"
        else:
            expected = {
                "failed": ValueError,
                "rejected": CancelOperation,
                "canceled": asyncio.CancelledError,
            }[outcome]
            with pytest.raises(expected):
                await agent.execute_tool_call(tool)
        collector = service.action_trace_collector
        collector.complete(agent.name)
        snapshot = collector.snapshot(agent.name)
        assert [event.status for event in snapshot.events] == [
            "started",
            outcome,
            "succeeded",
        ]
        assert snapshot.events[1].duration_ms >= 0
        assert legacy_after.call_count == (1 if outcome == "succeeded" else 0)
        assert executor.await_count == (0 if outcome == "rejected" else 1)
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_cancel_during_before_hook(service):
    async def canceled(ctx):
        raise asyncio.CancelledError()

    HookRegistry.get_instance().register(
        Hook(HookPoints.TOOL_EXECUTE, HookPhase.BEFORE, canceled)
    )
    token = bind_inference_scope("job:cancel")
    try:
        agent = make_agent(service)
        with pytest.raises(asyncio.CancelledError):
            await agent.execute_tool_call(
                {"name": "fictional_tool", "id": "c", "input": {}}
            )
        snapshot = service.action_trace_collector.snapshot(agent.name)
        assert snapshot.events[-1].status == "canceled"
        assert "unfinished_actions" not in snapshot.incomplete_reasons
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", ["safe", "tasks"])
async def test_parallel_batch_positions_and_overlap(service, runner):
    gate = asyncio.Event()
    started = []

    async def execute(name, arguments):
        started.append(arguments["index"])
        if len(started) == 3:
            gate.set()
        await asyncio.wait_for(gate.wait(), 1)
        await asyncio.sleep((2 - arguments["index"]) * 0.002)
        return "done"

    agent = make_agent(service, execute)
    tools = [
        {"name": "fictional_tool", "id": f"p-{index}", "input": {"index": index}}
        for index in range(3)
    ]
    token = bind_inference_scope("job:parallel")
    try:
        if runner == "safe":
            await parallel_executor.execute_tools_in_parallel(
                tools, agent.execute_tool_call
            )
        else:
            await parallel_executor.execute_tool_tasks_in_parallel(
                tools, agent.execute_tool_call
            )
        await agent.execute_tool_call(
            {"name": "fictional_tool", "id": "sequential", "input": {"index": 0}}
        )
        snapshot = service.action_trace_collector.snapshot(agent.name)
        starts = [event for event in snapshot.events if event.status == "started"]
        ends = [event for event in snapshot.events if event.status == "succeeded"]
        assert [event.ordinal for event in starts[:3]] == [0, 1, 2]
        assert len({event.batch for event in starts[:3]}) == 1
        assert starts[-1].batch > starts[0].batch
        assert max(event.started_at for event in starts[:3]) < min(
            event.completed_at for event in ends[:3]
        )
        assert [event.tool_call_id for event in ends[:3]] == ["p-2", "p-1", "p-0"]
    finally:
        reset_inference_scope(token)


@pytest.mark.parametrize(
    "flag,status", [("is_rejected", "rejected"), ("is_error", "not_performed")]
)
def test_unperformed_formatting_is_runtime_evidence(service, flag, status):
    agent = make_agent(service)
    token = bind_inference_scope("job:unperformed")
    try:
        agent.format_message(
            MessageType.ToolResult,
            {
                "tool_use": {"id": "unperformed", "name": "fictional_tool"},
                flag: True,
                "tool_result": "secret text not retained",
            },
        )
        event = service.action_trace_collector.snapshot(agent.name).events[0]
        assert event.status == status
        assert event.started_at is None
        assert "secret text" not in repr(event)
    finally:
        reset_inference_scope(token)


def test_large_sensitive_payloads_are_never_inspected_or_retained():
    class Opaque:
        def __str__(self):
            raise AssertionError("arbitrary stringify")

        def __iter__(self):
            raise AssertionError("recursive scan")

        def __deepcopy__(self, memo):
            raise AssertionError("extra deep copy")

    collector = ActionTraceCollector(HookRegistry.get_instance())
    token = collector.bind("job:sensitive")
    opaque = Opaque()
    try:
        ctx = {**context(), "tool_input": opaque, "tool_use": opaque}
        assert collector.before_tool(ctx) is ctx
        result = {
            "tool_result": {"password": "NEVER_RETAIN", "data": "x" * 5_000_000},
            "opaque": opaque,
        }
        assert collector.after_tool(ctx, result) is result
        snapshot = collector.snapshot("Engineer")
        encoded = json.dumps(asdict(snapshot))
        assert "NEVER_RETAIN" not in encoded
        assert len(encoded) < 3000
        assert not any(
            value is opaque or value is result
            for value in vars(collector._turn.get()).values()
        )
    finally:
        collector.reset(token)


def test_capped_no_tool_and_unfinished_states(service):
    collector = ActionTraceCollector(HookRegistry.get_instance(), max_events=3)
    snapshot = trace(collector, calls=20)
    assert len(snapshot.events) == 3
    assert snapshot.observed_count == 41
    assert "event_cap" in snapshot.incomplete_reasons
    empty = trace(collector, calls=0)
    assert len(empty.events) == 1
    assert empty.events[0].kind == "response"
    token = collector.bind("job:unfinished")
    try:
        collector.before_tool(context())
        assert "unfinished_actions" in collector.snapshot("Engineer").incomplete_reasons
    finally:
        collector.reset(token)


def test_ready_summary_is_redacted_bounded_and_model_reported(service):
    collector = service.action_trace_collector
    token = bind_inference_scope("job:summaries")
    summary = Mock()
    summary.get_ready_summary.return_value = (
        "Saved fictional widget; password=NEVER_RETAIN."
    )
    try:
        collector.before_tool(context())
        collector.after_tool(context(), object())
        collector.complete("Engineer")
        snapshot = collector.snapshot("Engineer", summary, "job:summaries")
        event = snapshot.events[1]
        assert "NEVER_RETAIN" not in event.result_summary
        assert event.provenance == "runtime_observed"
        assert event.summary_provenance == "model_reported"
        assert event.summary_availability == "ready"
        assert summary.mock_calls == [
            (("get_ready_summary"), ("job:summaries", "c1", "fictional_tool"), {})
        ]
        summary.get_ready_summary.return_value = None
        assert (
            collector.snapshot("Engineer", summary).events[1].summary_availability
            == "unavailable"
        )
        summary.get_ready_summary.side_effect = RuntimeError()
        assert (
            collector.snapshot("Engineer", summary).events[1].summary_availability
            == "lookup_failed"
        )
    finally:
        reset_inference_scope(token)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_schema_upgrade_and_readonly_legacy(tmp_path, version):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        statements = (
            *SCHEMA_V1,
            *(RESOURCE_SCHEMA if version >= 2 else ()),
            *(MEMOPAD_SCHEMA if version >= 3 else ()),
        )
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
        connection.execute(f"PRAGMA user_version={version}")
        if version >= 2:
            parsed, values = SQLiteMemoryRepository._prepare_record(record())
            SQLiteMemoryRepository._write_record(connection, record(), parsed, values)
    readonly = SQLiteMemoryRepository(path, read_only=True, allow_legacy_schema=True)
    with pytest.raises(SQLiteMemoryError):
        SQLiteMemoryRepository(path, read_only=True)
    repository = SQLiteMemoryRepository(path)
    repository.check_integrity()
    with repository._transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    if version >= 2:
        assert readonly.get_record("memory", "Engineer").document == DOCUMENT
        assert (
            repository.get_action_traces(["memory"], "Engineer")["memory"][
                "availability"
            ]
            == "legacy_no_trace"
        )


def test_fresh_schema_foreign_keys_soft_delete_and_atomic_rollback(service):
    repository = service._repository
    snapshot = trace(service.action_trace_collector)
    with pytest.raises(SQLiteMemoryError):
        repository.persist_turn(
            record(), "op", replace(snapshot, agent_name="AnotherAgent")
        )
    assert counts(repository) == (0, 0, 0)
    memory_id = repository.persist_turn(record(), "op", snapshot)
    assert counts(repository) == (1, 1, 3)
    with repository._transaction(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_action VALUES ('absent','turn',0,0,0,'{}')"
            )
        connection.execute(
            "UPDATE memory_record SET status='deleted' WHERE id=?", (memory_id,)
        )
    assert counts(repository) == (1, 0, 0)


def test_append_multiple_turns_and_repeated_snapshot(service):
    repository = service._repository
    first = trace(service.action_trace_collector)
    second = trace(service.action_trace_collector, calls=2)
    key = repository.persist_turn(record(), "op1", first)
    assert repository.persist_turn(record(), "op2", second) == key
    repository.persist_turn(record(), "op1", first)
    result = repository.get_action_traces([key], "Engineer")[key]
    assert result["availability"] == "supported"
    assert len(result["episodes"]) == 2
    assert counts(repository) == (1, 2, 8)
    assert {episode["session_id"] for episode in result["episodes"]} == {"previous"}


@pytest.mark.parametrize("mode", ["id", "topic", "conversation", "retention"])
def test_forget_and_retention_cascade(service, mode):
    repository = service._repository
    key = repository.persist_turn(record(), "op", trace(service.action_trace_collector))
    if mode == "id":
        assert service.forget_ids([key], "Engineer")["count"] == 1
    elif mode == "topic":
        assert service.forget_topic("widget", agent_name="Engineer")["count"] == 1
    elif mode == "conversation":
        assert service.delete_by_conversation_id("previous")["count"] == 1
    else:
        assert service.cleanup_old_memories(1) == 1
    assert counts(repository) == (0, 0, 0)


def test_consolidated_family_hydrates_source_trace_and_forgets_all(service):
    repository = service._repository
    first = repository.persist_turn(
        record("source", "source-session"), "op1", trace(service.action_trace_collector)
    )
    target = repository.persist_turn(
        record("target", "target-session"), "op2", trace(service.action_trace_collector)
    )
    repository.apply_consolidation(target, "Engineer", None, [first])
    result = repository.get_action_traces([target, first], "Engineer")
    assert len(result[target]["episodes"]) == 2
    assert len(result[first]["episodes"]) == 2
    assert {episode["memory_id"] for episode in result[target]["episodes"]} == {
        first,
        target,
    }
    assert service.forget_ids([target], "Engineer")["count"] == 2
    assert counts(repository) == (0, 0, 0)


def test_legacy_prefix_empty_missing_and_scope_states(service):
    repository = service._repository
    repository.upsert_record(record("legacy", "legacy-session"))
    assert (
        repository.get_action_traces(["legacy"], "Engineer")["legacy"]["availability"]
        == "legacy_no_trace"
    )
    repository.persist_turn(
        record("legacy", "legacy-session"), "new", trace(service.action_trace_collector)
    )
    legacy = repository.get_action_traces(["legacy"], "Engineer")["legacy"]
    assert legacy["availability"] == "truncated"
    assert "legacy_prefix" in legacy["incomplete_reasons"]
    empty = repository.persist_turn(
        record("empty", "empty-session"),
        "empty-op",
        trace(service.action_trace_collector, calls=0),
    )
    assert (
        repository.get_action_traces([empty], "Engineer")[empty]["availability"]
        == "supported_but_empty"
    )
    assert (
        repository.get_action_traces([empty], "Other")[empty]["availability"]
        == "not_found"
    )
    assert (
        repository.get_action_traces(["absent"], "Engineer")["absent"]["availability"]
        == "not_found"
    )


def test_hydration_caps_and_ordering(service):
    repository = service._repository
    snapshot = trace(service.action_trace_collector, calls=127)
    for index in range(5):
        repository.persist_turn(
            record(),
            f"op-{index}",
            replace(snapshot, turn_id=f"turn-{index}", started_at=index + 1),
        )
    result = repository.get_action_traces(["memory"], "Engineer")["memory"]
    assert result["availability"] == "truncated"
    assert "hydration_action_cap" in result["incomplete_reasons"]
    assert sum(len(episode["actions"]) for episode in result["episodes"]) == 1024
    for index in range(5, 34):
        repository.persist_turn(
            record(),
            f"op-{index}",
            replace(
                snapshot,
                turn_id=f"turn-{index}",
                started_at=index + 1,
                events=(),
                stored_count=0,
                observed_count=0,
            ),
        )
    result = repository.get_action_traces(["memory"], "Engineer")["memory"]
    assert len(result["episodes"]) == 32
    assert "hydration_episode_cap" in result["incomplete_reasons"]
    assert [episode["started_at"] for episode in result["episodes"]] == sorted(
        episode["started_at"] for episode in result["episodes"]
    )
    for episode in result["episodes"]:
        positions = [
            (item["batch"], item["ordinal"], item["event_index"])
            for item in episode["actions"]
        ]
        assert positions == sorted(positions)


def test_normal_search_never_reads_action_tables(service):
    repository = service._repository
    repository.persist_turn(record(), "op", trace(service.action_trace_collector))
    original = repository._connect
    statements = []

    def connect(**kwargs):
        connection = original(**kwargs)
        connection.set_trace_callback(statements.append)

        def authorize(action, table, column, db, source):
            if action == sqlite3.SQLITE_READ and table in {
                "memory_episode",
                "memory_action",
            }:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    with patch.object(repository, "_connect", side_effect=connect):
        result = MemorySearchCoordinator(repository).search(
            MemoryQuery("fictional widget", "Engineer", "current")
        )
    assert result.hits
    assert not any(
        "memory_episode" in sql or "memory_action" in sql for sql in statements
    )


@pytest.mark.asyncio
async def test_summary_discovery_vs_selected_evidence_and_full_hydration(service):
    repository = service._repository
    repository.persist_turn(record(), "op", trace(service.action_trace_collector))
    handler = get_memory_retrieve_tool_handler(MemoryToolContext("Engineer", service))
    with patch.object(
        service, "get_action_traces", wraps=service.get_action_traces
    ) as hydrate:
        summary = await handler(query="fictional widget", detail="summary")
        assert "ActionTrace:" not in summary
        hydrate.assert_not_called()
        evidence_discovery = await handler(query="fictional widget", detail="evidence")
        assert "ActionTrace:" not in evidence_discovery
        hydrate.assert_not_called()
        for detail in ("evidence", "full"):
            result = await handler(
                query="fictional widget", detail=detail, memory_ids=["memory"]
            )
            assert "ActionTrace:" in result
            assert "runtime_observed" in result
            assert "not authorization" in result
        assert hydrate.call_count == 2


def test_chroma_default_capability_and_no_setup_registration():
    from AgentCrew.modules.memory.chroma_service import ChromaMemoryService

    service = ChromaMemoryService.__new__(ChromaMemoryService)
    assert service.supports_action_trace is False
    assert (
        service.get_action_traces(["legacy"])["legacy"]["availability"]
        == "unsupported_backend"
    )
    assert service.get_action_traces.__func__ is BaseMemoryService.get_action_traces
    assert tool_result_summary._trace_collector is None
    assert parallel_executor._trace_collector is None
    assert not any(
        hook.owner and hook.owner.startswith("action_trace:")
        for hook in HookRegistry.get_instance().get_hooks()
    )
    setup = Path("AgentCrew/setup.py").read_text()
    sqlite_branch = setup[
        setup.index('if memory_engine == "sqlite":') : setup.index(
            'logger.info("Chroma memory service initialized")'
        )
    ]
    assert "enable_action_trace" in sqlite_branch.split("                else:")[0]
    assert "enable_action_trace" not in sqlite_branch.split("                else:")[1]
    service._worker = SimpleNamespace(stop=lambda: None)


@pytest.mark.asyncio
async def test_worker_batched_persistence_no_extra_builder_calls_and_receipts(service):
    agent = make_agent(service)
    worker = service._worker
    worker._builder = SimpleNamespace(
        build=AsyncMock(
            return_value=SimpleNamespace(
                document=DOCUMENT,
                timestamp=1,
                header="fictional",
                memopad_candidates=(),
            )
        )
    )
    worker._consolidate_related_memories = AsyncMock()
    token = bind_inference_scope("job:worker")
    try:
        await agent.execute_tool_call(
            {"name": "fictional_tool", "id": "c", "input": {}}
        )
        agent.store_memory_if_available("fictional", [], "done", session_id="previous")
        data = worker._conversation_queue.get_nowait()
        assert data["operation_id"] != data["action_trace"].turn_id
        assert data["action_trace"].events[-1].kind == "response"
        await worker._store_conversation_internal(data)
        receipt = service.trace_storage_receipts[data["action_trace"].turn_id]
        assert receipt["status"] == "stored"
        assert worker._builder.build.await_count == 1
        assert counts(service._repository) == (1, 1, 3)
        worker._builder.build.side_effect = ValueError("fictional")
        await worker._store_conversation_internal(data)
        assert receipt["status"] == "failed"
        worker._builder.build.side_effect = None
        worker._builder.build.return_value = None
        await worker._store_conversation_internal(data)
        assert receipt["status"] == "skipped"
    finally:
        reset_inference_scope(token)


def test_queue_saturation_is_nonfatal_and_observable(service):
    service._worker._conversation_queue = queue.Queue(maxsize=1)
    service._worker._conversation_queue.put({"fictional": True})
    snapshot = trace(service.action_trace_collector)
    assert (
        service.store_conversation(
            "fictional", ["done"], "Engineer", "previous", action_trace=snapshot
        )
        == []
    )
    assert (
        service.trace_storage_receipts[snapshot.turn_id]["status"] == "queue_rejected"
    )
    assert service._worker.get_queue_status()["queue_size"] == 1
    assert counts(service._repository) == (0, 0, 0)


def test_write_shape_uses_one_transaction_and_executemany(service):
    repository = service._repository
    original = repository._connect
    operations = []

    class Connection:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, key):
            return getattr(self.wrapped, key)

        def execute(self, sql, *args):
            operations.append(("execute", sql))
            return self.wrapped.execute(sql, *args)

        def executemany(self, sql, values):
            operations.append(("executemany", sql))
            return self.wrapped.executemany(sql, values)

    with patch.object(
        repository,
        "_connect",
        side_effect=lambda **kwargs: Connection(original(**kwargs)),
    ):
        repository.persist_turn(
            record(), "op", trace(service.action_trace_collector, calls=100)
        )
    assert sum(sql == "BEGIN IMMEDIATE" for _, sql in operations) == 1
    assert sum(sql == "COMMIT" for _, sql in operations) == 1
    assert (
        sum(
            kind == "executemany" and "memory_action" in sql for kind, sql in operations
        )
        == 1
    )
    assert not any(
        kind == "execute" and "INSERT INTO memory_action" in sql
        for kind, sql in operations
    )
    assert counts(repository) == (1, 1, 201)


def test_collector_constant_bound_configuration():
    with pytest.raises(ValueError):
        ActionTraceCollector(
            HookRegistry.get_instance(), max_events=MAX_TRACE_EVENTS + 1
        )


@pytest.mark.asyncio
async def test_nested_tool_does_not_inherit_parent_batch_position(service):
    collector = service.action_trace_collector
    child = make_agent(service, name="Child")

    async def parent_execute(name, arguments):
        await child.execute_tool_call(
            {"name": "child_tool", "id": "child", "input": {}}
        )
        return "done"

    parent = make_agent(service, parent_execute)
    token = bind_inference_scope("job:nested")
    try:
        await parallel_executor.execute_tools_in_parallel(
            [{"name": "delegate", "id": "parent", "input": {}}],
            parent.execute_tool_call,
        )
        parent_trace = collector.snapshot("Engineer")
        child_trace = collector.snapshot("Child")
        assert parent_trace.turn_id == child_trace.turn_id
        assert parent_trace.events[0].batch != child_trace.events[0].batch
        assert all(event.agent_name == "Child" for event in child_trace.events)
    finally:
        reset_inference_scope(token)


def test_duplicate_ids_are_explicitly_incomplete(service):
    collector = service.action_trace_collector
    token = collector.bind("job:duplicate")
    try:
        for _ in range(2):
            collector.before_tool(context())
            collector.after_tool(context(), None)
        assert (
            "duplicate_tool_call_id"
            in collector.snapshot("Engineer").incomplete_reasons
        )
    finally:
        collector.reset(token)


@pytest.mark.parametrize("malformed", ["oversized", "duplicate_index", "wrong_owner"])
def test_malformed_trace_rolls_back(service, malformed):
    snapshot = trace(service.action_trace_collector)
    if malformed == "oversized":
        snapshot = replace(
            snapshot, events=snapshot.events * 100, stored_count=300, observed_count=300
        )
    elif malformed == "duplicate_index":
        snapshot = replace(
            snapshot,
            events=tuple(replace(event, event_index=0) for event in snapshot.events),
        )
    else:
        snapshot = replace(
            snapshot,
            events=tuple(
                replace(event, agent_name="Other") for event in snapshot.events
            ),
        )
    with pytest.raises(SQLiteMemoryError):
        service._repository.persist_turn(record(), "op", snapshot)
    assert counts(service._repository) == (0, 0, 0)


@pytest.mark.asyncio
async def test_hydration_failure_preserves_full_memory_text(service):
    service._repository.persist_turn(
        record(), "op", trace(service.action_trace_collector)
    )
    handler = get_memory_retrieve_tool_handler(MemoryToolContext("Engineer", service))
    with patch.object(
        service, "get_action_traces", side_effect=SQLiteMemoryError("fictional")
    ):
        result = await handler(
            query="fictional widget", detail="full", memory_ids=["memory"]
        )
    assert DOCUMENT in result
    assert '"availability": "unavailable"' in result


def test_hydration_uses_three_batched_selects(service):
    repository = service._repository
    for index in range(8):
        repository.persist_turn(
            record(f"memory-{index}", f"session-{index}"),
            f"op-{index}",
            trace(service.action_trace_collector),
        )
    statements = []
    original = repository._connect

    def connect(**kwargs):
        connection = original(**kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    with patch.object(repository, "_connect", side_effect=connect):
        result = repository.get_action_traces(
            [f"memory-{index}" for index in range(8)], "Engineer"
        )
    assert len(result) == 8
    reads = [sql for sql in statements if sql.startswith(("SELECT", "WITH"))]
    assert len(reads) == 3
    assert sum("memory_action" in sql for sql in reads) == 1
    assert sum("memory_episode" in sql for sql in reads) == 1


def test_resource_references_are_opaque_and_bounded(service):
    from AgentCrew.modules.memory.resources import capture_resource

    token = bind_inference_scope("job:resources")
    try:
        for index in range(40):
            capture_resource(
                f"https://fictional.invalid/resource/{index}?token=NEVER_RETAIN"
            )
        service.action_trace_collector.complete("Engineer")
        snapshot = service.action_trace_collector.snapshot("Engineer")
        assert len(snapshot.resource_references) == 32
        assert "NEVER_RETAIN" not in repr(snapshot)
        assert all(len(value) == 64 for value in snapshot.resource_references)
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_final_response_hook_never_reads_thinking(service):
    token = bind_inference_scope("job:response")
    try:
        result = object()
        returned = await HookRegistry.get_instance().run_after(
            HookPoints.RESPONSE_COMPLETE,
            result=result,
            agent_name="Engineer",
            thinking=object(),
            canceled=True,
        )
        assert returned is result
        snapshot = service.action_trace_collector.snapshot("Engineer")
        assert len(snapshot.events) == 1
        assert snapshot.events[0].status == "canceled"
        assert "thinking" not in asdict(snapshot.events[0])
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
async def test_shared_agent_runner_queues_no_tool_trace_before_scope_reset(service):
    from AgentCrew.modules.agents.agent_runner import run_agent_loop
    from AgentCrew.modules.llm.token_usage import TokenUsage

    agent = make_agent(service)

    class Stream:
        def __init__(self):
            self.done = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.done:
                raise StopAsyncIteration
            self.done = True
            return "Fictional response", "Fictional response", None

        async def aclose(self):
            pass

        def format_assistant_message(self, response, **kwargs):
            return {"role": "assistant", "content": response}

    def process_messages(history, callback, **kwargs):
        callback([], TokenUsage())
        return Stream()

    agent.process_messages = process_messages
    history = [{"role": "user", "content": "Fictional request"}]
    response, _ = await run_agent_loop(agent, history, scope_id="job:shared")
    assert response == "Fictional response"
    data = service._worker._conversation_queue.get_nowait()
    assert data["action_trace"].inference_scope == "job:shared"
    assert [event.kind for event in data["action_trace"].events] == ["response"]
    assert service.action_trace_collector.snapshot("Engineer") is None


def test_real_chroma_construction_registers_no_trace(tmp_path, monkeypatch):
    from AgentCrew.modules.memory.chroma_service import ChromaMemoryService

    monkeypatch.setenv("MEMORYDB_PATH", str(tmp_path / "legacy" / "memory"))
    with patch.object(MemoryWorker, "start"):
        legacy = ChromaMemoryService()
    try:
        assert not hasattr(legacy, "action_trace_collector")
        assert tool_result_summary._trace_collector is None
        assert parallel_executor._trace_collector is None
        assert not any(
            hook.owner and hook.owner.startswith("action_trace:")
            for hook in HookRegistry.get_instance().get_hooks()
        )
    finally:
        legacy.shutdown()


@pytest.mark.parametrize("name", ["Kỹ sư", "Engineer (review)", "O'Connor"])
def test_configured_agent_names_keep_exact_trace_ownership(service, name):
    snapshot = trace(service.action_trace_collector, name=name)
    assert snapshot.agent_name == name
    assert all(event.agent_name == name for event in snapshot.events)
    key = service._repository.persist_turn(
        replace(record(), agent_name=name), "op", snapshot
    )
    assert service.get_action_traces([key], name)[key]["availability"] == "supported"


def test_unsupported_agent_identifier_does_not_drop_semantic_storage(service):
    token = bind_inference_scope("job:long-name")
    try:
        name = "x" * 161
        assert service.store_conversation("fictional", ["done"], name, "previous")
        data = service._worker._conversation_queue.get_nowait()
        assert data["agent_name"] == name
        assert "action_trace" not in data
    finally:
        reset_inference_scope(token)


def test_runtime_error_envelope_is_authoritative_without_result_scan(service):
    collector = service.action_trace_collector
    token = bind_inference_scope("job:error-envelope")
    try:
        collector.before_tool(context())
        collector.after_tool(context(), {"is_error": True, "tool_result": object()})
        assert collector.snapshot("Engineer").events[-1].status == "failed"
    finally:
        reset_inference_scope(token)


def test_redaction_growth_and_summary_truncation_are_bounded(service):
    collector = service.action_trace_collector
    token = bind_inference_scope("job:summary-bounds")
    try:
        collector.before_tool(context())
        collector.after_tool(context(), None)
        collector.complete("Engineer")
        summaries = SimpleNamespace(get_ready_summary=lambda *args: "password=x " * 200)
        snapshot = collector.snapshot("Engineer", summaries)
        event = snapshot.events[1]
        assert len(event.result_summary) <= 1024
        assert event.summary_truncated
        assert "password=x" not in event.result_summary
        service._repository.persist_turn(record(), "op", snapshot)
    finally:
        reset_inference_scope(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", ["safe", "tasks"])
async def test_single_tool_helper_preserves_caller_task_context(service, runner):
    from contextvars import ContextVar

    variable = ContextVar("fictional_single_tool_context", default="before")

    async def execute(tool):
        variable.set("after")
        return "done"

    tools = [{"id": "single", "name": "fictional_tool", "input": {}}]
    token = bind_inference_scope("job:single")
    try:
        function = (
            parallel_executor.execute_tools_in_parallel
            if runner == "safe"
            else parallel_executor.execute_tool_tasks_in_parallel
        )
        await function(tools, execute)
        assert variable.get() == "after"
    finally:
        reset_inference_scope(token)


@pytest.mark.parametrize("kind", ["standalone", "production"])
def test_latest_v3_migration_resume_stays_semantic_only(tmp_path, monkeypatch, kind):
    from verification.memory import test_migration_schema_resume as resume_checks

    monkeypatch.setitem(
        resume_checks.REFERENCES, 3, "42fe4ccf8ea0204773bc13c590e6265866c348ce"
    )
    case = resume_checks.partial_destination(tmp_path, monkeypatch, kind, version=3)
    report = case.resume()
    assert report["status"] == "verified"
    repository = SQLiteMemoryRepository(case.destination)
    assert counts(repository) == (3, 0, 0)
    with repository._transaction() as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM memory_record")]
    hydrated = repository.get_action_traces(ids, "FictionalAgent")
    assert all(item["availability"] == "legacy_no_trace" for item in hydrated.values())
