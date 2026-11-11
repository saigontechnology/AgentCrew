"""Disposable fictional checks for the fresh lexical holdout runner.

No private exam files, network, or embedding calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import BenchmarkError, digest, file_hash, load_fresh_holdout_dataset, record_from_row
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.voyageai_ef import VoyageEmbeddingFunction
import scripts.benchmark_memory_retrieval as runner


def vector(index: int) -> list[float]:
    values = [0.0] * 1024
    values[index] = 1.0
    return values


def memory_row(memory_id: str, token: str) -> dict:
    created = int(datetime(2026, 1, 2, tzinfo=UTC).timestamp())
    document = (
        "<memory><HEAD>Fixture</HEAD><FLOW><OUTCOME>The stored note mentions "
        + token + ".</OUTCOME></FLOW><ENTITIES><ENTITY><NAME>Fixture</NAME></ENTITY></ENTITIES></memory>"
    )
    return {
        "schema_version": 1, "synthetic": False, "id": memory_id, "created_at": created, "updated_at": created,
        "status": "active", "document": document, "cwd": None, "document_format": "xml", "kinds": ["decision"],
        "metadata": {"agent": "fixture-agent", "session_id": "fixture-past", "date": created, "header": "Fixture"},
    }


def case(index: int, memory_id: str | None, categories: list[str], abstain: bool = False) -> dict:
    return {
        "schema_version": 1, "case_id": f"fresh-{index:02d}", "split": "holdout", "synthetic": False,
        "draft": False, "reviewed_locally": True, "review_method": "local-human", "query_sanitized": True,
        "query": f"Where is the stored note about fixturetoken{index}?",
        "agent_name": "fixture-agent", "current_session_id": "fixture-current", "categories": categories, "kinds": [],
        "expected_memory_ids": [] if abstain else [memory_id], "acceptable_memory_ids": [], "excluded_memory_ids": [],
        "expected_cwds": {}, "relevance_grades": {}, "expected_facts": ["fixture"], "must_abstain": abstain,
        "notes": "fictional",
    }


def packet(root: Path, *, draft: bool = False, case_draft: bool = False, sanitized: bool = True, cutoff=None, witnessed: bool = False,
           mixed: bool = False) -> tuple[Path, Path, Path, Path, dict]:
    rows, cache_rows, records = [], [], {}
    categories = {
        0: ["exact_identifier", "decision"], 1: ["ambiguous_followup", "decision"],
        2: ["paraphrase", "constraint_rejected_approach"], 3: ["exact_identifier", "unresolved"],
    }
    for index in range(14):
        memory_id = f"memory-{index}"
        row = memory_row(memory_id, f"fixturetoken{index}")
        records[memory_id] = record_from_row(row)
        item = case(index, memory_id, categories.get(index, ["paraphrase", "preference"]))
        rows.append(item)
        cache_rows.append({
            "schema_version": 1, "provenance": "independent-configured-query", "reviewed_locally": True,
            "provider": "voyage", "model": "voyage-4", "query_sha256": digest(item["query"]),
            "query": item["query"], "embedding": vector(index),
            "source_fingerprint": "fictional-source",
        })
    for index in (14, 15):
        item = case(index, None, ["no_relevant_memory"], True)
        item["query"] = f"What did we decide about absenttopic{index}?"
        rows.append(item)
        cache_rows.append({
            "schema_version": 1, "provenance": "independent-configured-query", "reviewed_locally": True,
            "provider": "voyage", "model": "voyage-4", "query_sha256": digest(item["query"]),
            "query": item["query"], "embedding": vector(200 + index),
            "source_fingerprint": "fictional-source",
        })
    if draft or case_draft:
        rows[0]["draft"] = True
        rows[0]["reviewed_locally"] = False
    if not sanitized:
        rows[0]["query_sanitized"] = False
    if mixed:
        rows[0]["split"] = "calibration"
    corpus, cases, review, cache = root / "corpus.jsonl", root / "cases.jsonl", root / "review.json", root / "cache.jsonl"
    corpus.write_text("".join(json.dumps(memory_row(f"memory-{index}", f"fixturetoken{index}")) + "\n" for index in range(14)))
    cases.write_text("".join(json.dumps(row) + "\n" for row in rows))
    review.write_text(json.dumps({
        "schema_version": 1, "artifact_type": "fresh-lexical-holdout-review", "dataset_scope": "fresh-holdout",
        "split": "holdout", "reviewed_locally": not draft, "review_method": None if draft else "local-human",
        "draft": draft, "query_sanitized": sanitized, "lexical_only": cutoff is None,
        "sqlite_semantic_max_distance": cutoff, "semantic_nonoverlap_witnessed": witnessed,
        "user_acceptance_is_not_semantic_nonoverlap_audit": True, "case_count": len(rows),
        "corpus_sha256": file_hash(corpus), "cases_sha256": file_hash(cases),
    }))
    cache.write_text("".join(json.dumps(row) + "\n" for row in cache_rows))
    return corpus, cases, review, cache, records


def expect(code: str, function) -> None:
    try:
        function()
    except BenchmarkError as error:
        if str(error) != code:
            raise SystemExit(f"expected {code}, got {error}")
        return
    raise SystemExit(f"missing rejection {code}")


class Collection:
    def __init__(self, records):
        self.configuration = {"hnsw": {"space": "cosine"}}
        self.records = records

    def get(self, include=None):
        keys = [key for key, record in self.records.items() if record.status != "deleted"]
        return {"ids": keys, "documents": [self.records[key].document for key in keys],
                "metadatas": [self.records[key].metadata for key in keys]}

    def query(self, query_embeddings, n_results, where, include):
        values = query_embeddings[0]
        index = max(range(len(values)), key=lambda item: values[item])
        memory_id = f"memory-{index}"
        ids = [memory_id] if values[index] > 0 and memory_id in self.records else []
        return {"ids": [ids], "documents": [[self.records[item].document for item in ids]],
                "metadatas": [[self.records[item].metadata for item in ids]], "distances": [[0.05] * len(ids)]}


def main() -> None:
    root = ROOT / "verification/memory/local" / f"fresh-holdout-fictional-{uuid.uuid4().hex[:8]}"
    root.mkdir(mode=0o700, parents=True)
    previous = os.environ.get("VOYAGE_API_KEY")
    os.environ["VOYAGE_API_KEY"] = "fictional-not-a-call"
    called = {"count": 0}
    original = VoyageEmbeddingFunction.__call__

    def forbidden(self, inputs):
        called["count"] += 1
        raise AssertionError("embedding_call_forbidden")

    VoyageEmbeddingFunction.__call__ = forbidden
    try:
        consumed = Path("verification/memory/local/benchmark-holdout-ledger/cases.jsonl")
        expect("consumed_holdout_ledger_rejected", lambda: load_fresh_holdout_dataset(root / "corpus.jsonl", consumed, root / "review.json"))
        corpus, cases, review, cache, _records = packet(root, draft=True)
        expect("fresh_holdout_unreviewed", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, _records = packet(root, case_draft=True)
        expect("private_cases_unreviewed", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, _records = packet(root, sanitized=False)
        expect("fresh_holdout_unreviewed", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, _records = packet(root, cutoff=1)
        expect("fresh_holdout_semantic_cutoff_forbidden", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, _records = packet(root, witnessed=True)
        expect("unwitnessed_semantic_attestation_rejected", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, _records = packet(root, mixed=True)
        expect("consumed_combined_cohort_rejected", lambda: load_fresh_holdout_dataset(corpus, cases, review))
        corpus, cases, review, cache, records = packet(root)
        bad_cache = root / "bad-cache.jsonl"
        bad_cache.write_text(cache.read_text().replace("voyage-4", "voyage-3"))
        expect("fresh_holdout_embedding_identity_required", lambda: runner._fresh_vectors(load_fresh_holdout_dataset(corpus, cases, review), bad_cache))
        dataset = load_fresh_holdout_dataset(corpus, cases, review)
        ledger = root / "fresh-holdout-fictional-ledger"
        runner.reserve_fresh_holdout(dataset, ledger)
        expect("fresh_holdout_already_reserved", lambda: runner.reserve_fresh_holdout(dataset, ledger))
        missing = root / "missing-cache.jsonl"
        missing.write_text("\n".join(cache.read_text().splitlines()[:-1]) + "\n")
        expect("cached_query_embedding_missing", lambda: runner.run_fresh_lexical_holdout(
            corpus, cases, review, root / "unused.sqlite", missing, root / "missing-output",
            collection=Collection(records), source_hash="fictional-source", ledger=root / "missing-ledger",
        ))
        if (root / "missing-ledger").exists():
            raise SystemExit("reservation_before_validation")

        class BrokenCollection(Collection):
            def get(self, include=None):
                page = super().get(include)
                page["ids"] = []
                return page

        setup_ledger = root / "setup-failure-ledger"
        setup_ledger.mkdir(mode=0o700)
        setup_sqlite = root / "setup.sqlite"
        setup_repository = SQLiteMemoryRepository(setup_sqlite)
        for record in records.values():
            setup_repository.upsert_record(record)
        try:
            runner.run_fresh_lexical_holdout(
                corpus, cases, review, setup_sqlite, cache, root / "setup-output",
                collection=BrokenCollection(records), source_hash="fictional-source", ledger=setup_ledger,
            )
        except BenchmarkError as error:
            if str(error) != "chroma_corpus_id_mismatch":
                raise
        else:
            raise SystemExit("setup_failure_expected")
        if any(setup_ledger.iterdir()):
            raise SystemExit("setup_failure_left_ledger_files")
        partial_ledger = root / "partial-ledger"
        partial_ledger.mkdir(mode=0o700)
        stale = partial_ledger / ".partial-stale"
        stale.mkdir(mode=0o700)
        (stale / "leftover.consumed").write_text("stale\n")
        runner.reserve_fresh_holdout(dataset, partial_ledger)
        if any(child.name.startswith(".partial-") for child in partial_ledger.iterdir()):
            raise SystemExit("partial_reservation_not_cleared")
        runner.reserve_fresh_holdout(dataset, setup_ledger)
        sqlite = root / "fixture.sqlite"
        repository = SQLiteMemoryRepository(sqlite)
        for record in records.values():
            repository.upsert_record(record)
        output = root / "output"
        output.mkdir(mode=0o700)
        report = runner.run_fresh_lexical_holdout(
            corpus, cases, review, sqlite, cache, output, collection=Collection(records),
            source_hash="fictional-source", ledger=root / "passing-ledger",
        )
        if report["acceptance"]["status"] != "passed" or report["calls"]["embedding"] or called["count"]:
            raise SystemExit("passing_fictional_comparison_failed")
        if report["policy"]["sqlite_semantic_max_distance"] is not None or report["acceptance"]["production_approved"]:
            raise SystemExit("policy_or_approval_failed")
        expect("fresh_holdout_already_reserved", lambda: runner.run_fresh_lexical_holdout(
            corpus, cases, review, sqlite, cache, root / "second-output", collection=Collection(records),
            source_hash="fictional-source", ledger=root / "passing-ledger",
        ))
    finally:
        VoyageEmbeddingFunction.__call__ = original
        if previous is None:
            os.environ.pop("VOYAGE_API_KEY", None)
        else:
            os.environ["VOYAGE_API_KEY"] = previous
        shutil.rmtree(root)
    print("fresh holdout fictional checks passed")


if __name__ == "__main__":
    main()
