"""Fictional semantic-candidate checks. No private corpus, network, or benchmark."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import cached_query_vector, digest, local_document_vectors, query_from_case
from AgentCrew.modules.memory.retrieval import MemorySearchCoordinator
from AgentCrew.modules.memory.sqlite_repository import SCHEMA_VERSION, SQLiteMemoryRepository
from AgentCrew.modules.memory.types import (
    DOCUMENT_VECTOR_DIMENSIONS, CandidateBatch, CandidateSource, MatchReason,
    MemoryCandidate, MemoryQuery, MemoryRecord, RetrievalUnit,
)
from AgentCrew.modules.memory.voyageai_ef import VOYAGE_RELEVANT_THRESHOLD


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def basis(index: int, sign: float = 1.0) -> tuple[float, ...]:
    values = [0.0] * DOCUMENT_VECTOR_DIMENSIONS
    values[index] = sign
    return tuple(values)


def record(memory_id: str, headline: str, outcome: str, *, agent: str = "SemanticAgent", session: str = "semantic-past", status: str = "active") -> MemoryRecord:
    document = (
        f"<MEMORY><HEAD>{headline}</HEAD><FLOW><OUTCOME>{outcome}</OUTCOME></FLOW>"
        f"<ENTITIES><ENTITY><NAME>{headline}</NAME></ENTITY></ENTITIES></MEMORY>"
    )
    return MemoryRecord(
        memory_id, agent, session, document, 1767225600, 1767225600,
        {"synthetic": True, "agent": agent, "date": 1767225600}, header=headline, status=status,
    )


def query(text: str, vector: tuple[float, ...] | None = None, **changes) -> MemoryQuery:
    return MemoryQuery(text, "SemanticAgent", "semantic-current", query_vector=vector, **changes)


def reasons(result):
    return {
        hit.memory_id: tuple(reason.source for reason in hit.reasons)
        for hit in result.hits
    }


def _tie_candidate(memory_id: str, text: str, source: CandidateSource, distance: float | None) -> MemoryCandidate:
    item = record(memory_id, memory_id, text)
    unit = RetrievalUnit(f"unit-{memory_id}", memory_id, "outcome", "FLOW/OUTCOME", text, 1, "tie")
    return MemoryCandidate(item, unit, MatchReason(source, 1, "equal-score fixture", distance))


def main():
    require(VOYAGE_RELEVANT_THRESHOLD == 1, "Frozen Chroma cutoff drifted")
    lexical = record("sem-lexical", "quartzmarker", "quartzmarker remains the lexical token")
    near = record("sem-near", "orbitcache", "orbit cache expiry is seven minutes")
    far = record("sem-far", "cedarledger", "cedar ledger stores unrelated packing notes")
    other = record("sem-other", "otheragent", "other agent must stay isolated", agent="OtherAgent")
    current = record("sem-current", "currentsession", "current session must stay excluded", session="semantic-current")
    deleted = record("sem-deleted", "deletedtoken", "deleted token must stay excluded", status="deleted")
    ask = query("zephyr telescope battery")
    with TemporaryDirectory(prefix="agentcrew-semantic-") as directory:
        path = Path(directory) / "fixture.sqlite3"
        repository = SQLiteMemoryRepository(path)
        for item in (lexical, near, far, other, current, deleted):
            repository.upsert_record(item)
        coordinator = MemorySearchCoordinator(repository)
        before = reasons(coordinator.search(query("quartzmarker")))
        require(before.get("sem-lexical") == (CandidateSource.ENTITY, CandidateSource.FTS), "Lexical baseline changed")
        require(repository.copy_local_document_vectors({
            "sem-near": basis(0),
            "sem-far": basis(0, -1.0),
            "sem-other": basis(0),
            "sem-current": basis(0),
            "sem-deleted": basis(0),
        }) == 5, "Vector copy count")
        with repository._transaction() as connection:
            require(connection.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION, "Schema version changed")
            require(connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, "User version changed")
            table_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='memory_document_vector'"
            ).fetchone()[0]
            require(table_sql.upper().startswith("CREATE TABLE"), "Vector storage is not a plain table")
            require("vec0" not in table_sql.lower() and "sqlite_vec" not in table_sql.lower(), "Vector extension syntax appeared")
        unchanged = reasons(coordinator.search(query("quartzmarker")))
        require(unchanged == before, "Lexical result changed when no query vector was supplied")
        survived = coordinator.search(replace(ask, query_vector=basis(0)))
        require([hit.memory_id for hit in survived.hits] == ["sem-near"], "In-cutoff semantic hit did not survive the lexical floor")
        require(survived.hits[0].reasons[0].source == CandidateSource.SEMANTIC, "Semantic reason missing")
        require("zephyr" not in survived.hits[0].excerpt.casefold(), "Semantic test leaked the query into evidence")
        repository.copy_local_document_vectors({"sem-far": basis(1, -1.0)})
        orthogonal = coordinator.search(replace(ask, query_vector=basis(1)))
        require([hit.memory_id for hit in orthogonal.hits] == ["sem-near"], "Distance equal to the cutoff was dropped")
        repository.copy_local_document_vectors({"sem-near": basis(0), "sem-far": basis(0)})
        blocked = coordinator.search(replace(ask, query_vector=basis(0, -1.0)))
        require(not blocked.hits, "Beyond-cutoff semantic hit was kept")
        repository.copy_local_document_vectors({"sem-near": basis(0), "sem-far": basis(0, -1.0)})
        mixed = coordinator.search(query("quartzmarker", basis(0)))
        mixed_ids = {hit.memory_id for hit in mixed.hits}
        require(mixed_ids == {"sem-lexical", "sem-near"}, "Relative lexical floor dropped the semantic hit or lexical hit")
        require(all(hit.memory_id != "sem-other" and hit.memory_id != "sem-current" and hit.memory_id != "sem-deleted" for hit in mixed.hits), "Semantic scope leak")
        readonly = SQLiteMemoryRepository(path, read_only=True)
        require(reasons(MemorySearchCoordinator(readonly).search(query("quartzmarker"))) == before, "Read-only lexical path changed")
        bare = Path(directory) / "bare.sqlite3"
        bare_repository = SQLiteMemoryRepository(bare)
        bare_repository.upsert_record(lexical)
        require(reasons(MemorySearchCoordinator(bare_repository).search(query("quartzmarker")))["sem-lexical"], "Bare schema lost lexical retrieval")
        with bare_repository._transaction() as connection:
            require(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_document_vector'"
            ).fetchone() is None, "Optional table was created without an explicit copy")
        class EqualScoreRepository:
            def candidates(self, search_query, exact_values, expression):
                lexical = ("tie-a", "tie-b", "tie-c", "tie-d", "tie-e")
                rows = [
                    _tie_candidate(memory_id, "zephyr telescope orbit cache", CandidateSource.FTS, None)
                    for memory_id in lexical
                ]
                rows.append(_tie_candidate("tie-z", "orbit cache expiry minutes", CandidateSource.SEMANTIC, 0.0))
                return CandidateBatch(tuple(rows))

        tied = MemorySearchCoordinator(EqualScoreRepository()).search(query("zephyr telescope battery chemistry", limit=5))
        tied_ids = [hit.memory_id for hit in tied.hits]
        require(len(tied_ids) == 5 and tied_ids[0] == "tie-z" and "tie-e" not in tied_ids, "Nearest equal-score semantic hit stayed outside the top 5")
        require(all(abs(hit.score - tied.hits[0].score) < 1e-12 for hit in tied.hits), "Tie fixture scores were not equal")
        cached = cached_query_vector([{
            "query_sha256": digest("zephyr telescope battery"), "embedding": list(basis(0)),
        }], "zephyr telescope battery")
        require(cached == basis(0), "Cached query vector was not passed through")
        require(cached_query_vector([], "zephyr telescope battery") is None, "Missing cache vector did not stay lexical")
        require(cached_query_vector([{"query_sha256": digest("zephyr telescope battery"), "embedding": [1.0]}], "zephyr telescope battery") is None, "Non-1024 cache vector was accepted")
        copied = local_document_vectors([{
            "schema_version": 1, "provenance": "local-stored-document",
            "memory_id": "sem-near", "embedding": list(basis(0)),
        }])
        require(copied == {"sem-near": basis(0)}, "Stored document vector was not accepted")
        handed = query_from_case({
            "query": "zephyr telescope battery", "agent_name": "SemanticAgent",
            "current_session_id": "semantic-current", "kinds": [],
        }, 5, cached)
        require(handed.query_vector == cached, "MemoryQuery did not receive the cached vector")
        print("{\"status\": \"passed\", \"fictional\": true, \"private_cases\": 0, \"remote_calls\": 0, \"cutoff\": 1, \"equal_score_candidates\": 6}")


if __name__ == "__main__":
    main()
