"""Disposable fictional chain validation; no historical/private artifact reads."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import benchmark as memory
from scripts import benchmark_memory_retrieval as runner
from verification.memory.verify_calibration_loader import (
    checks_boundaries,
    rejects,
    require,
    write_json,
)
from verification.memory.verify_calibration_loader import (
    fixture as human_fixture,
)


def fixture(folder, mutate_cases=None):
    corpus, exported, root, parent, combined, cases, human = human_fixture(folder)
    if mutate_cases is not None:
        mutate_cases(cases)
    raw = b"".join(
        memory.encode(case).encode() + (b"\r\n" if i % 2 else b"\n")
        for i, case in enumerate(cases)
    ).rstrip(b"\r\n")
    exported.write_bytes(raw)
    fictional_other = b"".join(
        memory.encode(
            dict(case, case_id=f"fictional-other-{i}", split="holdout")
        ).encode()
        + b"\n"
        for i, case in enumerate(cases)
    )
    combined.write_bytes(fictional_other + raw)
    parent_data = json.loads(parent.read_text())
    parent_data["cases_sha256"] = memory.file_hash(combined)
    write_json(parent, parent_data)
    parent_hash = memory.file_hash(parent)
    counts = {
        key: 0
        for key in (
            "case_payload_objects_from_original_combined_constructed",
            "original_combined_reads_outside_reviewed_operator",
            "retrieval_benchmark_calibration_runs",
            "sqlite_chroma_connections",
            "cache_vector_files_opened",
            "embedding_model_network_calls",
            "cutoff_registration_or_selection",
            "real_freeze_or_holdout_execution",
            "consumed_ledger_access",
            "source_edits",
            "completed_human_attestations_created",
        )
    }
    counts.update(
        {
            key: 1
            for key in (
                "operator_invocations",
                "strict_calibration_validation_runs",
                "parent_reviews_revalidated",
                "corpus_integrity_hash_reads",
                "corpus_strict_validation_snapshot_reads",
                "calibration_export_strict_snapshot_reads",
            )
        }
    )
    counts.update(
        operator_completed=True,
        verified_heldout_containing_full_read_passes=2,
        non_discriminator_heldout_values_used=False,
    )
    extraction = folder / "fictional-mechanical-evidence.json"
    execution = folder / "fictional-execution.json"
    evidence = {
        "schema_version": 1,
        "artifact_type": "mechanical-calibration-extraction-evidence",
        "fixture_only": True,
        "source_sha256": memory.file_hash(combined),
        "export_sha256": memory.file_hash(exported),
        "expected_digest_origin": "caller-supplied; authenticity not established by this operator",
        "rows": {"calibration": len(cases), "holdout": len(cases)},
        "source_bytes": combined.stat().st_size,
        "source_full_read_passes": 2,
        "structural_scanning_includes_heldout_bytes": True,
        "heldout_payload_objects_constructed": False,
        "non_discriminator_payload_values_interpreted": False,
        "reviewed_locally": False,
        "review_method": None,
        "valid_split_review": False,
        "calibration_validation_and_genuine_human_review_required": True,
        "calibration_benchmark_freeze_or_holdout_execution": False,
        "method_sha256": memory.REVIEWED_DERIVATION_SOURCES[
            "scripts/extract_memory_calibration.py"
        ],
        "shared_validation_sha256": dict(memory.REVIEWED_DERIVATION_SHARED_SOURCES),
        "limits": {
            "source_bytes": 64 * 1024 * 1024,
            "row_bytes": 1024 * 1024,
            "rows": 100_000,
            "json_depth": 64,
        },
    }
    write_json(extraction, evidence)
    executed = {
        "schema_version": 1,
        "receipt_type": "phase3-milestone-b-mechanical-preparation",
        "fixture_only": True,
        "status": "MECHANICAL_EXPORT_AND_STRICT_PREPARATION_VALIDATION_COMPLETE_HUMAN_REVIEW_PENDING",
        "not_a_calibration_result": True,
        "metrics": None,
        "genuine_split_human_review_completed": False,
        "loader_pass_claimed": False,
        "counts": counts,
        "aggregate_split_counts": evidence["rows"],
        "identities": {
            "parent_review_sha256": parent_hash,
            "corpus_sha256": memory.file_hash(corpus),
            "combined_source_sha256_from_parent_and_verified_by_operator": memory.file_hash(
                combined
            ),
            "calibration_export_sha256": memory.file_hash(exported),
            "method_sha256": evidence["method_sha256"],
        },
        "code_identity": {
            "commit": memory.REVIEWED_DERIVATION_COMMIT,
            "computed_code_hash": memory.REVIEWED_DERIVATION_CODE_HASH,
            "checkout_clean": True,
            "source_file_hashes": dict(memory.REVIEWED_DERIVATION_SOURCES),
        },
        "isolation": {
            "exact_source_inputs_readonly": {
                "parent_review": True,
                "corpus": True,
                "combined_source": True,
            },
            "code_readonly": True,
            "host_root_readonly": True,
            "mount_propagation_private": True,
            "capabilities_zero": True,
            "network_user_namespaces_distinct": True,
        },
        "preservation": {
            "heldout_bytes_accessed": True,
            "heldout_containing_full_read_passes": 2,
            "no_heldout_payload_objects_or_per_row_evidence": True,
        },
        "strict_preparation_validation": {
            "status": "STRICT_PREPARATION_CHECKS_PASSED",
            "not_a_reviewed_dataset_or_calibration_result": True,
            "corpus_records": len(corpus.read_text().splitlines()),
            "calibration_cases": len(cases),
            "positive_memory_ids": len(
                {
                    value
                    for case in cases
                    for value in case["expected_memory_ids"]
                    + case["acceptable_memory_ids"]
                }
            ),
            "abstention_cases": sum(case["must_abstain"] for case in cases),
            "required_category_combinations": True,
            "unique_normalized_questions": True,
            "scope_date_lifecycle_references_cwd_labels": True,
            "all_rows_calibration": True,
            "final_loader_not_run_without_genuine_split_review": True,
        },
    }
    write_json(execution, executed)
    accepted = {
        "schema_version": 1,
        "artifact_type": memory.DERIVED_CALIBRATION_CONTRACT,
        "dataset_scope": "calibration-only",
        "split": "calibration",
        "fixture_only": True,
        "draft": False,
        "reviewed_locally": False,
        "review_method": None,
        "new_split_human_review": False,
        "original_review_trust_anchor_accepted": True,
        "derivation_policy": memory.DERIVATION_POLICY,
        "case_count": len(cases),
        "corpus_sha256": memory.file_hash(corpus),
        "cases_sha256": memory.file_hash(exported),
        "parent_review_manifest_sha256": parent_hash,
        "parent_cases_sha256": memory.file_hash(combined),
        "extraction_evidence_sha256": memory.file_hash(extraction),
        "execution_evidence_sha256": memory.file_hash(execution),
    }
    write_json(root, accepted)
    return SimpleNamespace(
        corpus=corpus,
        exported=exported,
        root=root,
        parent=parent,
        combined=combined,
        extraction=extraction,
        execution=execution,
        cases=cases,
        raw=raw,
        accepted=accepted,
        evidence=evidence,
        executed=executed,
        parent_hash=parent_hash,
    )


def load(data, **kwargs):
    inputs = {
        "extraction_evidence": data.extraction,
        "execution_evidence": data.execution,
        "trusted_derivation_sha256": memory.file_hash(data.root),
    }
    inputs.update(kwargs)
    return memory.load_calibration_dataset(
        data.corpus, data.exported, data.root, data.parent, **inputs
    )


def arguments(data):
    return SimpleNamespace(
        dataset_scope="calibration-only",
        split="calibration",
        freeze_manifest=None,
        parent_review_manifest=data.parent,
        review_manifest=data.root,
        derivation_extraction_evidence=data.extraction,
        derivation_execution_evidence=data.execution,
        trusted_derivation_sha256=memory.file_hash(data.root),
        k=5,
        warmups=1,
        repetitions=3,
        modes="sqlite",
        allow_remote_llm=False,
        allow_remote_embeddings=False,
        embedding_source="disabled",
        embedding_cache=None,
        document_vector_cache=None,
        sqlite_semantic_max_distance=None,
    )


def checks_chain(folder):
    data = fixture(folder)
    originals = {
        path: path.read_bytes()
        for path in (
            data.corpus,
            data.exported,
            data.root,
            data.parent,
            data.extraction,
            data.execution,
        )
    }
    original_open, original_read = Path.open, Path.read_bytes

    def guard_open(path, *args, **kwargs):
        require(
            path != data.combined,
            "Derived loader opened fictional original combined input",
        )
        return original_open(path, *args, **kwargs)

    def guard_read(path):
        require(
            path != data.combined,
            "Derived loader hashed fictional original combined input",
        )
        return original_read(path)

    with (
        patch.object(memory, "ACCEPTED_PARENT_REVIEW_SHA256", data.parent_hash),
        patch.object(Path, "open", guard_open),
        patch.object(Path, "read_bytes", guard_read),
    ):
        dataset = load(data)
        require(
            dataset.cases == tuple(data.cases)
            and data.exported.read_bytes() == data.raw,
            "Derived case labels/byte representation changed",
        )
        require(
            dataset.calibration_provenance_contract
            == memory.DERIVED_CALIBRATION_CONTRACT
            and dataset.dataset_scope == "calibration-only"
            and dataset.derivation.new_split_human_review is False,
            "Derived contract misrepresented scope or human event",
        )
        report = runner.dataset_evidence(dataset)
        lineage = report["calibration_derivation"]
        require(
            lineage["trusted_acceptance_hash"] == memory.file_hash(data.root)
            and lineage["extraction_evidence_hash"] == memory.file_hash(data.extraction)
            and lineage["execution_evidence_hash"] == memory.file_hash(data.execution)
            and lineage["historical_commit"] == memory.REVIEWED_DERIVATION_COMMIT
            and lineage["shared_source_hashes"]
            == memory.REVIEWED_DERIVATION_SHARED_SOURCES
            and lineage["new_split_human_review"] is False,
            "Report lost historical/current distinction or extra evidence bindings",
        )
        require(
            json.loads(memory.encode(report)) == report, "Provenance is not JSON-stable"
        )
        rejects(
            lambda: load(data, trusted_derivation_sha256=None),
            "trusted_derivation_acceptance_required",
        )
        rejects(
            lambda: load(data, trusted_derivation_sha256="0" * 64),
            "trusted_derivation_acceptance_required",
        )
        rejects(
            lambda: load(data, extraction_evidence=None),
            "mechanical_derivation_evidence_required",
        )
        rejects(
            lambda: load(data, execution_evidence=None),
            "mechanical_derivation_evidence_required",
        )
        for key in data.accepted:
            if key == "fixture_only":
                continue
            wrong = dict(data.accepted)
            wrong.pop(key)
            write_json(data.root, wrong)
            rejects(lambda: load(data))
        for key, value in (
            ("reviewed_locally", True),
            ("review_method", "local-human"),
            ("draft", True),
            ("new_split_human_review", True),
            ("original_review_trust_anchor_accepted", False),
            ("parent_review_manifest_sha256", "0" * 64),
            ("derivation_policy", "unreviewed-method"),
            ("membership_reviewed_locally", True),
        ):
            write_json(data.root, {**data.accepted, key: value})
            rejects(lambda: load(data))
        for path, content, pin in (
            (data.extraction, data.evidence, "extraction_evidence_sha256"),
            (data.execution, data.executed, "execution_evidence_sha256"),
        ):
            for key in content:
                if key == "fixture_only":
                    continue
                wrong = deepcopy(content)
                wrong.pop(key)
                write_json(path, wrong)
                write_json(data.root, {**data.accepted, pin: memory.file_hash(path)})
                rejects(lambda: load(data))
            path.write_bytes(originals[path])
        for path in originals:
            path.write_bytes(originals[path])
        for broken in (b"[]", b"null", b'{"x": 1, "x": 1}', b'{"x": NaN}', b"{"):
            data.extraction.write_bytes(broken)
            write_json(
                data.root,
                {
                    **data.accepted,
                    "extraction_evidence_sha256": memory.file_hash(data.extraction),
                },
            )
            rejects(lambda: load(data))
        data.extraction.write_bytes(originals[data.extraction])
        data.root.write_bytes(originals[data.root])
        for name in ("extraction_evidence", "execution_evidence"):
            try:
                load(data, **{name: folder / "fictional-nonexistent-input"})
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("Missing fictional evidence path accepted")
        mutations = [
            (data.extraction, data.evidence, "source_sha256", "0" * 64),
            (data.extraction, data.evidence, "export_sha256", "0" * 64),
            (data.extraction, data.evidence, "method_sha256", "0" * 64),
            (
                data.extraction,
                data.evidence,
                "shared_validation_sha256",
                {
                    **memory.REVIEWED_DERIVATION_SHARED_SOURCES,
                    "AgentCrew/modules/memory/benchmark.py": memory.file_hash(
                        Path(memory.__file__)
                    ),
                },
            ),
            (data.extraction, data.evidence, "source_full_read_passes", 1),
            (
                data.extraction,
                data.evidence,
                "rows",
                {"calibration": 15, "holdout": 16},
            ),
            (data.extraction, data.evidence, "rows", {"calibration": 16, "holdout": 0}),
            (data.extraction, data.evidence, "reviewed_locally", True),
            (
                data.execution,
                data.executed,
                "code_identity",
                {**data.executed["code_identity"], "commit": "bad"},
            ),
            (
                data.execution,
                data.executed,
                "code_identity",
                {**data.executed["code_identity"], "source_file_hashes": {}},
            ),
            (data.execution, data.executed, "identities", {}),
            (
                data.execution,
                data.executed,
                "failure",
                {"reason": "fictional-contradiction"},
            ),
            (
                data.execution,
                data.executed,
                "aggregate_split_counts",
                {"calibration": 16, "holdout": 1},
            ),
            (
                data.execution,
                data.executed,
                "counts",
                {**data.executed["counts"], "embedding_model_network_calls": 1},
            ),
            (
                data.execution,
                data.executed,
                "counts",
                {**data.executed["counts"], "operator_invocations": True},
            ),
            (
                data.execution,
                data.executed,
                "counts",
                {**data.executed["counts"], "operator_completed": False},
            ),
            (
                data.execution,
                data.executed,
                "isolation",
                {**data.executed["isolation"], "code_readonly": False},
            ),
            (
                data.execution,
                data.executed,
                "strict_preparation_validation",
                {
                    **data.executed["strict_preparation_validation"],
                    "calibration_cases": 15,
                },
            ),
        ]
        for path, content, key, value in mutations:
            write_json(path, {**content, key: value})
            pin = (
                "extraction_evidence_sha256"
                if path == data.extraction
                else "execution_evidence_sha256"
            )
            write_json(data.root, {**data.accepted, pin: memory.file_hash(path)})
            rejects(lambda: load(data))
            path.write_bytes(originals[path])
        for path in originals:
            path.write_bytes(originals[path])
        for path in (
            data.root,
            data.parent,
            data.extraction,
            data.execution,
            data.corpus,
            data.exported,
        ):
            path.write_bytes(originals[path] + b"\n")
            if path == data.root:
                rejects(
                    lambda: load(
                        data,
                        trusted_derivation_sha256=dataset.derivation.trusted_acceptance_hash,
                    ),
                    "trusted_derivation_acceptance_required",
                )
            else:
                rejects(lambda: load(data))
            path.write_bytes(originals[path])
        original_reader = memory.read_json

        def changed(path, **kwargs):
            if path == data.extraction:
                path.write_bytes(originals[path] + b"\n")
            return original_reader(path, **kwargs)

        with patch.object(memory, "read_json", changed):
            rejects(lambda: load(data), "json_evidence_changed")
        data.extraction.write_bytes(originals[data.extraction])
        for changes in (
            {"reviewed_locally": False},
            {"review_method": "script"},
            {"dataset_scope": "calibration-only"},
        ):
            parent = json.loads(originals[data.parent])
            parent.update(changes)
            write_json(data.parent, parent)
            altered_parent = memory.file_hash(data.parent)
            write_json(
                data.root,
                {**data.accepted, "parent_review_manifest_sha256": altered_parent},
            )
            with patch.object(memory, "ACCEPTED_PARENT_REVIEW_SHA256", altered_parent):
                rejects(lambda: load(data))
            data.parent.write_bytes(originals[data.parent])
        data.root.write_bytes(originals[data.root])
    return data, dataset


def checks_strict_cases(folder):
    variants = [
        lambda cases: cases[0].update(split="holdout"),
        lambda cases: cases[0].update(synthetic=True),
        lambda cases: cases[0].update(reviewed_locally=False),
        lambda cases: cases[0].update(draft=True),
        lambda cases: cases[0].update(query=" "),
        lambda cases: cases[0].update(query_sanitized="true"),
        lambda cases: cases[0].update(expected_memory_ids=["fictional-unknown"]),
        lambda cases: cases[0].update(same_ranking_as_case="nonexistent"),
        lambda cases: cases[1].update(case_id=cases[0]["case_id"]),
        lambda cases: cases.__setitem__(
            1, dict(cases[0], case_id="fictional-duplicate-question")
        ),
        lambda cases: cases.__delitem__(slice(14, None)),
        lambda cases: cases[0].update(from_date="not-a-date"),
        lambda cases: [case.update(categories=["paraphrase"]) for case in cases],
    ]
    for index, mutate in enumerate(variants):
        target = folder / f"case-{index}"
        target.mkdir()
        data = fixture(target, mutate)
        with patch.object(memory, "ACCEPTED_PARENT_REVIEW_SHA256", data.parent_hash):
            rejects(lambda: load(data))


def checks_prebackend_and_reports(folder, data, dataset):
    args = arguments(data)
    with (
        patch.object(runner, "input_path", lambda path, *unused: path),
        patch.object(memory, "ACCEPTED_PARENT_REVIEW_SHA256", data.parent_hash),
    ):
        require(
            runner.load_run_dataset(args, data.corpus, data.exported, data.root)
            == dataset,
            "CLI route lost derived contract",
        )
        runner.input_evidence(args, dataset, ["sqlite"])
        argv = [
            "benchmark-memory",
            "run",
            "--corpus",
            str(data.corpus),
            "--cases",
            str(data.exported),
            "--sqlite-source",
            str(folder / "fictional-not-opened.sqlite3"),
            "--output",
            str(folder / "never-cli-output"),
            "--modes",
            "sqlite",
            "--split",
            "calibration",
            "--dataset-scope",
            "calibration-only",
            "--review-manifest",
            str(data.root),
            "--parent-review-manifest",
            str(data.parent),
            "--derivation-extraction-evidence",
            str(data.extraction),
            "--derivation-execution-evidence",
            str(data.execution),
            "--trusted-derivation-sha256",
            memory.file_hash(data.root),
            "--k",
            "5",
            "--warmups",
            "1",
            "--repetitions",
            "3",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(
                runner, "new_output", return_value=folder / "never-cli-output"
            ) as output,
            patch.object(
                runner,
                "run_benchmark",
                return_value={
                    "acceptance": {"status": "blocked"},
                    "selected_cases": 16,
                    "modes": {},
                    "calls": {},
                },
            ) as measured,
            patch("builtins.print"),
        ):
            require(runner.main() == 0, "Derived CLI contract was not routed")
            require(
                measured.call_args.args[1] == dataset,
                "Derived CLI loaded a different dataset",
            )
            require(
                {data.extraction, data.execution} <= set(output.call_args.args[1]),
                "Output overlap validation omitted evidence inputs",
            )
        checks_boundaries(
            folder, dataset, args, data.corpus, data.exported, data.root, data.parent
        )
        with (
            patch.object(runner, "SQLiteMemoryRepository") as backend,
            patch.object(runner, "copy_chroma") as chroma,
            patch.object(runner, "_sdk_collection") as sdk,
            patch.object(runner, "reserve_holdout") as reserve,
        ):
            for path in (data.extraction, data.execution):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                rejects(
                    lambda: runner.run_benchmark(args, dataset, folder / "never-run"),
                    "json_evidence_changed",
                )
                path.write_bytes(original)
                for operation in (backend, chroma, sdk, reserve):
                    operation.assert_not_called()
            args.trusted_derivation_sha256 = "0" * 64
            rejects(
                lambda: runner.run_benchmark(args, dataset, folder / "never-run"),
                "mechanical_derivation_inputs_changed",
            )
            args.trusted_derivation_sha256 = memory.file_hash(data.root)
        report = runner.dataset_evidence(dataset)
        report.update(
            dataset_scope="full-cohort", dataset_provenance_kind="full-cohort"
        )
        require(
            not runner._full_cohort_report(report),
            "Scope markers alone converted derived evidence to full cohort",
        )
        full = {
            key: None
            for key in (
                "calibration_export_sha256",
                "parent_review_manifest_sha256",
                "parent_cases_sha256",
                "calibration_provenance_contract",
                "calibration_derivation",
            )
        }
        full.update(dataset_scope="full-cohort", dataset_provenance_kind="full-cohort")
        require(
            runner._full_cohort_report(full), "Explicit new full-cohort format rejected"
        )
        for key in full:
            missing = dict(full)
            missing.pop(key)
            require(
                not runner._full_cohort_report(missing),
                "Missing provenance marker silently defaulted to full cohort",
            )
        tampered = replace(
            dataset,
            dataset_scope="full-cohort",
            parent_review_manifest_hash=None,
            parent_cases_hash=None,
        )
        with patch.object(runner, "private_path") as private:
            rejects(
                lambda: runner.reserve_holdout({}, tampered, {}),
                "calibration_only_holdout_forbidden",
            )
            private.assert_not_called()
        args.dataset_scope = "full-cohort"
        rejects(
            lambda: runner.load_run_dataset(
                args, data.corpus, data.exported, data.root
            ),
            "invalid_dataset_scope_inputs",
        )
        args.dataset_scope = "calibration-only"


def main():
    require(
        memory.digest(memory.REVIEWED_DERIVATION_SOURCES)
        == memory.REVIEWED_DERIVATION_CODE_HASH,
        "Historical bundle hash does not reproduce its approved identity",
    )
    for name, expected in {
        **memory.REVIEWED_DERIVATION_SOURCES,
        **memory.REVIEWED_DERIVATION_SHARED_SOURCES,
    }.items():
        actual = hashlib.sha256(
            subprocess.check_output(
                ["git", "show", memory.REVIEWED_DERIVATION_COMMIT + ":" + name],
                cwd=ROOT,
            )
        ).hexdigest()
        require(
            actual == expected,
            "Historical public source pin does not match approved commit",
        )
    require(
        memory.file_hash(Path(memory.__file__))
        != memory.REVIEWED_DERIVATION_SHARED_SOURCES[
            "AgentCrew/modules/memory/benchmark.py"
        ],
        "Fictional historical/current identity distinction not exercised",
    )
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        data, dataset = checks_chain(folder)
        cases = folder / "invalid-cases"
        cases.mkdir()
        checks_strict_cases(cases)
        checks_prebackend_and_reports(folder, data, dataset)
    require(
        "chromadb" not in sys.modules and "voyageai" not in sys.modules,
        "Provider SDK imported",
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "fictional_only": True,
                "private_inputs_accessed": 0,
                "new_human_event_claimed": False,
                "trusted_pins_are_fixture_authority_inputs_not_authentication": True,
                "benchmark_runs": 0,
                "extraction_repeats": 0,
                "real_freezes_or_holdout_reservations": 0,
                "embeddings_models_network": 0,
                "private_provenance_loader_pass_claimed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
