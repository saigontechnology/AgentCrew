"""Fictional policy/structure/evidence checks; no private inputs or measurement."""

from __future__ import annotations

import json
import math
import struct
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import (
    cached_query_vector,
    digest,
    file_hash,
    local_document_vectors,
    query_from_case,
    read_jsonl,
)
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator
from AgentCrew.modules.memory.sqlite_repository import (
    SCHEMA_VERSION,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.types import (
    DOCUMENT_VECTOR_DIMENSIONS,
    CandidateBatch,
    CandidateSource,
    MatchReason,
    MemoryCandidate,
    MemoryQuery,
    MemoryRecord,
    RetrievalUnit,
    SQLiteSemanticPolicy,
)
from scripts.benchmark_memory_retrieval import (
    frozen_protocol,
    input_evidence,
    run_benchmark,
)

FIXTURE_POLICY = SQLiteSemanticPolicy(0.2)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(callback, expected=None):
    try:
        callback()
    except (TypeError, ValueError) as error:
        require(
            expected is None or str(error) == expected, "Wrong validation rejection"
        )
        return
    raise AssertionError("Expected rejection")


def basis(index: int, sign: float = 1.0) -> tuple[float, ...]:
    values = [0.0] * DOCUMENT_VECTOR_DIMENSIONS
    values[index] = sign
    return tuple(values)


def record(memory_id: str, headline: str, outcome: str, **changes) -> MemoryRecord:
    document = (
        f"<MEMORY><HEAD>{headline}</HEAD><FLOW><OUTCOME>{outcome}</OUTCOME></FLOW>"
        f"<ENTITIES><ENTITY><NAME>{headline}</NAME></ENTITY></ENTITIES></MEMORY>"
    )
    return replace(
        MemoryRecord(
            memory_id,
            "SemanticAgent",
            "semantic-past",
            document,
            1767225600,
            1767225600,
            {"synthetic": True},
            header=headline,
        ),
        **changes,
    )


def query(text="zephyr telescope battery", vector=None, **changes):
    return MemoryQuery(
        text,
        "SemanticAgent",
        "semantic-current",
        query_vector=vector,
        semantic_policy=changes.pop("semantic_policy", FIXTURE_POLICY),
        **changes,
    )


def signature(result):
    return [
        (hit.memory_id, hit.unit_id, hit.score, hit.reasons, hit.confidence)
        for hit in result.hits
    ]


def tie_candidate(memory_id, text, source, distance=None):
    item = record(memory_id, memory_id, text)
    unit = RetrievalUnit(
        f"unit-{memory_id}", memory_id, "outcome", "FLOW/OUTCOME", text, 1, "tie"
    )
    return MemoryCandidate(
        item, unit, MatchReason(source, 1, "equal-score fixture", distance)
    )


def checks_policy():
    for invalid in (-0.01, 2.01, 10**1000, math.nan, math.inf, -math.inf, True, "0.2"):
        rejects(lambda invalid=invalid: SQLiteSemanticPolicy(invalid))
    for invalid in (
        None,
        10**1000,
        math.nan,
        math.inf,
        -math.inf,
        -0.1,
        True,
        "0.1",
        0.21,
    ):
        require(
            not FIXTURE_POLICY.qualifies(invalid),
            "Invalid or rejected distance admitted",
        )
    require(FIXTURE_POLICY.qualifies(0.2), "Explicit boundary must be inclusive")
    require(
        not SQLiteSemanticPolicy().qualifies(0.0), "Unset policy admitted semantics"
    )
    rejects(lambda: query(vector=basis(0), semantic_policy={"max_distance": 0.2}))
    zero = struct.pack("<1024f", *basis(0, 0.0))
    invalid = struct.pack("<1024f", *basis(0, math.nan))
    for blob in (b"short", zero, invalid):
        require(
            math.isinf(SQLiteMemoryRepository._cosine_distance(basis(0), blob, 1.0)),
            "Invalid stored vector scored",
        )
    require(
        SQLiteMemoryRepository._cosine_distance(
            basis(0, 1e300), struct.pack("<1024f", *basis(0)), 1e300
        )
        == 0,
        "Finite large input overflowed cosine",
    )


def checks_retrieval(folder):
    repository = SQLiteMemoryRepository(folder / "fixture.sqlite3")
    lexical = record(
        "sem-lexical", "quartzmarker", "quartzmarker remains the lexical token"
    )
    near = record(
        "sem-near",
        "orbitcache",
        "orbit cache expiry is seven minutes",
        cwd="/fictional/one",
    )
    far = record("sem-far", "cedarledger", "cedar ledger stores packing notes")
    weak = record("sem-weak", "amberjournal", "amber journal stores shipping notes")
    other = record("sem-other", "otheragent", "isolated", agent_name="OtherAgent")
    current = record(
        "sem-current", "currentsession", "excluded", session_id="semantic-current"
    )
    deleted = record("sem-deleted", "deletedtoken", "excluded", status="deleted")
    superseded = record("sem-superseded", "oldtoken", "excluded", status="superseded")
    linked = record("sem-linked", "linkedtoken", "excluded", superseded_by="sem-near")
    future = record("sem-future", "futuretoken", "outside date", created_at=1798761600)
    expired = record(
        "sem-expired", "expiredtoken", "outside validity", valid_to=1760000000
    )
    deferred = record(
        "sem-deferred", "deferredtoken", "not valid yet", valid_from=1768000000
    )
    legacy = record(
        "sem-legacy", "legacytoken", "legacy session allowed", session_id=None
    )
    for item in (
        lexical,
        near,
        far,
        weak,
        other,
        current,
        deleted,
        superseded,
        linked,
        future,
        expired,
        deferred,
        legacy,
    ):
        repository.upsert_record(item)
    coordinator = MemorySearchCoordinator(repository)
    before = signature(coordinator.search(query("quartzmarker")))
    require(before and before[0][0] == "sem-lexical", "Lexical baseline missing")
    weak_vector = tuple([0.1, math.sqrt(0.99)] + [0.0] * 1022)
    vectors = {
        item.id: basis(0)
        for item in (
            near,
            other,
            current,
            deleted,
            superseded,
            linked,
            future,
            expired,
            deferred,
            legacy,
        )
    }
    vectors.update({far.id: basis(1), weak.id: weak_vector, lexical.id: weak_vector})
    require(
        repository.copy_local_document_vectors(vectors) == len(vectors),
        "Local vector copy count",
    )
    with repository._transaction() as connection:
        require(
            connection.execute("SELECT version FROM schema_version").fetchone()[0]
            == SCHEMA_VERSION,
            "Schema changed",
        )
        require(
            connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION,
            "User version changed",
        )
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='memory_document_vector'"
        ).fetchone()[0]
        require(
            table_sql.upper().startswith("CREATE TABLE")
            and "VIRTUAL" not in table_sql.upper(),
            "Not extension-free",
        )
    scoped = query(
        vector=basis(0),
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 1),
        as_of=1767225600,
    )
    survived = coordinator.search(scoped)
    with patch.object(
        repository, "_cosine_distance", wraps=repository._cosine_distance
    ) as score:
        capped = coordinator.search(replace(scoped, candidate_limit=1))
        require(
            score.call_count == 5 and len(capped.hits) == 1,
            "Hard eligibility was not applied before semantic scoring/cap",
        )
    require(
        {hit.memory_id for hit in survived.hits} == {near.id, legacy.id},
        "Semantic admission/scope/abstention failed",
    )
    require(
        all(
            [reason.source for reason in hit.reasons] == [CandidateSource.SEMANTIC]
            for hit in survived.hits
        ),
        "Zero-overlap paraphrase not independent",
    )
    require(
        signature(coordinator.search(query("quartzmarker"))) == before,
        "Missing vector changed lexical path",
    )
    require(
        signature(
            coordinator.search(
                query("quartzmarker", basis(0), semantic_policy=SQLiteSemanticPolicy())
            )
        )
        == before,
        "Unset semantic policy changed lexical path",
    )
    require(
        signature(
            coordinator.search(
                query("quartzmarker", basis(0), memory_ids=(lexical.id,))
            )
        )
        == signature(
            coordinator.search(query("quartzmarker", memory_ids=(lexical.id,)))
        ),
        "Rejected semantics boosted lexical score",
    )
    require(
        not coordinator.search(replace(scoped, query_vector=basis(2))).hits,
        "Unrelated vectors destroyed abstention",
    )
    require(
        not coordinator.search(replace(scoped, query_vector=basis(0, 0.0))).hits,
        "Zero query vector admitted",
    )
    require(
        not coordinator.search(
            replace(scoped, memory_ids=(current.id, other.id, deleted.id, linked.id))
        ).hits,
        "Explicit IDs bypassed eligibility",
    )
    explicit = coordinator.search(query(memory_ids=(lexical.id,)))
    require(
        [hit.memory_id for hit in explicit.hits] == [lexical.id],
        "Explicit eligible ID lost bypass",
    )
    require(
        not coordinator.search(
            replace(scoped, to_date=date(2025, 12, 31), from_date=None)
        ).hits,
        "UTC date fence failed",
    )
    old_signature = signature(survived)
    repository.upsert_record(replace(near, cwd="/fictional/two"))
    require(
        signature(coordinator.search(scoped)) == old_signature, "CWD changed ranking"
    )
    readonly = SQLiteMemoryRepository(repository.path, read_only=True)
    require(
        signature(MemorySearchCoordinator(readonly).search(scoped)) == old_signature,
        "Read-only semantic path differs",
    )
    bare = SQLiteMemoryRepository(folder / "bare.sqlite3")
    bare.upsert_record(lexical)
    bare_coordinator = MemorySearchCoordinator(bare)
    require(
        signature(bare_coordinator.search(query("quartzmarker", basis(0)))) == before,
        "Vectorless schema lost lexical retrieval",
    )
    with bare._transaction() as connection:
        require(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_document_vector'"
            ).fetchone()
            is None,
            "Optional vector table created by read",
        )


