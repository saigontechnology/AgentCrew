"""Standalone fictional-data verification; no real review/holdout or remote calls."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from scripts.benchmark_memory_retrieval import (
    ASSETS,
    ChromaBaseline,
    code_identity,
    dataset_evidence,
    freeze_calibration,
    input_evidence,
    private_path,
    reserve_holdout,
    run_benchmark,
    signature,
    synthetic_embedding,
)
from scripts.migrate_chroma_memory_to_sqlite import (
    MigrationError,
    _private_directory,
)
from scripts.migrate_chroma_memory_to_sqlite import (
    _write_json as write_json,
)
from verification.memory.benchmark import (
    POLICY,
    PROMPT,
    BenchmarkError,
    Calls,
    FakeAdapter,
    RemoteJSONAdapter,
    aggregate,
    digest,
    encode,
    evaluate_answer,
    expand_queries,
    file_hash,
    invoke_model,
    load_dataset,
    measure,
    query_from_case,
    read_json,
    read_jsonl,
    rerank_hits,
)
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
        lambda rows: rows[0].update(
            current_session_id=dataset.records[
                rows[0]["expected_memory_ids"][0]
            ].session_id
        ),
        lambda rows: rows[0].update(from_date="1900-01-01", to_date="1900-01-02"),
        lambda rows: rows[0].update(from_date=False),
        lambda rows: rows[0].update(from_date="20260101"),
        lambda rows: rows[0].update(excluded_memory_ids=rows[0]["expected_memory_ids"]),
        lambda rows: rows[0].update(must_abstain=True),
        lambda rows: rows[0].update(
            relevance_grades={rows[0]["expected_memory_ids"][0]: 0}
        ),
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
    rejects(
        lambda: load_dataset(
            folder / "duplicate-corpus.jsonl", ASSETS / "benchmark-cases.jsonl"
        )
    )
    for row in records:
        row["synthetic"] = False
    private_cases = deepcopy(list(dataset.cases))
    for case in private_cases:
        case.update(
            synthetic=False, split="calibration", reviewed_locally=False, draft=True
        )
    write_jsonl(folder / "fictional-private-class-corpus.jsonl", records)
    write_jsonl(cases, private_cases)
    rejects(
        lambda: load_dataset(folder / "fictional-private-class-corpus.jsonl", cases)
    )
    positives = [
        case
        for case in dataset.cases
        if not case["must_abstain"] and not case.get("same_ranking_as_case")
    ]
    templates = [
        next(case for case in positives if category in case["categories"])
        for category in (
            "exact_identifier",
            "paraphrase",
            "ambiguous_followup",
            "preference",
            "decision",
            "constraint_rejected_approach",
            "unresolved",
        )
    ]
    positive_ids = {
        memory_id
        for case in templates
        for memory_id in case["expected_memory_ids"] + case["acceptable_memory_ids"]
    }
    for case in positives:
        ids = set(case["expected_memory_ids"] + case["acceptable_memory_ids"])
        if len(templates) < 14 and ids - positive_ids:
            templates.append(case)
            positive_ids.update(ids)
    while len(templates) < 14:
        templates.append(positives[len(templates) % len(positives)])
    require(len(positive_ids) >= 10, "Fictional split fixture lacks positive memories")
    abstention_template = next(
        case
        for case in dataset.cases
        if case["must_abstain"] and "no_relevant_memory" in case["categories"]
    )
    reviewed_fixture = []
    for split in ("calibration", "holdout"):
        for index, template in enumerate(templates + [abstention_template] * 2):
            case = deepcopy(template)
            case.update(
                case_id=f"fictional-review-contract-{split}-{index}",
                query=f"Fictional {split} contract {index}: " + case["query"],
                synthetic=False,
                reviewed_locally=True,
                draft=False,
                split=split,
                query_sanitized=True,
            )
            reviewed_fixture.append(case)
    write_jsonl(cases, reviewed_fixture)
    review = folder / "fictional-review-attestation.json"
    write_json(
        review,
        {
            "schema_version": 1,
            "reviewed_locally": True,
            "review_method": "local-human",
            "fixture_only": True,
            "corpus_sha256": file_hash(folder / "fictional-private-class-corpus.jsonl"),
            "cases_sha256": file_hash(cases),
        },
    )
    accepted_fixture = load_dataset(
        folder / "fictional-private-class-corpus.jsonl", cases, review
    )
    require(
        len(accepted_fixture.cases) == 32,
        "Valid fictional review-contract fixture rejected",
    )
    require(
        accepted_fixture.review_manifest_hash == file_hash(review),
        "Review bytes not captured",
    )
    checks_split_coverage(folder, dataset, reviewed_fixture, cases, review)
    checks_input_evidence(folder, accepted_fixture, review)
    duplicate = deepcopy(reviewed_fixture[0])
    duplicate["case_id"] = "fictional-renamed-duplicate-question"
    write_jsonl(cases, reviewed_fixture + [duplicate])
    document = json.loads(review.read_text())
    document["cases_sha256"] = file_hash(cases)
    write_json(review, document)
    rejects(
        lambda: load_dataset(
            folder / "fictional-private-class-corpus.jsonl", cases, review
        )
    )
    rejects(lambda: private_path(Path("/synthetic/outside-allowed/output")))
    alias = folder / "symlink.jsonl"
    alias.symlink_to(corpus)
    rejects(lambda: private_path(alias))


def checks_split_coverage(folder, dataset, valid_cases, cases_path, review_path):
    """Reject split skew using fictional data and refreshed matching attestations."""
    corpus_path = folder / "fictional-private-class-corpus.jsonl"
    original_review = json.loads(review_path.read_text())

    def rejected_fixture(rows, reason):
        write_jsonl(cases_path, rows)
        attestation = {**original_review, "cases_sha256": file_hash(cases_path)}
        write_json(review_path, attestation)
        try:
            load_dataset(corpus_path, cases_path, review_path)
        except BenchmarkError as error:
            require(str(error) == reason, "Wrong split-coverage rejection")
        else:
            raise AssertionError("Unrepresentative split accepted")

    target_id = valid_cases[0]["expected_memory_ids"][0]
    target_agent = dataset.records[target_id].agent_name
    four_ids = [
        record.id
        for record in dataset.records.values()
        if record.agent_name == target_agent
        and record.status == "active"
        and not record.superseded_by
    ][:4]
    require(len(four_ids) == 4, "Fictional topic-boundary fixture is incomplete")

    def make_positive(case, memory_id=target_id):
        target = dataset.records[memory_id]
        case.update(
            must_abstain=False,
            expected_memory_ids=[memory_id],
            acceptable_memory_ids=[],
            expected_facts=[],
            agent_name=target.agent_name,
            current_session_id="fictional-unmatched-session",
            from_date=None,
            to_date=None,
            kinds=[],
            expected_cwds={memory_id: target.cwd},
        )
        case["excluded_memory_ids"] = [
            excluded_id
            for excluded_id in case["excluded_memory_ids"]
            if excluded_id != memory_id
        ]
        case.pop("expected_cwd_label", None)

    for split in ("calibration", "holdout"):
        for missing in (
            {"exact_identifier"},
            {"paraphrase"},
            {"no_relevant_memory"},
            {"ambiguous_followup"},
            {"decision", "preference"},
            {"constraint_rejected_approach", "unresolved"},
        ):
            rows = deepcopy(valid_cases)
            for case in rows:
                if case["split"] == split:
                    case["categories"] = [
                        category
                        for category in case["categories"]
                        if category not in missing
                    ]
                    if not case["categories"]:
                        case["categories"] = ["fictional_other"]
            rejected_fixture(rows, "private_split_category_coverage_incomplete")
        rows = deepcopy(valid_cases)
        for index, case in enumerate(
            case for case in rows if case["split"] == split and not case["must_abstain"]
        ):
            make_positive(case, four_ids[index % len(four_ids)])
        rejected_fixture(rows, "private_split_topics_too_few")
        rows = deepcopy(valid_cases)
        changed = next(
            case for case in rows if case["split"] == split and case["must_abstain"]
        )
        make_positive(changed)
        extra = next(
            case
            for case in reversed(rows)
            if case["split"] != split and not case["must_abstain"]
        )
        extra.update(
            must_abstain=True,
            expected_memory_ids=[],
            acceptable_memory_ids=[],
            expected_facts=[],
            expected_cwds={},
        )
        rejected_fixture(rows, "private_split_abstention_cases_too_few")
    write_jsonl(cases_path, valid_cases)
    write_json(review_path, original_review)


def checks_input_evidence(folder, dataset, review):
    """Fictional calibration evidence only; no backend or holdout is executed."""
    cache = folder / "fictional-query-cache.jsonl"
    write_jsonl(
        cache,
        [
            {
                "schema_version": 1,
                "query_sha256": "0" * 64,
                "embedding": [0.5, 0.5],
                "fixture_only": True,
            }
        ],
    )
    cache_bytes, review_bytes = cache.read_bytes(), review.read_bytes()
    report = {
        **code_identity(),
        **dataset_evidence(dataset),
        "schema_version": 1,
        "synthetic": False,
        "fixture_only": True,
        "split": "calibration",
        "selected_cases": 16,
        "corpus_sha256": dataset.corpus_hash,
        "cases_sha256": dataset.cases_hash,
        "sqlite_source_sha256": "1" * 64,
        "chroma_source_fingerprint": "2" * 64,
        "collection_sha256": digest("fictional-collection"),
        "embedding_cache_sha256": file_hash(cache),
        "document_vector_cache_sha256": None,
        "sqlite_semantic_policy": {"max_distance": None},
        "review_manifest_sha256": dataset.review_manifest_hash,
        "k": 5,
        "warmups": 0,
        "repetitions": 1,
        "policy": POLICY,
        "chroma": {
            "embedding_source": "cache",
            "provider": "fictional",
            "model": "fictional",
            "threshold": 0.3,
        },
        "modes": {
            name: {"evaluated_cases": 16, "backend_errors": 0}
            for name in ("chroma", "sqlite")
        },
    }
    calibration = folder / "fictional-calibration.json"
    frozen_dir = folder / "fictional-freeze"
    _private_directory(frozen_dir)
    write_json(calibration, report)
    freeze_calibration(calibration, frozen_dir)
    frozen_path = frozen_dir / "freeze.json"
    frozen = read_json(frozen_path)
    require(
        frozen["protocol"]["embedding_cache_sha256"] == file_hash(cache)
        and frozen["protocol"]["review_manifest_sha256"] == dataset.review_manifest_hash
        and frozen["review_manifest_sha256"] == dataset.review_manifest_hash,
        "Freeze omitted input evidence",
    )
    args = SimpleNamespace(
        k=5,
        warmups=0,
        repetitions=1,
        modes="chroma,sqlite",
        split="holdout",
        allow_remote_llm=False,
        allow_remote_embeddings=False,
        embedding_source="cache",
        embedding_cache=cache,
        review_manifest=review,
        freeze_manifest=frozen_path,
        model_call_budget=1,
        adapter=None,
        answer_eval=False,
        sqlite_source=folder / "never-open.sqlite3",
    )
    captured_hash, rows, captured_freeze, vector_hash, vectors, policy = input_evidence(
        args, dataset, ["chroma", "sqlite"]
    )
    require(
        vector_hash is None and vectors is None and policy.max_distance is None,
        "Absent vector policy changed",
    )
    require(
        captured_hash == file_hash(cache)
        and len(rows) == 1
        and captured_freeze == frozen,
        "Unchanged cache evidence rejected",
    )

    def rejected_before_execution(current_dataset, expected):
        with (
            patch(
                "scripts.benchmark_memory_retrieval.SQLiteMemoryRepository"
            ) as backend,
            patch("scripts.benchmark_memory_retrieval.copy_chroma") as copy_backend,
            patch("scripts.benchmark_memory_retrieval._sdk_collection") as sdk,
            patch("scripts.benchmark_memory_retrieval.reserve_holdout") as reserve,
        ):
            try:
                run_benchmark(args, current_dataset, folder / "never-run")
            except BenchmarkError as error:
                require(str(error) == expected, "Wrong input-evidence rejection")
            else:
                raise AssertionError("Changed evidence was accepted")
            for operation in (backend, copy_backend, sdk, reserve):
                operation.assert_not_called()

    write_jsonl(
        cache,
        [
            {
                "schema_version": 1,
                "query_sha256": "0" * 64,
                "embedding": [0.75, 0.25],
                "fixture_only": True,
            }
        ],
    )
    rejected_before_execution(dataset, "frozen_input_evidence_changed")
    rejects(lambda: read_jsonl(cache, expected_sha256=captured_hash))
    replacement = folder / "fictional-replacement-cache.jsonl"
    replacement.write_bytes(cache_bytes + b"\n")
    replacement.chmod(0o600)
    replacement.replace(cache)
    rejected_before_execution(dataset, "frozen_input_evidence_changed")
    cache.write_bytes(cache_bytes)
    review.write_bytes(review_bytes + b"\n")
    rejected_before_execution(dataset, "review_manifest_changed_after_loading")
    rejects(lambda: read_json(review, expected_sha256=dataset.review_manifest_hash))
    reloaded = replace(dataset, review_manifest_hash=file_hash(review))
    rejected_before_execution(reloaded, "frozen_input_evidence_changed")
    review.write_bytes(review_bytes)
    args.embedding_source, args.embedding_cache, args.split = (
        "configured",
        None,
        "calibration",
    )
    require(
        input_evidence(args, dataset, ["chroma", "sqlite"])[:5]
        == (None, None, None, None, None),
        "Non-cache source retained cache evidence",
    )


def checks_metrics(dataset):
    case = dataset.cases[0]
    identifier = case["expected_memory_ids"][0]
    record = dataset.records[identifier]
    hit = {
        "memory_id": identifier,
        "citation": "fictional-citation",
        "score": 1,
        "cwd": record.cwd,
        "date": datetime.fromtimestamp(record.created_at, UTC).date().isoformat(),
        "reasons": [],
        "kind": "fact",
        "excerpt": "fictional evidence",
        "conflict": False,
    }
    perfect = measure(case, [hit], dataset.records, 5)
    require(
        perfect["recall_at_5"] == 1
        and perfect["precision_at_5"] == 0.2
        and perfect["mrr"] == 1
        and perfect["ndcg"] == 1,
        "Known metric arithmetic failed",
    )
    duplicate = measure(case, [hit, hit], dataset.records, 5)
    require(
        duplicate["duplicate_parent_hits"] == 1 and duplicate["ndcg"] <= 1,
        "Duplicate reward regression",
    )
    excluded = next(
        key
        for key in case["excluded_memory_ids"]
        if dataset.records[key].status == "deleted"
    )
    leaked = measure(case, [{**hit, "memory_id": excluded}], dataset.records, 5)
    require(
        leaked["excluded_leaks"] == 1 and leaked["deletion_leaks"] == 1,
        "Deletion leak metric failed",
    )
    unknown = measure(
        case, [{**hit, "memory_id": "fictional-unknown"}], dataset.records, 5
    )
    require(unknown["unknown_id_leaks"] == 1, "Unknown ID leak metric failed")
    abstain = next(row for row in dataset.cases if row["must_abstain"])
    require(
        measure(abstain, [], dataset.records, 5)["abstention_correct"] == 1,
        "Abstention metric failed",
    )
    require(
        aggregate([perfect, duplicate])["duplicate_parent_hits"] == 1,
        "Metric aggregation failed",
    )
    require(
        query_from_case(case, 5)
        == query_from_case({**case, "current_cwd": "fictional-other-cwd"}, 5),
        "CWD entered scope",
    )
    require(
        signature([hit]) == signature([{**hit, "cwd": "fictional-other-cwd"}]),
        "CWD entered ranking signature",
    )


class FixedAdapter(FakeAdapter):
    def __init__(self, response):
        self.response = response

    def complete(self, purpose, payload):
        return self.response


def checks_adapters(dataset):
    calls = Calls()
    require(
        expand_queries(
            FixedAdapter({"queries": ["safe fictional expansion"]}),
            "original",
            synthetic=True,
            calls=calls,
            budget=2,
        )
        == ["original", "safe fictional expansion"],
        "Structured expansion failed",
    )
    require(
        expand_queries(
            FixedAdapter({"queries": ["safe"], "scope": "override"}),
            "original",
            synthetic=True,
            calls=calls,
            budget=2,
        )
        == ["original"],
        "Expansion schema fallback failed",
    )
    require(
        expand_queries(FakeAdapter(), "original", synthetic=True, calls=calls, budget=2)
        == ["original"]
        and calls.model == 2,
        "Budget was bypassed",
    )
    hits = [
        {"memory_id": "fictional-a", "citation": "a", "excerpt": "a"},
        {"memory_id": "fictional-b", "citation": "b", "excerpt": "b"},
    ]
    require(
        rerank_hits(
            FixedAdapter({"ranked_ids": ["foreign"], "abstain": False}),
            "q",
            hits,
            synthetic=True,
            calls=Calls(),
            budget=2,
        )
        == hits,
        "Reranker introduced candidate",
    )
    require(
        rerank_hits(
            FixedAdapter(
                {"ranked_ids": ["fictional-a", "fictional-a"], "abstain": False}
            ),
            "q",
            hits,
            synthetic=True,
            calls=Calls(),
            budget=2,
        )
        == hits,
        "Reranker duplicated candidate",
    )
    require(
        rerank_hits(FakeAdapter(), "q", hits, synthetic=True, calls=Calls(), budget=2)
        == hits,
        "Deterministic fake reranker failed",
    )
    invalid_calls = Calls()
    require(
        rerank_hits(
            FixedAdapter({"ranked_ids": [], "abstain": False}),
            "q",
            hits,
            synthetic=True,
            calls=invalid_calls,
            budget=1,
        )
        == hits
        and invalid_calls.fallback == 1,
        "Contradictory rerank did not fall back",
    )
    empty_calls = Calls()
    require(
        rerank_hits(FakeAdapter(), "q", [], synthetic=True, calls=empty_calls, budget=1)
        == []
        and empty_calls.model == 0,
        "Empty candidates consumed model calls",
    )
    remote = FixedAdapter({"queries": []})
    remote.remote = True
    private_calls = Calls()
    rejects(
        lambda: invoke_model(
            remote,
            "expand",
            {"query": "fictional-private-class"},
            synthetic=False,
            calls=private_calls,
            budget=2,
        )
    )
    require(private_calls.model == 0, "Private evidence reached remote adapter")
    case = dataset.cases[0]
    result = evaluate_answer(
        FakeAdapter(),
        case,
        hits,
        synthetic=True,
        calls=Calls(),
        budget=2,
        correctness_hook=lambda c, a: 1.0,
    )
    require(
        result["semantic_correctness"] == 1 and result["unsupported_citations"] == 0,
        "Answer hook/citation checks failed",
    )
    envelope = {
        "choices": [{"message": {"content": json.dumps({"queries": []})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }

    class NoWireOpener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            require(
                payload["messages"][0]["content"] == PROMPT
                and payload["model"] == "fictional-model",
                "Prompt/model parity failed",
            )
            return io.BytesIO(json.dumps(envelope).encode())

    with (
        patch.dict(os.environ, {"SYNTHETIC_BENCHMARK_KEY": "fictional-only"}),
        patch("urllib.request.build_opener", return_value=NoWireOpener()),
    ):
        adapter = RemoteJSONAdapter(
            "https://example.invalid/chat/completions",
            "fictional-provider",
            "fictional-model",
            "SYNTHETIC_BENCHMARK_KEY",
            authorized=True,
        )
        observed = Calls()
        require(
            invoke_model(
                adapter,
                "expand",
                {"query": "fictional-only"},
                synthetic=True,
                calls=observed,
                budget=1,
            )
            == {"queries": []},
            "Mocked HTTP contract failed",
        )
        require(
            observed.input_tokens == 10
            and observed.output_tokens == 3
            and observed.usage_observed_calls == 1
            and observed.cost_usd is None,
            "Observed usage/null cost failed",
        )


def checks_chroma_semantics(dataset):
    ids = ["syn-mem-falcon-old"] + [
        i for i, r in dataset.records.items() if r.status == "active"
    ][:3]
    observed = {}

    class Collection:
        def query(self, **kwargs):
            observed.update(kwargs)
            return {
                "ids": [ids],
                "distances": [[0.1, 0.2, 0.3, 0.4]],
                "metadatas": [[dataset.records[i].metadata for i in ids]],
                "documents": [[dataset.records[i].document for i in ids]],
            }

    baseline = object.__new__(ChromaBaseline)
    baseline.collection = Collection()
    baseline.calls = Calls()
    baseline.embedding = lambda case: synthetic_embedding(case["query"])
    baseline.threshold = 1.0
    case = dataset.cases[0]
    hits = baseline.search(case, 5)
    require(
        len(hits) == 3 and observed["n_results"] == 10,
        "Chroma runtime result caps changed",
    )
    require(
        measure(case, hits, dataset.records, 5)["lifecycle_leaks"] >= 1,
        "Chroma baseline repaired supersession",
    )
    where = json.dumps(observed["where"])
    require(
        "$ne" in where and case["agent_name"] in where and "cwd" not in where,
        "Chroma scope changed",
    )
    baseline.threshold = 0.15
    require(len(baseline.search(case, 5)) == 1, "Chroma threshold semantics changed")
    require(
        baseline.calls.remote_embedding == 0, "Semantic check invoked remote embeddings"
    )


def build_chroma(spec_path):
    import chromadb
    from chromadb.config import Settings

    spec = json.loads(spec_path.read_text())
    client = chromadb.PersistentClient(
        path=spec["source"], settings=Settings(anonymized_telemetry=False)
    )
    collection = client.create_collection(
        "conversation", embedding_function=None, metadata={"hnsw:space": "cosine"}
    )
    for row in spec["rows"]:
        if row["status"] == "deleted":
            continue
        collection.add(
            ids=[row["id"]],
            documents=[row["document"]],
            metadatas=[row["metadata"]],
            embeddings=[synthetic_embedding(row["document"])],
        )
    client._system.stop()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--build-chroma":
        build_chroma(Path(sys.argv[2]))
        return
    local = ROOT / "verification/memory/local"
    _private_directory(local)
    folder = Path(tempfile.mkdtemp(prefix="phase3-verification-", dir=local))
    dataset = load_dataset(
        ASSETS / "synthetic-corpus.jsonl", ASSETS / "benchmark-cases.jsonl"
    )
    checks_loading(folder, dataset)
    checks_metrics(dataset)
    checks_adapters(dataset)
    checks_chroma_semantics(dataset)
    identity = code_identity()
    held_case = deepcopy(dataset.cases[0])
    held_case.update(
        split="holdout", query="fictional untouched holdout " + folder.name
    )
    held_data = replace(dataset, cases=(held_case,), cases_hash=digest([held_case]))
    frozen = {
        "schema_version": 1,
        "calibration_complete": True,
        "corpus_sha256": held_data.corpus_hash,
        "cases_sha256": held_data.cases_hash,
        "code_hash": identity["code_hash"],
        "policy": POLICY,
        "fictional_verification_nonce": folder.name,
    }
    token = reserve_holdout(frozen, held_data, identity)
    require(
        isinstance(token, str) and len(token) == 64,
        "holdout reservation identity missing",
    )
    rejects(lambda: reserve_holdout(frozen, held_data, identity))
    rejects(
        lambda: reserve_holdout(
            {**frozen, "captured_at": "changed"}, held_data, identity
        )
    )
    renamed = deepcopy(held_case)
    renamed.update(
        case_id="fictional-renamed-case",
        expected_facts=["fictional corrected label"],
        current_cwd="/synthetic/changed",
    )
    renamed_data = replace(held_data, cases=(renamed,), cases_hash=digest([renamed]))
    rejects(
        lambda: reserve_holdout(
            {**frozen, "cases_sha256": renamed_data.cases_hash}, renamed_data, identity
        )
    )
    extra_case = dict(
        held_case, case_id="fictional-new-question", query=held_case["query"] + " new"
    )
    recycled = replace(
        held_data,
        cases=(held_case, extra_case),
        cases_hash=digest([held_case, extra_case]),
    )
    rejects(
        lambda: reserve_holdout(
            {**frozen, "cases_sha256": recycled.cases_hash}, recycled, identity
        )
    )
    rejects(
        lambda: reserve_holdout({**frozen, "code_hash": "changed"}, held_data, identity)
    )
    repository = SQLiteMemoryRepository(folder / "candidate.sqlite3")
    rows = [
        json.loads(line)
        for line in (ASSETS / "synthetic-corpus.jsonl").read_text().splitlines()
    ]
    for row in rows:
        repository.upsert_record(record_from_fixture(row))
    repository.delete_record("syn-mem-deleted", "SyntheticArchitect")
    repository.check_integrity()
    source = folder / "chroma-source"
    spec = folder / "source.json"
    spec.write_text(json.dumps({"source": str(source), "rows": rows}), encoding="utf-8")
    spec.chmod(0o600)
    run_child(
        uv_command(Path(__file__), "--build-chroma", spec), folder / "fixture.log"
    )
    output = folder / "run"
    command = uv_command(
        "scripts/benchmark_memory_retrieval.py",
        "run",
        "--corpus",
        ASSETS / "synthetic-corpus.jsonl",
        "--cases",
        ASSETS / "benchmark-cases.jsonl",
        "--sqlite-source",
        repository.path,
        "--chroma-source",
        source,
        "--embedding-source",
        "synthetic-hash",
        "--output",
        output,
        "--modes",
        "chroma,sqlite,sqlite-expand,sqlite-rerank",
        "--split",
        "synthetic",
        "--k",
        5,
        "--warmups",
        1,
        "--repetitions",
        3,
        "--adapter",
        "fake",
        "--answer-eval",
        "--model-call-budget",
        2000,
    )
    run_child(command, folder / "benchmark.log")
    report = json.loads((output / "results.json").read_text())
    require(next(iter(report["modes"])) == "chroma", "Chroma was not run before SQLite")
    require(
        report["calls"]["remote_model"] == 0
        and report["calls"]["remote_embedding"] == 0,
        "A real remote invocation occurred",
    )
    for mode in ("sqlite", "sqlite-expand", "sqlite-rerank"):
        summary = report["modes"][mode]
        require(
            summary["evaluated_cases"] == 29 and summary["backend_errors"] == 0,
            "Incomplete synthetic mode",
        )
        require(
            summary["scope_leaks"] == 0
            and summary["deletion_leaks"] == 0
            and summary["duplicate_parent_hits"] == 0,
            "SQLite safety regression",
        )
        require(
            summary["cwd_invariance_failures"] == 0
            and 0 <= summary["assisted_invocation_rate"] <= 1,
            "CWD/invocation rate regression",
        )
    require(
        report["modes"]["chroma"]["evaluated_cases"] == 29
        and report["chroma"]["k_cap"] == 3,
        "Chroma simulation failed",
    )
    require(
        report["chroma"]["fidelity"].startswith("synthetic-only")
        and not report["acceptance"]["passed"],
        "Fake baseline passed a real gate",
    )
    for name in ("results.json", "summary.md", "failures.jsonl"):
        require(
            (output / name).stat().st_mode & 0o777 == 0o600, "Report permissions failed"
        )
    require(
        subprocess.run(
            [
                "git",
                "check-ignore",
                "--quiet",
                "--no-index",
                str(output.relative_to(ROOT)),
            ],
            check=False,
            cwd=ROOT,
        ).returncode
        == 0,
        "Reports are not ignored",
    )
    print(
        "PASS Phase 3 strict loading/metrics/fallback/bounds/allowlist/answer-hook/fake-HTTP/freeze/single-use/isolated-backend/private-report checks"
    )
    print(
        "Synthetic corpus: 25 records, 29 cases, four modes; no actual remote calls. No private review or real holdout was run."
    )
    print(
        encode(
            {
                mode: {
                    key: row.get(key)
                    for key in (
                        "recall_at_5",
                        "precision_at_5",
                        "mrr",
                        "ndcg",
                        "no_relevant_abstention",
                        "scope_leaks",
                        "lifecycle_leaks",
                        "excluded_leaks",
                        "deletion_leaks",
                        "duplicate_parent_hits",
                        "p50_ms",
                        "p95_ms",
                    )
                }
                for mode, row in report["modes"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
