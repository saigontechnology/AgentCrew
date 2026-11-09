"""File-backed SQLite prototype with operation-owned connections and explicit SQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime, time
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3
import struct
from typing import Iterator

from .record_parser import checksum, derive_memory_units, normalize_text
from .types import (
    DOCUMENT_VECTOR_DIMENSIONS, CandidateBatch, CandidateSource, MatchReason,
    MemoryCandidate, MemoryQuery, MemoryRecord, ParsedMemory, RetrievalUnit,
)


SCHEMA_VERSION = 1
VECTOR_TABLE = "memory_document_vector"
VECTOR_TABLE_SQL = f"""CREATE TABLE {VECTOR_TABLE} (
    memory_id TEXT PRIMARY KEY REFERENCES memory_record(id) ON DELETE CASCADE,
    dimensions INTEGER NOT NULL CHECK(dimensions={DOCUMENT_VECTOR_DIMENSIONS}),
    vector BLOB NOT NULL
)"""
SCHEMA = (
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
RECORD_COLUMNS = (
    "id", "agent_name", "session_id", "created_at", "updated_at", "cwd", "header",
    "status", "superseded_by", "subject_key", "valid_from", "valid_to", "evolved_at",
    "document_format", "document", "document_checksum", "metadata_json",
    "metadata_checksum", "structured_json", "kinds_json", "parse_warnings_json",
)


class SQLiteMemoryError(RuntimeError):
    """Unsupported capability or incompatible schema; never repaired destructively."""


class SQLiteMemoryRepository:
    """Connections are created, used and closed on each calling thread.

    No live conversation state or connection is stored on this object. A search
    obtains all candidate sources and conflict markers within one read snapshot.
    Write methods support explicit setup and offline migration. They are not
    wired into automatic production memory storage.
    """

    def __init__(
        self, path: str | Path, *, read_only: bool = False,
        busy_timeout_ms: int = 2000,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("Use a disposable file for operation-owned connections")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
            raise ValueError("Busy timeout must be within 1..30000 ms")
        self.path = Path(path).resolve()
        self.read_only = read_only
        self.busy_timeout_ms = busy_timeout_ms
        self.check_fts5()
        if read_only:
            with self._transaction() as connection:
                self._validate_schema(connection)
        else:
            with self._transaction(write=True, create=True) as connection:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if not tables and connection.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in SCHEMA:
                        connection.execute(statement)
                    connection.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._validate_schema(connection)
            with closing(self._connect(write=True)) as connection:
                if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                    raise SQLiteMemoryError("SQLite memory requires file-backed WAL support")

    @staticmethod
    def check_fts5() -> None:
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise SQLiteMemoryError("SQLite 3.35+ is required for materialized candidate queries")
        try:
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        except sqlite3.OperationalError as error:
            raise SQLiteMemoryError("SQLite memory requires built-in FTS5 support") from error

    def _connect(self, *, write: bool = False, create: bool = False) -> sqlite3.Connection:
        if write and self.read_only:
            raise SQLiteMemoryError("Repository was opened read-only")
        mode = "rwc" if create else "rw" if write else "ro"
        uri = self.path.as_uri() + f"?mode={mode}"
        connection = sqlite3.connect(
            uri, uri=True, timeout=self.busy_timeout_ms / 1000,
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
    def _transaction(self, *, write: bool = False, create: bool = False) -> Iterator[sqlite3.Connection]:
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
    def _validate_schema(connection: sqlite3.Connection) -> None:
        objects = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        required = {
            "schema_version", "memory_record", "retrieval_unit", "memory_entity",
            "memory_tag", "migration_ledger", "memory_fts",
            "unit_insert", "unit_delete", "unit_update",
        }
        if not required <= objects:
            raise SQLiteMemoryError("Unrecognized or incomplete SQLite memory schema")
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_version")]
        if versions != [SCHEMA_VERSION] or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise SQLiteMemoryError("Unsupported SQLite memory schema version")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_record)")}
        if not set(RECORD_COLUMNS) <= columns:
            raise SQLiteMemoryError("Incomplete canonical memory schema")

    def upsert_record(self, record: MemoryRecord) -> ParsedMemory:
        """Atomically replace derived indexes without rewriting canonical input."""
        parsed, values = self._prepare_record(record)
        with self._transaction(write=True) as connection:
            self._write_record(connection, record, parsed, values)
        return parsed

    @staticmethod
    def _prepare_record(record: MemoryRecord) -> tuple[ParsedMemory, tuple]:
        if any(not isinstance(value, str) or not value.strip() for value in (
            record.id, record.agent_name,
        )):
            raise ValueError("Canonical ID and agent must be explicit")
        if record.session_id is not None and not isinstance(record.session_id, str):
            raise ValueError("Canonical legacy session must be text or null")
        for value in (record.cwd, record.header, record.subject_key):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Optional canonical labels must be nonempty text or null")
        if not isinstance(record.kinds, tuple) or any(not isinstance(kind, str) or not kind for kind in record.kinds):
            raise ValueError("Explicit kind labels must be a tuple of nonempty strings")
        if record.created_at is None or record.updated_at is None:
            raise ValueError("Canonical creation/update times are required")
        if not isinstance(record.document, str) or not isinstance(record.metadata, dict):
            raise ValueError("Canonical document and metadata must be text and a JSON object")
        for timestamp in (record.created_at, record.updated_at, record.valid_from, record.valid_to):
            if timestamp is not None and (type(timestamp) not in (int, float) or not math.isfinite(timestamp)):
                raise ValueError("Memory times must be finite Unix timestamps")
            if timestamp is not None:
                try:
                    datetime.fromtimestamp(timestamp, UTC)
                except (OverflowError, OSError, ValueError) as error:
                    raise ValueError("Memory time is outside the supported calendar") from error
        metadata_json = json.dumps(record.metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        parsed = derive_memory_units(record)
        values = (
            record.id, record.agent_name, record.session_id, record.created_at,
            record.updated_at, record.cwd, record.header, record.status,
            record.superseded_by, record.subject_key, record.valid_from, record.valid_to,
            record.evolved_at, record.document_format, record.document,
            checksum(record.document), metadata_json, checksum(metadata_json),
            parsed.structured_json, json.dumps(record.kinds), json.dumps(parsed.warnings),
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
        connection.execute("DELETE FROM retrieval_unit WHERE memory_id=?", (record.id,))
        for unit in parsed.units:
            connection.execute(
                """INSERT INTO retrieval_unit
                (id,memory_id,kind,source_field,text,ordinal,checksum,title,important,body,identifiers)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                SQLiteMemoryRepository._unit_values(unit),
            )
            for table, entries in (("memory_entity", unit.entities), ("memory_tag", unit.tags)):
                unique = SQLiteMemoryRepository._exact_values(entries)
                connection.executemany(
                    f"INSERT INTO {table}(memory_id,unit_id,value,normalized_value) VALUES (?,?,?,?)",
                    ((record.id, unit.id, value, normalized) for normalized, value in unique.items()),
                )

    @staticmethod
    def _unit_values(unit: RetrievalUnit) -> tuple:
        bucket = (
            "title" if unit.source_field == "HEAD" else
            "identifiers" if unit.kind in {"entity", "tag", "resource"} else
            "important" if unit.source_field in {
                "FLOW/OUTCOME", "INSIGHTS/INSIGHT", "CONVERSATION_NOTES/NOTE",
            } else "body"
        )
        return (
            unit.id, unit.memory_id, unit.kind, unit.source_field, unit.text,
            unit.ordinal, unit.checksum,
            *(unit.text if bucket == name else "" for name in (
                "title", "important", "body", "identifiers",
            )),
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
        return checksum(json.dumps(
            values, sort_keys=True, ensure_ascii=False, allow_nan=False,
        ))

    def migration_counts(self, source_fingerprint: str) -> dict[str, int]:
        """Return aggregate ownership counts without exposing IDs or values."""
        with self._transaction() as connection:
            result = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "memory_record", "migration_ledger", "retrieval_unit",
                    "memory_entity", "memory_tag", "memory_fts_docsize",
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
        self, records: list[tuple[MemoryRecord, str]], source_fingerprint: str,
        *, resume: bool = False, fail_on_parse_error: bool = False,
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
                raise SQLiteMemoryError("Destination belongs to another source snapshot")
            for record, source_checksum, parsed, values in prepared:
                exists = connection.execute(
                    "SELECT 1 FROM memory_record WHERE id=?", (record.id,),
                ).fetchone()
                ledger = connection.execute(
                    "SELECT 1 FROM migration_ledger WHERE source_id=?",
                    (record.id,),
                ).fetchone()
                if exists or ledger:
                    if not resume or not exists or not ledger:
                        raise SQLiteMemoryError("Unowned or already completed migration record")
                    self._verify_migrated_record(
                        connection, record, parsed, source_fingerprint, source_checksum,
                    )
                    skipped += 1
                    continue
                self._write_record(connection, record, parsed, values)
                stored = self._record(connection.execute(
                    "SELECT * FROM memory_record WHERE id=?", (record.id,),
                ).fetchone())
                expected = self.canonical_checksum(record)
                if expected != self.canonical_checksum(stored):
                    raise SQLiteMemoryError("Canonical projection changed during migration")
                connection.execute(
                    "INSERT INTO migration_ledger VALUES (?,?,?,?,?,?)",
                    (source_fingerprint, record.id, record.id, source_checksum,
                     expected, datetime.now(UTC).isoformat()),
                )
                inserted += 1
        return inserted, skipped

    def _verify_migrated_record(
        self, connection, record, parsed, source_fingerprint, source_checksum,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM memory_record WHERE id=?", (record.id,),
        ).fetchone()
        ledger = connection.execute(
            """SELECT * FROM migration_ledger
            WHERE source_fingerprint=? AND source_id=?""",
            (source_fingerprint, record.id),
        ).fetchone()
        expected = self.canonical_checksum(record)
        if (
            row is None or ledger is None or ledger["memory_id"] != record.id
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
                for normalized, value in self._exact_values(getattr(unit, attribute)).items()
            }
            if {tuple(row) for row in actual} != expected_entries:
                raise SQLiteMemoryError("Migration exact-index ownership mismatch")

    def verify_migration_batch(
        self, records: list[tuple[MemoryRecord, str]], source_fingerprint: str,
    ) -> int:
        """Compare every canonical field, parser result, derived row and ledger hash."""
        prepared = [
            (record, source_checksum, self.validate_record(record))
            for record, source_checksum in records
        ]
        with self._transaction() as connection:
            for record, source_checksum, parsed in prepared:
                self._verify_migrated_record(
                    connection, record, parsed, source_fingerprint, source_checksum,
                )
        return len(prepared)

    def delete_record(self, memory_id: str, agent_name: str) -> int:
        """Disposable repository deletion, not a production forgetting service."""
        with self._transaction(write=True) as connection:
            return connection.execute(
                "DELETE FROM memory_record WHERE id=? AND agent_name=?", (memory_id, agent_name),
            ).rowcount

    @staticmethod
    def _eligibility(query: MemoryQuery) -> tuple[str, list]:
        clauses = ["m.agent_name=?", "(m.session_id IS NULL OR m.session_id!=?)", "m.status='active'", "m.superseded_by IS NULL"]
        values: list = [query.agent_name, query.current_session_id]
        if query.from_date:
            clauses.append("m.created_at>=?")
            values.append(datetime.combine(query.from_date, time.min, tzinfo=UTC).timestamp())
        if query.to_date:
            clauses.append("m.created_at<=?")
            values.append(datetime.combine(query.to_date, time.max, tzinfo=UTC).timestamp())
        if query.memory_ids:
            clauses.append(f"m.id IN ({','.join('?' for _ in query.memory_ids)})")
            values.extend(query.memory_ids)
        if query.as_of is not None:
            clauses.extend(["(m.valid_from IS NULL OR m.valid_from<=?)", "(m.valid_to IS NULL OR m.valid_to>=?)"])
            values.extend([query.as_of, query.as_of])
        return " AND ".join(clauses), values

    def copy_local_document_vectors(self, vectors: Mapping[str, Sequence[float]]) -> int:
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
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
            ):
                raise ValueError("Document vector must contain 1024 finite numbers")
            encoded.append((memory_id, struct.pack(f"<{DOCUMENT_VECTOR_DIMENSIONS}f", *vector)))
        with self._transaction(write=True) as connection:
            self._ensure_vector_table(connection)
            stored = 0
            for memory_id, blob in encoded:
                if connection.execute("SELECT 1 FROM memory_record WHERE id=?", (memory_id,)).fetchone() is None:
                    raise ValueError("Document vector requires an existing memory record")
                connection.execute(
                    f"INSERT OR REPLACE INTO {VECTOR_TABLE}(memory_id,dimensions,vector) VALUES(?,?,?)",
                    (memory_id, DOCUMENT_VECTOR_DIMENSIONS, blob),
                )
                stored += 1
            if connection.execute("SELECT version FROM schema_version").fetchone()[0] != SCHEMA_VERSION:
                raise SQLiteMemoryError("Vector copy must not change schema version")
        return stored

    @staticmethod
    def _ensure_vector_table(connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VECTOR_TABLE,),
        ).fetchone()
        if existing is None:
            connection.execute(VECTOR_TABLE_SQL)
            return
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({VECTOR_TABLE})")}
        if not {"memory_id", "dimensions", "vector"} <= columns:
            raise SQLiteMemoryError("Optional document-vector table is incompatible")

    def candidates(
        self, query: MemoryQuery, exact_values: tuple[str, ...], fts_expression: str,
    ) -> CandidateBatch:
        """Filter before ranking/limits; exact indexes drive fenced inner joins.

        CROSS JOIN keeps normalized-value lookups ahead of unit/parent payload
        joins without changing eligibility, source-detail selection or ranking.
        FTS and ENTITY materialize ranking keys only, then hydrate capped parents
        within the same snapshot; BM25 stays in its original MATCH context.
        """
        scope, scope_values = self._eligibility(query)
        candidates = []
        with self._transaction() as connection:
            sources = []
            if query.memory_ids:
                sources.append((CandidateSource.MEMORY_ID, "", "1", [], "m.id", "0"))
            if exact_values:
                placeholders = ",".join("?" for _ in exact_values)
                for source, table, index in (
                    (CandidateSource.ENTITY, "memory_entity", "exact_entity"),
                    (CandidateSource.TAG, "memory_tag", "exact_tag"),
                ):
                    sources.append((source, f"{table} x INDEXED BY {index}", f"x.normalized_value IN ({placeholders})", list(exact_values), "x.value", "0"))
            if fts_expression:
                sources.append((CandidateSource.FTS, "JOIN memory_fts ON memory_fts.rowid=u.rowid", "memory_fts MATCH ?", [fts_expression], "'Unicode FTS5 field match'", "bm25(memory_fts,6.0,4.0,1.0,8.0)"))
            for source, join, predicate, values, detail, score in sources:
                if source in (CandidateSource.ENTITY, CandidateSource.TAG):
                    from_clause = f"""{join}
                    CROSS JOIN retrieval_unit u ON u.id=x.unit_id
                    CROSS JOIN memory_record m ON m.id=u.memory_id"""
                else:
                    from_clause = f"retrieval_unit u JOIN memory_record m ON m.id=u.memory_id {join}"
                payload = """m.*,u.id AS unit_id,u.kind AS unit_kind,u.source_field,
                    u.text AS unit_text,u.ordinal,u.checksum AS unit_checksum"""
                late_hydration = source in (CandidateSource.FTS, CandidateSource.ENTITY)
                projection = (
                    "m.id,u.id AS unit_id,u.source_field,u.ordinal"
                    if late_hydration else payload
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
                rows = connection.execute(sql, (*scope_values, *values, query.candidate_limit)).fetchall()
                for rank, row in enumerate(rows, 1):
                    candidates.append(self._candidate(row, source, rank, row["source_detail"]))
            candidates.extend(self._semantic_candidates(connection, query, scope, scope_values))
            conflicts = connection.execute(
                f"""SELECT m.subject_key FROM memory_record m WHERE {scope}
                AND m.subject_key IS NOT NULL AND m.subject_key!=''
                GROUP BY m.subject_key HAVING count(DISTINCT m.document_checksum)>1""",
                scope_values,
            ).fetchall()
        return CandidateBatch(tuple(candidates), frozenset(row[0] for row in conflicts))

    def _semantic_candidates(
        self, connection: sqlite3.Connection, query: MemoryQuery, scope: str, scope_values: list,
    ) -> list[MemoryCandidate]:
        if query.query_vector is None:
            return []
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VECTOR_TABLE,),
        ).fetchone() is None:
            return []
        from .voyageai_ef import VOYAGE_RELEVANT_THRESHOLD
        rows = connection.execute(
            f"""SELECT v.vector AS document_vector,m.*,u.id AS unit_id,u.kind AS unit_kind,
            u.source_field,u.text AS unit_text,u.ordinal,u.checksum AS unit_checksum
            FROM {VECTOR_TABLE} v
            JOIN memory_record m ON m.id=v.memory_id
            JOIN retrieval_unit u ON u.memory_id=m.id
            WHERE {scope}""",
            scope_values,
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["id"], []).append(row)
        scored = []
        for memory_id, group in grouped.items():
            distance = self._cosine_distance(query.query_vector, group[0]["document_vector"])
            if distance > VOYAGE_RELEVANT_THRESHOLD:
                continue
            chosen = min(group, key=lambda item: (
                0 if item["source_field"] == "FLOW/OUTCOME" else 1,
                item["ordinal"], item["unit_id"],
            ))
            scored.append((distance, memory_id, chosen))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [
            self._candidate(
                row, CandidateSource.SEMANTIC, rank, f"cosine_distance={distance:.6f}", distance,
            )
            for rank, (distance, _memory_id, row) in enumerate(scored[:query.candidate_limit], 1)
        ]

    @staticmethod
    def _cosine_distance(query_vector: tuple[float, ...], blob: bytes) -> float:
        width = DOCUMENT_VECTOR_DIMENSIONS * 4
        if not isinstance(blob, bytes) or len(blob) != width:
            return math.inf
        stored = struct.unpack(f"<{DOCUMENT_VECTOR_DIMENSIONS}f", blob)
        dot = left = right = 0.0
        for query_value, stored_value in zip(query_vector, stored, strict=True):
            dot += query_value * stored_value
            left += query_value * query_value
            right += stored_value * stored_value
        if left == 0 or right == 0:
            return math.inf
        similarity = max(-1.0, min(1.0, dot / math.sqrt(left * right)))
        return 1.0 - similarity

    def _candidate(
        self, row: sqlite3.Row, source: CandidateSource, rank: int, detail: str,
        distance: float | None = None,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            record=self._record(row),
            unit=RetrievalUnit(
                row["unit_id"], row["id"], row["unit_kind"], row["source_field"],
                row["unit_text"], row["ordinal"], row["unit_checksum"],
            ),
            reason=MatchReason(source, rank, detail, distance),
            parse_warnings=tuple(json.loads(row["parse_warnings_json"])),
            source_headline=(json.loads(row["structured_json"] or "{}").get("HEAD") or [None])[0],
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], agent_name=row["agent_name"], session_id=row["session_id"],
            document=row["document"], created_at=row["created_at"], updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]), cwd=row["cwd"], header=row["header"],
            status=row["status"], superseded_by=row["superseded_by"], subject_key=row["subject_key"],
            valid_from=row["valid_from"], valid_to=row["valid_to"], evolved_at=row["evolved_at"],
            document_format=row["document_format"], kinds=tuple(json.loads(row["kinds_json"])),
        )

    def get_record(self, memory_id: str, agent_name: str) -> MemoryRecord | None:
        """Repository inspection for setup/verification, not a retrieval bypass tool."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_record WHERE id=? AND agent_name=?", (memory_id, agent_name),
            ).fetchone()
            return self._record(row) if row else None

    def check_integrity(self) -> None:
        """Check SQLite, foreign keys and the real FTS index, not just FTS row counts."""
        with self._transaction(write=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SQLiteMemoryError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise SQLiteMemoryError("Memory foreign key check failed")
            connection.execute("INSERT INTO memory_fts(memory_fts,rank) VALUES('integrity-check',1)")
