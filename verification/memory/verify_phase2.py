"""Standalone disposable Chroma/SQLite migration verification, not a benchmark."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from scripts.migrate_chroma_memory_to_sqlite import (
    MigrationError,
    MigrationOptions,
    _lock,
    map_record,
    migrate,
    validate_options,
)
from verification.memory.capture_baseline import fingerprint

PRIVATE = ROOT / "verification/memory/local"
TOOL = ROOT / "scripts/migrate_chroma_memory_to_sqlite.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(callback):
    try:
        callback()
    except MigrationError:
        return
    raise AssertionError("Expected a static migration safety rejection")


def uv_command(*args):
    return [
        "uv",
        "run",
        "--no-project",
        "--no-config",
        "--offline",
        "--python",
        sys.executable,
        "python",
        *map(str, args),
    ]


def run_child(command, log_path, expected=0):
    with log_path.open("w", encoding="utf-8") as log:
        os.chmod(log_path, 0o600)
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=log,
            env={
                **os.environ,
                "UV_PYTHON_DOWNLOADS": "never",
                "PYTHONDONTWRITEBYTECODE": "1",
                "ANONYMIZED_TELEMETRY": "False",
            },
        )
    require(
        result.returncode == expected,
        "Child validation failed; inspect ignored private verifier logs",
    )


def build_fixture(spec_path):
    import chromadb
    from chromadb.config import Settings

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    client = chromadb.PersistentClient(
        path=spec["source"], settings=Settings(anonymized_telemetry=False)
    )
    collection = client.create_collection("conversation", embedding_function=None)
    for row in spec["records"]:
        collection.add(
            ids=[row["id"]],
            documents=[row["document"]],
            metadatas=[row["metadata"]],
            embeddings=[[0.25, 0.75]],
        )
    require(
        collection.count() == len(spec["records"]),
        "Synthetic Chroma fixture count changed",
    )
    client._system.stop()


def prepare_source(directory, name, records):
    source = directory / name
    source.mkdir(mode=0o700)
    spec = directory / f"{name}-fixture.json"
    spec.write_text(
        json.dumps({"source": str(source), "records": records}), encoding="utf-8"
    )
    run_child(
        uv_command(__file__, "--fixture-spec", spec), directory / f"{name}-build.log"
    )
    return source


def invoke(directory, source, name, extra=(), expected=0):
    work = directory / f"{name}-work"
    destination = directory / f"{name}.sqlite3"
    command = uv_command(
        TOOL,
        "--source",
        source,
        "--destination",
        destination,
        "--work-dir",
        work,
        "--batch-size",
        2,
        *extra,
    )
    run_child(command, directory / f"{name}-invoke.log", expected)
    report_path = work / "report.json"
    return (
        destination,
        work,
        json.loads(report_path.read_text()) if report_path.exists() else None,
    )


def interrupted_helper(config_path):
    config = json.loads(config_path.read_text())
    original = SQLiteMemoryRepository.apply_migration_batch
    completed = 0

    def interrupt_after_first_batch(self, *args, **kwargs):
        nonlocal completed
        if completed:
            raise KeyboardInterrupt
        result = original(self, *args, **kwargs)
        completed += 1
        return result

    with patch.object(
        SQLiteMemoryRepository, "apply_migration_batch", interrupt_after_first_batch
    ):
        try:
            migrate(
                MigrationOptions(
                    Path(config["source"]),
                    Path(config["destination"]),
                    Path(config["work"]),
                    batch_size=2,
                    apply=True,
                    verify=True,
                )
            )
        except KeyboardInterrupt:
            return 130
    raise AssertionError("Migration did not stop between batches")


def tamper_working_copy(spec_path):
    import chromadb
    from chromadb.config import Settings

    spec = json.loads(spec_path.read_text())
    client = chromadb.PersistentClient(
        path=spec["copy"], settings=Settings(anonymized_telemetry=False)
    )
    collection = client.get_collection("conversation", embedding_function=None)
    collection.update(
        ids=[spec["id"]],
        documents=["synthetic changed working copy"],
        embeddings=[[0.25, 0.75]],
    )
    client._system.stop()


def verify_paths_and_stability(directory, source):
    options = MigrationOptions(
        source, directory / "guard.sqlite3", directory / "guard-work"
    )
    for changed in (
        replace(options, destination=source / "inside.sqlite3"),
        replace(options, work_dir=source / "inside-work"),
        replace(options, destination=options.work_dir / "inside.sqlite3"),
        replace(options, destination=Path("/synthetic/outside-private.sqlite3")),
        replace(options, report=source / "outside-report.json"),
        replace(options, verify=True),
        replace(options, batch_size=0),
    ):
        rejects(lambda: validate_options(changed))
    linked = directory / "symlink-source"
    shutil.copytree(source, linked)
    (linked / "forbidden-link").symlink_to(source / "chroma.sqlite3")
    rejects(lambda: validate_options(replace(options, source=linked)))
    root_alias = directory / "source-alias"
    root_alias.symlink_to(source, target_is_directory=True)
    rejects(lambda: validate_options(replace(options, source=root_alias)))
    occupied = directory / "occupied-work"
    occupied.mkdir(mode=0o700)
    rejects(lambda: migrate(replace(options, work_dir=occupied, resume=True)))
    original_copy = shutil.copytree

    def simulate_writer(src, dst, *args, **kwargs):
        result = original_copy(src, dst, *args, **kwargs)
        if Path(src) == source:
            (source / "simulated-writer").write_text("synthetic writer changed source")
        return result

    before = fingerprint(source)
    with patch(
        "scripts.migrate_chroma_memory_to_sqlite.shutil.copytree", simulate_writer
    ):
        try:
            rejects(lambda: migrate(options))
        finally:
            (source / "simulated-writer").unlink(missing_ok=True)
    require(
        fingerprint(source) == before and not options.destination.exists(),
        "Copy instability wrote destination or lost source",
    )
    rejected_destination, _, _ = invoke(directory, source, "unrelated")
    connection = sqlite3.connect(rejected_destination)
    connection.execute("CREATE TABLE unrelated(value)")
    connection.close()
    original = rejected_destination.read_bytes()
    rejects(
        lambda: validate_options(replace(options, destination=rejected_destination))
    )
    _, _, refused = invoke(directory, source, "unrelated", ("--apply", "--resume"), 1)
    require(
        refused["reason"] == "SQLiteMemoryError",
        "Unrelated destination schema was not checked",
    )
    require(rejected_destination.read_bytes() == original, "Unrelated database changed")
    future, _, _ = invoke(directory, source, "future")
    SQLiteMemoryRepository(future)
    connection = sqlite3.connect(future)
    connection.execute("PRAGMA user_version=99")
    connection.commit()
    connection.close()
    original = future.read_bytes()
    _, _, refused = invoke(directory, source, "future", ("--apply", "--resume"), 1)
    require(
        refused["reason"] == "SQLiteMemoryError" and future.read_bytes() == original,
        "Future schema changed",
    )


def verify_batch_rollback(directory, records):
    repository = SQLiteMemoryRepository(directory / "rollback.sqlite3")
    mapped = [
        map_record(row["id"], row["document"], row["metadata"]) for row in records[:2]
    ]
    with repository._transaction(write=True) as connection:
        connection.execute(
            "CREATE TRIGGER stop_batch BEFORE INSERT ON retrieval_unit "
            "WHEN new.ordinal=0 AND (SELECT count(*) FROM memory_record)=2 "
            "BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END",
        )
    try:
        repository.apply_migration_batch(mapped, "0" * 64)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Batch rollback not exercised")
    counts = repository.migration_counts("0" * 64)
    require(
        all(
            counts[name] == 0
            for name in (
                "memory_record",
                "migration_ledger",
                "retrieval_unit",
                "memory_fts_docsize",
            )
        ),
        "Partial batch/ledger/index commit",
    )
    repository.check_integrity()


def main_verification():
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix="phase2-verification-", dir=PRIVATE))
    os.chmod(directory, 0o700)
    corpus = [
        json.loads(line)
        for line in (ROOT / "verification/memory/synthetic-corpus.jsonl")
        .read_text()
        .splitlines()
    ]
    records = [
        {
            "id": row["id"],
            "document": row["document"],
            "metadata": {
                **row["metadata"],
                "unknown-key": "fictional preserved",
                "unknown-bool": True,
                "unknown-number": 2.5,
                "cwd": "/synthetic/historical/untrusted",
            },
        }
        for row in corpus[:3]
    ]
    records += [
        {
            "id": "syn-phase2-malformed",
            "document": "<MEMORY><bad>",
            "metadata": {"agent": "SyntheticMigration", "date": 123.25},
        },
        {
            "id": "syn-phase2-legacy",
            "document": "<MEMORY><HEAD>Synthetic legacy</HEAD><CONVERSATION_NOTES><NOTE>Retain missing optional fields.</NOTE></CONVERSATION_NOTES></MEMORY>",
            "metadata": {"agent": "SyntheticMigration", "date": 0, "evolved_at": 123.5},
        },
        {
            "id": "syn-phase2-status",
            "document": "<MEMORY><HEAD>Synthetic inactive</HEAD></MEMORY>",
            "metadata": {
                "agent": "SyntheticMigration",
                "date": 456,
                "status": "deleted",
                "subject_key": "synthetic-do-not-infer",
                "superseded_by": "synthetic-do-not-infer",
                "kinds": "synthetic-do-not-infer",
            },
        },
    ]
    records[0]["metadata"]["session_id"] = ""
    source = prepare_source(directory, "source", records)
    before = fingerprint(source)
    destination, work, dry = invoke(directory, source, "migration")
    require(
        dry["status"] == "inspected" and not destination.exists(),
        "Dry run wrote destination",
    )
    require(
        dry["source_count"] == 6 and dry["parse_failure_count"] == 1,
        "Inspection count/fallback wrong",
    )
    require((work.stat().st_mode & 0o777) == 0o700, "Workspace not private")
    require(
        (work / "report.json").stat().st_mode & 0o777 == 0o600, "Report not private"
    )
    require(fingerprint(work / "source-backup") == before, "Frozen backup not exact")
    _, _, strict = invoke(
        directory, source, "strict", ("--apply", "--fail-on-parse-error"), 1
    )
    require(
        strict["reason"] == "strict_parse_warning_before_destination_writes"
        and not (directory / "strict.sqlite3").exists(),
        "Strict mode wrote destination",
    )
    destination, work, applied = invoke(
        directory, source, "migration", ("--apply", "--resume", "--verify")
    )
    require(
        applied["inserted"] == 6
        and applied["verified"] == 6
        and applied["skipped"] == 0,
        "Apply/verify failed",
    )
    require(
        applied["source_and_backup_unchanged"]
        and not applied["source_sdk_opened"]
        and not applied["writers_confirmed_paused"],
        "False source/writer claims",
    )
    repository = SQLiteMemoryRepository(destination)
    destination_bytes = destination.read_bytes()
    _, _, existing_dry = invoke(directory, source, "migration", ("--resume",))
    require(
        not existing_dry["destination_opened"]
        and destination.read_bytes() == destination_bytes,
        "Existing-destination dry run opened or wrote SQLite",
    )
    _, _, strict_resume = invoke(
        directory,
        source,
        "migration",
        ("--apply", "--resume", "--fail-on-parse-error"),
        1,
    )
    require(
        strict_resume["reason"] == "strict_parse_warning_before_destination_writes"
        and destination.read_bytes() == destination_bytes,
        "Strict resume wrote destination",
    )
    for row in records:
        record = repository.get_record(row["id"], row["metadata"]["agent"])
        require(
            record
            and record.document == row["document"]
            and record.metadata == row["metadata"],
            "Original document/metadata changed",
        )
        require(
            record.cwd is None
            and record.subject_key is None
            and record.superseded_by is None
            and record.kinds == (),
            "Historical context or inferred labels invented",
        )
        require(
            record.session_id == row["metadata"].get("session_id"),
            "Missing session invented",
        )
        require(
            record.header == row["metadata"].get("header"), "Missing header invented"
        )
        require(
            record.status == row["metadata"].get("status", "active"),
            "Explicit lifecycle changed",
        )
    _, _, resumed = invoke(
        directory, source, "migration", ("--apply", "--resume", "--verify")
    )
    require(
        resumed["inserted"] == 0 and resumed["skipped"] == resumed["verified"] == 6,
        "Resume duplicated data",
    )
    require(
        resumed["destination_counts"] == applied["destination_counts"],
        "Resume duplicated indexes",
    )
    require(
        resumed["verified_canonical_fingerprint"]
        == applied["verified_canonical_fingerprint"],
        "Resume changed canonical data",
    )
    before_report = (work / "report.json").read_bytes()
    with _lock(work / ".migration.lock"):
        invoke(directory, source, "migration", ("--apply", "--resume"), 1)
    require(
        (work / "report.json").read_bytes() == before_report,
        "Concurrent lock rejection overwrote owner's report",
    )
    first = records[0]
    original_record = repository.get_record(first["id"], first["metadata"]["agent"])
    with repository._transaction(write=True) as connection:
        connection.execute(
            "UPDATE memory_record SET document=? WHERE id=?",
            ("synthetic destination tamper", first["id"]),
        )
    _, _, mismatch = invoke(
        directory, source, "migration", ("--apply", "--resume", "--verify"), 1
    )
    require(mismatch["status"] == "blocked", "Destination mismatch not rejected")
    repository.upsert_record(original_record)
    with repository._transaction(write=True) as connection:
        original_hash = connection.execute(
            "SELECT source_checksum FROM migration_ledger WHERE source_id=?",
            (first["id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE migration_ledger SET source_checksum=? WHERE source_id=?",
            ("0" * 64, first["id"]),
        )
    invoke(directory, source, "migration", ("--apply", "--resume", "--verify"), 1)
    with repository._transaction(write=True) as connection:
        connection.execute(
            "UPDATE migration_ledger SET source_checksum=? WHERE source_id=?",
            (original_hash, first["id"]),
        )
    mutation_spec = directory / "mutate-copy.json"
    mutation_spec.write_text(
        json.dumps({"copy": str(work / "chroma-copy"), "id": first["id"]})
    )
    run_child(
        uv_command(__file__, "--tamper-copy", mutation_spec),
        directory / "tamper-copy.log",
    )
    _, _, tampered = invoke(directory, source, "migration", ("--apply", "--resume"), 1)
    require(
        tampered["reason"] == "working_copy_logical_content_changed",
        "Working copy was not bound to inspection",
    )
    (work / "chroma-copy").rename(work / "chroma-copy-tampered")
    shutil.copytree(work / "source-backup", work / "chroma-copy")
    invoke(directory, source, "migration", ("--apply", "--resume", "--verify"))
    changed_source = source / "synthetic-source-change"
    destination_bytes = destination.read_bytes()
    changed_source.write_text("synthetic source changed after snapshot")
    try:
        _, _, source_mismatch = invoke(
            directory, source, "migration", ("--apply", "--resume"), 1
        )
        require(
            source_mismatch["reason"] == "snapshot_identity_mismatch"
            and destination.read_bytes() == destination_bytes,
            "Changed source was accepted or altered destination",
        )
    finally:
        changed_source.unlink()
    config = directory / "interruption.json"
    config.write_text(
        json.dumps(
            {
                "source": str(source),
                "destination": str(directory / "interrupt.sqlite3"),
                "work": str(directory / "interrupt-work"),
            }
        )
    )
    run_child(
        uv_command(__file__, "--interrupt-config", config),
        directory / "interrupt.log",
        130,
    )
    interrupted = json.loads((directory / "interrupt-work/report.json").read_text())
    require(
        interrupted["inserted"] == 2 and interrupted["status"] == "blocked",
        "Interruption not between committed batches",
    )
    _, _, restarted = invoke(
        directory, source, "interrupt", ("--apply", "--resume", "--verify")
    )
    require(
        restarted["inserted"] == 4
        and restarted["skipped"] == 2
        and restarted["verified"] == 6,
        "Restart did not resume completed batches",
    )
    bad_source = prepare_source(
        directory,
        "unmappable",
        [
            {
                "id": "syn-unmappable",
                "document": "synthetic missing date",
                "metadata": {"agent": "SyntheticMigration"},
            }
        ],
    )
    bad_destination, _, blocked = invoke(
        directory, bad_source, "unmappable", ("--apply",), 1
    )
    require(
        blocked["unmappable_count"] == 1 and not bad_destination.exists(),
        "Missing required date was invented or silently skipped",
    )
    lifecycle_source = prepare_source(
        directory,
        "unsupported-lifecycle",
        [
            {
                "id": "syn-explicit-lifecycle",
                "document": "synthetic explicit lifecycle",
                "metadata": {
                    "agent": "SyntheticMigration",
                    "date": 1,
                    "is_deleted": True,
                },
            }
        ],
    )
    blocked_destination, _, lifecycle = invoke(
        directory, lifecycle_source, "unsupported-lifecycle", ("--apply",), 1
    )
    require(
        lifecycle["mapping_failure_counts"].get("unsupported_explicit_lifecycle_fields")
        == 1
        and not blocked_destination.exists(),
        "Unsupported lifecycle was silently reactivated",
    )
    verify_paths_and_stability(directory, source)
    verify_batch_rollback(directory, records)
    require(
        fingerprint(source) == before and fingerprint(work / "source-backup") == before,
        "Migration mutated original or backup",
    )
    repository.check_integrity()
    for path in (directory, destination, work / "report.json", work / "sdk.log"):
        require(
            subprocess.run(
                [
                    "git",
                    "check-ignore",
                    "--quiet",
                    "--no-index",
                    str(path.relative_to(ROOT)),
                ],
                cwd=ROOT,
            ).returncode
            == 0,
            "Private artifact not ignored",
        )
    for path in (
        TOOL,
        Path(__file__),
        ROOT / "AgentCrew/modules/memory/sqlite_repository.py",
    ):
        ast.parse(path.read_text(encoding="utf-8"))
    print(
        "PASS: Phase 2 dry-run/apply/strict parsing/arbitrary metadata/NULL CWD/ledger resume/checksums/FTS/FK/ownership/rollback/interruption/path/symlink/unstable-source checks"
    )
    print(
        "Synthetic source: 6 records; apply verified 6; repeat skipped 6; interrupted restart skipped 2 and inserted 4; one parser fallback preserved"
    )
    print(
        "Only disposable synthetic Chroma data used; private artifacts retained ignored; no live source, deprecated tests, application startup or Phase 3 benchmark run"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-spec", type=Path)
    parser.add_argument("--interrupt-config", type=Path)
    parser.add_argument("--tamper-copy", type=Path)
    args = parser.parse_args()
    if args.fixture_spec:
        build_fixture(args.fixture_spec)
    elif args.interrupt_config:
        return interrupted_helper(args.interrupt_config)
    elif args.tamper_copy:
        tamper_working_copy(args.tamper_copy)
    else:
        main_verification()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
