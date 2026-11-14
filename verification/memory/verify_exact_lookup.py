"""Fictional exact-lookup equivalence and index-led plan checks; no app startup."""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator, _query_parts
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.types import CandidateSource, MemoryQuery, MemoryRecord
from verification.memory.verify_phase1 import read_lines, record_from_fixture, require


class LegacyConnection:
    """Test oracle: restore only the reviewed, pre-fix FROM clause."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        for table, index in (
            ("memory_entity", "exact_entity"),
            ("memory_tag", "exact_tag"),
        ):
            current = (
                f"{table} x INDEXED BY {index}\n"
                "                    CROSS JOIN retrieval_unit u ON u.id=x.unit_id\n"
                "                    CROSS JOIN memory_record m ON m.id=u.memory_id"
            )
            legacy = (
                "retrieval_unit u JOIN memory_record m ON m.id=u.memory_id "
                f"JOIN {table} x ON x.unit_id=u.id"
            )
            sql = sql.replace(current, legacy)
        require(
            "INDEXED BY exact_entity" not in sql and "INDEXED BY exact_tag" not in sql,
            "Legacy test oracle did not restore the baseline FROM clause",
        )
        return self.connection.execute(sql, parameters)


class LegacyRepository(SQLiteMemoryRepository):
    @contextmanager
    def _transaction(self, *, write=False, create=False):
        with super()._transaction(write=write, create=create) as connection:
            yield LegacyConnection(connection)


class PlanConnection:
    def __init__(self, connection, statements):
        self.connection = connection
        self.statements = statements

    def execute(self, sql, parameters=()):
        if "WITH matches AS MATERIALIZED" in sql:
            self.statements.append((sql, tuple(parameters)))
        return self.connection.execute(sql, parameters)


class PlanRepository(SQLiteMemoryRepository):
    def __init__(self, *args, **kwargs):
        self.statements = []
        super().__init__(*args, **kwargs)

    @contextmanager
    def _transaction(self, *, write=False, create=False):
        with super()._transaction(write=write, create=create) as connection:
            yield PlanConnection(connection, self.statements)


def populate(repository):
    for row in read_lines("synthetic-corpus.jsonl"):
        repository.upsert_record(record_from_fixture(row))
    repository.delete_record("syn-mem-deleted", "SyntheticArchitect")
    document = (
        "<MEMORY><HEAD>Exact tie fixture</HEAD><FLOW><OUTCOME>"
        "Alpha_Code Beta_Code common_symbol</OUTCOME></FLOW>"
        "<DOMAINS><DOMAIN>common-domain</DOMAIN></DOMAINS>"
        "<CONVERSATION_NOTES><NOTE>Tiếng Việt Café_Model Straße_API</NOTE>"
        "</CONVERSATION_NOTES></MEMORY>"
    )
    epoch = 1767225600
    base = MemoryRecord(
        "exact-000",
        "ExactAgent",
        "past",
        document,
        epoch,
        epoch,
        {"synthetic": True},
        header="Fictional exact fixture",
    )
    for number in range(48):
        repository.upsert_record(
            replace(
                base,
                id=f"exact-{number:03}",
                document=document.replace(
                    "Exact tie fixture", f"Exact tie fixture {number}"
                ),
            )
        )
    repository.upsert_record(replace(base, id="exact-null", session_id=None))
    repository.upsert_record(replace(base, id="exact-blank", session_id=""))
    repository.upsert_record(replace(base, id="exact-current", session_id="current"))
    repository.upsert_record(
        replace(base, id="exact-other-agent", agent_name="OtherAgent")
    )
    repository.upsert_record(replace(base, id="exact-superseded", status="superseded"))
    repository.upsert_record(
        replace(base, id="exact-linked", superseded_by="exact-000")
    )
    repository.upsert_record(replace(base, id="exact-deleted", status="deleted"))
    repository.upsert_record(
        replace(base, id="exact-validity", valid_from=200, valid_to=300)
    )
    repository.upsert_record(
        replace(base, id="exact-fallback", document="<MEMORY><broken>")
    )
    with repository._transaction(write=True) as connection:
        unit = connection.execute(
            "SELECT id FROM retrieval_unit WHERE memory_id=? AND source_field='DOMAINS/DOMAIN'",
            (base.id,),
        ).fetchone()[0]
        for value in ("zeta-domain", "alpha-domain"):
            connection.execute(
                "INSERT INTO memory_tag(memory_id,unit_id,value,normalized_value) VALUES(?,?,?,?)",
                (base.id, unit, value, value),
            )
    repository.check_integrity()


def compare(new, old, query, values, fts):
    actual = new.candidates(query, values, fts)
    expected = old.candidates(query, values, fts)
    require(actual == expected, "Exact-source candidate DTO/order/reason mismatch")
    require(
        MemorySearchCoordinator(new).search(query)
        == MemorySearchCoordinator(old).search(query),
        "End-to-end rank/citation/score/reason mismatch",
    )
    for candidate in actual.candidates:
        record = candidate.record
        require(record.agent_name == query.agent_name, "Agent scope leak")
        require(
            record.session_id is None or record.session_id != query.current_session_id,
            "Session leak",
        )
        require(
            record.status == "active" and record.superseded_by is None, "Lifecycle leak"
        )
    return actual


def main():
    with TemporaryDirectory(prefix="agentcrew-exact-") as directory:
        path = Path(directory) / "fixture.sqlite3"
        setup = SQLiteMemoryRepository(path)
        populate(setup)
        new = SQLiteMemoryRepository(path, read_only=True)
        old = LegacyRepository(path, read_only=True)
        base = MemoryQuery("Alpha_Code common-domain", "ExactAgent", "current")
        variants = [
            base,
            replace(base, candidate_limit=1),
            replace(base, candidate_limit=3),
            replace(base, memory_ids=("exact-007",)),
            replace(base, memory_ids=("exact-null", "exact-blank")),
            replace(
                base,
                memory_ids=(
                    "exact-current",
                    "exact-other-agent",
                    "exact-deleted",
                    "exact-superseded",
                    "exact-linked",
                ),
            ),
            replace(base, from_date=date(2026, 1, 2)),
            replace(base, to_date=date(2025, 12, 31)),
            replace(base, as_of=199),
            replace(base, as_of=250),
            replace(base, as_of=301),
            replace(base, agent_name="missing-agent"),
            replace(base, query="Tiếng Việt Café_Model Straße_API"),
            replace(base, query="missing_identifier"),
            replace(base, query="exact-fallback", memory_ids=("exact-fallback",)),
        ]
        values_cases = [
            (),
            ("alpha_code",),
            ("beta_code", "alpha_code"),
            ("common-domain", "alpha-domain", "zeta-domain"),
            ("café_model", "strasse_api"),
            ("missing",),
            ("",),
            ("' OR 1=1 --",),
            ("common_symbol", "common-domain"),
        ]
        checks = 0
        for query in variants:
            for values in values_cases:
                compare(new, old, query, values, "")
                checks += 1
            exact, expression = _query_parts(query.query)
            compare(new, old, query, exact, expression)
            checks += 1
        batch = compare(
            new,
            old,
            replace(base, memory_ids=("exact-000",)),
            ("beta_code", "alpha_code", "zeta-domain", "alpha-domain"),
            "",
        )
        require(
            next(
                c.reason.detail
                for c in batch.candidates
                if c.reason.source == CandidateSource.ENTITY
            )
            == "Alpha_Code",
            "Matched-entity BINARY tie changed",
        )
        require(
            next(
                c.reason.detail
                for c in batch.candidates
                if c.reason.source == CandidateSource.TAG
            )
            == "alpha-domain",
            "Matched-tag BINARY tie changed",
        )
        scoped = new.candidates(
            replace(base, candidate_limit=1, memory_ids=("exact-047",)),
            ("alpha_code", "common-domain"),
            "",
        )
        require(
            sum(
                c.reason.source in (CandidateSource.ENTITY, CandidateSource.TAG)
                for c in scoped.candidates
            )
            == 2
            and all(c.record.id == "exact-047" for c in scoped.candidates),
            "Global exact cap applied before scope",
        )
        plans = PlanRepository(path, read_only=True)
        plans.statements = []
        plans.candidates(base, ("alpha_code", "common-domain"), "")
        with new._transaction() as connection:
            for sql, parameters in plans.statements:
                plan = [
                    str(row[3]).lower()
                    for row in connection.execute(
                        "EXPLAIN QUERY PLAN " + sql, parameters
                    )
                ]
                expected = "exact_entity" if "memory_entity x" in sql else "exact_tag"
                driving = [
                    i
                    for i, detail in enumerate(plan)
                    if "search x " in detail
                    and expected in detail
                    and "normalized_value" in detail
                ]
                unit_lookups = [
                    i for i, detail in enumerate(plan) if "search u " in detail
                ]
                require(
                    driving and unit_lookups and driving[0] < unit_lookups[0],
                    "Exact value index does not lead unit lookup",
                )
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(
                executor.map(
                    lambda q: compare(new, old, q, ("alpha_code", "common-domain"), ""),
                    variants,
                )
            )
        with new._transaction() as connection:
            require(
                connection.execute("PRAGMA query_only").fetchone()[0] == 1,
                "Search connection is writable",
            )
            require(
                connection.execute("PRAGMA foreign_key_check").fetchone() is None,
                "Foreign key failure",
            )
            for table in ("retrieval_unit", "memory_entity", "memory_tag"):
                require(
                    connection.execute(
                        f"SELECT count(*) FROM {table} WHERE memory_id=?",
                        ("syn-mem-deleted",),
                    ).fetchone()[0]
                    == 0,
                    "Physically deleted derived row survives",
                )
        timing = {}
        for label, query, values in (
            ("selective", base, ("alpha_code",)),
            ("broad", base, ("common_symbol", "common-domain")),
            (
                "broad_tight_scope",
                replace(base, memory_ids=("exact-047",)),
                ("common_symbol", "common-domain"),
            ),
        ):
            timing[label] = {}
            for name, repository in (("before", old), ("after", new)):
                start = time.perf_counter_ns()
                for _ in range(3):
                    repository.candidates(query, values, "")
                timing[label][name + "_mean_ms"] = (
                    time.perf_counter_ns() - start
                ) / 3e6
        print(
            json.dumps(
                {
                    "status": "passed",
                    "candidate_and_result_comparisons": checks + 1,
                    "index_led_plans": len(plans.statements),
                    "fictional_timings": timing,
                    "private_cases_executed": 0,
                    "remote_calls": 0,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
