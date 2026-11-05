"""Standalone fictional-data verification; no real review/holdout or remote calls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import (
    BenchmarkError, Calls, FakeAdapter, POLICY, PROMPT, RemoteJSONAdapter, aggregate,
    digest, encode, evaluate_answer, expand_queries, file_hash, invoke_model, load_dataset,
    measure, query_from_case, read_jsonl, rerank_hits,
)
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from scripts.benchmark_memory_retrieval import (
    ASSETS, ChromaBaseline, acceptance, code_identity, private_path, reserve_holdout, signature, synthetic_embedding,
)
from scripts.migrate_chroma_memory_to_sqlite import MigrationError, _private_directory, _write_json as write_json
from verification.memory.verify_phase1 import record_from_fixture
from verification.memory.verify_phase2 import run_child, uv_command


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(callback):
    try:
        callback()
    except (BenchmarkError, MigrationError, ValueError, sqlite3.Error):
        return
    raise AssertionError("Expected a static benchmark safety rejection")


def write_jsonl(path, rows):
    path.write_text("".join(encode(row) + "\n" for row in rows), encoding="utf-8")
    path.chmod(0o600)


def checks_loading(folder, dataset):
    corpus = ASSETS / "synthetic-corpus.jsonl"
    cases = folder / "mutated-cases.jsonl"
    for mutation in (
        lambda rows: rows.append(deepcopy(rows[0])),
        lambda rows: rows[0].update(schema_version=99),
        lambda rows: rows[0].update(expected_memory_ids=["missing-fictional-id"]),
        lambda rows: rows[0].update(agent_name="FictionalWrongAgent"),
        lambda rows: rows[0].update(current_session_id=dataset.records[rows[0]["expected_memory_ids"][0]].session_id),
        lambda rows: rows[0].update(from_date="1900-01-01", to_date="1900-01-02"),
        lambda rows: rows[0].update(from_date=False),
        lambda rows: rows[0].update(from_date="20260101"),
        lambda rows: rows[0].update(excluded_memory_ids=rows[0]["expected_memory_ids"]),
        lambda rows: rows[0].update(must_abstain=True),
        lambda rows: rows[0].update(relevance_grades={rows[0]["expected_memory_ids"][0]: 0}),
        lambda rows: rows[0].update(reviewed_locally="true"),
    ):
        rows = deepcopy(list(dataset.cases))
        mutation(rows)
        write_jsonl(cases, rows)
        rejects(lambda: load_dataset(corpus, cases))
    cases.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
    rejects(lambda: read_jsonl(cases))
    cases.write_text("[]\n", encoding="utf-8")
    rejects(lambda: read_jsonl(cases))
    cases.write_text("{broken}\n", encoding="utf-8")
    rejects(lambda: read_jsonl(cases))
    records = [json.loads(line) for line in corpus.read_text().splitlines()]
    write_jsonl(folder / "duplicate-corpus.jsonl", records + records[:1])
    rejects(lambda: load_dataset(folder / "duplicate-corpus.jsonl", ASSETS / "benchmark-cases.jsonl"))
    for row in records:
        row["synthetic"] = False
    private_cases = deepcopy(list(dataset.cases))
    for case in private_cases:
        case.update(synthetic=False, split="calibration", reviewed_locally=False, draft=True)
    write_jsonl(folder / "fictional-private-class-corpus.jsonl", records)
    write_jsonl(cases, private_cases)
    rejects(lambda: load_dataset(folder / "fictional-private-class-corpus.jsonl", cases))
    reviewed_fixture = [deepcopy(c) for c in dataset.cases if not c.get("same_ranking_as_case")]
    abstention_template = next(c for c in dataset.cases if c["must_abstain"])
    while len(reviewed_fixture) < 32:
        extra = deepcopy(abstention_template)
        extra.update(case_id="fictional-review-contract-" + str(len(reviewed_fixture)),
                     query="Find fictional missing contract nonce " + str(len(reviewed_fixture)),
                     categories=["no_relevant_memory"])
        reviewed_fixture.append(extra)
    for index, case in enumerate(reviewed_fixture):
        case.update(synthetic=False, reviewed_locally=True, draft=False,
                    split="calibration" if index < 16 else "holdout", query_sanitized=True)
    write_jsonl(cases, reviewed_fixture)
    review = folder / "fictional-review-attestation.json"
    write_json(review, {"schema_version": 1, "reviewed_locally": True,
                        "review_method": "local-human", "fixture_only": True,
                        "corpus_sha256": file_hash(folder / "fictional-private-class-corpus.jsonl"),
                        "cases_sha256": file_hash(cases)})
    accepted_fixture = load_dataset(folder / "fictional-private-class-corpus.jsonl", cases, review)
    require(len(accepted_fixture.cases) == 32, "Valid fictional review-contract fixture rejected")
    duplicate = deepcopy(reviewed_fixture[0])
    duplicate["case_id"] = "fictional-renamed-duplicate-question"
    write_jsonl(cases, reviewed_fixture + [duplicate])
    document = json.loads(review.read_text())
    document["cases_sha256"] = file_hash(cases)
    write_json(review, document)
    rejects(lambda: load_dataset(folder / "fictional-private-class-corpus.jsonl", cases, review))
    rejects(lambda: private_path(Path("/synthetic/outside-allowed/output")))
    alias = folder / "symlink.jsonl"
    alias.symlink_to(corpus)
    rejects(lambda: private_path(alias))


def checks_metrics(dataset):
    case = dataset.cases[0]
    identifier = case["expected_memory_ids"][0]
    record = dataset.records[identifier]
    hit = {"memory_id": identifier, "citation": "fictional-citation", "score": 1,
           "cwd": record.cwd, "date": datetime.fromtimestamp(record.created_at, UTC).date().isoformat(),
           "reasons": [], "kind": "fact", "excerpt": "fictional evidence", "conflict": False}
    perfect = measure(case, [hit], dataset.records, 5)
    require(perfect["recall_at_5"] == 1 and perfect["precision_at_5"] == .2 and perfect["mrr"] == 1 and perfect["ndcg"] == 1, "Known metric arithmetic failed")
    duplicate = measure(case, [hit, hit], dataset.records, 5)
    require(duplicate["duplicate_parent_hits"] == 1 and duplicate["ndcg"] <= 1, "Duplicate reward regression")
    excluded = next(key for key in case["excluded_memory_ids"] if dataset.records[key].status == "deleted")
    leaked = measure(case, [{**hit, "memory_id": excluded}], dataset.records, 5)
    require(leaked["excluded_leaks"] == 1 and leaked["deletion_leaks"] == 1, "Deletion leak metric failed")
    unknown = measure(case, [{**hit, "memory_id": "fictional-unknown"}], dataset.records, 5)
    require(unknown["unknown_id_leaks"] == 1, "Unknown ID leak metric failed")
    abstain = next(row for row in dataset.cases if row["must_abstain"])
    require(measure(abstain, [], dataset.records, 5)["abstention_correct"] == 1, "Abstention metric failed")
    require(aggregate([perfect, duplicate])["duplicate_parent_hits"] == 1, "Metric aggregation failed")
    require(query_from_case(case, 5) == query_from_case({**case, "current_cwd": "fictional-other-cwd"}, 5), "CWD entered scope")
    require(signature([hit]) == signature([{**hit, "cwd": "fictional-other-cwd"}]), "CWD entered ranking signature")


class FixedAdapter(FakeAdapter):
    def __init__(self, response):
        self.response = response

    def complete(self, purpose, payload):
        return self.response


def checks_adapters(dataset):
    calls = Calls()
    require(expand_queries(FixedAdapter({"queries": ["safe fictional expansion"]}), "original", synthetic=True, calls=calls, budget=2) == ["original", "safe fictional expansion"], "Structured expansion failed")
    require(expand_queries(FixedAdapter({"queries": ["safe"], "scope": "override"}), "original", synthetic=True, calls=calls, budget=2) == ["original"], "Expansion schema fallback failed")
    require(expand_queries(FakeAdapter(), "original", synthetic=True, calls=calls, budget=2) == ["original"] and calls.model == 2, "Budget was bypassed")
    hits = [{"memory_id": "fictional-a", "citation": "a", "excerpt": "a"}, {"memory_id": "fictional-b", "citation": "b", "excerpt": "b"}]
    require(rerank_hits(FixedAdapter({"ranked_ids": ["foreign"], "abstain": False}), "q", hits, synthetic=True, calls=Calls(), budget=2) == hits, "Reranker introduced candidate")
    require(rerank_hits(FixedAdapter({"ranked_ids": ["fictional-a", "fictional-a"], "abstain": False}), "q", hits, synthetic=True, calls=Calls(), budget=2) == hits, "Reranker duplicated candidate")
    require(rerank_hits(FakeAdapter(), "q", hits, synthetic=True, calls=Calls(), budget=2) == hits, "Deterministic fake reranker failed")
    invalid_calls = Calls()
    require(rerank_hits(FixedAdapter({"ranked_ids": [], "abstain": False}), "q", hits, synthetic=True, calls=invalid_calls, budget=1) == hits and invalid_calls.fallback == 1, "Contradictory rerank did not fall back")
    empty_calls = Calls()
    require(rerank_hits(FakeAdapter(), "q", [], synthetic=True, calls=empty_calls, budget=1) == [] and empty_calls.model == 0, "Empty candidates consumed model calls")
    remote = FixedAdapter({"queries": []})
    remote.remote = True
    private_calls = Calls()
    rejects(lambda: invoke_model(remote, "expand", {"query": "fictional-private-class"}, synthetic=False, calls=private_calls, budget=2))
    require(private_calls.model == 0, "Private evidence reached remote adapter")
    case = dataset.cases[0]
    result = evaluate_answer(FakeAdapter(), case, hits, synthetic=True, calls=Calls(), budget=2, correctness_hook=lambda c, a: 1.0)
    require(result["semantic_correctness"] == 1 and result["unsupported_citations"] == 0, "Answer hook/citation checks failed")
    envelope = {"choices": [{"message": {"content": json.dumps({"queries": []})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    class NoWireOpener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            require(payload["messages"][0]["content"] == PROMPT and payload["model"] == "fictional-model", "Prompt/model parity failed")
            return io.BytesIO(json.dumps(envelope).encode())
    with patch.dict(os.environ, {"SYNTHETIC_BENCHMARK_KEY": "fictional-only"}), patch("urllib.request.build_opener", return_value=NoWireOpener()):
        adapter = RemoteJSONAdapter("https://example.invalid/chat/completions", "fictional-provider", "fictional-model", "SYNTHETIC_BENCHMARK_KEY", authorized=True)
        observed = Calls()
        require(invoke_model(adapter, "expand", {"query": "fictional-only"}, synthetic=True, calls=observed, budget=1) == {"queries": []}, "Mocked HTTP contract failed")
        require(observed.input_tokens == 10 and observed.output_tokens == 3 and observed.usage_observed_calls == 1 and observed.cost_usd is None, "Observed usage/null cost failed")


def checks_chroma_semantics(dataset):
    ids = ["syn-mem-falcon-old"] + [i for i, r in dataset.records.items() if r.status == "active"][:3]
    observed = {}

    class Collection:
        def query(self, **kwargs):
            observed.update(kwargs)
            return {"ids": [ids], "distances": [[0.1, 0.2, 0.3, 0.4]],
                    "metadatas": [[dataset.records[i].metadata for i in ids]],
                    "documents": [[dataset.records[i].document for i in ids]]}

    baseline = object.__new__(ChromaBaseline)
    baseline.collection = Collection()
    baseline.calls = Calls()
    baseline.embedding = lambda case: synthetic_embedding(case["query"])
    baseline.threshold = 1.0
    case = dataset.cases[0]
    hits = baseline.search(case, 5)
    require(len(hits) == 3 and observed["n_results"] == 10, "Chroma runtime result caps changed")
    require(measure(case, hits, dataset.records, 5)["lifecycle_leaks"] >= 1, "Chroma baseline repaired supersession")
    where = json.dumps(observed["where"])
    require("$ne" in where and case["agent_name"] in where and "cwd" not in where, "Chroma scope changed")
    baseline.threshold = 0.15
    require(len(baseline.search(case, 5)) == 1, "Chroma threshold semantics changed")
    require(baseline.calls.remote_embedding == 0, "Semantic check invoked remote embeddings")


def build_chroma(spec_path):
    import chromadb
    from chromadb.config import Settings
    spec = json.loads(spec_path.read_text())
    client = chromadb.PersistentClient(path=spec["source"], settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection("conversation", embedding_function=None, metadata={"hnsw:space": "cosine"})
    for row in spec["rows"]:
        if row["status"] == "deleted":
            continue
        collection.add(ids=[row["id"]], documents=[row["document"]], metadatas=[row["metadata"]], embeddings=[synthetic_embedding(row["document"])])
    client._system.stop()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--build-chroma":
        build_chroma(Path(sys.argv[2]))
        return
    local = ROOT / "verification/memory/local"
    _private_directory(local)
    folder = Path(tempfile.mkdtemp(prefix="phase3-verification-", dir=local))
    dataset = load_dataset(ASSETS / "synthetic-corpus.jsonl", ASSETS / "benchmark-cases.jsonl")
    checks_loading(folder, dataset)
    checks_metrics(dataset)
    checks_adapters(dataset)
    checks_chroma_semantics(dataset)
    identity = code_identity()
    held_case = deepcopy(dataset.cases[0])
    held_case.update(split="holdout", query="fictional untouched holdout " + folder.name)
    held_data = replace(dataset, cases=(held_case,), cases_hash=digest([held_case]))
    frozen = {"schema_version": 1, "calibration_complete": True, "corpus_sha256": held_data.corpus_hash,
              "cases_sha256": held_data.cases_hash, "code_hash": identity["code_hash"], "policy": POLICY,
              "fictional_verification_nonce": folder.name}
    token = reserve_holdout(frozen, held_data, identity)
    require(isinstance(token, str) and len(token) == 64, "holdout reservation identity missing")
    rejects(lambda: reserve_holdout(frozen, held_data, identity))
    rejects(lambda: reserve_holdout({**frozen, "captured_at": "changed"}, held_data, identity))
    renamed = deepcopy(held_case)
    renamed.update(case_id="fictional-renamed-case", expected_facts=["fictional corrected label"], current_cwd="/synthetic/changed")
    renamed_data = replace(held_data, cases=(renamed,), cases_hash=digest([renamed]))
    rejects(lambda: reserve_holdout({**frozen, "cases_sha256": renamed_data.cases_hash}, renamed_data, identity))
    extra_case = dict(held_case, case_id="fictional-new-question", query=held_case["query"] + " new")
    recycled = replace(held_data, cases=(held_case, extra_case),
                       cases_hash=digest([held_case, extra_case]))
    rejects(lambda: reserve_holdout({**frozen, "cases_sha256": recycled.cases_hash}, recycled, identity))
    rejects(lambda: reserve_holdout({**frozen, "code_hash": "changed"}, held_data, identity))
    repository = SQLiteMemoryRepository(folder / "candidate.sqlite3")
    rows = [json.loads(line) for line in (ASSETS / "synthetic-corpus.jsonl").read_text().splitlines()]
    for row in rows:
        repository.upsert_record(record_from_fixture(row))
    repository.delete_record("syn-mem-deleted", "SyntheticArchitect")
    repository.check_integrity()
    source = folder / "chroma-source"
    spec = folder / "source.json"
    spec.write_text(json.dumps({"source": str(source), "rows": rows}), encoding="utf-8")
    spec.chmod(0o600)
    run_child(uv_command(Path(__file__), "--build-chroma", spec), folder / "fixture.log")
    output = folder / "run"
    command = uv_command("scripts/benchmark_memory_retrieval.py", "run", "--corpus", ASSETS / "synthetic-corpus.jsonl",
                         "--cases", ASSETS / "benchmark-cases.jsonl", "--sqlite-source", repository.path,
                         "--chroma-source", source, "--embedding-source", "synthetic-hash", "--output", output,
                         "--modes", "chroma,sqlite,sqlite-expand,sqlite-rerank", "--split", "synthetic", "--k", 5,
                         "--warmups", 1, "--repetitions", 3, "--adapter", "fake", "--answer-eval", "--model-call-budget", 2000)
    run_child(command, folder / "benchmark.log")
    report = json.loads((output / "results.json").read_text())
    require(list(report["modes"])[0] == "chroma", "Chroma was not run before SQLite")
    require(report["calls"]["remote_model"] == 0 and report["calls"]["remote_embedding"] == 0, "A real remote invocation occurred")
    for mode in ("sqlite", "sqlite-expand", "sqlite-rerank"):
        summary = report["modes"][mode]
        require(summary["evaluated_cases"] == 29 and summary["backend_errors"] == 0, "Incomplete synthetic mode")
        require(summary["scope_leaks"] == 0 and summary["deletion_leaks"] == 0 and summary["duplicate_parent_hits"] == 0, "SQLite safety regression")
        require(summary["cwd_invariance_failures"] == 0 and 0 <= summary["assisted_invocation_rate"] <= 1, "CWD/invocation rate regression")
    require(report["modes"]["chroma"]["evaluated_cases"] == 29 and report["chroma"]["k_cap"] == 3, "Chroma simulation failed")
    require(report["chroma"]["fidelity"].startswith("synthetic-only") and not report["acceptance"]["passed"], "Fake baseline passed a real gate")
    for name in ("results.json", "summary.md", "failures.jsonl"):
        require((output / name).stat().st_mode & 0o777 == 0o600, "Report permissions failed")
    require(subprocess.run(["git", "check-ignore", "--quiet", "--no-index", str(output.relative_to(ROOT))], cwd=ROOT).returncode == 0, "Reports are not ignored")
    print("PASS Phase 3 strict loading/metrics/fallback/bounds/allowlist/answer-hook/fake-HTTP/freeze/single-use/isolated-backend/private-report checks")
    print("Synthetic corpus: 25 records, 29 cases, four modes; no actual remote calls. No private review or real holdout was run.")
    print(encode({mode: {key: row.get(key) for key in ("recall_at_5", "precision_at_5", "mrr", "ndcg", "no_relevant_abstention", "scope_leaks", "lifecycle_leaks", "excluded_leaks", "deletion_leaks", "duplicate_parent_hits", "p50_ms", "p95_ms")} for mode, row in report["modes"].items()}))


if __name__ == "__main__":
    main()
