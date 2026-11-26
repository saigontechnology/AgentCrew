"""Offline cross-release resume regressions using committed schema-2/3 writers.

No private data, live Chroma service, embeddings or model calls are used. The SDK
boundary is replaced with a deterministic fictional collection; the old writer,
mapper, snapshot validation, ledger and current upgrade paths are real code.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import engine_selection
from AgentCrew.modules.memory import migrate_chroma_memory_to_sqlite as migration
from AgentCrew.modules.memory import sqlite_repository as repository_module
from AgentCrew.modules.memory.sqlite_repository import (
    SCHEMA_VERSION,
    SQLiteMemoryError,
    SQLiteMemoryRepository,
)

REFERENCES = {
    2: "661df4b0b43be32f6cb10265c806161a43a1253c",
    3: "407606aba878504c4c9cdc432585041fefb5df0d",
}
MIGRATION_MODULE = "AgentCrew.modules.memory.migrate_chroma_memory_to_sqlite"


class FictionalInterruption(RuntimeError):
    pass


class FictionalCollection:
    def __init__(self):
        self.rows = [
            {
                "id": f"fictional-{index}",
                "document": f"<MEMORY><HEAD>Fictional migration {index}</HEAD><CONVERSATION_NOTES><NOTE>Keep bounded retries.</NOTE></CONVERSATION_NOTES></MEMORY>",
                "metadata": {
                    "agent": "FictionalAgent",
                    "date": 1.0,
                    "session_id": f"session-{index}",
                },
            }
            for index in range(3)
        ]

    def count(self):
        return len(self.rows)

    def get(self, limit, offset, include):
        selected = self.rows[offset : offset + limit]
        return {
            "ids": [row["id"] for row in selected],
            "documents": [row["document"] for row in selected],
            "metadatas": [copy.deepcopy(row["metadata"]) for row in selected],
        }


def load_reference(root, name, version, monkeypatch):
    relative = f"AgentCrew/modules/memory/{name}.py"
    source = subprocess.check_output(
        ["git", "show", f"{REFERENCES[version]}:{relative}"], cwd=ROOT
    )
    path = root / f"schema-{version}" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source)
    module_name = f"AgentCrew.modules.memory._resume_reference_{version}_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def state(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        names = {row[1] for row in objects}
        tables = {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
            for name in (
                "schema_version",
                "memory_record",
                "migration_ledger",
                "retrieval_unit",
                "memopad",
                "memopad_evidence",
            )
            if name in names
        }
        return connection.execute("PRAGMA user_version").fetchone()[0], objects, tables


def partial_destination(tmp_path, monkeypatch, kind, version=2):
    package_version = migration.importlib.metadata.version
    monkeypatch.setattr(
        migration.importlib.metadata,
        "version",
        lambda name: "fictional-sdk" if name == "chromadb" else package_version(name),
    )
    source = tmp_path / "source"
    source.mkdir()
    with closing(sqlite3.connect(source / "chroma.sqlite3")) as connection:
        connection.execute("CREATE TABLE fictional_source (id INTEGER)")
        connection.commit()
    destination = tmp_path / "destination.sqlite3"
    work = tmp_path / "migration-work"
    collection = FictionalCollection()

    @contextmanager
    def sdk(*_args):
        yield collection

    old_repository = load_reference(tmp_path, "sqlite_repository", version, monkeypatch)
    load_reference(tmp_path, "record_parser", version, monkeypatch)
    old_migration = load_reference(
        tmp_path, "migrate_chroma_memory_to_sqlite", version, monkeypatch
    )
    old_engine = load_reference(tmp_path, "engine_selection", version, monkeypatch)
    assert old_repository.SCHEMA_VERSION == version
    monkeypatch.setattr(
        old_migration, "SQLiteMemoryRepository", old_repository.SQLiteMemoryRepository
    )
    monkeypatch.setattr(old_migration, "SCHEMA_VERSION", version)
    monkeypatch.setattr(old_migration, "_checkout_root", lambda: None)
    monkeypatch.setattr(migration, "_checkout_root", lambda: None)
    monkeypatch.setattr(old_migration, "_sdk_collection", sdk)
    monkeypatch.setattr(migration, "_sdk_collection", sdk)
    options = old_migration.MigrationOptions(
        source, destination, work, batch_size=1, apply=True, verify=True
    )
    original = old_repository.SQLiteMemoryRepository.apply_migration_batch

    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise FictionalInterruption("fictional interruption after committed batch")

    with (
        patch.object(
            old_repository.SQLiteMemoryRepository, "apply_migration_batch", interrupted
        ),
        pytest.raises(FictionalInterruption),
    ):
        if kind == "standalone":
            old_migration.migrate(options)
        else:
            with (
                patch.object(
                    repository_module,
                    "SQLiteMemoryRepository",
                    old_repository.SQLiteMemoryRepository,
                ),
                patch.dict(sys.modules, {MIGRATION_MODULE: old_migration}),
            ):
                old_engine._migrate_chroma_to_sqlite(
                    source, destination, work, "conversation", False, batch_size=1
                )
    before = state(destination)
    assert before[0] == version
    assert len(before[2]["memory_record"]) == len(before[2]["migration_ledger"]) == 1
    checkpoint = json.loads((work / "snapshot.json").read_text())
    assert checkpoint["inspection"]["source_count"] == 3
    if kind == "standalone":
        identity = checkpoint["identity"]
        assert (
            identity["schema_version"],
            identity["mapping_tool_sha256"],
            identity["repository_sha256"],
            identity["parser_sha256"],
        ) in migration.LEGACY_CHECKPOINT_CODE_IDENTITIES

    def resume(resume=True):
        if kind == "production":
            return engine_selection._migrate_chroma_to_sqlite(
                source, destination, work, "conversation", resume, batch_size=1
            )
        return migration.migrate(
            migration.MigrationOptions(
                source,
                destination,
                work,
                batch_size=1,
                apply=True,
                verify=True,
                resume=resume,
            )
        )

    return SimpleNamespace(
        source=source,
        destination=destination,
        work=work,
        before=before,
        resume=resume,
        collection=collection,
    )


@pytest.mark.parametrize("kind", ["production", "standalone"])
def test_initial_feature_reproduces_cross_version_resume_failure(
    tmp_path, monkeypatch, kind
):
    case = partial_destination(tmp_path, monkeypatch, kind)
    old_repository = load_reference(tmp_path, "sqlite_repository", 3, monkeypatch)
    load_reference(tmp_path, "record_parser", 3, monkeypatch)
    old_migration = load_reference(
        tmp_path, "migrate_chroma_memory_to_sqlite", 3, monkeypatch
    )
    old_engine = load_reference(tmp_path, "engine_selection", 3, monkeypatch)
    monkeypatch.setattr(
        old_migration, "SQLiteMemoryRepository", old_repository.SQLiteMemoryRepository
    )
    monkeypatch.setattr(old_migration, "_checkout_root", lambda: None)
    monkeypatch.setattr(old_migration, "_sdk_collection", migration._sdk_collection)
    with pytest.raises(
        (old_repository.SQLiteMemoryError, old_migration.MigrationError)
    ) as failure:
        if kind == "standalone":
            old_migration.migrate(
                old_migration.MigrationOptions(
                    case.source,
                    case.destination,
                    case.work,
                    batch_size=1,
                    apply=True,
                    verify=True,
                    resume=True,
                )
            )
        else:
            with (
                patch.object(
                    repository_module,
                    "SQLiteMemoryRepository",
                    old_repository.SQLiteMemoryRepository,
                ),
                patch.dict(sys.modules, {MIGRATION_MODULE: old_migration}),
            ):
                old_engine._migrate_chroma_to_sqlite(
                    case.source,
                    case.destination,
                    case.work,
                    "conversation",
                    True,
                    batch_size=1,
                )
    assert str(failure.value) == (
        "snapshot_identity_mismatch"
        if kind == "standalone"
        else "Incomplete memopad schema"
    )
    assert state(case.destination) == case.before


@pytest.mark.parametrize("kind", ["production", "standalone"])
@pytest.mark.parametrize("version", [2, 3])
def test_cross_release_partial_resume_upgrades_only_under_destination_lock(
    tmp_path, monkeypatch, kind, version
):
    case = partial_destination(tmp_path, monkeypatch, kind, version)
    original_lock = migration._lock
    original_init = SQLiteMemoryRepository.__init__
    held = set()
    observations = []
    destination_lock = case.destination.with_name(
        case.destination.name + ".migration.lock"
    )

    @contextmanager
    def observed_lock(path):
        with original_lock(path):
            held.add(path)
            try:
                yield
            finally:
                held.remove(path)

    def observed_init(self, path, **kwargs):
        before = state(Path(path))
        read_only = kwargs.get("read_only", False)
        if not read_only:
            assert destination_lock in held
        original_init(self, path, **kwargs)
        after = state(Path(path))
        if read_only:
            assert before == after
        observations.append((read_only, before[0], after[0]))

    monkeypatch.setattr(migration, "_lock", observed_lock)
    monkeypatch.setattr(SQLiteMemoryRepository, "__init__", observed_init)
    report = case.resume()
    assert report["status"] == "verified"
    assert (report["inserted"], report["skipped"], report["verified"]) == (2, 1, 3)
    assert observations[0] == (True, version, version)
    assert [row for row in observations if not row[0]] == [
        (False, version, SCHEMA_VERSION)
    ]
    after = state(case.destination)
    assert after[0] == SCHEMA_VERSION
    assert after[2]["memopad"] == after[2]["memopad_evidence"] == []
    assert after[2]["memory_record"][0] == case.before[2]["memory_record"][0]
    assert after[2]["migration_ledger"][0] == case.before[2]["migration_ledger"][0]
    SQLiteMemoryRepository(case.destination, read_only=True).check_read_only_integrity()
    again = case.resume()
    assert (again["inserted"], again["skipped"], again["verified"]) == (0, 3, 3)


@pytest.mark.parametrize("kind", ["production", "standalone"])
@pytest.mark.parametrize(
    "damage",
    [
        "future",
        "missing_index",
        "missing_column",
        "version_mismatch",
        "foreign_ledger",
        "orphan_unit",
    ],
)
def test_invalid_destination_is_refused_without_upgrade_or_mutation(
    tmp_path, monkeypatch, kind, damage
):
    case = partial_destination(tmp_path, monkeypatch, kind)
    with closing(sqlite3.connect(case.destination)) as connection:
        if damage == "future":
            connection.execute("UPDATE schema_version SET version=99")
            connection.execute("PRAGMA user_version=99")
        elif damage == "missing_index":
            connection.execute("DROP INDEX resource_uri")
        elif damage == "missing_column":
            connection.execute("ALTER TABLE retrieval_unit DROP COLUMN ordinal")
        elif damage == "version_mismatch":
            connection.execute("UPDATE schema_version SET version=1")
        elif damage == "foreign_ledger":
            connection.execute(
                "UPDATE migration_ledger SET source_fingerprint='foreign'"
            )
        else:
            connection.execute(
                """INSERT INTO retrieval_unit (
                    id,memory_id,kind,source_field,text,ordinal,checksum,title,important,body,identifiers
                ) SELECT 'orphan','missing',kind,source_field,text,ordinal,checksum,title,important,body,identifiers
                  FROM retrieval_unit LIMIT 1"""
            )
        connection.commit()
    before = state(case.destination)
    raw = case.destination.read_bytes()
    with pytest.raises((SQLiteMemoryError, migration.MigrationError)):
        case.resume()
    assert state(case.destination) == before
    assert case.destination.read_bytes() == raw
    assert "memopad" not in before[2]


@pytest.mark.parametrize("kind", ["production", "standalone"])
def test_upgrade_rollback_preserves_partial_destination_and_can_retry(
    tmp_path, monkeypatch, kind
):
    case = partial_destination(tmp_path, monkeypatch, kind)
    before = state(case.destination)
    with (
        patch.object(
            repository_module,
            "MEMOPAD_SCHEMA",
            (*repository_module.MEMOPAD_SCHEMA, "INVALID SQL"),
        ),
        pytest.raises(sqlite3.OperationalError),
    ):
        case.resume()
    assert state(case.destination) == before
    assert case.resume()["status"] == "verified"


@pytest.mark.parametrize(
    "field",
    [
        "repository_sha256",
        "mapping_tool_sha256",
        "parser_sha256",
        "destination_token",
        "source_fingerprint",
        "contract_version",
        "unexpected",
    ],
)
def test_legacy_checkpoint_requires_exact_audited_code_and_non_code_identity(
    tmp_path, monkeypatch, field
):
    case = partial_destination(tmp_path, monkeypatch, "standalone")
    path = case.work / "snapshot.json"
    checkpoint = json.loads(path.read_text())
    checkpoint["identity"][field] = "unknown"
    path.write_text(json.dumps(checkpoint))
    with pytest.raises(migration.MigrationError, match="snapshot_identity_mismatch"):
        case.resume()
    assert state(case.destination) == case.before


@pytest.mark.parametrize("kind", ["production", "standalone"])
@pytest.mark.parametrize("damage", ["inspection", "backup", "no_resume"])
def test_snapshot_and_resume_guarantees_still_precede_destination_writes(
    tmp_path, monkeypatch, kind, damage
):
    case = partial_destination(tmp_path, monkeypatch, kind)
    if damage == "inspection":
        path = case.work / "snapshot.json"
        checkpoint = json.loads(path.read_text())
        checkpoint["inspection"]["source_count"] = 99
        path.write_text(json.dumps(checkpoint))
    elif damage == "backup":
        (case.work / "source-backup" / "chroma.sqlite3").write_bytes(
            b"fictional damage"
        )
    with pytest.raises(migration.MigrationError):
        case.resume(resume=damage != "no_resume")
    assert state(case.destination) == case.before


def test_legacy_compatibility_does_not_ignore_current_parser_drift(
    tmp_path, monkeypatch
):
    case = partial_destination(tmp_path, monkeypatch, "standalone")
    identity = json.loads((case.work / "snapshot.json").read_text())["identity"]
    expected = {
        **identity,
        "schema_version": SCHEMA_VERSION,
        "mapping_tool_sha256": "f" * 64,
        "repository_sha256": "e" * 64,
    }
    assert migration._snapshot_identity_matches(identity, expected, allow_legacy=True)
    assert not migration._snapshot_identity_matches(
        identity, expected, allow_legacy=False
    )
    expected["parser_sha256"] = "0" * 64
    assert not migration._snapshot_identity_matches(
        identity, expected, allow_legacy=True
    )


def test_legacy_read_only_access_is_explicit_and_never_an_upgrade(
    tmp_path, monkeypatch
):
    case = partial_destination(tmp_path, monkeypatch, "standalone")
    with pytest.raises(SQLiteMemoryError):
        SQLiteMemoryRepository(case.destination, read_only=True)
    repository = SQLiteMemoryRepository(
        case.destination, read_only=True, allow_legacy_schema=True
    )
    repository.check_read_only_integrity()
    assert state(case.destination) == case.before
    with pytest.raises(ValueError, match="read-only"):
        SQLiteMemoryRepository(case.destination, allow_legacy_schema=True)
    assert state(case.destination) == case.before
