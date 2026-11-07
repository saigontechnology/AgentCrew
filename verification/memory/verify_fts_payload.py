"""Fictional FTS and lexical equivalence against actual committed source oracles."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import query_from_case
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator, _query_parts, _words
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.types import CandidateSource, MemoryQuery, MemoryRecord
from verification.memory.verify_exact_lookup import PlanRepository, populate
from verification.memory.verify_phase1 import require

REFERENCE_COMMIT = "2e6c6c4dceb77b06e5689491b106b12d0df92d3b"
REFERENCE_SHA256 = "ee27d5124cecdab95b1b805479426dfd361c969435e7d60b6e6df9600205c1d4"


WORDS_REFERENCE_COMMIT = "1fb71933ad0df24af162413726df4c7bbe138577"
WORDS_REFERENCE_SHA256 = "d273dfc068cc9f129689308217707fa6c183b683031c2ddf7f34f4ce5cb8763e"
ENTITY_REFERENCE_COMMIT = "b6d262d680590066a7e2d218c58a22068f8abcd7"
ENTITY_REFERENCE_SHA256 = "7533666b35ca9f39aedf0f90d0bebed26dd5a2990e153ed3fc9b84ec5dfa65b6"


def committed_reference(commit, path, expected_sha256, name):
    """Load hash-pinned actual code, never a string-rewritten reference."""
    source = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)
    require(hashlib.sha256(source).hexdigest() == expected_sha256, "Committed reference hash mismatch")
    module = types.ModuleType(f"AgentCrew.modules.memory.{name}")
    module.__package__ = "AgentCrew.modules.memory"
    sys.modules[module.__name__] = module
    exec(compile(source, "<committed-reference>", "exec"), module.__dict__)
    return module


def reference_repository():
    return committed_reference(REFERENCE_COMMIT, "AgentCrew/modules/memory/sqlite_repository.py",
                               REFERENCE_SHA256, "_fts_payload_reference").SQLiteMemoryRepository


def check_words(reference):
    samples = ["", "Alpha_Code HTTP://example.test/a b", "short query?", " ".join(reference.STOP_WORDS),
               "Tiếng Việt Café_Model Straße_API", "cafe\u0301 e\u0323\u0302 a\ufe0fb\u034fc",
               "Indic का enclosing a⃝", "漢字かな Ελληνικά Русский العربية עברית", "emoji 👩‍💻 x_y 42",
               "ﬃ ＡＢＣ K Å 𝔄", "ASCII payload symbol decision. " * 200,
               "Tiếng Việt kết quả. " * 200, "".join(chr(code) for code in range(128)),
               chr(0xD800) + "a" + chr(0x10FFFF),
               "a" + "".join(chr(code) for code in range(0x110000)
                              if unicodedata.category(chr(code)) in {"Mn", "Mc", "Me"}) + "z"]
    for sample in samples:
        expected = reference._words(sample)
        actual = _words(sample)
        require(actual == expected, "Lexical word set changed")
        other = _words(sample)
        require(other is not actual and other == expected, "Word result set was shared")
        actual.add("__fictional_mutation__")
        require(_words(sample) == expected, "Word result mutation leaked between calls")
    return len(samples)


def compare(current, reference, query, expression, coordinator_reference, *, exact_values=()):
    actual = current.candidates(query, exact_values, expression)
    require(actual == reference.candidates(query, exact_values, expression), "Full candidate DTO mismatch")
    result = MemorySearchCoordinator(current).search(query)
    require(result == MemorySearchCoordinator(reference).search(query),
            "Coordinator ranks/citations/scores/reasons/conflicts changed")
    require(result == coordinator_reference(current).search(query),
            "Prior coordinator outputs changed under lexical fast path")
    for candidate in actual.candidates:
        record = candidate.record
        require(record.agent_name == query.agent_name, "Agent scope leak")
        require(record.session_id is None or record.session_id != query.current_session_id, "Session leak")
        require(record.status == "active" and record.superseded_by is None, "Lifecycle leak")
        require(not query.memory_ids or record.id in query.memory_ids, "ID scope leak")
        if query.from_date:
            require(record.created_at >= datetime.combine(query.from_date, time.min, UTC).timestamp(), "From-date leak")
        if query.to_date:
            require(record.created_at <= datetime.combine(query.to_date, time.max, UTC).timestamp(), "To-date leak")
        if query.as_of is not None:
            require(record.valid_from is None or record.valid_from <= query.as_of, "Validity-from leak")
            require(record.valid_to is None or record.valid_to >= query.as_of, "Validity-to leak")
    return actual


def populate_payload(repository):
    padding = "fictional payload words " * 32
    notes = "".join(f"<NOTE>sharedtoken unit{number} {padding}</NOTE>" for number in range(24))
    document = ("<MEMORY><HEAD>sharedtoken payload</HEAD><FLOW><OUTCOME>sharedtoken outcome</OUTCOME></FLOW>"
                f"<CONVERSATION_NOTES>{notes}</CONVERSATION_NOTES></MEMORY>")
    for number in range(48):
        repository.upsert_record(MemoryRecord(
            f"payload-{number:03}", "PayloadAgent", "past", document.replace("payload</HEAD>", f"payload {number}</HEAD>"),
            1767225600, 1767225600, {"synthetic": True}, cwd=f"/fictional/source-{number}",
        ))
    base = MemoryRecord("conflict-one", "ExactAgent", "past",
                        "<MEMORY><HEAD>Alpha_Code conflict</HEAD><FLOW><OUTCOME>Alpha_Code first</OUTCOME></FLOW></MEMORY>",
                        1767225600, 1767225600, {"synthetic": True}, subject_key="fictional-subject")
    repository.upsert_record(base)
    repository.upsert_record(replace(base, id="conflict-two", document=base.document.replace("first", "second")))
    repository.upsert_record(replace(base, id="date-end", subject_key=None, created_at=1767311999.999999))
    for identifier, token in (("tie-outcome", "tietoken"), ("tie-unit", "tieidtoken")):
        repository.upsert_record(replace(base, id=identifier, subject_key=None, document=(
            f"<MEMORY><HEAD>{token}</HEAD><FLOW><OUTCOME>{token}</OUTCOME></FLOW>"
            f"<CONVERSATION_NOTES><NOTE>{token}</NOTE><NOTE>{token}</NOTE></CONVERSATION_NOTES></MEMORY>")))
        with repository._transaction(write=True) as connection:
            connection.execute("UPDATE retrieval_unit SET title='',important='',body=?,identifiers='',ordinal=0 WHERE memory_id=?",
                               (token, identifier))
            if identifier == "tie-unit":
                connection.execute("UPDATE retrieval_unit SET source_field='CONVERSATION_NOTES/NOTE' WHERE memory_id=?", (identifier,))


def check_snapshot(path):
    writer = SQLiteMemoryRepository(path)
    before = MemoryRecord("snapshot-old", "SnapshotAgent", "past", "<MEMORY><HEAD>snapshottoken old</HEAD></MEMORY>",
                          1767225600, 1767225600, {"synthetic": True}, subject_key="snapshot-subject")
    writer.upsert_record(before)
    changed = replace(before, document=before.document.replace("old", "new"))
    additional = replace(before, id="snapshot-additional", document=before.document.replace("old", "additional"))
    writes = []

    class HookConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            if "WITH matches AS MATERIALIZED" in sql and "memory_fts MATCH" in sql and not writes:
                writes.append(True)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(writer.upsert_record, changed).result(timeout=10)
                    executor.submit(writer.upsert_record, additional).result(timeout=10)
            return cursor

    class HookRepository(SQLiteMemoryRepository):
        @contextmanager
        def _transaction(self, *, write=False, create=False):
            with super()._transaction(write=write, create=create) as connection:
                yield HookConnection(connection)

    query = MemoryQuery("snapshottoken", "SnapshotAgent", "current")
    reader = HookRepository(path, read_only=True)
    snapshot = reader.candidates(query, (), '"snapshottoken"')
    require(len(snapshot.candidates) == 1 and snapshot.candidates[0].record == before, "Concurrent hydration lost its read snapshot")
    require("snapshot-subject" not in snapshot.conflicting_subjects, "Conflict scan used a different snapshot")
    next_snapshot = reader.candidates(query, (), '"snapshottoken"')
    require(len(next_snapshot.candidates) == 2 and "snapshot-subject" in next_snapshot.conflicting_subjects,
            "Next operation did not observe committed writes")


def check_entity_snapshot(path):
    writer = SQLiteMemoryRepository(path)
    before = MemoryRecord("entity-snapshot-a", "EntitySnapshotAgent", "past",
                          "<MEMORY><HEAD>Snapshot_Entity</HEAD><FLOW><OUTCOME>Snapshot_Entity old</OUTCOME></FLOW></MEMORY>",
                          1767225600, 1767225600, {"synthetic": True}, subject_key="entity-snapshot-subject")
    second = replace(before, id="entity-snapshot-b", session_id=None)
    writer.upsert_record(before)
    writer.upsert_record(second)
    writes = []

    class EntityHookConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            if "WITH matches AS MATERIALIZED" in sql and "memory_entity x" in sql and not writes:
                writes.append(True)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(writer.upsert_record, replace(second, document=second.document.replace("old", "new"))).result(timeout=10)
            return cursor

    class EntityHookRepository(SQLiteMemoryRepository):
        @contextmanager
        def _transaction(self, *, write=False, create=False):
            with super()._transaction(write=write, create=create) as connection:
                yield EntityHookConnection(connection)

    query = MemoryQuery("Snapshot_Entity", "EntitySnapshotAgent", "current")
    reader = EntityHookRepository(path, read_only=True)
    snapshot = reader.candidates(query, ("snapshot_entity",), "")
    require(len(snapshot.candidates) == 2 and all(candidate.record.document == before.document for candidate in snapshot.candidates),
            "ENTITY hydration lost its operation snapshot")
    require("entity-snapshot-subject" not in snapshot.conflicting_subjects, "ENTITY conflict scan changed snapshots")
    following = reader.candidates(query, ("snapshot_entity",), "")
    require(len(following.candidates) == 2 and "entity-snapshot-subject" in following.conflicting_subjects
            and any(candidate.record.document != before.document for candidate in following.candidates),
            "Following ENTITY operation missed committed writes")


def check_entity_reference(path, current, variants, coordinator_reference):
    legacy_class = committed_reference(ENTITY_REFERENCE_COMMIT, "AgentCrew/modules/memory/sqlite_repository.py",
                                       ENTITY_REFERENCE_SHA256, "_entity_payload_reference").SQLiteMemoryRepository
    reference = legacy_class(path, read_only=True)
    writer = SQLiteMemoryRepository(path)
    padding = "fictional wide canonical payload " * 32
    notes = "".join(f"<NOTE>Payload_Entity unit{number} {padding}</NOTE>" for number in range(14))
    document = ("<MEMORY><HEAD>Payload_Entity fixture</HEAD><FLOW><OUTCOME>Payload_Entity outcome</OUTCOME></FLOW>"
                f"<CONVERSATION_NOTES>{notes}</CONVERSATION_NOTES></MEMORY>")
    for number in range(24):
        writer.upsert_record(MemoryRecord(f"entity-payload-{number:03}", "EntityPayloadAgent", "past", document,
                                         1767225600, 1767225600, {"synthetic": True}))
    with writer._transaction(write=True) as connection:
        for value in ("Fixture_Tie_Entity_A", "Fixture_Tie_Entity_B"):
            connection.execute("INSERT INTO memory_entity(memory_id,unit_id,value,normalized_value) "
                               "SELECT memory_id,id,?,? FROM retrieval_unit WHERE memory_id IN ('tie-outcome','tie-unit')",
                               (value, value.casefold()))
    combinations = (("alpha_code",), ("alpha_code", "beta_code"), ("alpha_code", "alpha_code"),
                    ("tiếng_việt", "café_model", "strasse_api"), ("absent_identifier",), ())
    comparisons = 0
    for query in variants:
        for exact in combinations:
            compare(current, reference, query, _query_parts(query.query)[1], coordinator_reference, exact_values=exact)
            comparisons += 1
    payload = MemoryQuery("Payload_Entity", "EntityPayloadAgent", "current")
    for query in (payload, replace(payload, candidate_limit=1), replace(payload, candidate_limit=3),
                  replace(payload, candidate_limit=500),
                  replace(payload, memory_ids=("entity-payload-023",), candidate_limit=1)):
        compare(current, reference, query, '"Payload_Entity"', coordinator_reference, exact_values=("payload_entity",))
        comparisons += 1
    scoped = current.candidates(replace(payload, memory_ids=("entity-payload-023",), candidate_limit=1), ("payload_entity",), "")
    require(all(candidate.record.id == "entity-payload-023" for candidate in scoped.candidates)
            and sum(candidate.reason.source == CandidateSource.ENTITY for candidate in scoped.candidates) == 1,
            "ENTITY cap ran before scope")
    tie_query = MemoryQuery("Fixture_Tie_Entity_A", "ExactAgent", "current")
    tie_values = ("fixture_tie_entity_b", "fixture_tie_entity_a")
    ties = current.candidates(tie_query, tie_values, "")
    by_id = {candidate.record.id: candidate for candidate in ties.candidates}
    require(by_id["tie-outcome"].unit.source_field == "FLOW/OUTCOME", "ENTITY outcome tie changed")
    require(all(candidate.reason.detail == "Fixture_Tie_Entity_A" for candidate in ties.candidates), "ENTITY source-detail tie changed")
    with current._transaction() as connection:
        expected = connection.execute("SELECT min(id) FROM retrieval_unit WHERE memory_id='tie-unit'").fetchone()[0]
    require(by_id["tie-unit"].unit.id == expected, "ENTITY ordinal/unit-ID tie changed")
    compare(current, reference, tie_query, "", coordinator_reference, exact_values=tie_values)
    comparisons += 1

    class LegacyPlans(PlanRepository, legacy_class):
        candidates = legacy_class.candidates

    captured, old = PlanRepository(path, read_only=True), LegacyPlans(path, read_only=True)
    raw_checks = 0
    unchanged_families = set()
    entity_columns = set()
    raw_variants = ((payload, ("payload_entity",), '"Payload_Entity"'),
                    (variants[0], ("alpha_code", "beta_code"), _query_parts(variants[0].query)[1]),
                    (replace(variants[0], memory_ids=("exact-047",)), ("alpha_code",), '"Alpha_Code"'),
                    (tie_query, tie_values, ""))
    for query, exact, expression in raw_variants:
        captured.statements.clear()
        old.statements.clear()
        captured.candidates(query, exact, expression)
        old.candidates(query, exact, expression)
        require(len(captured.statements) == len(old.statements), "ENTITY source family count changed")
        for (new_sql, new_args), (old_sql, old_args) in zip(captured.statements, old.statements):
            require(new_args == old_args, "ENTITY bound parameters changed")
            if "memory_entity x" not in new_sql:
                require(" ".join(new_sql.split()) == " ".join(old_sql.split()), "Non-ENTITY SQL changed")
                unchanged_families.add("fts" if "memory_fts MATCH" in new_sql else "tag" if "memory_tag x" in new_sql else "memory_id")
            with current._transaction() as connection:
                actual = [{key: row[key] for key in row.keys() if key != "parent_rank"}
                          for row in connection.execute(new_sql, new_args)]
                expected_rows = [{key: row[key] for key in row.keys() if key != "parent_rank"}
                                 for row in connection.execute(old_sql, old_args)]
                require(actual == expected_rows, "ENTITY raw payload/source detail/score changed")
                if "memory_entity x" in new_sql:
                    entity_columns.add(next(row[3] for row in connection.execute("EXPLAIN " + new_sql, new_args)
                                            if row[1] == "OpenEphemeral"))
            raw_checks += 1
    require(entity_columns == {6}, "ENTITY intermediate payload is not six ranking keys")
    require(unchanged_families == {"fts", "tag", "memory_id"}, "Unaffected family SQL coverage missing")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda query: compare(current, reference, query, _query_parts(query.query)[1],
                                               coordinator_reference, exact_values=("alpha_code", "beta_code")), variants))
    check_entity_snapshot(path)
    return {"entity_reference_commit": ENTITY_REFERENCE_COMMIT, "entity_reference_sha256": ENTITY_REFERENCE_SHA256,
            "entity_candidate_and_coordinator_comparisons": comparisons, "entity_concurrent_comparisons": len(variants),
            "entity_raw_sql_checks": raw_checks, "entity_materialized_columns": 6,
            "entity_concurrent_snapshot_checked": True, "non_entity_sql_unchanged": sorted(unchanged_families)}


def main():
    reference_class = reference_repository()
    words_reference = committed_reference(WORDS_REFERENCE_COMMIT, "AgentCrew/modules/memory/retrieval.py",
                                          WORDS_REFERENCE_SHA256, "_words_reference")
    words_checks = check_words(words_reference)
    reference_coordinator = words_reference.MemorySearchCoordinator
    with TemporaryDirectory(prefix="agentcrew-fts-payload-") as directory:
        path = Path(directory) / "fictional.sqlite3"
        writer = SQLiteMemoryRepository(path)
        populate(writer)
        populate_payload(writer)
        current = SQLiteMemoryRepository(path, read_only=True)
        reference = reference_class(path, read_only=True)
        base = MemoryQuery("Alpha_Code common-domain", "ExactAgent", "current")
        variants = [base, replace(base, candidate_limit=1), replace(base, candidate_limit=3),
                    replace(base, candidate_limit=500), replace(base, memory_ids=("exact-047",)),
                    replace(base, memory_ids=("exact-null", "exact-blank")),
                    replace(base, memory_ids=("exact-current", "exact-other-agent", "exact-deleted", "exact-superseded", "exact-linked")),
                    replace(base, from_date=date(2026, 1, 1), to_date=date(2026, 1, 1)),
                    replace(base, from_date=date(2026, 1, 2)), replace(base, to_date=date(2025, 12, 31)),
                    replace(base, as_of=199), replace(base, as_of=250), replace(base, as_of=301),
                    replace(base, agent_name="OtherAgent"), replace(base, agent_name="missing-agent"),
                    replace(base, kinds=("decision",)), replace(base, query="Tiếng Việt Café_Model Straße_API"),
                    replace(base, query="missing_identifier"), replace(base, query="exact-fallback", memory_ids=("exact-fallback",))]
        expressions = ('', '"Alpha_Code"', '"Beta_Code" OR "common_symbol"',
                       '"Tiếng Việt" OR "Café_Model" OR "Straße_API"', '"absentword"', '"Exact tie fixture"')
        comparisons = 0
        for query in variants:
            for expression in expressions:
                compare(current, reference, query, expression, reference_coordinator)
                comparisons += 1
            compare(current, reference, query, _query_parts(query.query)[1], reference_coordinator)
            comparisons += 1
        payload = MemoryQuery("sharedtoken", "PayloadAgent", "current")
        for query in (payload, replace(payload, candidate_limit=1), replace(payload, candidate_limit=3),
                      replace(payload, candidate_limit=500), replace(payload, memory_ids=("payload-047",))):
            compare(current, reference, query, '"sharedtoken"', reference_coordinator)
            comparisons += 1
        cap = current.candidates(replace(payload, candidate_limit=1, memory_ids=("payload-047",)), (), '"sharedtoken"')
        fts_cap = [candidate for candidate in cap.candidates if candidate.reason.source == CandidateSource.FTS]
        require(len(fts_cap) == 1 and all(candidate.record.id == "payload-047" for candidate in cap.candidates),
                "FTS limit applied before scope")
        ties = current.candidates(base, (), '"tietoken"')
        require(len(ties.candidates) == 1 and ties.candidates[0].unit.source_field == "FLOW/OUTCOME", "Outcome tie preference changed")
        ties = current.candidates(base, (), '"tieidtoken"')
        with current._transaction() as connection:
            expected = connection.execute("SELECT min(id) FROM retrieval_unit WHERE memory_id='tie-unit'").fetchone()[0]
        require(len(ties.candidates) == 1 and ties.candidates[0].unit.id == expected, "Ordinal/unit-ID tie changed")
        for expression in ('"tietoken"', '"tieidtoken"'):
            compare(current, reference, base, expression, reference_coordinator)
            comparisons += 1
        captured = PlanRepository(path, read_only=True)
        captured.candidates(payload, (), '"sharedtoken"')
        sql, parameters = captured.statements[-1]
        require("selected AS MATERIALIZED" in sql and "SELECT m.id,u.id AS unit_id,u.source_field,u.ordinal" in sql,
                "FTS narrow/capped materialization missing")
        raw_checks = 0

        class ReferencePlans(PlanRepository, reference_class):
            candidates = reference_class.candidates

        old_plans = ReferencePlans(path, read_only=True)
        for query, exact, expression in ((payload, (), '"sharedtoken"'), (base, ("alpha_code",), ''),
                                          (replace(base, memory_ids=("exact-047",)), (), '')):
            captured.statements.clear()
            old_plans.statements.clear()
            captured.candidates(query, exact, expression)
            old_plans.candidates(query, exact, expression)
            require(len(captured.statements) == len(old_plans.statements), "Source family count changed")
            for (new_sql, new_args), (old_sql, old_args) in zip(captured.statements, old_plans.statements):
                require(new_args == old_args, "Bound parameters changed")
                if not expression and "memory_entity x" not in new_sql:
                    require(" ".join(new_sql.split()) == " ".join(old_sql.split()), "Unaffected SQL changed")
                with current._transaction() as connection:
                    actual = [dict(row) for row in connection.execute(new_sql, new_args)]
                    expected_rows = [{key: row[key] for key in row.keys() if key != "parent_rank"}
                                     for row in connection.execute(old_sql, old_args)]
                    if not expression:
                        actual = [{key: value for key, value in row.items() if key != "parent_rank"} for row in actual]
                    require(actual == expected_rows, "Raw payload or weighted BM25 scores changed")
                    raw_checks += 1
        with current._transaction() as connection:
            columns = next(row[3] for row in connection.execute("EXPLAIN " + sql, parameters) if row[1] == "OpenEphemeral")
            require(columns == 6, "FTS materialized payload grew beyond ranking keys")
            require(connection.execute("PRAGMA query_only").fetchone()[0] == 1, "Read connection is writable")
            require(connection.execute("PRAGMA foreign_key_check").fetchone() is None, "Foreign key failure")
            for table in ("retrieval_unit", "memory_entity", "memory_tag"):
                require(connection.execute(f"SELECT count(*) FROM {table} WHERE memory_id='syn-mem-deleted'").fetchone()[0] == 0,
                        "Deleted derived rows remain")
        case = {"query": base.query, "agent_name": base.agent_name, "current_session_id": base.current_session_id, "kinds": []}
        require(query_from_case({**case, "current_cwd": "/fictional/a"}, 5) == query_from_case({**case, "current_cwd": "/fictional/b"}, 5),
                "CWD entered retrieval scope")
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda query: compare(current, reference, query, _query_parts(query.query)[1], reference_coordinator), variants))
        alias_batch = current.candidates(base, ("alpha_code",), '"Alpha_Code"')
        siblings = [candidate for candidate in alias_batch.candidates
                    if candidate.record.id == alias_batch.candidates[0].record.id]
        require(len(siblings) > 1, "Mutable metadata alias fixture is incomplete")
        require(len({id(candidate.record.metadata) for candidate in siblings}) == len(siblings), "Candidate metadata dictionaries were shared")
        siblings[0].record.metadata["fictional_alias_probe"] = {"nested": [1]}
        require(all("fictional_alias_probe" not in candidate.record.metadata for candidate in siblings[1:]), "Sibling metadata mutation leaked")
        require(all("fictional_alias_probe" not in candidate.record.metadata
                    for candidate in current.candidates(base, ("alpha_code",), '"Alpha_Code"').candidates), "Metadata mutation leaked across operations")
        check_snapshot(path)
        entity_checks = check_entity_reference(path, current, variants, reference_coordinator)
        print(json.dumps({"status": "passed", **entity_checks, "reference_commit": REFERENCE_COMMIT, "reference_sha256": REFERENCE_SHA256,
                          "candidate_and_coordinator_comparisons": comparisons, "concurrent_comparisons": len(variants),
                          "raw_sql_score_payload_checks": raw_checks, "materialized_match_columns": columns,
                          "concurrent_writer_snapshot_checked": True,
                          "words_reference_commit": WORDS_REFERENCE_COMMIT, "words_reference_sha256": WORDS_REFERENCE_SHA256,
                          "word_set_equivalence_and_mutation_cases": words_checks,
                          "prior_coordinator_comparisons": comparisons + len(variants)
                          + entity_checks["entity_candidate_and_coordinator_comparisons"] + entity_checks["entity_concurrent_comparisons"],
                          "mutable_metadata_aliasing_checked": True, "private_cases_executed": 0, "remote_calls": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
