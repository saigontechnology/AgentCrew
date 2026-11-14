"""Standalone synthetic Phase 1 invariants; not the Phase 3 scored benchmark."""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.record_parser import checksum, derive_memory_units
from AgentCrew.modules.memory.retrieval import (
    MemorySearchCoordinator,
    format_search_result,
)
from AgentCrew.modules.memory.sqlite_repository import (
    SQLiteMemoryError,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.types import CandidateSource, MemoryQuery, MemoryRecord


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(error_type, callback):
    try:
        callback()
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def read_lines(name):
    return [json.loads(line) for line in (ASSETS / name).read_text().splitlines()]


def record_from_fixture(row):
    metadata = row["metadata"]
    return MemoryRecord(
        id=row["id"],
        agent_name=metadata["agent"],
        session_id=metadata["session_id"],
        document=row["document"],
        created_at=metadata["date"],
        updated_at=metadata["date"],
        metadata=metadata,
        cwd=row["cwd"],
        header=metadata["header"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        subject_key=row["subject_key"],
        document_format=row["document_format"],
        kinds=tuple(row["kinds"]),
    )


def query_from_case(case):
    return MemoryQuery(
        query=case["query"],
        agent_name=case["agent_name"],
        current_session_id=case["current_session_id"],
        from_date=date.fromisoformat(case["from_date"]) if case["from_date"] else None,
        to_date=date.fromisoformat(case["to_date"]) if case["to_date"] else None,
        kinds=tuple(case["kinds"]),
    )


def case_checks(coordinator, records, cases):
    results = {}
    misses = []
    for case in cases:
        query = query_from_case(case)
        result = coordinator.search(query)
        ids = {hit.memory_id for hit in result.hits}
        require(len(ids) == len(result.hits), "Repeated parent record")
        for hit in result.hits:
            source = records[hit.memory_id]
            require(source.agent_name == query.agent_name, "Cross-agent result")
            require(
                source.session_id != query.current_session_id, "Current-session result"
            )
            require(
                source.status == "active" and not source.superseded_by,
                "Non-active result",
            )
            require(
                not query.from_date or hit.date >= query.from_date.isoformat(),
                "Lower date leak",
            )
            require(
                not query.to_date or hit.date <= query.to_date.isoformat(),
                "Upper date leak",
            )
            require(hit.cwd == source.cwd, "Invented or dropped provenance")
            require(
                len(hit.excerpt) <= 360 and hit.reasons and hit.unit_id,
                "Incomplete compact evidence",
            )
        okay = set(case["expected_memory_ids"]) <= ids
        if not case["expected_memory_ids"] and case["acceptable_memory_ids"]:
            okay = okay and bool(ids & set(case["acceptable_memory_ids"]))
        okay = okay and not ids.intersection(case["excluded_memory_ids"])
        okay = okay and (not case["must_abstain"] or not ids)
        if case.get("must_mark_conflict"):
            okay = (
                okay
                and len(result.hits) >= 2
                and all(hit.conflict for hit in result.hits)
            )
        for memory_id, expected_cwd in case["expected_cwds"].items():
            for hit in result.hits:
                if hit.memory_id == memory_id:
                    okay = okay and hit.cwd == expected_cwd
        text = format_search_result(result)
        require(text.startswith("UNTRUSTED"), "Missing authority boundary")
        for line in text.splitlines()[2:]:
            if line.startswith("{"):
                data = json.loads(line)
                require(
                    data["citation"].startswith("memory:"), "Missing stable citation"
                )
        results[case["case_id"]] = result
        if not okay:
            misses.append(case["case_id"])
    for case in cases:
        if case.get("same_ranking_as_case"):
            require(
                results[case["case_id"]] == results[case["same_ranking_as_case"]],
                "CWD changed query ranking",
            )
    require(
        not results["syn-case-none"].hits and not results["syn-case-deleted"].hits,
        "Failed abstention",
    )
    require(
        "Unknown (legacy memory)"
        in format_search_result(results["syn-case-legacy-cwd"]),
        "Legacy provenance missing",
    )
    conflict = results["syn-case-conflict"].hits
    require(
        {hit.memory_id for hit in conflict}
        == {"syn-mem-conflict-a", "syn-mem-conflict-b"}
        and all(hit.conflict for hit in conflict),
        "Explicit active conflict not marked",
    )
    return misses


def verify_parser_and_storage(repository, original):
    parsed = derive_memory_units(original)
    require(parsed == derive_memory_units(original), "Unstable parser units")
    require(
        all(
            value in unit.text
            for unit in parsed.units
            for value in (*unit.entities, *unit.tags)
        ),
        "Invented identifier/tag",
    )
    require(
        any(unit.source_field == "CONVERSATION_NOTES/NOTE" for unit in parsed.units),
        "Wrong notes shape",
    )
    metadata = {"unknown": {"nested": [True, None, "Tiếng Việt"]}, "date": 123.5}
    source = replace(
        original,
        id="syn-check-preservation",
        agent_name="SyntheticChecks",
        metadata=metadata,
    )
    repository.upsert_record(source)
    require(
        repository.get_record(source.id, source.agent_name) == source,
        "Canonical fields changed",
    )
    require(
        repository.get_record(source.id, "SyntheticOther") is None,
        "Inspection lacks agent scope",
    )
    without_header = replace(source, id="syn-check-no-header", header=None)
    repository.upsert_record(without_header)
    found = MemorySearchCoordinator(repository).search(
        MemoryQuery(
            "citation",
            source.agent_name,
            "synthetic-current",
            memory_ids=(without_header.id,),
        )
    )
    require(found.hits and found.hits[0].headline, "Original XML headline lost")
    legacy = replace(source, id="syn-check-legacy-session", session_id=None)
    repository.upsert_record(legacy)
    require(
        MemorySearchCoordinator(repository)
        .search(
            MemoryQuery(
                "legacy citation",
                legacy.agent_name,
                "synthetic-current",
                memory_ids=(legacy.id,),
            )
        )
        .hits,
        "Unknown legacy session was fabricated or filtered",
    )
    for document in (
        "<MEMORY><broken>",
        '<!DOCTYPE MEMORY [<!ENTITY x "unsafe">]><MEMORY>&x;</MEMORY>',
        "plain synthetic body",
    ):
        record = replace(source, document=document)
        result = repository.upsert_record(record)
        require(
            result.warnings
            and len(result.units) == 1
            and result.units[0].source_field == "BODY",
            "No observable fallback",
        )
        require(
            repository.get_record(record.id, record.agent_name).document == document,
            "Fallback lost original",
        )
    repository.upsert_record(source)
    before = derive_memory_units(source)
    repository.upsert_record(source)
    require(
        before
        == derive_memory_units(repository.get_record(source.id, source.agent_name)),
        "Rebuild changed stable citations",
    )
    with repository._transaction() as connection:
        row = connection.execute(
            "SELECT * FROM memory_record WHERE id=?", (source.id,)
        ).fetchone()
        require(
            row["document_checksum"] == checksum(source.document),
            "Wrong document checksum",
        )
        require(
            row["metadata_checksum"] == checksum(row["metadata_json"]),
            "Wrong metadata checksum",
        )
        require(
            json.loads(row["metadata_json"]) == metadata,
            "Unknown metadata not preserved",
        )
    with repository._transaction(write=True) as connection:
        connection.execute("""CREATE TRIGGER block_fixture BEFORE INSERT ON retrieval_unit
            WHEN new.memory_id='syn-check-preservation' BEGIN
            SELECT RAISE(ABORT,'synthetic rollback check'); END""")
    rejects(
        sqlite3.IntegrityError,
        lambda: repository.upsert_record(replace(source, document="changed")),
    )
    require(
        repository.get_record(source.id, source.agent_name) == source,
        "Partial update after rollback",
    )
    with repository._transaction(write=True) as connection:
        connection.execute("DROP TRIGGER block_fixture")
    repository.check_integrity()


def verify_repository_guards(directory, repository, original):
    with repository._transaction() as connection:
        require(
            connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
            "Foreign keys disabled",
        )
        require(
            connection.execute("PRAGMA query_only").fetchone()[0] == 1,
            "Reader can write",
        )
        require(
            connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal",
            "WAL disabled",
        )
        require(
            connection.execute("PRAGMA busy_timeout").fetchone()[0] == 2000,
            "Busy timeout unbounded",
        )
        require(
            connection.execute("SELECT count(*) FROM migration_ledger").fetchone()[0]
            == 0,
            "Phase 2 started",
        )
    unit_id = derive_memory_units(original).units[0].id
    with repository._transaction(write=True) as connection:
        for table in ("memory_entity", "memory_tag"):
            rejects(
                sqlite3.IntegrityError,
                lambda: connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?)",
                    (
                        "syn-mem-symbols",
                        unit_id,
                        "synthetic ownership marker",
                        "synthetic ownership marker",
                    ),
                ),
            )
    missing_path = directory / "removed.sqlite3"
    removed = SQLiteMemoryRepository(missing_path)
    missing_path.unlink()
    rejects(
        sqlite3.OperationalError,
        lambda: removed.delete_record(original.id, original.agent_name),
    )
    require(not missing_path.exists(), "Operation silently recreated missing storage")
    readonly = SQLiteMemoryRepository(repository.path, read_only=True)
    rejects(
        SQLiteMemoryError,
        lambda: readonly.delete_record(original.id, original.agent_name),
    )
    with readonly._transaction() as connection:
        rejects(
            sqlite3.OperationalError,
            lambda: connection.execute("DELETE FROM memory_record"),
        )
    alien = directory / "unrelated.sqlite3"
    connection = sqlite3.connect(alien)
    connection.execute("CREATE TABLE untouched(value)")
    connection.execute("INSERT INTO untouched VALUES ('synthetic marker')")
    connection.commit()
    connection.close()
    before = alien.read_bytes()
    rejects(SQLiteMemoryError, lambda: SQLiteMemoryRepository(alien))
    require(
        alien.read_bytes() == before, "Destructive unrelated-database initialization"
    )
    future = directory / "future.sqlite3"
    SQLiteMemoryRepository(future)
    connection = sqlite3.connect(future)
    connection.execute("PRAGMA user_version=99")
    connection.commit()
    connection.close()
    before = future.read_bytes()
    rejects(SQLiteMemoryError, lambda: SQLiteMemoryRepository(future))
    require(future.read_bytes() == before, "Destructive version handling")

    class NoFTS:
        def execute(self, statement):
            raise sqlite3.OperationalError("no such module: fts5")

        def close(self):
            pass

    with patch("sqlite3.connect", return_value=NoFTS()):
        rejects(SQLiteMemoryError, SQLiteMemoryRepository.check_fts5)
    bounded = SQLiteMemoryRepository(repository.path, busy_timeout_ms=25)
    with repository._transaction(write=True):
        rejects(sqlite3.OperationalError, lambda: bounded.upsert_record(original))
    rejects(
        ValueError,
        lambda: MemoryQuery("", "SyntheticArchitect", "synthetic-session-current"),
    )
    rejects(ValueError, lambda: MemoryQuery("query", "SyntheticArchitect", ""))
    rejects(
        ValueError,
        lambda: MemoryQuery("query", "SyntheticArchitect", "session", limit=501),
    )
    rejects(
        ValueError,
        lambda: MemoryQuery(
            "query",
            "SyntheticArchitect",
            "session",
            from_date=date(2026, 11, 2),
            to_date=date(2026, 11, 1),
        ),
    )
    require(
        not any("cwd" in field.name for field in fields(MemoryQuery)),
        "Project provenance became query scope",
    )