def checks_fusion():
    class FixtureRepository:
        def __init__(self, rows):
            self.rows = rows

        def candidates(self, *args):
            return CandidateBatch(tuple(self.rows))

    lexical = [
        tie_candidate(
            f"tie-{letter}", "zephyr telescope orbit cache", CandidateSource.FTS
        )
        for letter in "abcde"
    ]
    semantic = tie_candidate(
        "tie-z", "orbit cache expiry minutes", CandidateSource.SEMANTIC, 0.0
    )
    ask = query("zephyr telescope battery chemistry", basis(0))
    rows = lexical + [semantic]
    tied = MemorySearchCoordinator(FixtureRepository(rows)).search(ask)
    require(
        [hit.memory_id for hit in tied.hits] == [f"tie-{letter}" for letter in "abcde"],
        "Blanket semantic tie preference remains",
    )
    require(
        signature(tied)
        == signature(
            MemorySearchCoordinator(FixtureRepository(list(reversed(rows)))).search(ask)
        ),
        "Tie order is unstable",
    )
    base = signature(MemorySearchCoordinator(FixtureRepository(lexical)).search(ask))
    rejected = replace(
        semantic,
        record=lexical[0].record,
        reason=MatchReason(CandidateSource.SEMANTIC, 1, "weak", 0.9),
    )
    require(
        signature(
            MemorySearchCoordinator(FixtureRepository(lexical + [rejected])).search(ask)
        )
        == base,
        "Rejected signal changed lexical rank/score",
    )
    poisoned = replace(
        semantic,
        unit=replace(semantic.unit, text=ask.query),
        reason=MatchReason(CandidateSource.SEMANTIC, 1, "invalid", math.nan),
    )
    require(
        signature(
            MemorySearchCoordinator(FixtureRepository(lexical + [poisoned])).search(ask)
        )
        == base,
        "Rejected semantic evidence affected relative lexical coverage",
    )
    partial = tie_candidate("partial", "zephyr irrelevant topic", CandidateSource.FTS)
    qualified = replace(
        semantic,
        record=partial.record,
        unit=replace(semantic.unit, memory_id=partial.record.id),
    )
    result = MemorySearchCoordinator(FixtureRepository([partial, qualified])).search(
        ask
    )
    require(
        len(result.hits) == 1
        and [reason.source for reason in result.hits[0].reasons]
        == [CandidateSource.SEMANTIC],
        "Unqualified lexical source boosted a semantic-only hit",
    )


