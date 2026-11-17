"""Stage, inspect and optionally migrate AgentCrew memory in one local command.

SQLite is an output, not an input. Stop all memory writers before supplying
--writers-paused. Inspection is the default; --apply creates a fresh verified
candidate without changing the application's backend. No remote calls occur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import benchmark_memory_retrieval as benchmark
from scripts import migrate_chroma_memory_to_sqlite as migration
from verification.memory.benchmark import BenchmarkError

RUNS = ROOT / "verification/memory-migration"
REVIEWS = ROOT / "verification/memory/local"


class OperatorFailure(RuntimeError):
    """A static error code and workflow stage, never a private path or value."""

    def __init__(self, stage: str, code: str):
        super().__init__(code)
        self.stage = stage
        self.code = code


class OperatorParser(argparse.ArgumentParser):
    def error(self, message):
        raise OperatorFailure("arguments", "invalid_arguments")


@dataclass(frozen=True)
class OperatorOptions:
    source: Path | None = None
    writers_paused: bool = False
    apply: bool = False
    prepare_review: bool = False
    run_id: str | None = None
    collection: str = "conversation"
    batch_size: int = 100
    fail_on_parse_error: bool = False


def resolve_source(explicit: Path | None) -> tuple[Path, str]:
    """Use an explicit source, or a single unambiguous environment/CWD source."""
    if explicit is not None:
        return migration.validate_source_tree(explicit), "argument"
    configured = os.environ.get("MEMORYDB_PATH")
    fallback = Path.cwd() / "memory_db"
    if configured is not None:
        if not configured.strip():
            raise migration.MigrationError(
                "empty_memorydb_path_requires_explicit_source"
            )
        source = migration.validate_source_tree(Path(configured))
        if fallback.exists() or fallback.is_symlink():
            other = migration._path(fallback)
            if (other / "chroma.sqlite3").is_file() and other != source:
                raise migration.MigrationError(
                    "ambiguous_source_requires_explicit_source"
                )
        return source, "MEMORYDB_PATH"
    if not (fallback / "chroma.sqlite3").is_file():
        raise migration.MigrationError("source_required_no_unambiguous_default")
    return migration.validate_source_tree(fallback), "cwd_default"


def require_untracked_source(source: Path) -> None:
    """Refuse source files tracked by the source directory's enclosing Git repo."""
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "ls-files", "--", "."],
        check=False,
        cwd=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0 and result.stdout.strip():
        raise migration.MigrationError("source_contains_tracked_files")
    if result.returncode == 128 and any(
        (parent / ".git").exists() for parent in (source, *source.parents)
    ):
        raise migration.MigrationError("source_tracking_check_failed")
    if result.returncode not in (0, 128):
        raise migration.MigrationError("source_tracking_check_failed")


def static_code(error: BaseException) -> str:
    if isinstance(error, OperatorFailure):
        return error.code
    if isinstance(error, (migration.MigrationError, BenchmarkError)):
        return str(error)
    return type(error).__name__


