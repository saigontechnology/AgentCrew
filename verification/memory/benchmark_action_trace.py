"""Reproducible offline ActionTrace microbenchmark, not an application latency SLA.

Run from the repository root: uv run --no-sync python -B -m verification.memory.benchmark_action_trace
Only fictional data and temporary databases are used. No providers are invoked.
"""

from __future__ import annotations

import asyncio
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from loguru import logger

from AgentCrew.modules.events.hooks import HookPoints, HookRegistry
from AgentCrew.modules.memory.action_trace import ActionTraceCollector
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.types import MemoryQuery, MemoryRecord

DOCUMENT = "<MEMORY><HEAD>Fictional benchmark widget</HEAD><CONVERSATION_NOTES><NOTE>Fictional widget saved.</NOTE></CONVERSATION_NOTES></MEMORY>"
TOOLS_PER_TURN = 64
TURNS_PER_SAMPLE = 40
SAMPLES = 5
WRITE_TURNS = 100


def elapsed_ms(operation, repetitions=1):
    start = time.perf_counter()
    for _ in range(repetitions):
        operation()
    return (time.perf_counter() - start) * 1000 / repetitions


async def hooks_sample(enabled, payload):
    HookRegistry.reset_instance()
    hooks = HookRegistry.get_instance()
    collector = ActionTraceCollector(hooks) if enabled else None
    if collector is not None:
        collector.register()
    start = time.perf_counter()
    for _ in range(TURNS_PER_SAMPLE):
        token = collector.bind("job:benchmark") if collector is not None else None
        for ordinal in range(TOOLS_PER_TURN):
            context = {
                "agent_name": "Engineer",
                "tool_id": f"call-{ordinal}",
                "tool_name": "fictional_tool",
            }
            await hooks.run_before(HookPoints.TOOL_EXECUTE, **context)
            await hooks.run_after(HookPoints.TOOL_EXECUTE, result=payload, **context)
        if collector is not None:
            collector.reset(token)
    elapsed = (
        (time.perf_counter() - start) * 1_000_000 / (TOOLS_PER_TURN * TURNS_PER_SAMPLE)
    )
    if collector is not None:
        collector.close()
    return elapsed


def snapshot(collector):
    token = collector.bind("job:benchmark")
    try:
        for ordinal in range(TOOLS_PER_TURN):
            context = {
                "agent_name": "Engineer",
                "tool_id": f"call-{ordinal}",
                "tool_name": "fictional_tool",
            }
            collector.before_tool(context)
            collector.after_tool(context, None)
        collector.complete("Engineer")
        result = collector.snapshot("Engineer")
        snapshot_ms = elapsed_ms(lambda: collector.snapshot("Engineer"), 100)
        return result, snapshot_ms
    finally:
        collector.reset(token)


async def main():
    logger.disable("AgentCrew")
    small = {"tool_result": "done"}
    large = {"tool_result": {"password": "fictional-secret", "data": "x" * 5_000_000}}
    baseline, traced, traced_large = [], [], []
    for _ in range(SAMPLES):
        baseline.append(await hooks_sample(False, small))
        traced.append(await hooks_sample(True, small))
        traced_large.append(await hooks_sample(True, large))
    collector = ActionTraceCollector(HookRegistry.get_instance())
    episode, snapshot_ms = snapshot(collector)
    output = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "samples": SAMPLES,
        "tools_per_sample": TOOLS_PER_TURN * TURNS_PER_SAMPLE,
        "hooks_baseline_median_us_per_tool": statistics.median(baseline),
        "hooks_trace_median_us_per_tool": statistics.median(traced),
        "hooks_trace_5mb_result_median_us_per_tool": statistics.median(traced_large),
        "hooks_delta_median_us_per_tool": statistics.median(traced)
        - statistics.median(baseline),
        "snapshot_129_events_mean_ms": snapshot_ms,
    }
    with tempfile.TemporaryDirectory(prefix="agentcrew-trace-benchmark-") as directory:
        baseline_repo = SQLiteMemoryRepository(Path(directory) / "baseline.db")
        traced_repo = SQLiteMemoryRepository(Path(directory) / "traced.db")
        timings = {"baseline": [], "traced": []}
        for index in range(WRITE_TURNS):
            record = MemoryRecord(
                f"memory-{index}", "Engineer", f"session-{index}", DOCUMENT, 1, 1
            )
            for label, repository, trace_data in (
                ("baseline", baseline_repo, None),
                ("traced", traced_repo, replace(episode, turn_id=f"turn-{index}")),
            ):
                timings[label].append(
                    elapsed_ms(
                        lambda repository=repository, record=record, index=index, trace_data=trace_data: (
                            repository.persist_turn(record, f"op-{index}", trace_data)
                        )
                    )
                )
        selected = [f"memory-{index}" for index in range(6)]
        hydrated = traced_repo.get_action_traces(selected, "Engineer")
        assert all(item["availability"] == "supported" for item in hydrated.values())
        output["write_turns"] = WRITE_TURNS
        output["events_per_turn"] = episode.stored_count
        output["write_baseline_median_ms"] = statistics.median(timings["baseline"])
        output["write_trace_median_ms"] = statistics.median(timings["traced"])
        output["hydrate_6_memories_774_events_mean_ms"] = elapsed_ms(
            lambda: traced_repo.get_action_traces(selected, "Engineer"), 50
        )
        query = MemoryQuery("fictional widget", "Engineer", "current", limit=5)
        for label, repository in (("baseline", baseline_repo), ("traced", traced_repo)):
            coordinator = MemorySearchCoordinator(repository)
            output[f"search_{label}_100_records_mean_ms"] = elapsed_ms(
                lambda coordinator=coordinator: coordinator.search(query), 100
            )
        with traced_repo._transaction() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN WITH selected(memory_id,turn_id) AS (VALUES (?,?)) "
                "SELECT a.* FROM selected s JOIN memory_action a ON a.memory_id=s.memory_id AND a.turn_id=s.turn_id",
                ("memory-0", "turn-0"),
            ).fetchall()
            output["action_hydration_query_plan"] = [row[3] for row in plan]
        traced_repo.check_integrity()
    output["scope"] = (
        "Warm offline microbenchmark; excludes provider latency, cold startup, end-to-end UI and production-size corpora. No new LLM calls."
    )
    print(json.dumps(output, indent=2))
    HookRegistry.reset_instance()


if __name__ == "__main__":
    asyncio.run(main())
