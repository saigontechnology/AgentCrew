"""Fictional-only standalone checks for the one-command migration operator."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from scripts import migrate_chroma_memory_to_sqlite as migration
from scripts import migrate_memory as operator
from verification.memory.verify_phase2 import uv_command as python_command

ASSETS = ROOT / "verification/memory"


def require(value, code):
    if not value:
        raise AssertionError(code)


def rejected(callback, code, stage):
    try:
        callback()
    except operator.OperatorFailure as error:
        require(
            (error.code, error.stage) == (code, stage), "unexpected_operator_failure"
        )
        return
    raise AssertionError("unsafe_operator_action_accepted")


def invoke(folder, arguments, expected=0):
    command = python_command() + ["scripts/migrate_memory.py", *map(str, arguments)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    log = folder / ("operator-cli-" + uuid.uuid4().hex + ".log")
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    log.chmod(0o600)
    require(
        result.returncode == expected,
        "operator_cli_status_inspect_private_fictional_log",
    )
    value = json.loads(result.stdout if expected == 0 else result.stderr)
    require(
        "SyntheticArchitect" not in result.stdout + result.stderr,
        "operator_printed_agent",
    )
    require(
        "syn-mem-" not in result.stdout + result.stderr, "operator_printed_memory_id"
    )
    if expected:
        require(
            "sqlite_source" not in value and "directory" not in value,
            "error_exposed_private_path",
        )
        require(set(value) == {"status", "stage", "reason"}, "unexpected_error_shape")
    return value


def interrupt_helper(specification):
    source = Path(specification["source"])
    label = specification["run_id"]
    original_batch = SQLiteMemoryRepository.apply_migration_batch
    original_sdk = migration._sdk_collection
    calls = 0

    def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return original_batch(*args, **kwargs)

    @contextmanager
    def copy_only_sdk(work, collection):
        require(work.is_relative_to(operator.RUNS / label), "sdk_outside_operator_run")
        require(work / "chroma-copy" != source, "live_sdk_open")
        with original_sdk(work, collection) as value:
            yield value

    try:
        with (
            patch.object(SQLiteMemoryRepository, "apply_migration_batch", interrupt),
            patch.object(migration, "_sdk_collection", copy_only_sdk),
        ):
            operator.operate(
                operator.OperatorOptions(
                    source=source,
                    writers_paused=True,
                    apply=True,
                    run_id=label,
                    batch_size=5,
                )
            )
    except operator.OperatorFailure as error:
        require(
            error.stage == "apply_verify_migration"
            and error.code == "KeyboardInterrupt",
            "unexpected_interruption_stage",
        )
        return 130
    raise AssertionError("interruption_not_exercised")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--interrupt-fixture":
        return interrupt_helper(
            json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        )
    folder = Path(
        tempfile.mkdtemp(
            prefix="migration-operator-verification-", dir=ASSETS / "local"
        )
    )
    folder.chmod(0o700)
    rows = [
        json.loads(line)
        for line in (ASSETS / "synthetic-corpus.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    with tempfile.TemporaryDirectory(prefix="fictional-external-chroma-") as external:
        source = Path(external) / "source"
        specification = folder / "fictional-source.json"
        migration._write_json(
            specification,
            {
                "source": str(source),
                "records": [
                    {
                        "id": row["id"],
                        "document": row["document"],
                        "metadata": row["metadata"],
                    }
                    for row in rows
                ],
            },
        )
        build = subprocess.run(
            python_command()
            + [
                "verification/memory/verify_phase2.py",
                "--fixture-spec",
                str(specification),
            ],
            cwd=ROOT,
            capture_output=True,
        )
        require(build.returncode == 0, "fictional_chroma_creation_failed")
        before = migration.fingerprint(source)
        label = "operator-check-" + uuid.uuid4().hex[:16]
        base = operator.OperatorOptions(source=source, run_id=label)
        rejected(
            lambda: operator.operate(base),
            "writers_paused_attestation_required",
            "writer_attestation",
        )
        rejected(
            lambda: operator.operate(
                replace(base, writers_paused=True, prepare_review=True)
            ),
            "prepare_review_requires_apply",
            "arguments",
        )
        rejected(
            lambda: operator.operate(
                replace(base, writers_paused=True, run_id="../unsafe")
            ),
            "invalid_run_id",
            "arguments",
        )
        require(not (operator.RUNS / label).exists(), "preflight_created_run")
        inspection = invoke(
            folder, ["--source", source, "--writers-paused", "--run-id", label]
        )
        run = operator.RUNS / label
        require(
            inspection["status"] == "inspected" and inspection["sqlite_source"] is None,
            "inspection_created_destination",
        )
        require(not (run / "candidate.sqlite3").exists(), "inspection_database_exists")
        require(
            inspection["source_records"] == len(rows)
            and not inspection["writers_confirmed_paused"],
            "invalid_inspection_evidence",
        )
        require(run.stat().st_mode & 0o777 == 0o700, "run_not_private")
        require(
            (run / "operator.json").stat().st_mode & 0o777 == 0o600,
            "operator_report_not_private",
        )
        require(
            subprocess.run(
                [
                    "git",
                    "check-ignore",
                    "--quiet",
                    "--no-index",
                    str(run.relative_to(ROOT)),
                ],
                cwd=ROOT,
            ).returncode
            == 0,
            "run_not_ignored",
        )
        require(
            not subprocess.check_output(
                ["git", "ls-files", "--", str(run.relative_to(ROOT))], cwd=ROOT
            ),
            "private_run_tracked",
        )
        rejected(
            lambda: operator.operate(replace(base, writers_paused=True)),
            "run_directory_reused",
            "path_preflight",
        )
        arguments_error = invoke(
            folder, ["--unknown-option", "/synthetic/private-path-canary"], expected=1
        )
        require(
            arguments_error
            == {
                "status": "blocked",
                "stage": "arguments",
                "reason": "invalid_arguments",
            },
            "argument_error_disclosed_value",
        )
        source_error = invoke(
            folder,
            ["--source", folder / "missing.sqlite3", "--writers-paused"],
            expected=1,
        )
        require(
            source_error["stage"] == "source_resolution"
            and source_error["reason"] == "source_is_not_a_chroma_directory",
            "source_error_collapsed",
        )
        alias = folder / "fictional-source-link"
        alias.symlink_to(source, target_is_directory=True)
        rejected(
            lambda: operator.operate(replace(base, writers_paused=True, source=alias)),
            "symlink_path_rejected",
            "source_resolution",
        )
        apply_label = label + "-apply"
        applied = invoke(
            folder,
            [
                "--source",
                source,
                "--writers-paused",
                "--apply",
                "--prepare-review",
                "--batch-size",
                "5",
                "--run-id",
                apply_label,
            ],
        )
        require(
            applied["status"] == "verified"
            and applied["records"]
            == applied["ledger_records"]
            == applied["verified_records"]
            == len(rows),
            "candidate_not_verified",
        )
        candidate = Path(applied["sqlite_source"])
        require(
            candidate == operator.RUNS / apply_label / "candidate.sqlite3"
            and candidate.is_file(),
            "sqlite_source_not_output",
        )
        require(
            not applied["source_sdk_opened"]
            and not applied["production_backend_changed"]
            and not applied["phase4_started"],
            "unsafe_workflow_claim",
        )
        review = Path(applied["review_directory"])
        cases = [
            json.loads(line)
            for line in (review / "cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        require(len(cases) == applied["draft_cases"] == 32, "review_not_prepared")
        require(
            applied["review_status"] == "unreviewed_proposals"
            and not applied["reviewed_locally"],
            "review_auto_attested",
        )
        require(
            all(
                case["draft"]
                and not case["reviewed_locally"]
                and not case["query_sanitized"]
                for case in cases
            ),
            "draft_flags_promoted",
        )
        require(
            json.loads((review / "review-manifest.json").read_text())["review_method"]
            is None,
            "human_attestation_created",
        )
        require(
            all(
                path.stat().st_mode & 0o777 == 0o600
                for path in review.iterdir()
                if path.is_file()
            ),
            "review_assets_not_private",
        )
        strict_label = label + "-strict"
        strict_source = Path(external) / "strict-source"
        strict_spec = folder / "strict-source.json"
        migration._write_json(
            strict_spec,
            {
                "source": str(strict_source),
                "records": [
                    {
                        "id": "fictional-malformed-operator-record",
                        "document": "<MEMORY><broken>",
                        "metadata": {
                            "agent": "SyntheticArchitect",
                            "date": 123.5,
                            "header": "Fictional malformed memory",
                        },
                    }
                ],
            },
        )
        strict_build = subprocess.run(
            python_command()
            + [
                "verification/memory/verify_phase2.py",
                "--fixture-spec",
                str(strict_spec),
            ],
            cwd=ROOT,
            capture_output=True,
        )
        require(strict_build.returncode == 0, "strict_fixture_build_failed")
        strict = invoke(
            folder,
            [
                "--source",
                strict_source,
                "--writers-paused",
                "--apply",
                "--fail-on-parse-error",
                "--run-id",
                strict_label,
            ],
            expected=1,
        )
        require(
            strict["stage"] == "inspect_migration"
            and strict["reason"] == "strict_parse_warning_before_destination_writes",
            "strict_error_collapsed",
        )
        require(
            not (operator.RUNS / strict_label / "candidate.sqlite3").exists(),
            "strict_parse_failure_wrote_destination",
        )
        require(migration.fingerprint(source) == before, "source_changed_by_operator")
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(Path, "cwd", return_value=Path(external)),
        ):
            os.environ.pop("MEMORYDB_PATH", None)
            try:
                operator.resolve_source(None)
            except migration.MigrationError as error:
                require(
                    str(error) == "source_required_no_unambiguous_default",
                    "wrong_default_error",
                )
            else:
                raise AssertionError("guessed_missing_default")
            shutil.copytree(source, Path(external) / "memory_db")
            resolved, selection = operator.resolve_source(None)
            require(
                resolved == Path(external) / "memory_db" and selection == "cwd_default",
                "default_not_resolved",
            )
            os.environ["MEMORYDB_PATH"] = str(source)
            try:
                operator.resolve_source(None)
            except migration.MigrationError as error:
                require(
                    str(error) == "ambiguous_source_requires_explicit_source",
                    "wrong_ambiguity_code",
                )
            else:
                raise AssertionError("ambiguous_sources_guessed")
            require(
                operator.resolve_source(source)[0] == source,
                "explicit_source_not_authoritative",
            )
        overlap_source = folder / "overlap-source"
        shutil.copytree(source, overlap_source)
        with patch.object(operator, "RUNS", overlap_source / "outputs"):
            rejected(
                lambda: operator.operate(
                    replace(base, writers_paused=True, source=overlap_source)
                ),
                "source_run_overlap",
                "path_preflight",
            )
        tracked = Path(external) / "tracked-source"
        shutil.copytree(source, tracked)
        require(
            subprocess.run(
                ["git", "init", "-q"], cwd=tracked, capture_output=True
            ).returncode
            == 0,
            "fictional_repo_creation_failed",
        )
        (tracked / "fictional-tracked-file").write_text(
            "fictional only", encoding="utf-8"
        )
        require(
            subprocess.run(
                ["git", "add", "fictional-tracked-file"],
                cwd=tracked,
                capture_output=True,
            ).returncode
            == 0,
            "fictional_tracking_failed",
        )
        rejected(
            lambda: operator.operate(
                replace(base, writers_paused=True, source=tracked)
            ),
            "source_contains_tracked_files",
            "source_resolution",
        )
        interrupted_label = label + "-interrupted"
        interruption = folder / "interrupted.json"
        migration._write_json(
            interruption, {"source": str(source), "run_id": interrupted_label}
        )
        child = subprocess.run(
            python_command()
            + [
                "verification/memory/verify_migration_operator.py",
                "--interrupt-fixture",
                str(interruption),
            ],
            cwd=ROOT,
            capture_output=True,
        )
        require(child.returncode == 130, "operator_interruption_failed")
        interrupted_run = operator.RUNS / interrupted_label
        recovered = migration.migrate(
            migration.MigrationOptions(
                source=interrupted_run / "staged-source",
                destination=interrupted_run / "candidate.sqlite3",
                work_dir=interrupted_run / "migration",
                apply=True,
                resume=True,
                verify=True,
                batch_size=5,
                report=interrupted_run / "migration" / "recovery.json",
            )
        )
        require(
            recovered["skipped"] == 5
            and recovered["inserted"] == len(rows) - 5
            and recovered["verified"] == len(rows),
            "resume_lost_or_duplicated_records",
        )
        require(
            (interrupted_run / "migration" / "source-backup").is_dir(),
            "rollback_backup_missing",
        )
        require(
            migration.fingerprint(source) == before, "original_source_bytes_changed"
        )
        SQLiteMemoryRepository(candidate, read_only=True)
    print(
        "PASS operator: attestation, explicit/default/ambiguous source, inspect/apply, draft-only review, static errors, freshness, ignored/untracked private paths, source stability, and ledger-backed recovery"
    )
    print(
        json.dumps(
            {
                "fictional_source_records": len(rows),
                "verified_records": len(rows),
                "review_drafts": 32,
                "recovery_skipped": 5,
                "recovery_inserted": len(rows) - 5,
                "remote_calls": 0,
                "real_sources_or_private_drafts_used": False,
                "phase4_started": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
