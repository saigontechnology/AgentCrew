"""File-backed SQLite prototype with operation-owned connections and explicit SQL."""

from __future__ import annotations

import heapq
import json
import math
import sqlite3
import struct
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, nullcontext
from dataclasses import asdict, replace
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from . import provenance
from .memopad import MemopadCandidate, parse_memopad_candidates
from .project_identity import ProjectIdentity
from .record_parser import checksum, derive_memory_units, normalize_text
from .resources import query_resource_uris
from .types import (
    DOCUMENT_VECTOR_DIMENSIONS,
    CandidateBatch,
    CandidateSource,
    MatchReason,
    MemoryCandidate,
    MemoryQuery,
    MemoryRecord,
    ParsedMemory,
    ResourceEvidence,
    RetrievalUnit,
)

SCHEMA_VERSION = 3
MEMOPAD_SCHEMA = (
    """CREATE TABLE memopad (
        id TEXT PRIMARY KEY, agent_name TEXT NOT NULL,
        project_key TEXT NOT NULL, project_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('decision','lesson','constraint','correction',
            'workaround','unresolved','verified_fact','project_preference')),
        title TEXT NOT NULL, statement TEXT NOT NULL, subject_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('candidate','active','superseded','rejected','stale')),
        confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
        importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        valid_from REAL, valid_to REAL,
        superseded_by TEXT REFERENCES memopad(id) ON DELETE SET NULL,
        support_count INTEGER NOT NULL DEFAULT 0 CHECK(support_count >= 0),
        CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
    )""",
    "CREATE INDEX memopad_scope ON memopad(agent_name,project_key,status,kind)",
    """CREATE TABLE memopad_evidence (
        item_id TEXT NOT NULL REFERENCES memopad(id) ON DELETE CASCADE,
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        memory_checksum TEXT NOT NULL, unit_id TEXT, unit_checksum TEXT,
        created_at REAL NOT NULL, PRIMARY KEY(item_id,memory_id),
        CHECK((unit_id IS NULL) = (unit_checksum IS NULL))
    )""",
    "CREATE INDEX memopad_evidence_source ON memopad_evidence(memory_id)",
    """CREATE TRIGGER memopad_support_insert AFTER INSERT ON memopad_evidence BEGIN
        UPDATE memopad SET support_count=support_count+1 WHERE id=new.item_id;
    END""",
    """CREATE TRIGGER memopad_support_delete AFTER DELETE ON memopad_evidence BEGIN
        UPDATE memopad SET support_count=support_count-1,
            updated_at=CAST(strftime('%s','now') AS REAL)
            WHERE id=old.item_id;
        DELETE FROM memopad WHERE id=old.item_id AND support_count=0;
    END""",
    """CREATE TRIGGER memopad_source_deleted AFTER UPDATE OF status ON memory_record
        WHEN new.status='deleted' BEGIN
        DELETE FROM memopad_evidence WHERE memory_id=new.id;
    END""",
)
RESOURCE_SCHEMA = (
    """CREATE TABLE memory_resource (
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        resource_id TEXT NOT NULL, resource_unit_id TEXT NOT NULL,
        uri TEXT NOT NULL, normalized_uri TEXT NOT NULL, scheme TEXT NOT NULL,
        display_name TEXT, PRIMARY KEY(memory_id,resource_id),
        FOREIGN KEY(resource_unit_id,memory_id) REFERENCES retrieval_unit(id,memory_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX resource_uri ON memory_resource(normalized_uri)",
    "CREATE INDEX resource_parent ON memory_resource(memory_id)",
    """CREATE TABLE memory_resource_entity (
        memory_id TEXT NOT NULL, resource_id TEXT NOT NULL,
        entity_unit_id TEXT NOT NULL, relation TEXT NOT NULL,
        PRIMARY KEY(memory_id,resource_id,entity_unit_id,relation),
        FOREIGN KEY(memory_id,resource_id) REFERENCES memory_resource(memory_id,resource_id) ON DELETE CASCADE,
        FOREIGN KEY(entity_unit_id,memory_id) REFERENCES retrieval_unit(id,memory_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX resource_entity ON memory_resource_entity(memory_id,entity_unit_id)",
)
VECTOR_TABLE = "memory_document_vector"
VECTOR_TABLE_SQL = f"""CREATE TABLE {VECTOR_TABLE} (
    memory_id TEXT PRIMARY KEY REFERENCES memory_record(id) ON DELETE CASCADE,
    dimensions INTEGER NOT NULL CHECK(dimensions={DOCUMENT_VECTOR_DIMENSIONS}),
    vector BLOB NOT NULL
)"""
SCHEMA_V1 = (
    "CREATE TABLE schema_version (version INTEGER PRIMARY KEY)",
    """CREATE TABLE memory_record (
        id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, session_id TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, cwd TEXT, header TEXT,
        status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')),
        superseded_by TEXT, subject_key TEXT, valid_from REAL, valid_to REAL,
        evolved_at TEXT, document_format TEXT NOT NULL, document TEXT NOT NULL,
        document_checksum TEXT NOT NULL, metadata_json TEXT NOT NULL,
        metadata_checksum TEXT NOT NULL, structured_json TEXT,
        kinds_json TEXT NOT NULL, parse_warnings_json TEXT NOT NULL,
        CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
    )""",
    "CREATE INDEX memory_scope ON memory_record(agent_name,status,created_at,session_id)",
    "CREATE INDEX memory_subject ON memory_record(subject_key)",
    """CREATE TABLE retrieval_unit (
        rowid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, source_field TEXT NOT NULL, text TEXT NOT NULL,
        ordinal INTEGER NOT NULL, checksum TEXT NOT NULL,
        title TEXT NOT NULL, important TEXT NOT NULL,
        body TEXT NOT NULL, identifiers TEXT NOT NULL,
        UNIQUE(id,memory_id)
    )""",
    "CREATE INDEX unit_parent ON retrieval_unit(memory_id)",
    """CREATE TABLE memory_entity (
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        unit_id TEXT NOT NULL, value TEXT NOT NULL, normalized_value TEXT NOT NULL,
        PRIMARY KEY(unit_id,normalized_value),
        FOREIGN KEY(unit_id,memory_id) REFERENCES retrieval_unit(id,memory_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX exact_entity ON memory_entity(normalized_value)",
    """CREATE TABLE memory_tag (
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        unit_id TEXT NOT NULL, value TEXT NOT NULL, normalized_value TEXT NOT NULL,
        PRIMARY KEY(unit_id,normalized_value),
        FOREIGN KEY(unit_id,memory_id) REFERENCES retrieval_unit(id,memory_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX exact_tag ON memory_tag(normalized_value)",
    """CREATE TABLE migration_ledger (
        source_fingerprint TEXT NOT NULL, source_id TEXT NOT NULL,
        memory_id TEXT NOT NULL REFERENCES memory_record(id) ON DELETE CASCADE,
        source_checksum TEXT NOT NULL, destination_checksum TEXT NOT NULL,
        migrated_at TEXT NOT NULL, PRIMARY KEY(source_fingerprint,source_id)
    )""",
    """CREATE VIRTUAL TABLE memory_fts USING fts5(
        title,important,body,identifiers,content='retrieval_unit',
        content_rowid='rowid',tokenize='unicode61 remove_diacritics 2'
    )""",
    """CREATE TRIGGER unit_insert AFTER INSERT ON retrieval_unit BEGIN
        INSERT INTO memory_fts(rowid,title,important,body,identifiers)
        VALUES(new.rowid,new.title,new.important,new.body,new.identifiers);
    END""",
    """CREATE TRIGGER unit_delete AFTER DELETE ON retrieval_unit BEGIN
        INSERT INTO memory_fts(memory_fts,rowid,title,important,body,identifiers)
        VALUES('delete',old.rowid,old.title,old.important,old.body,old.identifiers);
    END""",
    """CREATE TRIGGER unit_update AFTER UPDATE ON retrieval_unit BEGIN
        INSERT INTO memory_fts(memory_fts,rowid,title,important,body,identifiers)
        VALUES('delete',old.rowid,old.title,old.important,old.body,old.identifiers);
        INSERT INTO memory_fts(rowid,title,important,body,identifiers)
        VALUES(new.rowid,new.title,new.important,new.body,new.identifiers);
    END""",
)
SCHEMA = (*SCHEMA_V1, *RESOURCE_SCHEMA, *MEMOPAD_SCHEMA)
RECORD_COLUMNS = (
    "id",
    "agent_name",
    "session_id",
    "created_at",
    "updated_at",
    "cwd",
    "header",
    "status",
    "superseded_by",
    "subject_key",
    "valid_from",
    "valid_to",
    "evolved_at",
    "document_format",
    "document",
    "document_checksum",
    "metadata_json",
    "metadata_checksum",
    "structured_json",
    "kinds_json",
    "parse_warnings_json",
)


class SQLiteMemoryError(RuntimeError):
    """Unsupported capability or incompatible schema; never repaired destructively."""


class SQLiteMemoryRepository:
    """Connections are created, used and closed on each calling thread.

    No live conversation state or connection is stored on this object. A search
    obtains all candidate sources and conflict markers within one read snapshot.
    Write methods support queued production memory storage and offline migration.
    Legacy read-only inspection is opt-in for migration preflight; it never upgrades
    the store or promises current-schema query capabilities.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        allow_legacy_schema: bool = False,
        busy_timeout_ms: int = 2000,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("Use a disposable file for operation-owned connections")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
            raise ValueError("Busy timeout must be within 1..30000 ms")
        if allow_legacy_schema and not read_only:
            raise ValueError("Legacy schema inspection requires read-only access")
        self.path = Path(path).resolve()
        self.read_only = read_only
        self.busy_timeout_ms = busy_timeout_ms
        self.check_fts5()
        if read_only:
            with self._transaction() as connection:
                version = (
                    connection.execute("PRAGMA user_version").fetchone()[0]
                    if allow_legacy_schema
                    else SCHEMA_VERSION
                )
                if version not in (1, 2, SCHEMA_VERSION):
                    raise SQLiteMemoryError("Unsupported SQLite memory schema version")
                self._validate_schema(connection, version=version)
        else:
            with self._transaction(write=True, create=True) as connection:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if (
                    not tables
                    and connection.execute("PRAGMA user_version").fetchone()[0] == 0
                ):
                    for statement in SCHEMA:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,)
                    )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    self._validate_schema(connection, version=1)
                    for statement in RESOURCE_SCHEMA:
                        connection.execute(statement)
                    rows = connection.execute(
                        "SELECT * FROM memory_record ORDER BY id"
                    ).fetchall()
                    for row in rows:
                        record = self._record(row)
                        parsed = self.validate_record(record)
                        self._reindex_record(connection, record, parsed)
                        connection.execute(
                            "UPDATE memory_record SET structured_json=?,parse_warnings_json=? WHERE id=?",
                            (
                                parsed.structured_json,
                                json.dumps(parsed.warnings),
                                record.id,
                            ),
                        )
                    connection.execute("UPDATE schema_version SET version=2")
                    connection.execute("PRAGMA user_version=2")
                if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
                    self._validate_schema(connection, version=2)
                    for statement in MEMOPAD_SCHEMA:
                        connection.execute(statement)
                    connection.execute("UPDATE schema_version SET version=3")
                    connection.execute("PRAGMA user_version=3")
                self._validate_schema(connection)
            with closing(self._connect(write=True)) as connection:
                if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                    raise SQLiteMemoryError(
                        "SQLite memory requires file-backed WAL support"
                    )

    @staticmethod
    def check_fts5() -> None:
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise SQLiteMemoryError(
                "SQLite 3.35+ is required for materialized candidate queries"
            )
        try:
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        except sqlite3.OperationalError as error:
            raise SQLiteMemoryError(
                "SQLite memory requires built-in FTS5 support"
            ) from error

    def _connect(
        self, *, write: bool = False, create: bool = False
    ) -> sqlite3.Connection:
        if write and self.read_only:
            raise SQLiteMemoryError("Repository was opened read-only")
        mode = "rwc" if create else "rw" if write else "ro"
        uri = self.path.as_uri() + f"?mode={mode}"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=self.busy_timeout_ms / 1000,
            autocommit=True,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _transaction(
        self, *, write: bool = False, create: bool = False
    ) -> Iterator[sqlite3.Connection]:
        with closing(self._connect(write=write, create=create)) as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _validate_schema(
        connection: sqlite3.Connection, version: int = SCHEMA_VERSION
    ) -> None:
        objects = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master")
        }
        required = {
            "schema_version",
            "memory_record",
            "retrieval_unit",
            "memory_entity",
            "memory_tag",
            "migration_ledger",
            "memory_fts",
            "unit_insert",
            "unit_delete",
            "unit_update",
        }
        if version >= 2:
            required.update(
                {
                    "memory_resource",
                    "memory_resource_entity",
                    "resource_uri",
                    "resource_parent",
                    "resource_entity",
                }
            )
        if version >= 3:
            required.update(
                {
                    "memopad",
                    "memopad_scope",
                    "memopad_evidence",
                    "memopad_evidence_source",
                    "memopad_support_insert",
                    "memopad_support_delete",
                    "memopad_source_deleted",
                }
            )
            for table, expected in (
                (
                    "memopad",
                    {
                        "id",
                        "agent_name",
                        "project_key",
                        "project_name",
                        "kind",
                        "title",
                        "statement",
                        "subject_key",
                        "status",
                        "confidence",
                        "importance",
                        "created_at",
                        "updated_at",
                        "valid_from",
                        "valid_to",
                        "superseded_by",
                        "support_count",
                    },
                ),
                (
                    "memopad_evidence",
                    {
                        "item_id",
                        "memory_id",
                        "memory_checksum",
                        "unit_id",
                        "unit_checksum",
                        "created_at",
                    },
                ),
            ):
                actual = {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if actual != expected:
                    raise SQLiteMemoryError("Incomplete memopad schema")
        if not required <= objects:
            raise SQLiteMemoryError("Unrecognized or incomplete SQLite memory schema")
        versions = [
            row[0] for row in connection.execute("SELECT version FROM schema_version")
        ]
        if (
            versions != [version]
            or connection.execute("PRAGMA user_version").fetchone()[0] != version
        ):
            raise SQLiteMemoryError("Unsupported SQLite memory schema version")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_record)")
        }
        if not set(RECORD_COLUMNS) <= columns:
            raise SQLiteMemoryError("Incomplete canonical memory schema")
        for table, expected in (
            ("schema_version", {"version"}),
            (
                "retrieval_unit",
                {
                    "rowid",
                    "id",
                    "memory_id",
                    "kind",
                    "source_field",
                    "text",
                    "ordinal",
                    "checksum",
                    "title",
                    "important",
                    "body",
                    "identifiers",
                },
            ),
            ("memory_entity", {"memory_id", "unit_id", "value", "normalized_value"}),
            ("memory_tag", {"memory_id", "unit_id", "value", "normalized_value"}),
            (
                "migration_ledger",
                {
                    "source_fingerprint",
                    "source_id",
                    "memory_id",
                    "source_checksum",
                    "destination_checksum",
                    "migrated_at",
                },
            ),
            ("memory_fts", {"title", "important", "body", "identifiers"}),
        ):
            actual = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if actual != expected:
                raise SQLiteMemoryError(f"Incomplete {table} schema")
        if version >= 2:
            for table, expected in (
                (
                    "memory_resource",
                    {
                        "memory_id",
                        "resource_id",
                        "resource_unit_id",
                        "uri",
                        "normalized_uri",
                        "scheme",
                        "display_name",
                    },
                ),
                (
                    "memory_resource_entity",
                    {"memory_id", "resource_id", "entity_unit_id", "relation"},
                ),
            ):
                actual = {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if actual != expected:
                    raise SQLiteMemoryError("Incomplete resource projection schema")

    def collect_memopad(
        self,
        memory_id: str,
        agent_name: str,
        project: ProjectIdentity,
        candidates: list[MemopadCandidate],
    ) -> int:
        """Publish bounded proposals after canonical commit in a separate transaction.

        Unit IDs/checksums are historical snapshots, intentionally not foreign keys:
        cumulative writes rebuild retrieval units. The canonical memory FK remains
        live, including preserved consolidation sources, and cascades on forgetting.
        No model-provided source attribution is trusted: all items are candidates.
        """
        candidates = parse_memopad_candidates(candidates)
        if not candidates:
            return 0
        with self._transaction(write=True) as connection:
            memory = connection.execute(
                "SELECT document_checksum,status FROM memory_record WHERE id=? AND agent_name=?",
                (memory_id, agent_name),
            ).fetchone()
            if memory is None or memory["status"] != "active":
                raise SQLiteMemoryError("Memopad source is not an active owned memory")
            units = connection.execute(
                "SELECT id,checksum,text FROM retrieval_unit WHERE memory_id=? ORDER BY ordinal,id",
                (memory_id,),
            ).fetchall()
            timestamp = datetime.now(UTC).timestamp()
            for candidate in candidates:
                subject = normalize_text(candidate.subject_key)
                statement = " ".join(candidate.statement.split())
                item_id = checksum(
                    json.dumps(
                        [agent_name, project.key, candidate.kind, subject, statement],
                        ensure_ascii=False,
                    )
                )
                importance = (
                    0.7
                    if candidate.kind in {"decision", "constraint", "correction"}
                    else 0.5
                )
                connection.execute(
                    """INSERT INTO memopad (
                        id,agent_name,project_key,project_name,kind,title,statement,
                        subject_key,status,confidence,importance,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,'candidate',0.35,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET project_name=excluded.project_name,
                        updated_at=excluded.updated_at""",
                    (
                        item_id,
                        agent_name,
                        project.key,
                        project.name,
                        candidate.kind,
                        candidate.title.strip(),
                        candidate.statement.strip(),
                        subject,
                        importance,
                        timestamp,
                        timestamp,
                    ),
                )
                matches = [
                    unit
                    for unit in units
                    if " ".join(unit["text"].split()) == statement
                ]
                unit = matches[0] if len(matches) == 1 else None
                connection.execute(
                    """INSERT INTO memopad_evidence (
                        item_id,memory_id,memory_checksum,unit_id,unit_checksum,created_at
                    ) VALUES (?,?,?,?,?,?) ON CONFLICT(item_id,memory_id) DO UPDATE SET
                        memory_checksum=excluded.memory_checksum,
                        unit_id=excluded.unit_id,unit_checksum=excluded.unit_checksum""",
                    (
                        item_id,
                        memory_id,
                        memory["document_checksum"],
                        unit["id"] if unit else None,
                        unit["checksum"] if unit else None,
                        timestamp,
                    ),
                )
        return len(candidates)

    def memopad_stats(
        self, agent_name: str | None = None, project_key: str | None = None
    ) -> dict[str, Any]:
        """Read one consistent aggregate snapshot; never return item statements."""
        clauses, parameters = [], []
        for field, value in (("agent_name", agent_name), ("project_key", project_key)):
            if value is not None:
                clauses.append(f"m.{field}=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT count(*) AS total,coalesce(avg(confidence),0) AS mean_confidence,
                    coalesce(sum(support_count>1),0) AS corroborated,
                    coalesce(sum(NOT EXISTS(SELECT 1 FROM memopad_evidence e WHERE e.item_id=m.id)),0) AS orphans
                    FROM memopad m"""
                + where,
                parameters,
            ).fetchone()
            stats = dict(row)
            stats["source_memories"] = connection.execute(
                "SELECT count(DISTINCT e.memory_id) FROM memopad_evidence e JOIN memopad m ON m.id=e.item_id"
                + where,
                parameters,
            ).fetchone()[0]
            for label, column in (
                ("status", "status"),
                ("kind", "kind"),
                ("project", "project_key"),
                ("agent", "agent_name"),
            ):
                stats[label] = dict(
                    connection.execute(
                        f"SELECT m.{column},count(*) FROM memopad m"
                        + where
                        + f" GROUP BY m.{column} ORDER BY m.{column}",
                        parameters,
                    ).fetchall()
                )
        return stats

    def upsert_record(self, record: MemoryRecord) -> ParsedMemory:
        """Atomically replace derived indexes without rewriting canonical input."""
        parsed, values = self._prepare_record(record)
        with self._transaction(write=True) as connection:
            self._write_record(connection, record, parsed, values)
        return parsed

    @staticmethod
    def _prepare_record(record: MemoryRecord) -> tuple[ParsedMemory, tuple]:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                record.id,
                record.agent_name,
            )
        ):
            raise ValueError("Canonical ID and agent must be explicit")
        if record.session_id is not None and not isinstance(record.session_id, str):
            raise ValueError("Canonical legacy session must be text or null")
        for value in (record.cwd, record.header, record.subject_key):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(
                    "Optional canonical labels must be nonempty text or null"
                )
        if not isinstance(record.kinds, tuple) or any(
            not isinstance(kind, str) or not kind for kind in record.kinds
        ):
            raise ValueError("Explicit kind labels must be a tuple of nonempty strings")
        if record.created_at is None or record.updated_at is None:
            raise ValueError("Canonical creation/update times are required")
        if not isinstance(record.document, str) or not isinstance(
            record.metadata, dict
        ):
            raise TypeError(
                "Canonical document and metadata must be text and a JSON object"
            )
        for timestamp in (
            record.created_at,
            record.updated_at,
            record.valid_from,
            record.valid_to,
        ):
            if timestamp is not None and (
                type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            ):
                raise ValueError("Memory times must be finite Unix timestamps")
            if timestamp is not None:
                try:
                    datetime.fromtimestamp(timestamp, UTC)
                except (OverflowError, OSError, ValueError) as error:
                    raise ValueError(
                        "Memory time is outside the supported calendar"
                    ) from error
        metadata_json = json.dumps(
            record.metadata, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        parsed = derive_memory_units(record)
        values = (
            record.id,
            record.agent_name,
            record.session_id,
            record.created_at,
            record.updated_at,
            record.cwd,
            record.header,
            record.status,
            record.superseded_by,
            record.subject_key,
            record.valid_from,
            record.valid_to,
            record.evolved_at,
            record.document_format,
            record.document,
            checksum(record.document),
            metadata_json,
            checksum(metadata_json),
            parsed.structured_json,
            json.dumps(record.kinds),
            json.dumps(parsed.warnings),
        )
        return parsed, values

    @staticmethod
    def validate_record(record: MemoryRecord) -> ParsedMemory:
        """Validate a canonical input without opening or writing a database."""
        return SQLiteMemoryRepository._prepare_record(record)[0]

    @staticmethod
    def _write_record(connection, record, parsed, values) -> None:
        assignments = ",".join(f"{name}=excluded.{name}" for name in RECORD_COLUMNS[1:])
        connection.execute(
            f"INSERT INTO memory_record ({','.join(RECORD_COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in values)}) ON CONFLICT(id) DO UPDATE SET {assignments}",
            values,
        )
        SQLiteMemoryRepository._reindex_record(connection, record, parsed)

    @staticmethod
    def _reindex_record(connection, record, parsed) -> None:
        """Replace only derived rows; schema upgrades never reserialize canonical data."""
        connection.execute("DELETE FROM retrieval_unit WHERE memory_id=?", (record.id,))
        for unit in parsed.units:
            connection.execute(
                """INSERT INTO retrieval_unit
                (id,memory_id,kind,source_field,text,ordinal,checksum,title,important,body,identifiers)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                SQLiteMemoryRepository._unit_values(unit),
            )
            for table, entries in (
                ("memory_entity", unit.entities),
                ("memory_tag", unit.tags),
            ):
                unique = SQLiteMemoryRepository._exact_values(entries)
                connection.executemany(
                    f"INSERT INTO {table}(memory_id,unit_id,value,normalized_value) VALUES (?,?,?,?)",
                    (
                        (record.id, unit.id, value, normalized)
                        for normalized, value in unique.items()
                    ),
                )

        SQLiteMemoryRepository._write_resources(connection, record.id, parsed)

    @staticmethod
    def _resource_rows(memory_id, parsed):
        return [
            (
                memory_id,
                item.resource_id,
                item.resource_unit_id,
                item.uri,
                item.normalized_uri,
                item.scheme,
                item.display_name,
            )
            for item in parsed.resources
        ]

    @staticmethod
    def _link_rows(memory_id, parsed):
        return [
            (memory_id, item.resource_id, item.entity_unit_id, item.relation)
            for item in parsed.resource_links
        ]

    @staticmethod
    def _write_resources(connection, memory_id, parsed):
        connection.executemany(
            "INSERT INTO memory_resource VALUES (?,?,?,?,?,?,?)",
            SQLiteMemoryRepository._resource_rows(memory_id, parsed),
        )
        connection.executemany(
            "INSERT INTO memory_resource_entity VALUES (?,?,?,?)",
            SQLiteMemoryRepository._link_rows(memory_id, parsed),
        )

    @staticmethod
    def _unit_values(unit: RetrievalUnit) -> tuple:
        bucket = (
            "title"
            if unit.source_field == "HEAD"
            else "identifiers"
            if unit.kind in {"entity", "tag", "resource"}
            else "important"
            if unit.source_field
            in {
                "FLOW/OUTCOME",
                "INSIGHTS/INSIGHT",
                "CONVERSATION_NOTES/NOTE",
            }
            else "body"
        )
        return (
            unit.id,
            unit.memory_id,
            unit.kind,
            unit.source_field,
            unit.text,
            unit.ordinal,
            unit.checksum,
            *(
                unit.text if bucket == name else ""
                for name in (
                    "title",
                    "important",
                    "body",
                    "identifiers",
                )
            ),
        )

    @staticmethod
    def _exact_values(entries: tuple[str, ...]) -> dict[str, str]:
        return {normalize_text(value): value for value in entries}

    @staticmethod
    def canonical_checksum(record: MemoryRecord) -> str:
        """Hash all projected canonical fields with SQLite numeric normalization."""
        values = asdict(record)
        for name in ("created_at", "updated_at", "valid_from", "valid_to"):
            if values[name] is not None:
                values[name] = float(values[name])
        if values["evolved_at"] is not None:
            values["evolved_at"] = str(values["evolved_at"])
        return checksum(
            json.dumps(
                values,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
        )

    def migration_counts(self, source_fingerprint: str) -> dict[str, int]:
        """Return aggregate ownership counts without exposing IDs or values."""
        with self._transaction() as connection:
            result = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "memory_record",
                    "migration_ledger",
                    "retrieval_unit",
                    "memory_entity",
                    "memory_tag",
                    "memory_resource",
                    "memory_resource_entity",
                    "memory_fts_docsize",
                )
            }
            result["foreign_ledger"] = connection.execute(
                "SELECT count(*) FROM migration_ledger WHERE source_fingerprint!=?",
                (source_fingerprint,),
            ).fetchone()[0]
            result["non_null_cwd"] = connection.execute(
                "SELECT count(*) FROM memory_record WHERE cwd IS NOT NULL",
            ).fetchone()[0]
            result["unitless_active"] = connection.execute(
                """SELECT count(*) FROM memory_record m WHERE m.status='active'
                AND NOT EXISTS (SELECT 1 FROM retrieval_unit u WHERE u.memory_id=m.id)""",
            ).fetchone()[0]
            return result

    def apply_migration_batch(
        self,
        records: list[tuple[MemoryRecord, str]],
        source_fingerprint: str,
        *,
        resume: bool = False,
        fail_on_parse_error: bool = False,
    ) -> tuple[int, int]:
        """Commit canonical/index/ledger rows together, or roll back the entire batch."""
        if len({record.id for record, _ in records}) != len(records):
            raise SQLiteMemoryError("Duplicate migration input IDs")
        prepared = [
            (record, source_checksum, *self._prepare_record(record))
            for record, source_checksum in records
        ]
        if fail_on_parse_error and any(parsed.warnings for _, _, parsed, _ in prepared):
            raise SQLiteMemoryError("Strict migration refused parser warnings")
        inserted = skipped = 0
        with self._transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM migration_ledger WHERE source_fingerprint!=? LIMIT 1",
                (source_fingerprint,),
            ).fetchone():
                raise SQLiteMemoryError(
                    "Destination belongs to another source snapshot"
                )
            for record, source_checksum, parsed, values in prepared:
                exists = connection.execute(
                    "SELECT 1 FROM memory_record WHERE id=?",
                    (record.id,),
                ).fetchone()
                ledger = connection.execute(
                    "SELECT 1 FROM migration_ledger WHERE source_id=?",
                    (record.id,),
                ).fetchone()
                if exists or ledger:
                    if not resume or not exists or not ledger:
                        raise SQLiteMemoryError(
                            "Unowned or already completed migration record"
                        )
                    self._verify_migrated_record(
                        connection,
                        record,
                        parsed,
                        source_fingerprint,
                        source_checksum,
                    )
                    skipped += 1
                    continue
                self._write_record(connection, record, parsed, values)
                stored = self._record(
                    connection.execute(
                        "SELECT * FROM memory_record WHERE id=?",
                        (record.id,),
                    ).fetchone()
                )
                expected = self.canonical_checksum(record)
                if expected != self.canonical_checksum(stored):
                    raise SQLiteMemoryError(
                        "Canonical projection changed during migration"
                    )
                connection.execute(
                    "INSERT INTO migration_ledger VALUES (?,?,?,?,?,?)",
                    (
                        source_fingerprint,
                        record.id,
                        record.id,
                        source_checksum,
                        expected,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                inserted += 1
        return inserted, skipped

    def _verify_migrated_record(
        self,
        connection,
        record,
        parsed,
        source_fingerprint,
        source_checksum,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM memory_record WHERE id=?",
            (record.id,),
        ).fetchone()
        ledger = connection.execute(
            """SELECT * FROM migration_ledger
            WHERE source_fingerprint=? AND source_id=?""",
            (source_fingerprint, record.id),
        ).fetchone()
        expected = self.canonical_checksum(record)
        if (
            row is None
            or ledger is None
            or ledger["memory_id"] != record.id
            or ledger["source_checksum"] != source_checksum
            or ledger["destination_checksum"] != expected
            or self.canonical_checksum(self._record(row)) != expected
            or row["document_checksum"] != checksum(record.document)
            or row["metadata_checksum"] != checksum(row["metadata_json"])
            or row["structured_json"] != parsed.structured_json
            or tuple(json.loads(row["parse_warnings_json"])) != parsed.warnings
        ):
            raise SQLiteMemoryError("Migration source/destination checksum mismatch")
        unit_rows = connection.execute(
            """SELECT id,memory_id,kind,source_field,text,ordinal,checksum,
            title,important,body,identifiers FROM retrieval_unit WHERE memory_id=?""",
            (record.id,),
        ).fetchall()
        if {tuple(row) for row in unit_rows} != {
            self._unit_values(unit) for unit in parsed.units
        }:
            raise SQLiteMemoryError("Migration retrieval unit mismatch")
        for table, attribute in (("memory_entity", "entities"), ("memory_tag", "tags")):
            actual = connection.execute(
                f"SELECT unit_id,normalized_value,value FROM {table} WHERE memory_id=?",
                (record.id,),
            ).fetchall()
            expected_entries = {
                (unit.id, normalized, value)
                for unit in parsed.units
                for normalized, value in self._exact_values(
                    getattr(unit, attribute)
                ).items()
            }
            if {tuple(row) for row in actual} != expected_entries:
                raise SQLiteMemoryError("Migration exact-index ownership mismatch")

        for table, expected_rows in (
            ("memory_resource", self._resource_rows(record.id, parsed)),
            ("memory_resource_entity", self._link_rows(record.id, parsed)),
        ):
            actual = connection.execute(
                f"SELECT * FROM {table} WHERE memory_id=?", (record.id,)
            ).fetchall()
            if {tuple(row) for row in actual} != set(expected_rows):
                raise SQLiteMemoryError("Migration resource projection mismatch")

    def verify_migration_batch(
        self,
        records: list[tuple[MemoryRecord, str]],
        source_fingerprint: str,
    ) -> int:
        """Compare every canonical field, parser result, derived row and ledger hash."""
        prepared = [
            (record, source_checksum, self.validate_record(record))
            for record, source_checksum in records
        ]
        with self._transaction() as connection:
            for record, source_checksum, parsed in prepared:
                self._verify_migrated_record(
                    connection,
                    record,
                    parsed,
                    source_fingerprint,
                    source_checksum,
                )
        return len(prepared)

    def delete_record(self, memory_id: str, agent_name: str) -> int:
        """Disposable repository deletion, not a production forgetting service."""
        with self._transaction(write=True) as connection:
            return connection.execute(
                "DELETE FROM memory_record WHERE id=? AND agent_name=?",
                (memory_id, agent_name),
            ).rowcount

    @staticmethod
    def _eligibility(query: MemoryQuery) -> tuple[str, list]:
        clauses = [
            "m.agent_name=?",
            "(m.session_id IS NULL OR m.session_id!=?)",
            "m.status='active'",
            "m.superseded_by IS NULL",
        ]
        values: list = [query.agent_name, query.current_session_id]
        if query.from_date:
            clauses.append("m.created_at>=?")
            values.append(
                datetime.combine(query.from_date, time.min, tzinfo=UTC).timestamp()
            )
        if query.to_date:
            clauses.append("m.created_at<=?")
            values.append(
                datetime.combine(query.to_date, time.max, tzinfo=UTC).timestamp()
            )
        if query.memory_ids:
            clauses.append(f"m.id IN ({','.join('?' for _ in query.memory_ids)})")
            values.extend(query.memory_ids)
        if query.as_of is not None:
            clauses.extend(
                [
                    "(m.valid_from IS NULL OR m.valid_from<=?)",
                    "(m.valid_to IS NULL OR m.valid_to>=?)",
                ]
            )
            values.extend([query.as_of, query.as_of])
        return " AND ".join(clauses), values

    def copy_local_document_vectors(
        self, vectors: Mapping[str, Sequence[float]]
    ) -> int:
        """Store already-materialized local vectors. Does not embed or open a network."""
        if not isinstance(vectors, Mapping) or not vectors:
            raise ValueError("Local document vectors must be a nonempty mapping")
        encoded = []
        for memory_id, vector in vectors.items():
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise ValueError("Document vector memory ID must be explicit")
            if (
                not isinstance(vector, Sequence)
                or isinstance(vector, (str, bytes))
                or len(vector) != DOCUMENT_VECTOR_DIMENSIONS
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in vector
                )
            ):
                raise ValueError("Document vector must contain 1024 finite numbers")
            encoded.append(
                (memory_id, struct.pack(f"<{DOCUMENT_VECTOR_DIMENSIONS}f", *vector))
            )
        with self._transaction(write=True) as connection:
            self._ensure_vector_table(connection)
            stored = 0
            for memory_id, blob in encoded:
                if (
                    connection.execute(
                        "SELECT 1 FROM memory_record WHERE id=?", (memory_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError(
                        "Document vector requires an existing memory record"
                    )
                connection.execute(
                    f"INSERT OR REPLACE INTO {VECTOR_TABLE}(memory_id,dimensions,vector) VALUES(?,?,?)",
                    (memory_id, DOCUMENT_VECTOR_DIMENSIONS, blob),
                )
                stored += 1
            if (
                connection.execute("SELECT version FROM schema_version").fetchone()[0]
                != SCHEMA_VERSION
            ):
                raise SQLiteMemoryError("Vector copy must not change schema version")
        return stored

    @staticmethod
    def _ensure_vector_table(connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (VECTOR_TABLE,),
        ).fetchone()
        if existing is None:
            connection.execute(VECTOR_TABLE_SQL)
            return
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({VECTOR_TABLE})")
        }
        if not {"memory_id", "dimensions", "vector"} <= columns:
            raise SQLiteMemoryError("Optional document-vector table is incompatible")

    def candidates(
        self,
        query: MemoryQuery,
        exact_values: tuple[str, ...],
        fts_expression: str,
        *,
        read_connection: sqlite3.Connection | None = None,
    ) -> CandidateBatch:
        """Filter before ranking/limits; exact indexes drive fenced inner joins.

        CROSS JOIN keeps normalized-value lookups ahead of unit/parent payload
        joins without changing eligibility, source-detail selection or ranking.
        FTS and ENTITY materialize ranking keys only, then hydrate capped parents
        within the same snapshot; BM25 stays in its original MATCH context.
        """
        scope, scope_values = self._eligibility(query)
        candidates = []
        with (
            nullcontext(read_connection)
            if read_connection is not None
            else self._transaction()
        ) as connection:
            sources = []
            if query.memory_ids:
                sources.append((CandidateSource.MEMORY_ID, "", "1", [], "m.id", "0"))
            if exact_values:
                placeholders = ",".join("?" for _ in exact_values)
                for source, table, index in (
                    (CandidateSource.ENTITY, "memory_entity", "exact_entity"),
                    (CandidateSource.TAG, "memory_tag", "exact_tag"),
                ):
                    sources.append(
                        (
                            source,
                            f"{table} x INDEXED BY {index}",
                            f"x.normalized_value IN ({placeholders})",
                            list(exact_values),
                            "x.value",
                            "0",
                        )
                    )
            uris = query_resource_uris(query.query)
            if uris:
                placeholders = ",".join("?" for _ in uris)
                sources.append(
                    (
                        CandidateSource.RESOURCE_URI,
                        "memory_resource x INDEXED BY resource_uri",
                        f"x.normalized_uri IN ({placeholders})",
                        list(uris),
                        "x.uri",
                        "0",
                    )
                )
            if fts_expression:
                sources.append(
                    (
                        CandidateSource.FTS,
                        "JOIN memory_fts ON memory_fts.rowid=u.rowid",
                        "memory_fts MATCH ?",
                        [fts_expression],
                        "'Unicode FTS5 field match'",
                        "bm25(memory_fts,6.0,4.0,1.0,8.0)",
                    )
                )
            for source, join, predicate, values, detail, score in sources:
                if source in (
                    CandidateSource.ENTITY,
                    CandidateSource.TAG,
                    CandidateSource.RESOURCE_URI,
                ):
                    unit_column = (
                        "resource_unit_id"
                        if source == CandidateSource.RESOURCE_URI
                        else "unit_id"
                    )
                    from_clause = f"""{join}
                    CROSS JOIN retrieval_unit u ON u.id=x.{unit_column}
                    CROSS JOIN memory_record m ON m.id=u.memory_id"""
                else:
                    from_clause = f"retrieval_unit u JOIN memory_record m ON m.id=u.memory_id {join}"
                payload = """m.*,u.id AS unit_id,u.kind AS unit_kind,u.source_field,
                    u.text AS unit_text,u.ordinal,u.checksum AS unit_checksum"""
                late_hydration = source in (CandidateSource.FTS, CandidateSource.ENTITY)
                projection = (
                    "m.id,u.id AS unit_id,u.source_field,u.ordinal"
                    if late_hydration
                    else payload
                )
                sql = f"""WITH matches AS MATERIALIZED (
                    SELECT {projection},
                    {detail} AS source_detail,{score} AS source_score
                    FROM {from_clause} WHERE {scope} AND ({predicate})
                ), ranked AS (
                    SELECT *,row_number() OVER (
                        PARTITION BY id ORDER BY source_score,
                        CASE WHEN source_field='FLOW/OUTCOME' THEN 0 ELSE 1 END,ordinal,unit_id,source_detail
                    ) AS parent_rank FROM matches
                )"""
                if late_hydration:
                    sql += f""", selected AS MATERIALIZED (
                        SELECT * FROM ranked WHERE parent_rank=1
                        ORDER BY source_score,id,unit_id LIMIT ?
                    ) SELECT {payload},s.source_detail,s.source_score
                    FROM selected s
                    JOIN memory_record m ON m.id=s.id
                    JOIN retrieval_unit u ON u.id=s.unit_id
                    ORDER BY s.source_score,s.id,s.unit_id"""
                else:
                    sql += """ SELECT * FROM ranked WHERE parent_rank=1
                ORDER BY source_score,id,unit_id LIMIT ?"""
                rows = connection.execute(
                    sql, (*scope_values, *values, query.candidate_limit)
                ).fetchall()
                for rank, row in enumerate(rows, 1):
                    candidates.append(
                        self._candidate(row, source, rank, row["source_detail"])
                    )
            candidates.extend(
                self._semantic_candidates(connection, query, scope, scope_values)
            )
            conflicts = connection.execute(
                f"""SELECT m.subject_key FROM memory_record m WHERE {scope}
                AND m.subject_key IS NOT NULL AND m.subject_key!=''
                GROUP BY m.subject_key HAVING count(DISTINCT m.document_checksum)>1""",
                scope_values,
            ).fetchall()
        return CandidateBatch(tuple(candidates), frozenset(row[0] for row in conflicts))

    def resource_evidence(
        self,
        query: MemoryQuery,
        memory_ids: list[str],
        *,
        read_connection: sqlite3.Connection | None = None,
    ) -> dict[str, tuple[ResourceEvidence, ...]]:
        """One scoped batch for selected parents; no resource joins in ranking paths."""
        if not memory_ids:
            return {}
        scope, values = self._eligibility(query)
        placeholders = ",".join("?" for _ in memory_ids)
        with (
            nullcontext(read_connection)
            if read_connection is not None
            else self._transaction()
        ) as connection:
            rows = connection.execute(
                f"""SELECT r.memory_id,r.uri,r.display_name,e.value AS entity_name,l.relation
                FROM memory_resource r JOIN memory_record m ON m.id=r.memory_id
                LEFT JOIN memory_resource_entity l ON l.memory_id=r.memory_id AND l.resource_id=r.resource_id
                LEFT JOIN memory_entity e ON e.memory_id=l.memory_id AND e.unit_id=l.entity_unit_id
                    AND e.rowid=(SELECT rowid FROM memory_entity WHERE unit_id=l.entity_unit_id ORDER BY rowid LIMIT 1)
                WHERE {scope} AND m.id IN ({placeholders})
                ORDER BY r.memory_id,r.uri,e.value,l.relation""",
                (*values, *memory_ids),
            ).fetchall()
        result = {}
        for row in rows:
            evidence = ResourceEvidence(
                row["uri"], row["display_name"], row["entity_name"], row["relation"]
            )
            entries = result.setdefault(row["memory_id"], [])
            if evidence not in entries:
                entries.append(evidence)
        return {key: tuple(items) for key, items in result.items()}

    def _semantic_candidates(
        self,
        connection: sqlite3.Connection,
        query: MemoryQuery,
        scope: str,
        scope_values: list,
    ) -> list[MemoryCandidate]:
        if query.query_vector is None or query.semantic_policy.max_distance is None:
            return []
        query_norm = math.hypot(*query.query_vector)
        if not math.isfinite(query_norm) or query_norm == 0:
            return []
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (VECTOR_TABLE,),
            ).fetchone()
            is None
        ):
            return []
        rows = connection.execute(
            f"""SELECT m.id,v.vector FROM {VECTOR_TABLE} v
            JOIN memory_record m ON m.id=v.memory_id WHERE {scope}
            AND EXISTS (SELECT 1 FROM retrieval_unit u WHERE u.memory_id=m.id)""",
            scope_values,
        )

        def qualified():
            for row in rows:
                distance = self._cosine_distance(
                    query.query_vector, row["vector"], query_norm
                )
                if query.semantic_policy.qualifies(distance):
                    yield distance, row["id"]

        selected = heapq.nsmallest(query.candidate_limit, qualified())
        candidates = []
        for rank, (distance, memory_id) in enumerate(selected, 1):
            row = connection.execute(
                """SELECT m.*,u.id AS unit_id,u.kind AS unit_kind,u.source_field,
                u.text AS unit_text,u.ordinal,u.checksum AS unit_checksum
                FROM memory_record m JOIN retrieval_unit u ON u.id=(
                    SELECT id FROM retrieval_unit WHERE memory_id=m.id
                    ORDER BY CASE WHEN source_field='FLOW/OUTCOME' THEN 0 ELSE 1 END,
                    ordinal,id LIMIT 1
                ) WHERE m.id=?""",
                (memory_id,),
            ).fetchone()
            candidates.append(
                self._candidate(
                    row,
                    CandidateSource.SEMANTIC,
                    rank,
                    f"cosine_distance={distance:.6f}",
                    distance,
                )
            )
        return candidates

    @staticmethod
    def _cosine_distance(
        query_vector: tuple[float, ...], blob: bytes, query_norm: float
    ) -> float:
        width = DOCUMENT_VECTOR_DIMENSIONS * 4
        if not isinstance(blob, bytes) or len(blob) != width:
            return math.inf
        stored = struct.unpack(f"<{DOCUMENT_VECTOR_DIMENSIONS}f", blob)
        if not all(math.isfinite(value) for value in stored):
            return math.inf
        stored_norm = math.hypot(*stored)
        if stored_norm == 0:
            return math.inf
        similarity = math.fsum(
            (query_value / query_norm) * (stored_value / stored_norm)
            for query_value, stored_value in zip(query_vector, stored, strict=True)
        )
        return 1.0 - max(-1.0, min(1.0, similarity))

    def _candidate(
        self,
        row: sqlite3.Row,
        source: CandidateSource,
        rank: int,
        detail: str,
        distance: float | None = None,
    ) -> MemoryCandidate:
        structured = json.loads(row["structured_json"] or "{}")
        return MemoryCandidate(
            record=self._record(row),
            unit=RetrievalUnit(
                row["unit_id"],
                row["id"],
                row["unit_kind"],
                row["source_field"],
                row["unit_text"],
                row["ordinal"],
                row["unit_checksum"],
            ),
            reason=MatchReason(source, rank, detail, distance),
            parse_warnings=tuple(json.loads(row["parse_warnings_json"])),
            source_headline=(structured.get("HEAD") or [None])[0],
            has_resources=bool(structured.get("RESOURCES/RESOURCE")),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            session_id=row["session_id"],
            document=row["document"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]),
            cwd=row["cwd"],
            header=row["header"],
            status=row["status"],
            superseded_by=row["superseded_by"],
            subject_key=row["subject_key"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            evolved_at=row["evolved_at"],
            document_format=row["document_format"],
            kinds=tuple(json.loads(row["kinds_json"])),
        )

    def get_record(self, memory_id: str, agent_name: str) -> MemoryRecord | None:
        """Repository inspection for setup/verification, not a retrieval bypass tool."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_record WHERE id=? AND agent_name=?",
                (memory_id, agent_name),
            ).fetchone()
            return self._record(row) if row else None

    def get_memory_documents(
        self, memory_ids: list[str], agent_name: str
    ) -> dict[str, str]:
        """Expand already-eligible IDs in requested order within one read snapshot.

        Agent scope is retained; search owns lifecycle, session and date eligibility.
        This performs no search or ranking and never rewrites canonical IDs.
        """
        if not memory_ids or not isinstance(agent_name, str) or not agent_name.strip():
            return {}
        requested = dict.fromkeys(memory_ids)
        documents: dict[str, str] = {}
        with self._transaction() as connection:
            for memory_id in requested:
                row = connection.execute(
                    "SELECT document FROM memory_record WHERE id=? AND agent_name=?",
                    (memory_id, agent_name),
                ).fetchone()
                if row is not None:
                    documents[memory_id] = row["document"]
        return documents

    def check_integrity(self) -> None:
        """Check SQLite, foreign keys and the real FTS index, not just FTS row counts."""
        with self._transaction(write=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SQLiteMemoryError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise SQLiteMemoryError("Memory foreign key check failed")
            connection.execute(
                "INSERT INTO memory_fts(memory_fts,rank) VALUES('integrity-check',1)"
            )

    def list_headers(
        self,
        agent_name: str,
        exclude_session_id: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 20,
    ) -> list[str]:
        """Newest-first header lines for one agent, without CWD (MEM-503)."""
        clauses = [
            "agent_name=?",
            "status='active'",
            "superseded_by IS NULL",
            "header IS NOT NULL",
        ]
        values: list = [agent_name]
        if exclude_session_id:
            clauses.append("(session_id IS NULL OR session_id!=?)")
            values.append(exclude_session_id)
        if from_ts is not None:
            clauses.append("created_at>=?")
            values.append(from_ts)
        if to_ts is not None:
            clauses.append("created_at<=?")
            values.append(to_ts)
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT header, created_at FROM memory_record WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        headers = []
        for row in rows:
            if row["created_at"] and row["created_at"] > 0:
                try:
                    stamp = datetime.fromtimestamp(row["created_at"], tz=UTC).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                except (OverflowError, OSError, ValueError):
                    stamp = "unknown"
                headers.append(f"{row['header']} ({stamp})")
            else:
                headers.append(row["header"])
        return headers

    def agent_records(
        self,
        agent_name: str,
        max_items: int = 100,
        exclude_session_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Newest-first corpus records for one agent, excluding evolved memories (MEM-504)."""
        clauses = [
            "agent_name=?",
            "status='active'",
            "superseded_by IS NULL",
            "evolved_at IS NULL",
        ]
        values: list = [agent_name]
        if exclude_session_id:
            clauses.append("(session_id IS NULL OR session_id!=?)")
            values.append(exclude_session_id)
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM memory_record WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*values, max_items),
            ).fetchall()
        return [self._record(row) for row in rows]

    def mark_records_evolved(
        self, memory_ids: list[str], agent_name: str, evolved_at: str
    ) -> int:
        """Stamp evolved_at on owned records; returns the number marked (MEM-504)."""
        unique = list(
            dict.fromkeys(
                str(memory_id) for memory_id in memory_ids if str(memory_id).strip()
            )
        )
        if not unique:
            return 0
        placeholders = ",".join("?" for _ in unique)
        with self._transaction(write=True) as connection:
            return connection.execute(
                f"UPDATE memory_record SET evolved_at=? WHERE agent_name=? AND id IN ({placeholders})",
                (evolved_at, agent_name, *unique),
            ).rowcount

    def delete_records(self, memory_ids: list[str], agent_name: str) -> int:
        """Physically remove owned records and every derived row in one transaction (MEM-505)."""
        unique = list(
            dict.fromkeys(
                str(memory_id) for memory_id in memory_ids if str(memory_id).strip()
            )
        )
        if not unique:
            return 0
        placeholders = ",".join("?" for _ in unique)
        with self._transaction(write=True) as connection:
            return connection.execute(
                f"DELETE FROM memory_record WHERE agent_name=? AND id IN ({placeholders})",
                (agent_name, *unique),
            ).rowcount

    def delete_records_by_session(
        self, session_id: str, agent_name: str | None = None
    ) -> int:
        """Physically remove every record of one conversation session (MEM-505)."""
        clauses = ["session_id=?"]
        values: list = [session_id]
        if agent_name:
            clauses.append("agent_name=?")
            values.append(agent_name)
        with self._transaction(write=True) as connection:
            return connection.execute(
                f"DELETE FROM memory_record WHERE {' AND '.join(clauses)}",
                values,
            ).rowcount

    def delete_records_matching(
        self,
        fts_expression: str,
        agent_name: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 100,
    ) -> int:
        """Physically remove agent-owned records whose units match an FTS expression (MEM-505)."""
        if not fts_expression.strip():
            return 0
        clauses = ["m.agent_name=?", "m.status='active'", "m.superseded_by IS NULL"]
        values: list = [fts_expression, agent_name]
        if from_ts is not None:
            clauses.append("m.created_at>=?")
            values.append(from_ts)
        if to_ts is not None:
            clauses.append("m.created_at<=?")
            values.append(to_ts)
        with self._transaction(write=True) as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT m.id FROM memory_record m
                JOIN retrieval_unit u ON u.memory_id=m.id
                JOIN memory_fts ON memory_fts.rowid=u.rowid
                WHERE memory_fts MATCH ? AND {" AND ".join(clauses)}
                ORDER BY m.created_at DESC LIMIT ?""",
                (*values, limit),
            ).fetchall()
            ids = [row[0] for row in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            return connection.execute(
                f"DELETE FROM memory_record WHERE id IN ({placeholders})",
                ids,
            ).rowcount

    def delete_records_older_than(
        self, cutoff_ts: float, agent_name: str | None = None
    ) -> int:
        """Explicit retention: remove records created before a cutoff (MEM-506)."""
        clauses = ["created_at<?"]
        values: list = [cutoff_ts]
        if agent_name:
            clauses.append("agent_name=?")
            values.append(agent_name)
        with self._transaction(write=True) as connection:
            return connection.execute(
                f"DELETE FROM memory_record WHERE {' AND '.join(clauses)}",
                values,
            ).rowcount

    def consolidation_candidates(
        self,
        memory_id: str,
        agent_name: str,
        fts_expression: str,
        limit: int = 5,
    ) -> list[dict]:
        """Bounded exact/entity/FTS consolidation candidates; vectors are never used (MEM-507)."""
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT session_id FROM memory_record WHERE id=? AND agent_name=?",
                (memory_id, agent_name),
            ).fetchone()
            if current is None:
                return []
            exclude_session = current["session_id"]
            candidates: dict[str, dict] = {}
            for table in ("memory_entity", "memory_tag"):
                if len(candidates) >= limit:
                    break
                rows = connection.execute(
                    f"""SELECT DISTINCT m.id, m.document, m.header FROM {table} x
                    JOIN retrieval_unit cu ON cu.id=x.unit_id
                    JOIN {table} e ON e.normalized_value=x.normalized_value
                    JOIN retrieval_unit u ON u.id=e.unit_id
                    JOIN memory_record m ON m.id=u.memory_id
                    WHERE cu.memory_id=? AND m.agent_name=? AND m.id!=?
                    AND (m.session_id IS NULL OR m.session_id!=?)
                    AND m.status='active' AND m.superseded_by IS NULL
                    ORDER BY m.created_at DESC LIMIT ?""",
                    (memory_id, agent_name, memory_id, exclude_session, limit),
                ).fetchall()
                for row in rows:
                    candidates.setdefault(
                        row["id"],
                        {
                            "id": row["id"],
                            "document": row["document"],
                            "header": row["header"],
                            "source": "exact",
                        },
                    )
            for candidate in candidates.values():
                candidate["evidence"] = self._unit_evidence(connection, candidate["id"])
            if fts_expression.strip() and len(candidates) < limit:
                rows = connection.execute(
                    """SELECT DISTINCT m.id, m.document, m.header FROM retrieval_unit u
                    JOIN memory_fts ON memory_fts.rowid=u.rowid
                    JOIN memory_record m ON m.id=u.memory_id
                    WHERE memory_fts MATCH ? AND m.agent_name=? AND m.id!=?
                    AND (m.session_id IS NULL OR m.session_id!=?)
                    AND m.status='active' AND m.superseded_by IS NULL
                    ORDER BY bm25(memory_fts,6.0,4.0,1.0,8.0) LIMIT ?""",
                    (
                        fts_expression,
                        agent_name,
                        memory_id,
                        exclude_session,
                        limit - len(candidates),
                    ),
                ).fetchall()
                for row in rows:
                    candidates.setdefault(
                        row["id"],
                        {
                            "id": row["id"],
                            "document": row["document"],
                            "header": row["header"],
                            "source": "fts",
                        },
                    )
            for candidate in candidates.values():
                if "evidence" not in candidate:
                    candidate["evidence"] = self._unit_evidence(
                        connection, candidate["id"]
                    )
            return list(candidates.values())[:limit]

    def _unit_evidence(self, connection, memory_id: str) -> str:
        """Bounded concatenation of one record's unit texts for lexical gating."""
        rows = connection.execute(
            "SELECT text FROM retrieval_unit WHERE memory_id=? ORDER BY id",
            (memory_id,),
        ).fetchall()
        return " ".join(row[0] for row in rows)[:2000]

    def latest_session_record(self, session_id: str) -> MemoryRecord | None:
        """Newest active record of one conversation session, across agents."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_record WHERE session_id=? AND status='active' "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return self._record(row) if row else None

    @staticmethod
    def _family_entry(row) -> provenance.FamilyRecord:
        try:
            metadata = json.loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                raise TypeError
        except (ValueError, TypeError) as error:
            raise provenance.MemoryFamilyError(
                "Invalid canonical family metadata"
            ) from error
        metadata = {
            **metadata,
            "status": row["status"],
            "superseded_by": row["superseded_by"],
        }
        return provenance.FamilyRecord(
            row["id"], row["agent_name"], row["session_id"], metadata, row["updated_at"]
        )

    def _family_snapshot(self, connection, agent_name: str) -> provenance.FamilyIndex:
        rows = connection.execute(
            "SELECT id,agent_name,session_id,status,superseded_by,metadata_json,updated_at "
            "FROM memory_record WHERE agent_name=? ORDER BY id LIMIT ?",
            (agent_name, provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS + 1),
        ).fetchall()
        return provenance.FamilyIndex(
            [self._family_entry(row) for row in rows], agent_name
        )

    def persist_turn(self, record: MemoryRecord, operation_id: str) -> str:
        """Resolve and publish one exact-session identity atomically, preserving sources."""
        with self._transaction(write=True) as connection:
            rows = connection.execute(
                "SELECT * FROM memory_record WHERE agent_name=? AND session_id=? LIMIT ?",
                (
                    record.agent_name,
                    record.session_id,
                    provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS + 1,
                ),
            ).fetchall()
            if len(rows) > provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS:
                raise provenance.MemoryFamilyError(
                    "Persistence identity scan limit exceeded"
                )
            memory_id = provenance.choose_identity(
                record.id,
                operation_id,
                record.agent_name,
                record.session_id,
                [self._family_entry(row) for row in rows],
            )
            existing_row = connection.execute(
                "SELECT * FROM memory_record WHERE id=?", (memory_id,)
            ).fetchone()
            if existing_row is not None:
                existing = self._record(existing_row)
                if (
                    existing.agent_name != record.agent_name
                    or existing.session_id != record.session_id
                    or existing.status != "active"
                    or existing.superseded_by is not None
                ):
                    raise provenance.MemoryFamilyError("Persistence identity collision")
                provenance.source_ids(existing.metadata)
                record = replace(
                    record,
                    created_at=existing.created_at,
                    metadata=provenance.merge_metadata(
                        existing.metadata, record.metadata
                    ),
                )
            record = replace(record, id=memory_id)
            parsed, values = self._prepare_record(record)
            self._write_record(connection, record, parsed, values)
            return memory_id

    def apply_consolidation(
        self,
        memory_id: str,
        agent_name: str,
        merged_document: str | None,
        ids_to_supersede: list[str],
    ) -> None:
        """Atomically publish the active target and supersede, never rewrite, sources."""
        if not ids_to_supersede:
            return
        with self._transaction(write=True) as connection:
            index = self._family_snapshot(connection, agent_name)
            metadata = provenance.consolidation_metadata(
                index, memory_id, ids_to_supersede
            )
            row = connection.execute(
                "SELECT * FROM memory_record WHERE id=? AND agent_name=?",
                (memory_id, agent_name),
            ).fetchone()
            target = self._record(row)
            target = replace(
                target,
                document=merged_document
                if merged_document is not None
                else target.document,
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if key not in {"status", "superseded_by"}
                },
                updated_at=datetime.now(UTC).timestamp(),
            )
            parsed, values = self._prepare_record(target)
            self._write_record(connection, target, parsed, values)
            connection.executemany(
                "UPDATE memory_record SET status='superseded',superseded_by=? WHERE id=? AND agent_name=?",
                ((memory_id, key, agent_name) for key in ids_to_supersede),
            )

    def resolve_memory_families(
        self, memory_ids: list[str], agent_name: str
    ) -> tuple[str, ...]:
        """Internal bounded family access, never a search eligibility bypass."""
        with self._transaction() as connection:
            return self._family_snapshot(connection, agent_name).resolve(memory_ids)

    def delete_memory_families(
        self,
        *,
        agent_name: str | None = None,
        memory_ids: list[str] | None = None,
        session_id: str | None = None,
        fts_expression: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        cutoff_ts: float | None = None,
        topic_seed_limit: int = 100,
    ) -> int:
        """Resolve selected families atomically; topic seeds are bounded before expansion."""
        from loguru import logger

        with self._transaction(write=True) as connection:
            if agent_name is not None:
                agents = [agent_name]
            elif session_id is not None:
                agents = [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT agent_name FROM memory_record WHERE session_id=?",
                        (session_id,),
                    )
                ]
            else:
                agents = [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT agent_name FROM memory_record"
                    )
                ]

            resolved: list[tuple[str, str]] = []
            for agent in agents:
                if memory_ids is not None and not any(
                    connection.execute(
                        "SELECT 1 FROM memory_record WHERE id=? AND agent_name=?",
                        (key, agent),
                    ).fetchone()
                    for key in dict.fromkeys(memory_ids)
                ):
                    continue
                try:
                    index = self._family_snapshot(connection, agent)
                except provenance.MemoryFamilyError:
                    if cutoff_ts is None:
                        raise
                    logger.warning(
                        "Retention skipped invalid or oversized agent snapshot"
                    )
                    continue
                if cutoff_ts is not None:
                    ids = index.expired(cutoff_ts)
                else:
                    if memory_ids is not None:
                        seeds = memory_ids
                    elif session_id is not None:
                        seeds = [
                            key
                            for key, row in index.records.items()
                            if row.session == session_id
                        ]
                    elif fts_expression and fts_expression.strip():
                        if (
                            type(topic_seed_limit) is not int
                            or not 1 <= topic_seed_limit <= 100
                        ):
                            raise provenance.MemoryFamilyError(
                                "Invalid topic seed limit"
                            )
                        clauses = ["m.agent_name=?"]
                        values: list = [fts_expression, agent]
                        if from_ts is not None:
                            clauses.append("m.created_at>=?")
                            values.append(from_ts)
                        if to_ts is not None:
                            clauses.append("m.created_at<=?")
                            values.append(to_ts)
                        values.append(topic_seed_limit)
                        seeds = [
                            row[0]
                            for row in connection.execute(
                                "SELECT DISTINCT m.id FROM memory_record m JOIN retrieval_unit u ON u.memory_id=m.id "
                                "JOIN memory_fts ON memory_fts.rowid=u.rowid WHERE memory_fts MATCH ? AND "
                                + " AND ".join(clauses)
                                + " ORDER BY m.created_at DESC,m.id ASC LIMIT ?",
                                values,
                            )
                        ]
                    else:
                        seeds = []
                    ids = index.resolve(seeds)
                resolved.extend((agent, key) for key in ids)
            connection.executemany(
                "DELETE FROM memory_record WHERE agent_name=? AND id=?", resolved
            )
            return len(resolved)

    def mark_active_roots_evolved(
        self, memory_ids: list[str], agent_name: str, evolved_at: str
    ) -> int:
        """Production evolution never stamps a preserved source."""
        with self._transaction(write=True) as connection:
            index = self._family_snapshot(connection, agent_name)
            count = 0
            for key in dict.fromkeys(memory_ids):
                if key not in index.records:
                    continue
                try:
                    index.require_roots([key])
                except provenance.MemoryFamilyError:
                    continue
                count += connection.execute(
                    "UPDATE memory_record SET evolved_at=? WHERE id=? AND agent_name=? AND status='active' AND superseded_by IS NULL",
                    (evolved_at, key, agent_name),
                ).rowcount
            return count

    def active_session_record(
        self, session_id: str, agent_name: str
    ) -> MemoryRecord | None:
        """Production context loading never crosses agents or selects a source."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_record WHERE session_id=? AND agent_name=? AND status='active' AND superseded_by IS NULL LIMIT 2",
                (session_id, agent_name),
            ).fetchall()
            if len(rows) > 1:
                raise provenance.MemoryFamilyError("Multiple active session contexts")
            return self._record(rows[0]) if rows else None

    def check_read_only_integrity(self) -> None:
        """Check SQLite/FK integrity without writes; the migrator checks FTS postings."""
        with self._transaction() as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SQLiteMemoryError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise SQLiteMemoryError("Memory foreign key check failed")
