"""Offline, dry-run-first migration from a Chroma snapshot to SQLite.

Stop/pause AgentCrew writers operationally before copying a source. This tool
cannot confirm that writers are paused. Paths are restricted to ignored
private workspaces only when ``MigrationOptions.private_roots`` is set (the
dev CLI in ``scripts/migrate_chroma_memory_to_sqlite.py`` passes the
repository roots); it never opens the source with Chroma or runs retention.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import traceback
from collections import Counter
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from AgentCrew.modules.memory.record_parser import checksum
from AgentCrew.modules.memory.sqlite_repository import (
    SCHEMA_VERSION,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.types import MemoryRecord

CONTRACT_VERSION = 1


def fingerprint(directory: Path) -> dict[str, str]:
    """Per-file SHA-256 map of a directory tree (vendored baseline helper)."""
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Source symlinks require operator review")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(path.relative_to(directory))] = digest.hexdigest()
    return result


class MigrationError(RuntimeError):
    """A static error code, never a private record value or path."""


@dataclass(frozen=True)
class MigrationOptions:
    source: Path
    destination: Path
    work_dir: Path
    collection: str = "conversation"
    batch_size: int = 100
    report: Path | None = None
    apply: bool = False
    resume: bool = False
    verify: bool = False
    fail_on_parse_error: bool = False
    private_roots: tuple[Path, ...] | None = None
    skip_warned_records: bool = False


def json_text(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _path(value: Path) -> Path:
    path = Path(os.path.abspath(value.expanduser()))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise MigrationError("symlink_path_rejected")
    return path


def _overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _checkout_root() -> Path | None:
    """Best-effort source-checkout root for dev metadata; None when installed."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _ignored(path: Path, private_roots: tuple[Path, ...] | None = None) -> None:
    if private_roots is None:
        return
    if not any(path.is_relative_to(root) and path != root for root in private_roots):
        raise MigrationError("path_outside_private_workspace")
    root = _checkout_root()
    if root is None:
        raise MigrationError("private_path_not_ignored")
    if subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", str(path.relative_to(root))],
        check=False,
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise MigrationError("private_path_not_ignored")
    if subprocess.check_output(
        ["git", "ls-files", "--", f":(literal){path.relative_to(root)}"],
        cwd=root,
    ).strip():
        raise MigrationError("private_path_is_tracked")


def validate_source_tree(value: Path) -> Path:
    """Validate a read-only Chroma input without constructing a Chroma client."""
    source = _path(value)
    if not source.is_dir() or not (source / "chroma.sqlite3").is_file():
        raise MigrationError("source_is_not_a_chroma_directory")
    for path in source.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise MigrationError("unsupported_source_tree_entry")
    return source


def copy_stable_source(
    source: Path, destination: Path, private_roots: tuple[Path, ...] | None = None
) -> dict:
    """Copy a validated source into a fresh, ignored private directory."""
    source = validate_source_tree(source)
    destination = _path(destination)
    _ignored(destination, private_roots)
    if destination.exists() or _overlap(source, destination):
        raise MigrationError("unsafe_snapshot_destination")
    before = fingerprint(source)
    _private_directory(destination.parent, private_roots)
    shutil.copytree(source, destination)
    if fingerprint(source) != before or fingerprint(destination) != before:
        raise MigrationError("source_unstable_during_copy")
    return before


