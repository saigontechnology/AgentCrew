"""Memory engine selection and first-run Chroma to SQLite migration.

The engine is chosen from ``global_settings.memory_engine`` in config.json
(``sqlite`` default, ``chromadb`` optional). When the SQLite engine starts
without an existing store but with Chroma data on disk, the verified migrator
semantics from ``scripts/migrate_chroma_memory_to_sqlite.py`` run once:
offline source copy, idempotent batching with resume, canonical-checksum
verification, report and lock files. No network and no embedding calls
happen; the Chroma source directory stays untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from loguru import logger

MEMORY_ENGINES = ("sqlite", "chromadb")
DEFAULT_MEMORY_ENGINE = "sqlite"
SQLITE_STORE_PATH = "./memory_db/agent_memory.sqlite3"
SQLITE_MIGRATION_WORK_PATH = "./memory_db/migration-work"
CHROMA_SOURCE_ENV = "MEMORYDB_PATH"
CHROMA_DEFAULT_SOURCE_PATH = "./memory_db"
MIGRATION_BATCH_SIZE = 100
_NON_CHROMA_NAMES = ("migration-work",)
_NON_CHROMA_PREFIXES = ("agent_memory.sqlite3",)


class MemoryEngineError(RuntimeError):
    """Startup-time engine selection error with actionable instructions."""


def resolve_memory_engine(global_settings: dict[str, Any]) -> str:
    """Return the configured memory engine or raise a clear startup error."""
    engine = str(global_settings.get("memory_engine", DEFAULT_MEMORY_ENGINE)).strip().lower()
    if engine not in MEMORY_ENGINES:
        raise MemoryEngineError(
            f"Invalid memory_engine '{engine}' in config.json global_settings. "
            f"Valid values: {', '.join(MEMORY_ENGINES)}."
        )
    return engine


def sqlite_store_path() -> Path:
    """Configured SQLite store location under the memory_db directory."""
    return Path(SQLITE_STORE_PATH)


def chroma_source_path() -> Path:
    """Chroma data location, matching the ChromaMemoryService default."""
    return Path(os.getenv(CHROMA_SOURCE_ENV, CHROMA_DEFAULT_SOURCE_PATH))


def sqlite_migration_work_path() -> Path:
    """Migration work directory for the offline copy, report and lock files."""
    return Path(SQLITE_MIGRATION_WORK_PATH)


def chromadb_install_hint() -> str:
    return (
        "Install the Chroma extra with 'uv sync --extra chromadb' or "
        "'pip install \"agentcrew-ai[chromadb]\"', or set global_settings.memory_engine "
        "to 'sqlite' in config.json."
    )


def require_chromadb(reason: str | None = None) -> None:
    """Fail fast with install instructions when chromadb is not importable.

    The default message fits the engine=chromadb selection; callers that need
    chromadb for another purpose pass their own reason.
    """
    try:
        import chromadb  # noqa: F401
    except ImportError as error:
        message = (
            "memory_engine is 'chromadb' but the chromadb package is not installed. "
            f"{chromadb_install_hint()}"
            if reason is None
            else reason
        )
        raise MemoryEngineError(message) from error


def _is_non_chroma_name(name: str) -> bool:
    return name in _NON_CHROMA_NAMES or name.startswith(_NON_CHROMA_PREFIXES)


def _production_fingerprint(directory: Path) -> dict[str, str]:
    """Per-file SHA-256 map of the Chroma source, excluding co-located artifacts.

    The SQLite store and the migration work directory live under the same
    memory_db directory as the Chroma data, so the migrator's whole-tree
    fingerprint is computed over the Chroma files only.
    """
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise MemoryEngineError("Chroma source symlinks require operator review")
        relative = path.relative_to(directory)
        if any(_is_non_chroma_name(part) for part in relative.parts):
            continue
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(relative)] = digest.hexdigest()
    return result


def _migration_completed(work_dir: Path) -> bool:
    report_path = work_dir / "report.json"
    if not report_path.is_file():
        return False
    try:
        return str(json.loads(report_path.read_text(encoding="utf-8")).get("status")) in (
            "applied",
            "verified",
        )
    except (OSError, ValueError):
        return False


def _snapshot_state(work_dir: Path, source_files: dict[str, str], source_hash: str, collection: str) -> dict:
    return {
        "identity": str(uuid.uuid4()),
        "source_files": source_files,
        "source_hash": source_hash,
        "collection": collection,
    }


def _production_snapshot(source: Path, work_dir: Path, collection: str, resume: bool) -> tuple[str, dict[str, str]]:
    """Offline copy of the Chroma source with frozen-backup verification."""
    from scripts.migrate_chroma_memory_to_sqlite import MigrationError, _write_json
    from verification.memory.capture_baseline import fingerprint

    backup = work_dir / "source-backup"
    working = work_dir / "chroma-copy"
    state_path = work_dir / "snapshot.json"
    if resume:
        if not state_path.is_file():
            raise MemoryEngineError(
                f"Unsafe reused migration work directory at '{work_dir}': remove it or "
                "complete the migration before starting again."
            )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source_files = state["source_files"]
        if not backup.is_dir() or fingerprint(backup) != source_files:
            raise MigrationError("frozen_backup_changed")
        if working.exists():
            shutil.rmtree(working)
        shutil.copytree(backup, working)
        if fingerprint(working) != source_files:
            raise MigrationError("working_copy_checksum_mismatch")
        logger.info("Resuming memory migration from the frozen offline copy")
        return state["source_hash"], source_files

    if backup.exists() or working.exists():
        raise MigrationError("unsafe_snapshot_destination")
    source_files = _production_fingerprint(source)
    shutil.copytree(
        source,
        backup,
        ignore=shutil.ignore_patterns(
            *_NON_CHROMA_NAMES, *(f"{prefix}*" for prefix in _NON_CHROMA_PREFIXES)
        ),
    )
    if _production_fingerprint(source) != source_files or fingerprint(backup) != source_files:
        raise MigrationError("source_unstable_during_copy")
    shutil.copytree(backup, working)
    if fingerprint(working) != source_files:
        raise MigrationError("working_copy_checksum_mismatch")
    with closing(
        sqlite3.connect((working / "chroma.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("chroma_copy_integrity_failed")
    from scripts.migrate_chroma_memory_to_sqlite import checksum, json_text

    source_hash = checksum(
        json_text(
            {
                "source_tree": checksum(json_text(source_files)),
                "collection": checksum(collection),
            }
        )
    )
    state = _snapshot_state(work_dir, source_files, source_hash, collection)
    _write_json(state_path, state)
    logger.info("Offline copy of the Chroma source captured")
    return source_hash, source_files


def _migrate_chroma_to_sqlite(
    source: Path,
    destination: Path,
    work_dir: Path,
    collection: str,
    resume: bool,
) -> dict[str, Any]:
    """Run the verified migrator semantics against production paths."""
    from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
    from scripts.migrate_chroma_memory_to_sqlite import (
        MigrationError,
        MigrationOptions,
        _assert_counts,
        _destination_fingerprint,
        _inspect,
        _lock,
        _records,
        _report_base,
        _sdk_collection,
        _write_json,
        checksum,
        json_text,
        validate_source_tree,
    )

    source = validate_source_tree(source)
    report_path = work_dir / "report.json"
    options = MigrationOptions(
        source=source,
        destination=destination,
        work_dir=work_dir,
        collection=collection,
        batch_size=MIGRATION_BATCH_SIZE,
        report=report_path,
        apply=True,
        resume=resume,
        verify=True,
        fail_on_parse_error=True,
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(work_dir, 0o700)
    with _lock(work_dir / ".migration.lock"):
        previous_umask = os.umask(0o077)
        report: dict[str, Any] = {"status": "initializing", "writers_confirmed_paused": True}
        try:
            report.update(_report_base(options))
            source_hash, source_files = _production_snapshot(source, work_dir, collection, resume)
            report["source_fingerprint"] = source_hash
            report["source_tree_fingerprint"] = checksum(json_text(source_files))
            if destination.exists():
                repository = SQLiteMemoryRepository(destination, read_only=True)
                report["destination_opened"] = True
                _assert_counts(repository.migration_counts(source_hash))
            with _sdk_collection(work_dir, collection) as collection_obj:
                expected = _inspect(collection_obj, MIGRATION_BATCH_SIZE, report)
                state = json.loads((work_dir / "snapshot.json").read_text(encoding="utf-8"))
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
                    if destination.exists():
                        raise MigrationError("missing_inspection_checkpoint_for_existing_records")
                    state["inspection"] = inspection
                    _write_json(work_dir / "snapshot.json", state)
                if report["warning_record_count"]:
                    raise MigrationError("strict_parse_warning_before_destination_writes")
                with _lock(destination.with_name(destination.name + ".migration.lock")):
                    if destination.exists() and not resume:
                        raise MigrationError("destination_created_concurrently")
                    repository = SQLiteMemoryRepository(destination)
                    report["destination_opened"] = True
                    report["destination_write_opened"] = True
                    _assert_counts(repository.migration_counts(source_hash))
                    repository.check_integrity()
                    logger.info(
                        f"Applying {report['source_count']} memory records to the SQLite store"
                    )
                    for records in _records(collection_obj, MIGRATION_BATCH_SIZE, expected):
                        inserted, skipped = repository.apply_migration_batch(
                            records, source_hash, resume=resume, fail_on_parse_error=True
                        )
                        report["inserted"] += inserted
                        report["skipped"] += skipped
                    for records in _records(collection_obj, MIGRATION_BATCH_SIZE, expected):
                        report["verified"] += repository.verify_migration_batch(records, source_hash)
                    repository.check_integrity()
                    counts = repository.migration_counts(source_hash)
                    _assert_counts(counts, len(expected))
                    report["destination_counts"] = counts
                    report["destination_files_fingerprint"] = _destination_fingerprint(destination)
                    report["verified_canonical_fingerprint"] = report[
                        "expected_canonical_fingerprint"
                    ]
            if _production_fingerprint(source) != source_files:
                raise MigrationError("source_changed_during_migration")
            from verification.memory.capture_baseline import fingerprint

            if fingerprint(work_dir / "source-backup") != source_files:
                raise MigrationError("frozen_backup_changed")
            report["source_and_backup_unchanged"] = True
            report["status"] = "verified"
        except BaseException as error:
            report["status"] = "blocked"
            report["reason"] = (
                str(error) if isinstance(error, MigrationError) else type(error).__name__
            )
            _write_json(report_path, report)
            raise
        finally:
            os.umask(previous_umask)
        _write_json(report_path, report)
        return report


def ensure_sqlite_store(
    db_path: Path,
    chroma_source: Path,
    work_dir: Path,
    collection: str = "conversation",
) -> Path:
    """Resolve the SQLite store for startup using the migration decision matrix.

    An existing store is used as-is; a completed migration is not repeated.
    Chroma data without a store triggers the automatic migration, which
    requires the chromadb package; a missing package fails fast instead of
    silently starting an empty store. With no data at all the service starts
    with a fresh empty store. Migration failures abort startup.
    """
    if db_path.exists() and _migration_completed(work_dir):
        return db_path
    resume = work_dir.exists()
    if db_path.exists() and not resume:
        return db_path
    chroma_present = chroma_source is not None and (chroma_source / "chroma.sqlite3").is_file()
    if not chroma_present:
        if resume:
            raise MemoryEngineError(
                f"An incomplete memory migration work directory exists at '{work_dir}' but the "
                f"Chroma source at '{chroma_source}' is missing. Restore the Chroma source or "
                "remove the work directory, then start again."
            )
        logger.info("No SQLite memory store found; starting with an empty store")
        return db_path
    require_chromadb(
        f"Chroma memory data found at '{chroma_source}'; the automatic migration to SQLite "
        "needs the chromadb package to read it. Install with 'uv sync --extra chromadb' or "
        "'pip install \"agentcrew-ai[chromadb]\"', or migrate manually with "
        "scripts/migrate_chroma_memory_to_sqlite.py, then start again."
    )
    logger.info(
        f"Chroma memory data found at '{chroma_source}'; running the automatic migration to SQLite"
    )
    _migrate_chroma_to_sqlite(chroma_source, db_path, work_dir, collection, resume)
    logger.info("Automatic memory migration complete")
    return db_path