def checks_scan_and_snapshot(folder):
    class ObservedRepository(SQLiteMemoryRepository):
        def __init__(self, path):
            self.sql = []
            self.scored = []
            self.hydrated = []
            self.writer = None
            super().__init__(path)

        @contextmanager
        def _transaction(self, **kwargs):
            with super()._transaction(**kwargs) as connection:
                connection.set_trace_callback(self.sql.append)
                yield connection

        def _cosine_distance(self, vector, blob, query_norm):
            self.scored.append(blob)
            if self.writer is not None:
                writer, self.writer = self.writer, None
                writer()
            return super()._cosine_distance(vector, blob, query_norm)

        def _candidate(self, row, source, rank, detail, distance=None):
            if source == CandidateSource.SEMANTIC:
                self.hydrated.append(row["id"])
            return super()._candidate(row, source, rank, detail, distance)

    repository = ObservedRepository(folder / "scan.sqlite3")
    items = [
        record(
            f"scan-{index}",
            f"orchid{index}",
            "retained outcome" + "<NOTE>extra evidence</NOTE>" * 20,
        )
        for index in range(8)
    ]
    for item in items:
        repository.upsert_record(item)
    repository.copy_local_document_vectors({item.id: basis(0) for item in items})
    with repository._transaction() as connection:
        require(
            connection.execute(
                "SELECT count(*) FROM retrieval_unit WHERE memory_id=?", (items[0].id,)
            ).fetchone()[0]
            > 1,
            "Scan fixture lacks retrieval-unit fan-out",
        )
    repository.sql.clear()
    ask = query(vector=basis(0), candidate_limit=2)
    writer = SQLiteMemoryRepository(repository.path)
    repository.writer = lambda: (
        writer.delete_record(items[0].id, items[0].agent_name),
        writer.upsert_record(replace(items[1], document="replacement after snapshot")),
    )
    original_hypot = math.hypot
    hypot_calls = []

    def counted_hypot(*values):
        hypot_calls.append(values)
        return original_hypot(*values)

    with patch("AgentCrew.modules.memory.sqlite_repository.math.hypot", counted_hypot):
        batch = repository.candidates(ask, (), "")
    require(
        len(repository.scored) == len(items), "Scoring was not once per eligible parent"
    )
    require(len(hypot_calls) == len(items) + 1, "Query norm was not computed once")
    require(
        repository.hydrated == ["scan-0", "scan-1"], "Late hydration not capped/stable"
    )
    require(
        all(
            candidate.unit.source_field == "FLOW/OUTCOME"
            for candidate in batch.candidates
        ),
        "Deterministic outcome selection changed",
    )
    require(
        [candidate.record.document for candidate in batch.candidates]
        == [item.document for item in items[:2]],
        "Hydration did not share vector/lexical snapshot",
    )
    projection = next(sql for sql in repository.sql if "SELECT m.id,v.vector" in sql)
    require(
        "m.*" not in projection
        and "JOIN retrieval_unit" not in projection
        and "u.text" not in projection,
        "Scoring loaded payload or unit fan-out",
    )
    hydrated_sql = [
        sql for sql in repository.sql if "SELECT m.*,u.id AS unit_id" in sql
    ]
    require(
        len(hydrated_sql) == ask.candidate_limit, "Hydration SQL exceeded selected cap"
    )
    repository.scored.clear()
    repository.hydrated.clear()
    next_batch = repository.candidates(ask, (), "")
    require(
        len(repository.scored) == 7
        and next_batch.candidates[0].record.document == "replacement after snapshot",
        "Next request failed to observe committed writer",
    )
    repository.scored.clear()
    repository.candidates(replace(ask, semantic_policy=SQLiteSemanticPolicy()), (), "")
    require(not repository.scored, "Unset policy still scanned vectors")
    vectors = {
        item.id: tuple([0.85, math.sqrt(1 - 0.85**2)] + [0.0] * 1022)
        for item in items[1:]
    }
    vectors["scan-1"] = tuple([0.9, math.sqrt(1 - 0.9**2)] + [0.0] * 1022)
    vectors["scan-7"] = basis(0)
    writer.copy_local_document_vectors(vectors)
    ordered = repository.candidates(ask, (), "").candidates
    require(
        [candidate.record.id for candidate in ordered] == ["scan-7", "scan-1"]
        and ordered[0].reason.rank == 1
        and ordered[0].reason.distance < ordered[1].reason.distance,
        "Qualified semantic source was not ordered by distance before its cap",
    )