def validate_options(options: MigrationOptions) -> MigrationOptions:
    source, destination, work = (
        _path(value)
        for value in (
            options.source,
            options.destination,
            options.work_dir,
        )
    )
    report = _path(options.report) if options.report else work / "report.json"
    for path in (source, destination, work, report):
        _ignored(path, options.private_roots)
    if any(
        _overlap(left, right)
        for left, right in (
            (source, destination),
            (source, work),
            (destination, work),
        )
    ):
        raise MigrationError("source_destination_work_overlap")
    if report.parent != work or report.name in {
        "snapshot.json",
        "source-backup",
        "chroma-copy",
        "sdk.log",
        ".migration.lock",
    }:
        raise MigrationError("report_must_be_an_owned_work_directory_file")
    if type(options.batch_size) is not int or not 1 <= options.batch_size <= 1000:
        raise MigrationError("invalid_batch_size")
    if not options.collection.strip() or len(options.collection) > 512:
        raise MigrationError("invalid_collection")
    if options.verify and not options.apply:
        raise MigrationError("verification_requires_apply")
    validate_source_tree(source)
    if destination.exists() and (not destination.is_file() or not options.resume):
        raise MigrationError("destination_requires_explicit_resume")
    if work.exists() and (not work.is_dir() or not options.resume):
        raise MigrationError("snapshot_reuse_requires_explicit_resume")
    for suffix in ("-wal", "-shm", "-journal", ".migration.lock"):
        _path(destination.with_name(destination.name + suffix))
        if (
            not destination.exists()
            and destination.with_name(destination.name + suffix).exists()
            and suffix != ".migration.lock"
        ):
            raise MigrationError("orphan_destination_sidecar")
    return MigrationOptions(
        source,
        destination,
        work,
        options.collection,
        options.batch_size,
        report,
        options.apply,
        options.resume,
        options.verify,
        options.fail_on_parse_error,
        options.private_roots,
        options.skip_warned_records,
    )


def _private_directory(
    path: Path, private_roots: tuple[Path, ...] | None = None
) -> None:
    if private_roots is None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        return
    root = next(root for root in private_roots if path.is_relative_to(root))
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        os.chmod(current, 0o700)