def verify_exact_lifecycle_and_dedup(repository, original):
    coordinator = MemorySearchCoordinator(repository)
    base = MemoryQuery(
        "specific citation",
        original.agent_name,
        "synthetic-session-current",
        memory_ids=(original.id,),
    )
    result = coordinator.search(base)
    require(
        len(result.hits) == 1 and result.hits[0].memory_id == original.id,
        "Exact memory ID failed",
    )
    require(
        any(
            reason.source == CandidateSource.MEMORY_ID
            for reason in result.hits[0].reasons
        ),
        "Exact reason missing",
    )
    for text, expected_source in (
        ("response_service", CandidateSource.ENTITY),
        ("https://docs.example.invalid/orbit/recovery", CandidateSource.ENTITY),
        ("synthetic-evaluation", CandidateSource.TAG),
    ):
        hits = coordinator.search(replace(base, query=text, memory_ids=())).hits
        require(
            any(
                reason.source == expected_source
                for hit in hits
                for reason in hit.reasons
            ),
            "Exact entity/resource/tag source missing",
        )
    require(
        not coordinator.search(
            replace(base, current_session_id=original.session_id)
        ).hits,
        "Exact ID bypassed session exclusion",
    )
    require(
        not coordinator.search(replace(base, agent_name="SyntheticOther")).hits,
        "Exact ID bypassed agent exclusion",
    )
    require(
        not coordinator.search(replace(base, from_date=date(2099, 1, 1))).hits,
        "Exact ID bypassed dates",
    )
    require(
        not coordinator.search(
            replace(base, agent_name="SyntheticArchitect' OR 1=1 --")
        ).hits,
        "SQL scope values not bound",
    )
    require(
        not coordinator.search(
            replace(base, memory_ids=("synthetic' OR 1=1 --",))
        ).hits,
        "SQL ID selector not bound",
    )
    coordinator.search(
        replace(base, query='response_service OR " * NOT : ( )', memory_ids=())
    )
    for status, superseded_by in (
        ("superseded", None),
        ("deleted", None),
        ("active", "syn-mem-falcon-new"),
    ):
        repository.upsert_record(
            replace(original, status=status, superseded_by=superseded_by)
        )
        require(not coordinator.search(base).hits, "Exact ID bypassed lifecycle")
    repository.upsert_record(replace(original, valid_from=100, valid_to=200))
    require(
        not coordinator.search(replace(base, as_of=201)).hits, "Validity end ignored"
    )
    require(coordinator.search(replace(base, as_of=150)).hits, "Valid record filtered")
    repository.upsert_record(original)
    duplicate = replace(
        original, id="syn-check-duplicate", session_id="synthetic-session-duplicate"
    )
    repository.upsert_record(duplicate)
    query = replace(base, query="Orbit cache", memory_ids=())
    matches = coordinator.search(query).hits
    require(
        len({hit.memory_id for hit in matches} & {original.id, duplicate.id}) == 1,
        "Duplicate documents repeated",
    )
    require(
        repository.delete_record(duplicate.id, "SyntheticOther") == 0,
        "Delete bypassed agent",
    )
    require(
        repository.delete_record(duplicate.id, duplicate.agent_name) == 1,
        "Scoped physical delete failed",
    )
    injection = replace(
        original,
        id="syn-check-control",
        agent_name="SyntheticChecks",
        document="synthetic \u001b terminal marker",
    )
    repository.upsert_record(injection)
    rendered = format_search_result(
        coordinator.search(
            MemoryQuery(
                "synthetic terminal marker",
                "SyntheticChecks",
                "synthetic-current",
                memory_ids=(injection.id,),
            )
        )
    )
    require(
        "\u001b" not in rendered and "\\u001b" in rendered,
        "Unescaped recalled control content",
    )