def operate(options: OperatorOptions) -> dict:
    """Delegate migration and review logic; own only the operator workflow."""
    stage = "writer_attestation"
    owned: Path | None = None
    try:
        if not options.writers_paused:
            raise migration.MigrationError("writers_paused_attestation_required")
        stage = "arguments"
        if options.prepare_review and not options.apply:
            raise migration.MigrationError("prepare_review_requires_apply")
        if type(options.batch_size) is not int or not 1 <= options.batch_size <= 1000:
            raise migration.MigrationError("invalid_batch_size")
        if (
            not isinstance(options.collection, str)
            or not options.collection.strip()
            or len(options.collection) > 200
        ):
            raise migration.MigrationError("invalid_collection")
        label = (
            options.run_id
            if options.run_id is not None
            else (
                "memory-"
                + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                + "-"
                + uuid.uuid4().hex[:12]
            )
        )
        if (
            not isinstance(label, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", label) is None
        ):
            raise migration.MigrationError("invalid_run_id")
        stage = "source_resolution"
        source, selection = resolve_source(options.source)
        require_untracked_source(source)
        stage = "path_preflight"
        run = migration._path(RUNS / label)
        review = migration._path(REVIEWS / (label + "-review"))
        migration._ignored(run)
        if run.exists():
            raise migration.MigrationError("run_directory_reused")
        if migration._overlap(source, run):
            raise migration.MigrationError("source_run_overlap")
        if options.prepare_review:
            migration._ignored(review)
            if review.exists():
                raise migration.MigrationError("review_directory_reused")
            if migration._overlap(source, review) or migration._overlap(run, review):
                raise migration.MigrationError("source_review_overlap")
        stage = "claim_run"
        migration._private_directory(RUNS)
        try:
            run.mkdir(mode=0o700)
        except FileExistsError as error:
            raise migration.MigrationError("run_directory_reused") from error
        owned = run
        staged = run / "staged-source"
        destination = run / "candidate.sqlite3"
        work = run / "migration"
        manifest = {
            "schema_version": 1,
            "status": "running",
            "run_id": label,
            "writers_paused_attested": True,
            "writers_confirmed_paused": False,
            "source_selection": selection,
            "source_sdk_opened": False,
            "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "checkout_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "production_backend_changed": False,
            "phase4_started": False,
            "artifacts": {
                "staged_source": "staged-source",
                "sqlite_candidate": "candidate.sqlite3",
                "migration_work": "migration",
            },
        }
        migration._write_json(run / "operator.json", manifest)
        stage = "stage_source"
        before = migration.copy_stable_source(source, staged)
        manifest["source_tree_fingerprint"] = migration.checksum(
            migration.json_text(before)
        )
        migration._write_json(run / "operator.json", manifest)
        stage = "inspect_migration"
        inspection = migration.migrate(
            migration.MigrationOptions(
                source=staged,
                destination=destination,
                work_dir=work,
                collection=options.collection,
                batch_size=options.batch_size,
                report=work / "inspection.json",
                fail_on_parse_error=options.fail_on_parse_error,
            )
        )
        if migration.fingerprint(source) != before:
            raise migration.MigrationError("source_changed_after_staging")
        result = inspection
        if options.apply:
            stage = "apply_verify_migration"
            result = migration.migrate(
                migration.MigrationOptions(
                    source=staged,
                    destination=destination,
                    work_dir=work,
                    collection=options.collection,
                    batch_size=options.batch_size,
                    report=work / "apply.json",
                    apply=True,
                    resume=True,
                    verify=True,
                    fail_on_parse_error=options.fail_on_parse_error,
                )
            )
            if result.get("status") != "verified":
                raise migration.MigrationError("verified_candidate_required")
            if migration.fingerprint(source) != before:
                raise migration.MigrationError("source_changed_after_staging")
        review_result = None
        if options.prepare_review:
            stage = "prepare_review"
            review_result = benchmark.draft_review(
                benchmark.input_path(destination),
                benchmark.new_output(review, [destination]),
            )
        stage = "verify_source_unchanged"
        if (
            migration.fingerprint(source) != before
            or migration.fingerprint(staged) != before
        ):
            raise migration.MigrationError("source_changed_after_staging")
        counts = result.get("destination_counts", {})
        summary = {
            "schema_version": 1,
            "status": "verified" if options.apply else "inspected",
            "stage": "complete",
            "run_id": label,
            "apply": options.apply,
            "source_selection": selection,
            "writers_paused_attested": True,
            "writers_confirmed_paused": False,
            "source_sdk_opened": False,
            "source_bytes_unchanged": True,
            "sqlite_source": str(destination) if options.apply else None,
            "source_records": result["source_count"],
            "records": counts.get("memory_record", 0),
            "ledger_records": counts.get("migration_ledger", 0),
            "verified_records": result["verified"],
            "inserted": result["inserted"],
            "skipped": result["skipped"],
            "parse_failures": result["parse_failure_count"],
            "review_status": "unreviewed_proposals"
            if review_result is not None
            else "not_requested",
            "review_directory": str(review) if review_result is not None else None,
            "draft_cases": review_result["draft_cases"]
            if review_result is not None
            else 0,
            "reviewed_locally": False,
            "production_backend_changed": False,
            "phase4_started": False,
        }
        migration._write_json(run / "operator.json", {**manifest, **summary})
        return summary
    except BaseException as error:
        code = static_code(error)
        if owned is not None:
            failure = {
                "schema_version": 1,
                "status": "blocked",
                "stage": stage,
                "reason": code,
                "writers_confirmed_paused": False,
                "production_backend_changed": False,
                "phase4_started": False,
            }
            with suppress(Exception):
                migration._write_json(owned / "operator-error.json", failure)
                diagnostic = migration._path(owned / "operator.log")
                with diagnostic.open("a", encoding="utf-8") as stream:
                    os.chmod(diagnostic, 0o600)
                    traceback.print_exc(file=stream)
        raise OperatorFailure(stage, code) from None


def main() -> int:
    parser = OperatorParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Existing Chroma persistence directory, not a SQLite file. Relative to your shell CWD.",
    )
    parser.add_argument(
        "--writers-paused",
        action="store_true",
        help="Attest that all AgentCrew/memory writers are stopped; this is not auto-detected.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="After inspection, create and verify a fresh SQLite candidate.",
    )
    mode.add_argument(
        "--inspect-only",
        action="store_true",
        help="Inspect and stage only; do not create a SQLite destination (default).",
    )
    parser.add_argument(
        "--prepare-review",
        action="store_true",
        help="After verified --apply, generate unreviewed proposals, never a human attestation.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional fresh run label: letters, digits, underscore and hyphen, up to 64 characters.",
    )
    parser.add_argument("--collection", default="conversation")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--fail-on-parse-error", action="store_true")
    try:
        args = parser.parse_args()
        result = operate(
            OperatorOptions(
                source=args.source,
                writers_paused=args.writers_paused,
                apply=args.apply,
                prepare_review=args.prepare_review,
                run_id=args.run_id,
                collection=args.collection,
                batch_size=args.batch_size,
                fail_on_parse_error=args.fail_on_parse_error,
            )
        )
    except OperatorFailure as error:
        print(
            json.dumps(
                {"status": "blocked", "stage": error.stage, "reason": error.code}
            ),
            file=sys.stderr,
        )
        return 130 if error.code == "KeyboardInterrupt" else 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