def _write_json(path: Path, value) -> None:
    _path(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(path: Path):
    _path(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt

                os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as error:
            raise MigrationError("migration_workspace_locked") from error
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _sdk_collection(work: Path, collection_name: str):
    log_path = _path(work / "sdk.log")
    with log_path.open("a", encoding="utf-8") as log:
        os.chmod(log_path, 0o600)
        sys.stdout.flush()
        sys.stderr.flush()
        saved = (os.dup(1), os.dup(2))
        client = None
        try:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            with redirect_stdout(log), redirect_stderr(log):
                import chromadb
                from chromadb.config import Settings

                client = chromadb.PersistentClient(
                    path=str(work / "chroma-copy"),
                    settings=Settings(anonymized_telemetry=False),
                )
                try:
                    yield client.get_collection(
                        collection_name, embedding_function=None
                    )
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
                    else:
                        client._system.stop()
        finally:
            log.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for descriptor in saved:
                os.close(descriptor)


def _snapshot(options: MigrationOptions) -> tuple[str, dict]:
    source_files = fingerprint(options.source)
    source_hash = checksum(
        json_text(
            {
                "source_tree": checksum(json_text(source_files)),
                "collection": checksum(options.collection),
            }
        )
    )
    root = _checkout_root()
    state_path = options.work_dir / "snapshot.json"
    expected = {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "source_fingerprint": source_hash,
        "collection_fingerprint": checksum(options.collection),
        "destination_token": checksum(
            str(options.destination.relative_to(root))
            if root is not None
            else str(options.destination)
        ),
        "mapping_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "repository_sha256": hashlib.sha256(
            Path(__file__).with_name("sqlite_repository.py").read_bytes()
        ).hexdigest(),
        "parser_sha256": hashlib.sha256(
            Path(__file__).with_name("record_parser.py").read_bytes()
        ).hexdigest(),
    }
    backup = _path(options.work_dir / "source-backup")
    working = _path(options.work_dir / "chroma-copy")
    if state_path.exists():
        _path(state_path)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        if not options.resume or saved.get("identity") != expected:
            raise MigrationError("snapshot_identity_mismatch")
        if (
            saved.get("source_files") != source_files
            or fingerprint(backup) != source_files
        ):
            raise MigrationError("frozen_snapshot_mismatch")
        if not (working / "chroma.sqlite3").is_file():
            raise MigrationError("working_copy_missing")
        for path in working.rglob("*"):
            if path.is_symlink():
                raise MigrationError("working_copy_symlink_rejected")
    else:
        if options.resume or backup.exists() or working.exists():
            raise MigrationError("unsafe_snapshot_destination")
        copied = copy_stable_source(options.source, backup, options.private_roots)
        if copied != source_files or fingerprint(options.source) != source_files:
            raise MigrationError("source_unstable_during_copy")
        shutil.copytree(backup, working)
        if fingerprint(working) != source_files:
            raise MigrationError("working_copy_checksum_mismatch")
        with closing(
            sqlite3.connect(
                (working / "chroma.sqlite3").as_uri() + "?mode=ro", uri=True
            )
        ) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("chroma_copy_integrity_failed")
        _write_json(state_path, {"identity": expected, "source_files": source_files})
    return source_hash, source_files


def _source_rows(collection, batch_size: int):
    count = collection.count()
    seen = set()
    for offset in range(0, count, batch_size):
        batch = collection.get(
            limit=batch_size, offset=offset, include=["documents", "metadatas"]
        )
        ids = batch["ids"]
        documents = batch["documents"] or [None] * len(ids)
        metadatas = batch["metadatas"] or [None] * len(ids)
        if not (len(ids) == len(documents) == len(metadatas)):
            raise MigrationError("source_batch_shape_mismatch")
        rows = []
        for identifier, document, metadata in zip(ids, documents, metadatas):
            if identifier in seen:
                raise MigrationError("duplicate_source_id")
            seen.add(identifier)
            rows.append((identifier, document, metadata))
        yield rows
    if len(seen) != count or collection.count() != count:
        raise MigrationError("source_collection_count_changed")


def map_record(identifier, document, metadata) -> tuple[MemoryRecord, str]:
    """Preserve source JSON; never infer missing scope, dates or historical CWD."""
    if not isinstance(metadata, dict) or not isinstance(document, str):
        raise MigrationError("unmappable_required_record")
    agent = metadata.get("agent")
    timestamp = metadata.get("date")
    if not isinstance(agent, str) or not agent.strip():
        raise MigrationError("unmappable_required_agent")
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise MigrationError("unmappable_required_date")
    evolved = metadata.get("evolved_at")
    if evolved is not None:
        if type(evolved) not in (str, int, float):
            raise MigrationError("unmappable_evolution_value")
        evolved = str(evolved)
    if any(
        name in metadata
        for name in (
            "lifecycle",
            "lifecycle_status",
            "deleted",
            "is_deleted",
            "superseded",
            "is_superseded",
        )
    ):
        raise MigrationError("unsupported_explicit_lifecycle_fields")
    if "status" not in metadata and "superseded_by" in metadata:
        raise MigrationError("unsupported_explicit_supersession_without_status")
    status = metadata.get("status", "active")
    if status == "active" and metadata.get("superseded_by") is not None:
        raise MigrationError("conflicting_explicit_lifecycle_fields")
    if status not in {"active", "superseded", "deleted"}:
        raise MigrationError("unsupported_explicit_status")
    record = MemoryRecord(
        id=identifier,
        agent_name=agent,
        session_id=metadata.get("session_id"),
        document=document,
        created_at=timestamp,
        updated_at=timestamp,
        metadata=metadata,
        cwd=None,
        header=metadata.get("header"),
        status=status,
        evolved_at=evolved,
    )
    source_hash = checksum(
        json_text(
            {
                "id": identifier,
                "document": document,
                "metadata": metadata,
            }
        )
    )
    return record, source_hash


def _inspect(
    collection, batch_size: int, report: dict, skip_warned: bool = False
) -> dict:
    index = {}
    warnings = Counter()
    failures = Counter()
    skipped = []
    parser_failures = warning_records = 0
    for rows in _source_rows(collection, batch_size):
        for raw in rows:
            try:
                record, source_hash = map_record(*raw)
                parsed = SQLiteMemoryRepository.validate_record(record)
                if skip_warned and parsed.warnings:
                    skipped.append(record.id)
                else:
                    index[record.id] = (
                        source_hash,
                        SQLiteMemoryRepository.canonical_checksum(record),
                    )
                warning_records += bool(parsed.warnings)
                parser_failures += any(
                    unit.source_field == "BODY" for unit in parsed.units
                )
                warnings.update(parsed.warnings)
            except (MigrationError, ValueError, TypeError, OverflowError) as error:
                reason = (
                    str(error)
                    if isinstance(error, MigrationError)
                    else "invalid_canonical_record"
                )
                failures[reason] += 1
    report.update(
        {
            "source_count": len(index) + len(skipped) + sum(failures.values()),
            "readable_count": len(index) + len(skipped),
            "unmappable_count": sum(failures.values()),
            "parse_failure_count": parser_failures,
            "warning_record_count": warning_records,
            "warning_counts": dict(warnings),
            "mapping_failure_counts": dict(failures),
            "source_records_fingerprint": checksum(
                json_text(
                    sorted(
                        (checksum(identifier), hashes[0])
                        for identifier, hashes in index.items()
                    )
                )
            ),
            "expected_canonical_fingerprint": checksum(
                json_text(
                    sorted(
                        (checksum(identifier), hashes[1])
                        for identifier, hashes in index.items()
                    )
                )
            ),
        }
    )
    if skip_warned:
        report["warning_skipped_count"] = len(skipped)
        report["warning_skipped_ids"] = sorted(checksum(item) for item in skipped)
    if failures:
        raise MigrationError("unmappable_source_records_no_silent_skip")
    return index


def _records(collection, batch_size: int, expected: dict, skip_warned: bool = False):
    seen = set()
    for rows in _source_rows(collection, batch_size):
        records = []
        for raw in rows:
            record, source_hash = map_record(*raw)
            if record.id not in expected:
                if skip_warned:
                    continue
                raise MigrationError("source_record_changed_after_inspection")
            if expected[record.id] != (
                source_hash,
                SQLiteMemoryRepository.canonical_checksum(record),
            ):
                raise MigrationError("source_record_changed_after_inspection")
            seen.add(record.id)
            records.append((record, source_hash))
        yield records
    if seen != set(expected):
        raise MigrationError("source_id_set_changed")


def _assert_counts(counts: dict, expected: int | None = None) -> None:
    if (
        counts["foreign_ledger"]
        or counts["non_null_cwd"]
        or counts["unitless_active"]
        or counts["memory_record"] != counts["migration_ledger"]
        or counts["retrieval_unit"] != counts["memory_fts_docsize"]
        or (expected is not None and counts["memory_record"] != expected)
    ):
        raise MigrationError("destination_ownership_or_count_mismatch")


def _destination_fingerprint(path: Path) -> str:
    files = {}
    for label, suffix in (("database", ""), ("wal", "-wal")):
        candidate = path.with_name(path.name + suffix)
        if candidate.exists():
            _path(candidate)
            with candidate.open("rb") as stream:
                files[label] = hashlib.file_digest(stream, "sha256").hexdigest()
    return checksum(json_text(files))


def _report_base(options: MigrationOptions) -> dict:
    root = _checkout_root()
    if root is None:
        try:
            package_version: str | None = importlib.metadata.version("agentcrew-ai")
        except importlib.metadata.PackageNotFoundError:
            package_version = None
        checkout_commit: str | None = None
        checkout_dirty = False
    else:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        package_version = project["version"]
        checkout_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        checkout_dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            ).strip()
        )
    SQLiteMemoryRepository.check_fts5()
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "checkout_commit": checkout_commit,
        "checkout_dirty": checkout_dirty,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "package_version": package_version,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "chromadb": importlib.metadata.version("chromadb"),
        "mode": "apply" if options.apply else "dry-run",
        "resume": options.resume,
        "verification_requested": options.verify,
        "strict_parse": options.fail_on_parse_error,
        "writers_confirmed_paused": False,
        "source_sdk_opened": False,
        "retention_run": False,
        "destination_opened": False,
        "destination_write_opened": False,
        "fts5_available": True,
        "inserted": 0,
        "skipped": 0,
        "verified": 0,
    }


