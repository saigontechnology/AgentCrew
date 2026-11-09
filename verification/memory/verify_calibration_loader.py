"""Disposable fictional loader/provenance checks; never run a benchmark or ledger."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import benchmark
from AgentCrew.modules.memory.benchmark import (
    BenchmarkError, encode, file_hash, load_calibration_dataset, load_dataset,
)
from scripts import benchmark_memory_retrieval as runner
from verification.memory.verify_phase3 import checks_loading

ASSETS = ROOT / "verification/memory"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(callback, reason=None):
    try:
        callback()
    except BenchmarkError as error:
        require(reason is None or str(error) == reason, "Wrong fictional rejection")
        return
    raise AssertionError("Invalid fictional input accepted")


def write_json(path, value):
    path.write_text(encode(value) + "\n", encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(encode(row) + "\n" for row in rows), encoding="utf-8")


def fixture(folder):
    rows = [json.loads(line) for line in (ASSETS / "synthetic-corpus.jsonl").read_text().splitlines()]
    for row in rows:
        row["synthetic"] = False
    corpus = folder / "fictional-corpus.jsonl"
    write_rows(corpus, rows)
    eligible = [row for row in rows if row["status"] == "active" and not row.get("superseded_by")][:6]
    require(len(eligible) == 6, "Fictional positive fixture incomplete")
    categories = ["exact_identifier", "paraphrase", "decision", "preference",
                  "constraint_rejected_approach", "unresolved", "ambiguous_followup", "no_relevant_memory"]
    cases = []
    for index in range(16):
        target = eligible[index % len(eligible)]
        abstention = index >= 14
        cases.append({
            "schema_version": 1, "synthetic": False, "case_id": f"fictional-cal-{index}",
            "categories": categories, "query": f"Fictional calibration question {index}",
            "agent_name": target["metadata"]["agent"], "current_session_id": "fictional-unused-session",
            "current_cwd": None, "from_date": None, "to_date": None, "kinds": [],
            "expected_memory_ids": [] if abstention else [target["id"]],
            "acceptable_memory_ids": [], "excluded_memory_ids": [], "expected_facts": [],
            "expected_cwds": {} if abstention else {target["id"]: target["cwd"]},
            "must_abstain": abstention, "split": "calibration", "reviewed_locally": True,
            "query_sanitized": True, "draft": False,
        })
    exported = folder / "fictional-calibration.jsonl"
    combined = folder / "fictional-combined-do-not-open.jsonl"
    write_rows(exported, cases)
    write_rows(combined, cases + [dict(case, split="holdout", case_id=f"fictional-held-{index}",
                                     query=f"Fictional holdout question {index}") for index, case in enumerate(cases)])
    parent = folder / "fictional-parent-review.json"
    write_json(parent, {"schema_version": 1, "reviewed_locally": True, "review_method": "local-human",
                        "fixture_only": True, "corpus_sha256": file_hash(corpus), "cases_sha256": file_hash(combined),
                        "cases_path": str(combined)})
    attestation = {
        "schema_version": 1, "artifact_type": "reviewed-calibration-export", "dataset_scope": "calibration-only",
        "split": "calibration", "reviewed_locally": True, "review_method": "local-human", "fixture_only": True,
        "membership_reviewed_locally": True, "calibration_rows_complete": True,
        "case_bytes_preserved": True, "labels_and_sanitization_preserved": True,
        "case_count": 16, "corpus_sha256": file_hash(corpus), "cases_sha256": file_hash(exported),
        "parent_review_manifest_sha256": file_hash(parent), "parent_cases_sha256": file_hash(combined),
    }
    review = folder / "fictional-split-review.json"
    write_json(review, attestation)
    return corpus, exported, review, parent, combined, cases, attestation


def checks_loader(folder):
    corpus, exported, review, parent, combined, cases, attestation = fixture(folder)
    original = {path: path.read_bytes() for path in (corpus, exported, review, parent)}
    original_read_bytes, original_open = Path.read_bytes, Path.open

    def guarded_read(path):
        require(path != combined, "Combined case bytes were opened by calibration route")
        return original_read_bytes(path)

    def guarded_open(path, *args, **kwargs):
        require(path != combined, "Combined case objects were opened by calibration route")
        return original_open(path, *args, **kwargs)

    args = SimpleNamespace(dataset_scope="calibration-only", split="calibration", freeze_manifest=None,
                           parent_review_manifest=parent, review_manifest=review, k=5, warmups=1, repetitions=3,
                           modes="chroma,sqlite", allow_remote_llm=False, allow_remote_embeddings=False,
                           embedding_source="disabled", embedding_cache=None, document_vector_cache=None,
                           sqlite_semantic_max_distance=None)
    with patch.object(Path, "read_bytes", guarded_read), patch.object(Path, "open", guarded_open), \
            patch.object(runner, "input_path", lambda path: path), \
            patch.object(runner, "load_dataset", side_effect=AssertionError("Wrong loader routed")):
        dataset = runner.load_run_dataset(args, corpus, exported, review)
    require(dataset.dataset_scope == "calibration-only" and len(dataset.cases) == 16
            and not dataset.synthetic and dataset.cases_hash == file_hash(exported)
            and dataset.cases_hash != dataset.parent_cases_hash
            and dataset.review_manifest_hash == file_hash(review)
            and dataset.parent_review_manifest_hash == file_hash(parent), "Limited input identity lost")
    require(tuple(cases) == dataset.cases, "Export labels or query sanitization were rewritten")
    require(runner.dataset_evidence(dataset) == {
        "dataset_scope": "calibration-only", "dataset_provenance_kind": "calibration-only",
        "calibration_provenance_contract": benchmark.HUMAN_CALIBRATION_CONTRACT, "calibration_derivation": None,
        "calibration_export_sha256": file_hash(exported),
        "parent_review_manifest_sha256": file_hash(parent), "parent_cases_sha256": attestation["parent_cases_sha256"],
    }, "Report provenance does not identify actual export and parent separately")
    with patch.object(runner, "input_path", lambda path: path):
        runner.input_evidence(args, dataset, ["chroma", "sqlite"])

    def load():
        return load_calibration_dataset(corpus, exported, review, parent)

    def restore():
        for path, content in original.items():
            path.write_bytes(content)

    for key in ("artifact_type", "dataset_scope", "split", "reviewed_locally", "review_method",
                "membership_reviewed_locally", "calibration_rows_complete", "case_bytes_preserved",
                "labels_and_sanitization_preserved"):
        changed = {key_: value for key_, value in attestation.items() if key_ != key}
        write_json(review, changed)
        rejects(load, "calibration_export_attestation_invalid")
        write_json(review, {**attestation, key: "fabricated"})
        rejects(load, "calibration_export_attestation_invalid")
    for key in ("corpus_sha256", "cases_sha256", "parent_review_manifest_sha256", "parent_cases_sha256"):
        write_json(review, {**attestation, key: "not-a-hash"})
        rejects(load, "calibration_export_hashes_invalid")
        write_json(review, {**attestation, key: "0" * 64})
        rejects(load)
    restore()
    for key in ("reviewed_locally", "review_method", "corpus_sha256", "cases_sha256"):
        changed = json.loads(original[parent])
        changed[key] = False if key == "reviewed_locally" else "fictional-wrong-value"
        write_json(parent, changed)
        write_json(review, {**attestation, "parent_review_manifest_sha256": file_hash(parent)})
        rejects(load, "calibration_parent_review_invalid")
    restore()
    for changes in ({"dataset_scope": "calibration-only"}, {"artifact_type": "reviewed-calibration-export"}):
        write_json(parent, {**json.loads(original[parent]), **changes})
        write_json(review, {**attestation, "parent_review_manifest_sha256": file_hash(parent)})
        rejects(load, "calibration_parent_review_invalid")
        restore()
    write_json(review, {**attestation, "parent_cases_sha256": attestation["cases_sha256"]})
    rejects(load, "calibration_parent_review_invalid")
    restore()
    write_json(review, {**attestation, "case_count": True})
    rejects(load, "calibration_export_count_mismatch")
    restore()

    def changed_cases(rows, reason=None):
        write_rows(exported, rows)
        write_json(review, {**attestation, "cases_sha256": file_hash(exported), "case_count": len(rows)})
        rejects(load, reason)
        restore()

    changed_cases([dict(case, split="holdout") if index == 0 else case for index, case in enumerate(cases)],
                  "calibration_export_must_contain_only_calibration")
    changed_cases(cases + [dict(cases[0], case_id="fictional-held-row", split="holdout")],
                  "calibration_export_must_contain_only_calibration")
    changed_cases(cases[:14], "calibration_export_too_small")
    for changes, reason in (
        ({"schema_version": 99}, "unsupported_schema_version"),
        ({"synthetic": True}, "mixed_or_missing_privacy_class"),
        ({"reviewed_locally": False}, "private_cases_unreviewed"),
        ({"draft": True}, "private_cases_unreviewed"),
        ({"query_sanitized": "true"}, "invalid_boolean_label"),
        ({"agent_name": "fictional-wrong-agent"}, "ineligible_expected_memory"),
        ({"current_session_id": dataset.records[cases[0]["expected_memory_ids"][0]].session_id}, "ineligible_expected_memory"),
        ({"to_date": "1900-01-01"}, "ineligible_expected_memory"),
        ({"from_date": False}, "invalid_case_date_format"),
        ({"expected_memory_ids": ["fictional-unknown-id"]}, "unknown_referenced_memory"),
        ({"expected_cwds": {cases[0]["expected_memory_ids"][0]: "/fictional/wrong"}}, "contradictory_cwd"),
        ({"same_ranking_as_case": "fictional-missing"}, "invalid_cwd_pair"),
        ({"same_ranking_as_case": cases[1]["case_id"]}, "cwd_pair_scope_changed"),
    ):
        rows = deepcopy(cases)
        rows[0].update(changes)
        changed_cases(rows, reason)
    rows = deepcopy(cases)
    rows[1] = dict(rows[0], case_id=rows[1]["case_id"], query="  " + rows[0]["query"].upper() + "  ")
    changed_cases(rows, "duplicate_private_question_inputs")
    rows = deepcopy(cases)
    rows[14] = dict(rows[0], case_id=rows[14]["case_id"], query=rows[14]["query"])
    changed_cases(rows, "private_split_abstention_cases_too_few")
    rows = deepcopy(cases)
    for index in range(14):
        rows[index] = dict(cases[index % 4], case_id=cases[index]["case_id"], query=cases[index]["query"])
    changed_cases(rows, "private_split_topics_too_few")
    for missing in ({"exact_identifier"}, {"paraphrase"}, {"no_relevant_memory"}, {"ambiguous_followup"},
                    {"decision", "preference"}, {"constraint_rejected_approach", "unresolved"}):
        rows = deepcopy(cases)
        for row in rows:
            row["categories"] = [category for category in row["categories"] if category not in missing]
        changed_cases(rows, "private_split_category_coverage_incomplete")
    for path in (corpus, exported, parent):
        path.write_bytes(original[path] + b" ")
        rejects(load)
        restore()
    original_reader = benchmark.read_json
    for target in (review, parent):
        def changed_json(path, **kwargs):
            if path == target:
                path.write_bytes(original[path] + b" ")
            return original_reader(path, **kwargs)
        with patch.object(benchmark, "read_json", changed_json):
            rejects(load, "json_evidence_changed")
        restore()
    write_json(review, [])
    rejects(load, "calibration_export_attestation_invalid")
    restore()
    return dataset, args, corpus, exported, review, parent


def checks_boundaries(folder, dataset, args, corpus, exported, review, parent):
    with patch.object(runner, "input_path", lambda path: path), \
            patch.object(runner, "SQLiteMemoryRepository") as backend, \
            patch.object(runner, "copy_chroma") as chroma, \
            patch.object(runner, "_sdk_collection") as sdk, \
            patch.object(runner, "reserve_holdout") as reserve:
        def blocked(reason):
            rejects(lambda: runner.run_benchmark(args, dataset, folder / "never-run"), reason)
            for operation in (backend, chroma, sdk, reserve):
                operation.assert_not_called()
        args.split = "holdout"
        blocked("calibration_only_run_required")
        args.split = "calibration"
        args.freeze_manifest = folder / "fictional-old-freeze-never-open"
        blocked("calibration_only_run_required")
        args.freeze_manifest = None
        args.dataset_scope = "full-cohort"
        blocked("dataset_scope_mismatch")
        args.dataset_scope = "calibration-only"
        for path, reason in ((parent, "calibration_parent_review_changed_after_loading"),
                             (review, "review_manifest_changed_after_loading")):
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            blocked(reason)
            path.write_bytes(original)
    with patch.object(runner, "load_calibration_dataset") as limited, patch.object(runner, "load_dataset") as full:
        args.split = "holdout"
        rejects(lambda: runner.load_run_dataset(args, corpus, exported, review), "calibration_only_run_required")
        limited.assert_not_called()
        full.assert_not_called()
        args.split = "calibration"
        args.parent_review_manifest = None
        rejects(lambda: runner.load_run_dataset(args, corpus, exported, review), "calibration_export_reviews_required")
        limited.assert_not_called()
        full.assert_not_called()
        args.parent_review_manifest = parent
    with patch.object(runner, "private_path") as private, patch.object(runner, "_private_directory") as directory:
        rejects(lambda: runner.reserve_holdout({}, dataset, {}), "calibration_only_holdout_forbidden")
        private.assert_not_called()
        directory.assert_not_called()
    report = {**runner.dataset_evidence(dataset), "synthetic": False, "split": "holdout", "k": 5,
              "selected_cases": 16, "chroma": {"embedding_source": "cache"},
              "modes": {name: {"evaluated_cases": 16, "backend_errors": 0, "excluded_leaks": 0,
                               "scope_leaks": 0, "lifecycle_leaks": 0, "deletion_leaks": 0, "unknown_id_leaks": 0,
                               "duplicate_parent_hits": 0, "cwd_invariance_failures": 0,
                               "recall_at_5": 1.0, "precision_at_5": 0.2,
                               "no_relevant_abstention": 1.0, "p95_ms": 1.0} for name in ("sqlite", "chroma")}}
    require(not runner.acceptance(report)["passed"] and not runner.acceptance(report)["private_real_gate"],
            "Limited fake report reached holdout acceptance")
    rejects(lambda: runner.frozen_protocol(report, {}), "calibration_only_freeze_unsupported")
    path = folder / "fictional-not-a-real-calibration-report.json"
    write_json(path, {**report, "split": "calibration"})
    with patch.object(runner, "_write_json") as writer, patch.object(runner, "code_identity") as identity:
        rejects(lambda: runner.freeze_calibration(path, folder / "never-freeze"), "calibration_only_freeze_unsupported")
        writer.assert_not_called()
        identity.assert_not_called()
    report["dataset_scope"] = "full-cohort"
    require(not runner.acceptance(report)["passed"], "Parent/export provenance was ignored after scope tamper")



def checks_cli_routing(folder, dataset, args, corpus, exported, review, parent):
    argv = ["benchmark_memory_retrieval", "run", "--corpus", str(corpus), "--cases", str(exported),
            "--sqlite-source", str(folder / "fictional-never-open.sqlite3"), "--output", str(folder / "never-run"),
            "--modes", "sqlite", "--split", "calibration", "--dataset-scope", "calibration-only",
            "--review-manifest", str(review), "--parent-review-manifest", str(parent),
            "--k", "5", "--warmups", "1", "--repetitions", "3"]
    with patch.object(sys, "argv", argv), patch.object(runner, "input_path", lambda path, *extra: path), \
            patch.object(runner, "new_output", return_value=folder / "never-run"), \
            patch.object(runner, "run_benchmark", return_value={"acceptance": {"status": "blocked"},
                         "selected_cases": 16, "modes": {}, "calls": {}}) as execute, \
            patch.object(runner, "load_dataset", side_effect=AssertionError("Full loader used by limited CLI")), \
            patch.object(runner, "SQLiteMemoryRepository") as backend, \
            patch.object(runner, "reserve_holdout") as reserve, patch("builtins.print"):
        require(runner.main() == 0, "Explicit CLI routing failed")
        actual = execute.call_args.args[1]
        require(actual == dataset and actual.dataset_scope == "calibration-only", "CLI lost validated limited dataset")
        backend.assert_not_called()
        reserve.assert_not_called()


def checks_full_cohort(folder):
    public = load_dataset(ASSETS / "synthetic-corpus.jsonl", ASSETS / "benchmark-cases.jsonl")
    with patch("verification.memory.verify_phase3.checks_input_evidence") as evidence:
        checks_loading(folder, public)
        require(evidence.call_count == 1, "Existing full-cohort regression checks did not finish")
    corpus, exported, review, parent, combined, cases, attestation = fixture(folder)
    full_review = folder / "fictional-full-review.json"
    write_json(full_review, {"schema_version": 1, "reviewed_locally": True, "review_method": "local-human",
                            "corpus_sha256": file_hash(corpus), "cases_sha256": file_hash(exported)})
    rejects(lambda: load_dataset(corpus, exported, full_review), "private_cohort_too_small")
    original_reader = benchmark.read_jsonl
    def changed_rows(path, **kwargs):
        if path == exported:
            path.write_bytes(path.read_bytes().replace(b"Fictional", b"Changed_", 1))
        return original_reader(path, **kwargs)
    with patch.object(benchmark, "read_jsonl", changed_rows):
        rejects(lambda: load_dataset(corpus, exported, full_review), "jsonl_evidence_changed")


def main():
    with TemporaryDirectory(prefix="agentcrew-fictional-calibration-loader-") as directory:
        folder = Path(directory)
        data = checks_loader(folder)
        checks_boundaries(folder, *data)
        checks_cli_routing(folder, *data)
        checks_full_cohort(folder)
    print(json.dumps({"status": "passed", "fictional_only": True, "private_inputs_accessed": 0,
                      "benchmark_executions": 0, "holdout_reservations": 0, "embedding_calls": 0,
                      "network_calls": 0, "freeze_created": False, "cutoff_grid_registered": False}))


if __name__ == "__main__":
    main()