def verify_cwd_and_concurrency(directory, records, cases):
    repository = SQLiteMemoryRepository(directory / "cwd.sqlite3")
    for record in records.values():
        repository.upsert_record(record)
    coordinator = MemorySearchCoordinator(repository)
    queries = [query_from_case(case) for case in cases]
    before = [coordinator.search(query) for query in queries]
    for record in records.values():
        repository.upsert_record(
            replace(record, cwd="/synthetic/projects/changed-provenance")
        )
    after = [coordinator.search(query) for query in queries]
    for left, right in zip(before, after):
        require(
            [(hit.memory_id, hit.unit_id, hit.score, hit.reasons) for hit in left.hits]
            == [
                (hit.memory_id, hit.unit_id, hit.score, hit.reasons)
                for hit in right.hits
            ],
            "CWD field changed candidate/rank",
        )
    original = records["syn-mem-cache"]

    def operation(index):
        source = replace(
            original, id=f"syn-thread-{index}", agent_name="SyntheticThreads"
        )
        repository.upsert_record(source)
        query = MemoryQuery(
            "Orbit cache",
            source.agent_name,
            source.session_id if index % 2 else "synthetic-current",
            memory_ids=(source.id,),
        )
        return bool(coordinator.search(query).hits) == (index % 2 == 0)

    with ThreadPoolExecutor(max_workers=4) as executor:
        require(
            all(executor.map(operation, range(12))),
            "Connection/scope leaked across threads",
        )
    repository.check_integrity()


