"""Regression tests for the JSONL FileAgentCrewTaskStore.

Covers: steady-state saves not rereading the file, legacy migration,
single-line JSONL append behavior, torn trailing-line tolerance, threshold and
terminal compaction with count resets, defensive copies on the get() disk-miss
path, and restart reads across all file formats.
"""

from __future__ import annotations

import json

import pytest
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import (
    Artifact,
    ListTasksRequest,
    Part,
    Task,
    TaskState,
    TaskStatus,
)

from AgentCrew.modules.a2a import session_store as ss
from AgentCrew.modules.a2a.session_store import FileAgentCrewTaskStore


def _read_lines(path: str) -> list[str]:
    return [ln for ln in open(path).read().splitlines() if ln.strip()]


def _write_lines(path: str, *lines: str) -> None:
    """Seed a task file with raw JSONL lines (sync helper for async tests)."""
    with open(path, "w") as f:
        f.writelines(lines)


def _write_legacy(path: str, data: dict) -> None:
    """Seed a legacy single-object JSON task file (sync helper for async tests)."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _working(task_id: str, text: str = "") -> Task:
    parts = [Part(text=text)] if text else []
    return Task(
        id=task_id,
        context_id="c",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        artifacts=[Artifact(artifact_id="a", parts=parts)] if text else [],
    )


# -- FIX 3: steady-state saves must not reread the file ------------------------


@pytest.mark.asyncio
async def test_steady_state_saves_do_not_reread_file(tmp_path, monkeypatch):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("t1", "default")

    _write_lines(
        path,
        ss._task_to_jsonl_line(_working("t1")),
        ss._task_to_jsonl_line(
            Task(
                id="t1",
                context_id="c",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                artifacts=[Artifact(artifact_id="a", parts=[Part(text="x")])],
            )
        ),
    )

    inspect_calls = {"n": 0}
    original_inspect = ss._inspect_task_file

    def counting_inspect(p):
        inspect_calls["n"] += 1
        return original_inspect(p)

    monkeypatch.setattr(ss, "_inspect_task_file", counting_inspect)

    for i in range(5):
        await store.save(_working("t1", text="x" * (i + 1)), ctx)

    assert inspect_calls["n"] == 1, "only the first save may inspect the file"
    lines = _read_lines(path)
    assert len(lines) == 2 + 5
    assert store._line_counts[path] == 7
    got = await store.get("t1", ctx)
    assert got.artifacts[0].parts[0].text == "xxxxx"


# -- FIX 3: format handling -----------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_file_reads_and_migrates_on_save(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("legacy", "default")
    _write_legacy(
        path,
        {"id": "legacy", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"}},
    )

    got = await store.get("legacy", ctx)
    assert got is not None and got.id == "legacy"
    assert store._line_counts[path] == -1, "legacy files are marked for migration"

    await store.save(_working("legacy", text="new"), ctx)
    assert store._line_counts[path] == 1
    lines = _read_lines(path)
    assert len(lines) == 1
    assert lines[0].startswith("{")
    latest = await store.get("legacy", ctx)
    assert latest.artifacts[0].parts[0].text == "new"


@pytest.mark.asyncio
async def test_single_line_jsonl_appends_not_rewrites(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("one", "default")
    _write_lines(path, ss._task_to_jsonl_line(_working("one")))

    for i in range(3):
        await store.save(_working("one", text=str(i)), ctx)

    assert store._line_counts[path] == 4
    lines = _read_lines(path)
    assert len(lines) == 4, "a single-line JSONL file must be appended to"
    latest = await store.get("one", ctx)
    assert latest.artifacts[0].parts[0].text == "2"


@pytest.mark.asyncio
async def test_torn_trailing_line_resolves_to_last_valid_snapshot(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("torn", "default")
    _write_lines(
        path,
        ss._task_to_jsonl_line(_working("torn")),
        '{"id":"torn",',  # torn trailing line from a crash mid-append
    )

    got = await store.get("torn", ctx)
    assert got is not None and got.id == "torn"
    assert store._line_counts[path] == 1, "the torn line must not be counted"

    await store.save(_working("torn", text="after"), ctx)
    assert store._line_counts[path] == 2
    latest = await store.get("torn", ctx)
    assert latest.artifacts[0].parts[0].text == "after"


@pytest.mark.asyncio
async def test_threshold_compaction_resets_count(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    store._JSONL_COMPACTION_THRESHOLD = 3
    path = store._task_path("thresh", "default")
    _write_lines(
        path,
        ss._task_to_jsonl_line(_working("thresh")),
        ss._task_to_jsonl_line(
            Task(
                id="thresh",
                context_id="c",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                artifacts=[Artifact(artifact_id="a", parts=[Part(text="old")])],
            )
        ),
    )

    await store.save(_working("thresh", text="3"), ctx)  # appends: 2 -> 3 lines
    assert store._line_counts[path] == 3
    await store.save(_working("thresh", text="4"), ctx)  # 3 >= 3: compact
    assert store._line_counts[path] == 1
    lines = _read_lines(path)
    assert len(lines) == 1
    latest = await store.get("thresh", ctx)
    assert latest.artifacts[0].parts[0].text == "4"


@pytest.mark.asyncio
async def test_terminal_compaction_resets_count(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("t1", "default")
    await store.save(_working("t1"), ctx)
    await store.save(_working("t1", text="x"), ctx)
    assert store._line_counts[path] == 2

    await store.save(
        Task(
            id="t1",
            context_id="c",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[Artifact(artifact_id="a", parts=[Part(text="final")])],
        ),
        ctx,
    )
    assert store._line_counts[path] == 1
    lines = _read_lines(path)
    assert len(lines) == 1
    latest = await store.get("t1", ctx)
    assert latest.status.state == TaskState.TASK_STATE_COMPLETED
    assert latest.artifacts[0].parts[0].text == "final"


# -- FIX 4: defensive copy on the get() disk-miss path --------------------------


@pytest.mark.asyncio
async def test_get_first_disk_load_returns_isolated_copy(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    await store.save(
        Task(
            id="t1",
            context_id="c",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            artifacts=[Artifact(artifact_id="a", parts=[Part(text="orig")])],
        ),
        ctx,
    )

    # Fresh store forces the disk-miss path on the first get().
    store2 = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    first = await store2.get("t1", ctx)
    assert first is not None
    first.id = "MUTATED"
    first.artifacts[0].parts[0].text = "MUTATED"

    second = await store2.get("t1", ctx)
    assert second.id == "t1"
    assert second.artifacts[0].parts[0].text == "orig"
    assert first is not second


# -- restart reads across all formats -------------------------------------------


@pytest.mark.asyncio
async def test_restart_fresh_store_reads_all_formats(tmp_path):
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))

    await store.save(_working("a"), ctx)
    await store.save(_working("a", text="latest"), ctx)

    legacy_path = store._task_path("legacy", "default")
    _write_legacy(
        legacy_path,
        {"id": "legacy", "contextId": "c", "status": {"state": "TASK_STATE_COMPLETED"}},
    )

    torn_path = store._task_path("torn", "default")
    _write_lines(
        torn_path,
        ss._task_to_jsonl_line(_working("torn")),
        '{"id":"torn",',
    )

    fresh = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    assert (await fresh.get("a", ctx)).artifacts[0].parts[0].text == "latest"
    assert (await fresh.get("legacy", ctx)).id == "legacy"
    assert (await fresh.get("torn", ctx)).id == "torn"
    res = await fresh.list(ListTasksRequest(), ctx)
    assert sorted(t.id for t in res.tasks) == ["a", "legacy", "torn"]


# -- BLOCKER: append after a torn trailing line must survive a restart ---------


def _append_raw(path: str, text: str) -> None:
    """Append raw text without a trailing newline (sync helper for async tests)."""
    with open(path, "a") as f:
        f.write(text)


@pytest.mark.asyncio
async def test_append_after_torn_tail_survives_restart(tmp_path):
    """A snapshot saved after a torn fragment is readable by a fresh store."""
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("torn", "default")
    _write_lines(path, ss._task_to_jsonl_line(_working("torn")))
    _append_raw(path, '{"id":"torn",')  # torn fragment without trailing newline

    await store.save(_working("torn", text="newer"), ctx)
    assert store._line_counts[path] == 2, "torn fragment must not be counted"

    fresh = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    got = await fresh.get("torn", ctx)
    assert got is not None and got.id == "torn"
    assert got.artifacts[0].parts[0].text == "newer"

    lines = _read_lines(path)
    assert len(lines) == 3  # valid line, torn fragment, newer snapshot


@pytest.mark.asyncio
async def test_direct_save_after_torn_only_tail(tmp_path):
    """Direct save (no prior get) after a torn-only tail still recovers."""
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("onlytorn", "default")
    _append_raw(path, '{"id":"onlytorn",')

    await store.save(_working("onlytorn", text="newer"), ctx)
    assert store._line_counts[path] == 1

    fresh = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    got = await fresh.get("onlytorn", ctx)
    assert got is not None and got.id == "onlytorn"
    assert got.artifacts[0].parts[0].text == "newer"


@pytest.mark.asyncio
async def test_torn_tail_then_terminal_compaction(tmp_path):
    """Terminal compaction after a torn tail resets to a single valid line."""
    ctx = ServerCallContext()
    store = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    path = store._task_path("torn", "default")
    _write_lines(path, ss._task_to_jsonl_line(_working("torn")))
    _append_raw(path, '{"id":"torn",')
    await store.save(_working("torn", text="newer"), ctx)

    await store.save(
        Task(
            id="torn",
            context_id="c",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[Artifact(artifact_id="a", parts=[Part(text="final")])],
        ),
        ctx,
    )
    assert store._line_counts[path] == 1
    assert len(_read_lines(path)) == 1

    fresh = FileAgentCrewTaskStore(base_dir=str(tmp_path))
    got = await fresh.get("torn", ctx)
    assert got.status.state == TaskState.TASK_STATE_COMPLETED
    assert got.artifacts[0].parts[0].text == "final"
