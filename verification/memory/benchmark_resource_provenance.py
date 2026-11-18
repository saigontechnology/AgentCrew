"""Offline resource guardrail samples; no production instrumentation or frozen pins.

Run in independent processes against the same fictional corpus using --source-root
for the pre-feature checkout. Times are raw samples, not portable guarantees.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import platform
import sqlite3
import statistics
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns


def measure(callback, repeats=200):
    for _ in range(15):
        callback()
    samples = []
    for _ in range(repeats):
        start = perf_counter_ns()
        callback()
        samples.append((perf_counter_ns() - start) / 1000)
    ordered = sorted(samples)
    return {
        "unit": "microseconds",
        "samples": samples,
        "median": statistics.median(samples),
        "p95": ordered[int(len(ordered) * 0.95)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--records", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--compare-root", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from AgentCrew.modules.memory.record_parser import derive_memory_units
    from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator
    from AgentCrew.modules.memory.sqlite_repository import (
        SCHEMA_VERSION,
        SQLiteMemoryRepository,
    )
    from AgentCrew.modules.memory.types import MemoryQuery, MemoryRecord

    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "schema_version": SCHEMA_VERSION,
        "records": args.records,
        "benchmarks": {},
    }
    timings = report["benchmarks"]
    with TemporaryDirectory() as directory:
        repository = SQLiteMemoryRepository(Path(directory) / "benchmark.db")
        records = [
            MemoryRecord(
                f"memory-{index}",
                "FictionalAgent",
                f"session-{index}",
                f"<MEMORY><HEAD>Service{index} deployment runbook</HEAD><FLOW><OUTCOME>Validated service{index} rollout using deployment runbook</OUTCOME></FLOW><ENTITIES><ENTITY><NAME>Service{index}</NAME></ENTITY></ENTITIES></MEMORY>",
                1,
                1,
            )
            for index in range(args.records)
        ]
        repository.apply_migration_batch(
            [(record, f"fixture-{index}") for index, record in enumerate(records)],
            "fictional-benchmark",
        )
        coordinator = MemorySearchCoordinator(repository)
        for name, text in (
            ("normal_selective_search", "Service173"),
            ("normal_broad_search", "deployment runbook"),
        ):
            query = MemoryQuery(text, "FictionalAgent", "current-session")
            timings[name] = measure(
                lambda query=query: coordinator.search(query), args.repeats
            )
        if args.compare_root:
            root = args.compare_root.resolve() / "AgentCrew/modules/memory"
            spec = importlib.util.spec_from_file_location(
                "resource_baseline_memory",
                root / "__init__.py",
                submodule_search_locations=[str(root)],
            )
            baseline_package = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = baseline_package
            spec.loader.exec_module(baseline_package)
            old_repository_type = importlib.import_module(
                spec.name + ".sqlite_repository"
            ).SQLiteMemoryRepository
            old_types = importlib.import_module(spec.name + ".types")
            old_coordinator_type = importlib.import_module(
                spec.name + ".retrieval"
            ).MemorySearchCoordinator
            old_repository = old_repository_type(Path(directory) / "baseline.db")
            old_records = [
                old_types.MemoryRecord(
                    item.id,
                    item.agent_name,
                    item.session_id,
                    item.document,
                    item.created_at,
                    item.updated_at,
                )
                for item in records
            ]
            old_repository.apply_migration_batch(
                [(item, f"fixture-{index}") for index, item in enumerate(old_records)],
                "fictional-benchmark",
            )
            old_coordinator = old_coordinator_type(old_repository)
            for name, text in (
                ("normal_selective_search", "Service173"),
                ("normal_broad_search", "deployment runbook"),
            ):
                current_query = MemoryQuery(text, "FictionalAgent", "current-session")
                old_query = old_types.MemoryQuery(
                    text, "FictionalAgent", "current-session"
                )
                operations = (
                    lambda query=current_query: coordinator.search(query),
                    lambda query=old_query: old_coordinator.search(query),
                )
                for _ in range(20):
                    for operation in operations:
                        operation()
                samples = ([], [])
                for iteration in range(args.repeats):
                    for index in (0, 1) if iteration % 2 else (1, 0):
                        start = perf_counter_ns()
                        operations[index]()
                        samples[index].append((perf_counter_ns() - start) / 1000)
                for label, values in zip(
                    (name, name + "_paired_baseline"), samples, strict=True
                ):
                    timings[label] = {
                        "unit": "microseconds",
                        "samples": values,
                        "median": statistics.median(values),
                        "p95": sorted(values)[int(len(values) * 0.95)],
                    }
        if SCHEMA_VERSION >= 2:
            from AgentCrew.modules.memory.resources import (
                bind_resources,
                capture_resource,
                capture_tool_access,
                reset_resources,
                snapshot_resources,
            )

            token = bind_resources()
            try:
                context = {
                    "resolved_tool_name": "run_command",
                    "resolved_tool_input": {"command": "echo fictional"},
                }
                result = {"tool_result": "fictional output", "is_error": False}
                timings["non_resource_hook"] = measure(
                    lambda: capture_tool_access(context, result), 5000
                )
                sequence = 0

                def capture_turn():
                    nonlocal sequence
                    sequence += 1
                    turn = bind_resources()
                    try:
                        capture_resource(f"ui://fictional/screen/{sequence}")
                    finally:
                        reset_resources(turn)

                timings["resource_capture_with_scope"] = measure(capture_turn, 5000)
                file_context = {
                    "resolved_tool_name": "read_file",
                    "resolved_tool_input": {"file_path": "fictional/candidate.pdf"},
                }

                def capture_file_turn():
                    turn = bind_resources()
                    try:
                        capture_tool_access(file_context, result)
                    finally:
                        reset_resources(turn)

                timings["file_resource_hook_with_scope"] = measure(
                    capture_file_turn, 5000
                )
                for index in range(32):
                    capture_resource(f"urn:resource:{index}")
                timings["snapshot_32"] = measure(snapshot_resources, 5000)
            finally:
                reset_resources(token)
            for index, item in enumerate(records):
                document = item.document.replace(
                    "</MEMORY>",
                    f'<RESOURCES><RESOURCE uri="urn:fixture:{index}"><ENTITY_REF relation="source_document">Service{index}</ENTITY_REF></RESOURCE></RESOURCES></MEMORY>',
                )
                updated = MemoryRecord(
                    item.id, item.agent_name, item.session_id, document, 1, 1
                )
                records[index] = updated
            with repository._transaction(write=True) as connection:
                for item in records:
                    parsed, values = repository._prepare_record(item)
                    repository._write_record(connection, item, parsed, values)
            exact = MemoryQuery("urn:fixture:173", "FictionalAgent", "current-session")
            timings["exact_uri_search"] = measure(
                lambda: coordinator.search(exact), args.repeats
            )
            resource_xml = "".join(
                f'<RESOURCE uri="urn:write:{index}"><ENTITY_REF relation="source_document">Writer</ENTITY_REF></RESOURCE>'
                for index in range(32)
            )
            write_record = MemoryRecord(
                "writer",
                "FictionalAgent",
                "writer-session",
                f"<MEMORY><ENTITIES><ENTITY><NAME>Writer</NAME></ENTITY></ENTITIES><RESOURCES>{resource_xml}</RESOURCES></MEMORY>",
                1,
                1,
            )
            repository.upsert_record(write_record)
            parsed = derive_memory_units(write_record)
            with repository._transaction(write=True) as connection:
                samples = []
                for _ in range(200):
                    connection.execute(
                        "DELETE FROM memory_resource WHERE memory_id='writer'"
                    )
                    start = perf_counter_ns()
                    repository._write_resources(connection, "writer", parsed)
                    samples.append((perf_counter_ns() - start) / 1000)
                timings["write_32_resources_and_links_excluding_commit"] = {
                    "unit": "microseconds",
                    "samples": samples,
                    "median": statistics.median(samples),
                    "p95": sorted(samples)[190],
                }
    print(json.dumps(report))


if __name__ == "__main__":
    main()