def migrate(options: MigrationOptions) -> dict:
    options = validate_options(options)
    existing_work = options.work_dir.exists()
    if existing_work and not (options.work_dir / "snapshot.json").is_file():
        raise MigrationError("unsafe_reused_work_directory")
    _private_directory(options.work_dir, options.private_roots)
    with _lock(options.work_dir / ".migration.lock"):
        previous_umask = os.umask(0o077)
        report = {"status": "initializing", "writers_confirmed_paused": False}
        try:
            report.update(_report_base(options))
            source_hash, source_files = _snapshot(options)
            report["source_fingerprint"] = source_hash
            report["source_tree_fingerprint"] = checksum(json_text(source_files))
            if options.destination.exists() and options.apply:
                repository = SQLiteMemoryRepository(options.destination, read_only=True)
                report["destination_opened"] = True
                _assert_counts(repository.migration_counts(source_hash))
            with _sdk_collection(options.work_dir, options.collection) as collection:
                expected = _inspect(
                    collection, options.batch_size, report, options.skip_warned_records
                )
                state_path = options.work_dir / "snapshot.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                inspection = {
                    name: report[name]
                    for name in (
                        "source_count",
                        "source_records_fingerprint",
                        "expected_canonical_fingerprint",
                    )
                }
                if "inspection" in state and state["inspection"] != inspection:
                    raise MigrationError("working_copy_logical_content_changed")
                if "inspection" not in state:
                    if options.destination.exists():
                        raise MigrationError(
                            "missing_inspection_checkpoint_for_existing_records"
                        )
                    state["inspection"] = inspection
                    _write_json(state_path, state)
                if (
                    options.fail_on_parse_error
                    and not options.skip_warned_records
                    and report["warning_record_count"]
                ):
                    raise MigrationError(
                        "strict_parse_warning_before_destination_writes"
                    )
                if options.apply:
                    _private_directory(
                        options.destination.parent, options.private_roots
                    )
                    with _lock(
                        options.destination.with_name(
                            options.destination.name + ".migration.lock"
                        )
                    ):
                        if options.destination.exists():
                            if not options.resume:
                                raise MigrationError("destination_created_concurrently")
                            SQLiteMemoryRepository(options.destination, read_only=True)
                        repository = SQLiteMemoryRepository(options.destination)
                        report["destination_opened"] = True
                        report["destination_write_opened"] = True
                        _assert_counts(repository.migration_counts(source_hash))
                        repository.check_integrity()
                        for records in _records(
                            collection,
                            options.batch_size,
                            expected,
                            options.skip_warned_records,
                        ):
                            inserted, skipped = repository.apply_migration_batch(
                                records,
                                source_hash,
                                resume=options.resume,
                                fail_on_parse_error=options.fail_on_parse_error,
                            )
                            report["inserted"] += inserted
                            report["skipped"] += skipped
                        if options.verify:
                            for records in _records(
                                collection,
                                options.batch_size,
                                expected,
                                options.skip_warned_records,
                            ):
                                report["verified"] += repository.verify_migration_batch(
                                    records, source_hash
                                )
                        repository.check_integrity()
                        counts = repository.migration_counts(source_hash)
                        _assert_counts(counts, len(expected))
                        report["destination_counts"] = counts
                        report["destination_files_fingerprint"] = (
                            _destination_fingerprint(options.destination)
                        )
                        if options.verify:
                            report["verified_canonical_fingerprint"] = report[
                                "expected_canonical_fingerprint"
                            ]
                if fingerprint(options.source) != source_files:
                    raise MigrationError("source_changed_during_migration")
                if fingerprint(options.work_dir / "source-backup") != source_files:
                    raise MigrationError("frozen_backup_changed")
                report["source_and_backup_unchanged"] = True
                report["status"] = (
                    "verified"
                    if options.verify
                    else "applied"
                    if options.apply
                    else "inspected"
                )
        except BaseException as error:
            report["status"] = "blocked"
            report["reason"] = (
                str(error)
                if isinstance(error, MigrationError)
                else type(error).__name__
            )
            diagnostic = _path(options.work_dir / "sdk.log")
            with diagnostic.open("a", encoding="utf-8") as log:
                os.chmod(diagnostic, 0o600)
                traceback.print_exc(file=log)
            _write_json(options.report, report)
            raise
        finally:
            os.umask(previous_umask)
        _write_json(options.report, report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--collection", default="conversation")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--fail-on-parse-error", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate(
            MigrationOptions(
                args.source,
                args.destination,
                args.work_dir,
                args.collection,
                args.batch_size,
                args.report,
                args.apply,
                args.resume,
                args.verify,
                args.fail_on_parse_error,
            )
        )
    except BaseException as error:
        reason = (
            str(error) if isinstance(error, MigrationError) else type(error).__name__
        )
        print(json.dumps({"status": "blocked", "reason": reason}), file=sys.stderr)
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