def main():
    require(
        not any(
            name in sys.modules
            for name in (
                "chromadb",
                "loguru",
                "numpy",
                "AgentCrew.setup",
                "AgentCrew.app",
            )
        ),
        "Prototype import loaded optional/runtime services",
    )
    for name in (
        "types.py",
        "record_parser.py",
        "sqlite_repository.py",
        "retrieval.py",
    ):
        source = ast.parse((ROOT / "AgentCrew/modules/memory" / name).read_text())
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                require(
                    all(
                        alias.name.split(".")[0] in sys.stdlib_module_names
                        for alias in node.names
                    ),
                    "Non-stdlib prototype dependency",
                )
            if isinstance(node, ast.ImportFrom) and not node.level:
                require(
                    node.module.split(".")[0] in sys.stdlib_module_names,
                    "Non-stdlib prototype dependency",
                )
    for path in (ROOT / "AgentCrew/modules/memory").glob("*.py"):
        ast.parse(path.read_text(), filename=path.name)
    records = {
        row["id"]: record_from_fixture(row)
        for row in read_lines("synthetic-corpus.jsonl")
    }
    cases = read_lines("benchmark-cases.jsonl")
    with TemporaryDirectory(prefix="agentcrew-phase1-") as temporary:
        directory = Path(temporary)
        repository = SQLiteMemoryRepository(directory / "synthetic.sqlite3")
        for record in records.values():
            repository.upsert_record(record)
        require(
            repository.delete_record("syn-mem-deleted", "SyntheticArchitect") == 1,
            "Deletion setup failed",
        )
        with repository._transaction() as connection:
            for table, column in (
                ("memory_record", "id"),
                ("retrieval_unit", "memory_id"),
                ("memory_entity", "memory_id"),
                ("memory_tag", "memory_id"),
                ("migration_ledger", "memory_id"),
            ):
                require(
                    connection.execute(
                        f"SELECT count(*) FROM {table} WHERE {column}=?",
                        ("syn-mem-deleted",),
                    ).fetchone()[0]
                    == 0,
                    "Deleted record retained in derived tables",
                )
            require(
                connection.execute(
                    "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH ?",
                    ('"copper-forgotten-synthetic-001"',),
                ).fetchone()[0]
                == 0,
                "Deleted tokens retained in FTS results",
            )
        repository.check_integrity()
        misses = case_checks(MemorySearchCoordinator(repository), records, cases)
        verify_parser_and_storage(repository, records["syn-mem-cache"])
        verify_repository_guards(directory, repository, records["syn-mem-cache"])
        verify_exact_lifecycle_and_dedup(repository, records["syn-mem-cache"])
        verify_cwd_and_concurrency(directory, records, cases)
        repository.check_integrity()
    print(
        "PASS: Phase 1 schema/parser/canonical preservation/rollback/FTS deletion/exact scope/date/lifecycle/CWD/dedup/concurrency/formatter invariants"
    )
    print(
        f"Synthetic labeled-ID checks: {len(cases) - len(misses)}/{len(cases)}; misses: {', '.join(misses) or 'none'}"
    )
    print(
        "Not asserted: semantic fact coverage, calibrated relevance, latency/cost, real-memory quality, production forget or migration"
    )


if __name__ == "__main__":
    main()