def checks_benchmark_evidence(folder):
    vector_path = folder / "fictional-vectors.jsonl"
    vector_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provenance": "local-stored-document",
                "memory_id": "fictional",
                "embedding": list(basis(0)),
            }
        )
        + "\n"
    )
    review = folder / "fictional-review.json"
    review.write_text("{}\n")
    dataset = SimpleNamespace(synthetic=False, review_manifest_hash=file_hash(review))
    report = {
        "dataset_scope": "full-cohort",
        "dataset_provenance_kind": "full-cohort",
        "calibration_provenance_contract": None,
        "calibration_derivation": None,
        "calibration_export_sha256": None,
        "parent_review_manifest_sha256": None,
        "parent_cases_sha256": None,
        "k": 5,
        "warmups": 0,
        "repetitions": 1,
        "sqlite_source_sha256": "1" * 64,
        "chroma_source_fingerprint": "2" * 64,
        "collection_sha256": "3" * 64,
        "embedding_cache_sha256": None,
        "review_manifest_sha256": file_hash(review),
        "document_vector_cache_sha256": file_hash(vector_path),
        "sqlite_semantic_policy": asdict(FIXTURE_POLICY),
    }
    embedding = {
        "embedding_source": "disabled",
        "provider": None,
        "model": None,
        "threshold": None,
    }
    frozen = {
        "protocol": frozen_protocol(report, embedding),
        "review_manifest_sha256": file_hash(review),
    }
    args = SimpleNamespace(
        k=5,
        warmups=0,
        repetitions=1,
        modes="chroma,sqlite",
        split="holdout",
        allow_remote_llm=False,
        allow_remote_embeddings=False,
        embedding_source="disabled",
        embedding_cache=None,
        review_manifest=review,
        freeze_manifest=folder / "unused.json",
        document_vector_cache=vector_path,
        sqlite_semantic_max_distance=0.2,
    )
    with (
        patch("scripts.benchmark_memory_retrieval.input_path", lambda path: path),
        patch("scripts.benchmark_memory_retrieval.read_json", lambda path: frozen),
        patch("scripts.benchmark_memory_retrieval.SQLiteMemoryRepository") as backend,
        patch("scripts.benchmark_memory_retrieval.reserve_holdout") as reserve,
        patch("scripts.benchmark_memory_retrieval.copy_chroma") as chroma,
    ):
        captured = input_evidence(args, dataset, ["chroma", "sqlite"])
        require(
            captured[3] == file_hash(vector_path)
            and captured[4] == {"fictional": basis(0)}
            and captured[5] == FIXTURE_POLICY,
            "Document vector/policy evidence omitted",
        )

        def blocked(expected="frozen_input_evidence_changed"):
            rejects(
                lambda: run_benchmark(args, dataset, folder / "never-run"), expected
            )
            for operation in (backend, reserve, chroma):
                operation.assert_not_called()

        original = vector_path.read_bytes()
        vector_path.write_bytes(original + b"\n")
        blocked()
        vector_path.write_bytes(original)
        args.document_vector_cache = None
        blocked()
        args.document_vector_cache = vector_path
        args.sqlite_semantic_max_distance = 0.3
        blocked()
        args.sqlite_semantic_max_distance = math.nan
        blocked("invalid_sqlite_semantic_policy")
        args.sqlite_semantic_max_distance = 0.2
        for key in ("document_vector_cache_sha256", "sqlite_semantic_policy"):
            saved = frozen["protocol"].pop(key)
            blocked("frozen_input_evidence_missing")
            frozen["protocol"][key] = saved
        frozen["protocol"]["document_vector_cache_sha256"] = None
        args.document_vector_cache = None
        require(
            input_evidence(args, dataset, ["chroma", "sqlite"])[3:5] == (None, None),
            "Explicit vector absence omitted",
        )
        args.document_vector_cache = vector_path
        blocked()
        args.split = "calibration"

        def changed_read(path, **kwargs):
            vector_path.write_bytes(original.replace(b"1.0", b"0.9", 1))
            return read_jsonl(path, **kwargs)

        with patch("scripts.benchmark_memory_retrieval.read_jsonl", changed_read):
            blocked("jsonl_evidence_changed")
        vector_path.write_bytes(original)
    cached = cached_query_vector(
        [{"query_sha256": digest("fictional query"), "embedding": list(basis(0))}],
        "fictional query",
    )
    require(
        cached == basis(0) and cached_query_vector([], "fictional query") is None,
        "Cached vector pass-through changed",
    )
    require(
        cached_query_vector(
            [{"query_sha256": digest("fictional query"), "embedding": [1.0]}],
            "fictional query",
        )
        is None,
        "Invalid cached vector accepted",
    )
    handed = query_from_case(
        {
            "query": "fictional query",
            "agent_name": "SemanticAgent",
            "current_session_id": "now",
            "kinds": [],
        },
        5,
        cached,
        FIXTURE_POLICY,
    )
    require(
        handed.query_vector == cached and handed.semantic_policy == FIXTURE_POLICY,
        "Benchmark lost query policy",
    )
    rejects(
        lambda: local_document_vectors(
            [
                {
                    "schema_version": 1,
                    "provenance": "generated",
                    "memory_id": "fictional",
                    "embedding": list(basis(0)),
                }
            ]
        )
    )


def main():
    checks_policy()
    with TemporaryDirectory(prefix="agentcrew-semantic-fictional-") as directory:
        folder = Path(directory)
        checks_retrieval(folder)
        checks_fusion()
        checks_scan_and_snapshot(folder)
        checks_benchmark_evidence(folder)
    print(
        json.dumps(
            {
                "status": "passed",
                "fictional": True,
                "fixture_only_max_distance": FIXTURE_POLICY.max_distance,
                "default_max_distance": None,
                "private_cases": 0,
                "remote_calls": 0,
                "measurement": False,
                "holdout_reserved": False,
            }
        )
    )


if __name__ == "__main__":
    main()
