"""Slice 4 lifecycle verification: fictional SQLite and offline Chroma only."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
import struct
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import provenance as p
from AgentCrew.modules.memory.chroma_service import ChromaMemoryService
from AgentCrew.modules.memory.memory_worker import ChromaMemoryPersistence, MemoryWorker
from AgentCrew.modules.memory.sqlite_repository import SCHEMA_VERSION
from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService
from verification.memory import verify_phase4 as phase4
from verification.memory.verify_phase4 import require, run_handler
from verification.memory.verify_phase5 import MEMORY_DATA, MEMORY_XML

AGENT = "FictionalAgent"
NOW = datetime.now(UTC).timestamp()
OLD = (datetime.now(UTC) - timedelta(days=90)).timestamp()


def matches(metadata, where):
    if not where:
        return True
    if "$and" in where:
        return all(matches(metadata, item) for item in where["$and"])
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$ne" and actual == value:
                    return False
                if operator == "$gte" and (actual is None or actual < value):
                    return False
                if operator == "$lte" and (actual is None or actual > value):
                    return False
        elif actual != expected:
            return False
    return True


class OfflineCollection:
    def __init__(self):
        self.rows = {}
        self.events = []
        self.fail = None

    def get(self, ids=None, where=None, include=None, limit=None, **kwargs):
        selected = [
            key
            for key in (ids if ids is not None else self.rows)
            if key in self.rows and matches(self.rows[key]["metadata"], where)
        ]
        if limit is not None:
            selected = selected[:limit]
        return {
            "ids": selected,
            "documents": [self.rows[key]["document"] for key in selected],
            "metadatas": [
                copy.deepcopy(self.rows[key]["metadata"]) for key in selected
            ],
            "embeddings": [self.rows[key]["embedding"] for key in selected],
        }

    def query(self, query_texts, n_results, where=None, **kwargs):
        found = self.get(where=where)
        selected = [
            key
            for key in found["ids"]
            if query_texts[0] != "needle"
            or "needle" in self.rows[key]["document"].lower()
        ][:n_results]
        values = self.get(ids=selected)
        return {
            key: [value] for key, value in values.items() if key != "embeddings"
        } | {"distances": [[0.05] * len(selected)]}

    def _check(self, kind, ids):
        self.events.append((kind, tuple(ids)))
        if self.fail in ids:
            raise RuntimeError("fictional target or mark failure")

    def upsert(self, ids, documents, embeddings, metadatas):
        self._check("upsert", ids)
        for key, doc, vector, meta in zip(ids, documents, embeddings, metadatas):
            self.rows[key] = {
                "document": doc,
                "embedding": copy.deepcopy(vector),
                "metadata": copy.deepcopy(meta),
            }

    def update(self, ids, metadatas):
        self._check("update", ids)
        for key, meta in zip(ids, metadatas):
            self.rows[key]["metadata"].update(copy.deepcopy(meta))

    def delete(self, ids, **kwargs):
        self._check("delete", ids)
        for key in ids:
            self.rows.pop(key, None)


class Harness:
    def __init__(self, kind, directory):
        self.kind = kind
        self.operations = {}
        if kind == "sqlite":
            self.service = SQLiteMemoryService(Path(directory) / "memory.sqlite3")
            self.adapter = self.service._persistence
            self.repo = self.service._repository
        else:
            self.collection = OfflineCollection()
            self.adapter = ChromaMemoryPersistence()
            self.adapter.set_collection(self.collection)
            self.adapter.set_embedding_fn(lambda texts: [[0.1, 0.2] for _ in texts])
            self.service = object.__new__(ChromaMemoryService)
            self.service._collection = self.collection
            self.service._initialize_collection = lambda: self.collection
            self.service.relevant_threshold = 1
            self.service._worker = SimpleNamespace(
                _persistence=self.adapter,
                current_conversation_context={},
                context_embedding=[],
                stop=lambda: None,
            )
        self.service.session_id = "current-unrelated"

    def store(
        self, session, document=MEMORY_XML, *, agent=AGENT, stamp=NOW, operation=None
    ):
        operation_data = {
            "session_id": session,
            "agent_name": agent,
            "operation_id": operation or f"op:{session}:{len(self.operations)}",
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": "/fictional/path",
        }
        built = SimpleNamespace(
            document=document,
            header="Fictional header",
            timestamp=stamp,
            memory_data=MEMORY_DATA,
        )
        key = self.adapter.persist(operation_data, built)
        self.operations[key] = (operation_data, built)
        return key

    def merge(self, target, sources, document=None):
        operation, built = self.operations[target]
        self.adapter.apply_consolidation(operation, built, target, document, sources)

    def row(self, key):
        if self.kind == "sqlite":
            with self.repo._transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM memory_record WHERE id=?", (key,)
                ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["metadata"] = json.loads(value["metadata_json"])
            return value
        value = self.collection.rows.get(key)
        if value is None:
            return None
        return {
            **copy.deepcopy(value),
            "status": value["metadata"].get("status", "active"),
            "superseded_by": value["metadata"].get("superseded_by"),
        }

    def preserved(self, key):
        row = self.row(key)
        if self.kind == "sqlite":
            row.pop("status")
            row.pop("superseded_by")
            with self.repo._transaction() as connection:
                derived = {}
                for table in (
                    "retrieval_unit",
                    "memory_entity",
                    "memory_tag",
                    "memory_document_vector",
                    "migration_ledger",
                ):
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                    ).fetchone()
                    if exists:
                        derived[table] = [
                            tuple(item)
                            for item in connection.execute(
                                f"SELECT * FROM {table} WHERE memory_id=? ORDER BY 1",
                                (key,),
                            )
                        ]
                derived["fts"] = [
                    tuple(item)
                    for item in connection.execute(
                        "SELECT rowid,* FROM memory_fts WHERE rowid IN (SELECT rowid FROM retrieval_unit WHERE memory_id=?) ORDER BY rowid",
                        (key,),
                    )
                ]
            return row, derived
        row["metadata"].pop("status", None)
        row["metadata"].pop("superseded_by", None)
        row.pop("status")
        row.pop("superseded_by")
        return row

    def add_vector(self, key):
        with self.repo._transaction(write=True) as connection:
            self.repo._ensure_vector_table(connection)
            connection.execute(
                "INSERT INTO memory_document_vector VALUES(?,?,?)",
                (key, 1024, struct.pack("<1024f", *([0.25] * 1024))),
            )
            connection.execute(
                "INSERT INTO migration_ledger VALUES(?,?,?,?,?,?)",
                ("fixture", key, key, "source", "destination", "2026-11-16"),
            )

    def resolve(self, seed, agent=AGENT):
        return (
            self.repo.resolve_memory_families([seed], agent)
            if self.kind == "sqlite"
            else self.adapter.family_snapshot(agent).resolve([seed])
        )

    def mutate(self, key, *, status=None, parent=None, metadata=None):
        if self.kind == "sqlite":
            row = self.repo.get_record(key, AGENT)
            self.repo.upsert_record(
                replace(
                    row,
                    status=status or row.status,
                    superseded_by=parent,
                    metadata=metadata if metadata is not None else row.metadata,
                )
            )
        else:
            meta = self.collection.rows[key]["metadata"]
            if metadata is not None:
                meta.clear()
                meta.update(agent=AGENT, session_id=key)
                meta.update(metadata)
            if status is not None:
                meta["status"] = status
            if parent is not None:
                meta["superseded_by"] = parent


@contextmanager
def backend(kind):
    with TemporaryDirectory(prefix="agentcrew-lossless-") as directory:
        harness = Harness(kind, directory)
        try:
            yield harness
        finally:
            harness.service.shutdown()


def expect_family_error(callback):
    try:
        callback()
    except p.MemoryFamilyError:
        return
    raise AssertionError("Unsafe family operation was accepted")


def checks_lossless_supersession_and_visibility():
    for kind in ("sqlite", "chroma"):
        with backend(kind) as h:
            source = h.store(
                " source:# ",
                MEMORY_XML.replace("AlphaService", "NeedleSource"),
                stamp=OLD,
            )
            discard = h.store("discard")
            target = h.store("target")
            foreign = h.store("foreign", agent="OtherAgent")
            if kind == "sqlite":
                h.add_vector(source)
                with h.repo._transaction() as connection:
                    schema = [
                        tuple(row)
                        for row in connection.execute(
                            "SELECT type,name,sql FROM sqlite_master ORDER BY name"
                        )
                    ]
            before = {key: h.preserved(key) for key in (source, discard)}
            h.merge(
                target,
                [source, discard],
                MEMORY_XML.replace("AlphaService", "MergedService"),
            )
            require(
                [source, discard]
                == [
                    key
                    for key in (source, discard)
                    if h.row(key)["status"] == "superseded"
                    and h.row(key)["superseded_by"] == target
                ],
                "Source linkage missing",
            )
            for key, preserved in before.items():
                require(
                    h.preserved(key) == preserved,
                    "Supersession rewrote source evidence",
                )
                require(
                    run_handler(
                        h.service, query="alpha", memory_ids=[key], detail="full"
                    ).startswith("No memories found"),
                    "Exact source selection bypassed lifecycle",
                )
            require(
                set(p.source_ids(h.row(target)["metadata"])) == {source, discard},
                "Direct provenance missing",
            )
            require(
                json.dumps(
                    sorted([source, discard]), ensure_ascii=False, separators=(",", ":")
                )
                == h.row(target)["metadata"]["consolidated_from_json"],
                "Provenance is not deterministic scalar JSON",
            )
            require(h.row(foreign)["status"] == "active", "Cross-agent source affected")
            corpus = h.service.get_agent_memory_corpus(AGENT)
            require([row["id"] for row in corpus] == [target], "Corpus exposed sources")
            require(
                len(h.service.list_memory_headers(agent_name=AGENT)) == 1,
                "Headers exposed sources",
            )
            require(
                h.service.mark_memories_evolved([source, discard], AGENT) == 0,
                "Evolution stamped sources",
            )
            operation, built = h.operations[target]
            require(
                h.adapter.consolidation_candidates(operation, built, target) == [],
                "Candidates exposed sources",
            )
            require(
                "NeedleSource"
                not in h.service.retrieve_memory("NeedleSource", agent_name=AGENT),
                "Legacy search exposed preserved source",
            )
            h.merge(target, [source, discard])
            require(
                set(p.source_ids(h.row(target)["metadata"])) == {source, discard},
                "Repeated consolidation duplicated provenance",
            )
            for key, preserved in before.items():
                require(
                    h.preserved(key) == preserved,
                    "Repeated consolidation rewrote source",
                )
            if kind == "sqlite":
                with h.repo._transaction() as connection:
                    require(
                        [
                            tuple(row)
                            for row in connection.execute(
                                "SELECT type,name,sql FROM sqlite_master ORDER BY name"
                            )
                        ]
                        == schema,
                        "Consolidation changed schema",
                    )
                    require(
                        connection.execute(
                            "SELECT version FROM schema_version"
                        ).fetchone()[0]
                        == SCHEMA_VERSION
                        == 1,
                        "Schema version changed",
                    )
                h.repo.check_integrity()


def checks_continuation_identity_and_metadata():
    for kind in ("sqlite", "chroma"):
        with backend(kind) as h:
            source, root = h.store("session"), h.store("root")
            h.merge(root, [source])
            before = h.preserved(source)
            continuation = h.store("session", operation="opaque:operation#1")
            require(
                continuation == "continuation:opaque:operation#1",
                "Missing opaque continuation",
            )
            require(
                h.store("session", operation="new-operation") == continuation,
                "Continuation identity was not reused",
            )
            require(
                h.preserved(source) == before, "Resumed native source was overwritten"
            )
            created = h.row(root).get("created_at")
            provenance = h.row(root)["metadata"]["consolidated_from_json"]
            require(
                h.store("root", stamp=NOW + 10) == root, "Active root identity changed"
            )
            require(
                h.row(root)["metadata"]["consolidated_from_json"] == provenance,
                "Ordinary write discarded provenance",
            )
            if kind == "sqlite":
                require(
                    h.row(root)["created_at"] == created
                    and h.row(root)["updated_at"] == NOW + 10,
                    "Ordinary update changed root creation time",
                )
                h.repo.upsert_record(
                    replace(
                        h.repo.get_record(continuation, AGENT), id="other-continuation"
                    )
                )
            else:
                require(
                    h.row(root)["metadata"]["updated_at"] == NOW + 10,
                    "Root last-update metadata missing",
                )
                h.collection.rows["other-continuation"] = copy.deepcopy(
                    h.collection.rows[continuation]
                )
            saved = h.preserved(continuation)
            expect_family_error(lambda: h.store("session", operation="ambiguous"))
            require(
                h.preserved(continuation) == saved and h.preserved(source) == before,
                "Ambiguous identity mutated records",
            )


def checks_inactive_identity_collision():
    for kind in ("sqlite", "chroma"):
        with backend(kind) as h:
            source, root = h.store("session"), h.store("root")
            h.merge(root, [source])
            continuation = h.store("session", operation="same-operation")
            h.mutate(continuation, status="active", parent="")
            before = h.preserved(continuation)
            expect_family_error(lambda: h.store("session", operation="same-operation"))
            require(
                h.preserved(continuation) == before,
                "Malformed linked identity was overwritten",
            )


def checks_chains_deletion_and_retention():
    for kind in ("sqlite", "chroma"):
        for selected in ("source", "root", "topic", "session"):
            with backend(kind) as h:
                first = h.store(
                    "old-session", MEMORY_XML.replace("AlphaService", "needle")
                )
                middle, root = h.store("middle"), h.store("root")
                foreign = h.store("foreign", agent="OtherAgent")
                if kind == "sqlite":
                    h.add_vector(first)
                h.merge(middle, [first])
                middle_evidence = h.preserved(middle)
                h.merge(root, [middle])
                require(
                    h.preserved(middle) == middle_evidence,
                    "Merged root evidence was rewritten",
                )
                require(
                    set(h.resolve(first)) == {first, middle, root},
                    "Multi-level family not traversable",
                )
                require(
                    p.source_ids(h.row(root)["metadata"]) == (middle,),
                    "Provenance flattened multi-level parents",
                )
                h.service._worker.current_conversation_context["root"] = (
                    "fictional stale context"
                )
                if selected == "topic":
                    result = h.service.forget_topic("needle", agent_name=AGENT)
                elif selected == "session":
                    result = h.service.delete_by_conversation_id("old-session")
                else:
                    result = h.service.forget_ids(
                        [first if selected == "source" else root, "already-deleted"],
                        AGENT,
                    )
                require(
                    result["success"] and result["count"] == 3,
                    "Family deletion incomplete",
                )
                require(
                    all(h.row(key) is None for key in (first, middle, root))
                    and h.row(foreign) is not None,
                    "Family deletion crossed agent or left facts",
                )
                require(
                    not h.service._worker.current_conversation_context,
                    "Deleted facts survived in turn context",
                )
                require(
                    h.service.forget_ids([first], AGENT)["count"] == 0,
                    "Missing ID deletion was not idempotent",
                )
                if kind == "sqlite":
                    with h.repo._transaction() as connection:
                        for table in (
                            "retrieval_unit",
                            "memory_entity",
                            "memory_tag",
                            "memory_document_vector",
                            "migration_ledger",
                        ):
                            require(
                                connection.execute(
                                    f"SELECT COUNT(*) FROM {table} WHERE memory_id IN (?,?,?)",
                                    (first, middle, root),
                                ).fetchone()[0]
                                == 0,
                                "Derived rows survived family deletion",
                            )
                    h.repo.check_integrity()
        with backend(kind) as h:
            source, root = (
                h.store("old-source", stamp=OLD),
                h.store("old-root", stamp=OLD),
            )
            h.merge(root, [source])
            require(
                h.service.cleanup_old_memories() == 0,
                "Retention used source age or root creation age",
            )
            h.store("old-root", stamp=OLD)
            require(
                h.service.cleanup_old_memories() == 2,
                "Expired root did not delete complete family",
            )
            require(
                h.row(source) is None and h.row(root) is None,
                "Retention left preserved evidence orphaned",
            )


def checks_invalid_families_and_limits():
    require(
        p.MAX_MEMORY_FAMILY_SCAN_RECORDS == p.MAX_MEMORY_FAMILY_MEMBERS == 10_000,
        "Approved bounds changed",
    )
    nodes = [p.FamilyRecord(str(i), AGENT, str(i), {}) for i in range(10_001)]
    expect_family_error(lambda: p.FamilyIndex(nodes, AGENT))
    for kind in ("sqlite", "chroma"):
        for malformed in (
            "missing",
            "cross-agent",
            "parents",
            "cycle",
            "self",
            "json",
            "one-key",
            "active-link",
        ):
            with backend(kind) as h:
                a, b, good = (
                    h.store("a", stamp=OLD),
                    h.store("b", stamp=OLD),
                    h.store("good", stamp=OLD),
                )
                foreign = h.store("foreign", agent="OtherAgent")
                if malformed in {"missing", "cross-agent", "self", "active-link"}:
                    parent = {
                        "missing": "missing",
                        "cross-agent": foreign,
                        "self": a,
                        "active-link": b,
                    }[malformed]
                    h.mutate(
                        a,
                        status="active" if malformed == "active-link" else "superseded",
                        parent=parent,
                    )
                elif malformed == "cycle":
                    h.mutate(a, status="superseded", parent=b)
                    h.mutate(b, status="superseded", parent=a)
                elif malformed == "parents":
                    h.mutate(a, metadata=p.with_sources(h.row(a)["metadata"], [b]))
                    h.mutate(
                        good, metadata=p.with_sources(h.row(good)["metadata"], [b])
                    )
                else:
                    metadata = {**h.row(a)["metadata"], "provenance_version": 1}
                    if malformed == "json":
                        metadata["consolidated_from_json"] = "{"
                    h.mutate(a, metadata=metadata)
                before = {
                    key: copy.deepcopy(h.row(key)) for key in (a, b, good, foreign)
                }
                result = h.service.forget_ids([good, a], AGENT)
                require(
                    not result["success"] and result["count"] == 0,
                    "Invalid family deletion reported success",
                )
                require(
                    all(h.row(key) == row for key, row in before.items()),
                    "Invalid resolution partially deleted data",
                )
                h.service.cleanup_old_memories()
                require(h.row(a) is not None, "Retention removed malformed family")
        with backend(kind) as h:
            a, b, c = h.store("a"), h.store("b"), h.store("c")
            h.merge(b, [a])
            h.merge(c, [b])
            with patch.object(p, "MAX_MEMORY_FAMILY_MEMBERS", 2):
                require(
                    not h.service.forget_ids([a], AGENT)["success"],
                    "Member limit ignored",
                )
            with patch.object(p, "MAX_MEMORY_FAMILY_SCAN_RECORDS", 2):
                require(
                    not h.service.forget_ids([a], AGENT)["success"],
                    "Scan limit ignored",
                )
                require(
                    h.service.cleanup_old_memories() == 0,
                    "Oversized retention was not conservative",
                )
            require(
                all(h.row(key) is not None for key in (a, b, c)),
                "Bound failure mutated a prefix",
            )


def checks_atomicity_and_chroma_partial_failures():
    for kind in ("sqlite", "chroma"):
        with backend(kind) as h:
            source, target = h.store("source"), h.store("target")
            before_source, before_target = h.preserved(source), h.preserved(target)
            if kind == "sqlite":
                with h.repo._transaction(write=True) as connection:
                    connection.execute(
                        "CREATE TRIGGER reject_target BEFORE UPDATE ON memory_record WHEN old.id='target_FictionalAgent' BEGIN SELECT RAISE(ABORT,'fictional target failure'); END"
                    )
            else:
                h.collection.fail = target
                h.collection.events.clear()
            try:
                h.merge(target, [source], MEMORY_XML)
                raise AssertionError("Target failure not propagated")
            except (RuntimeError, sqlite3.DatabaseError):
                pass
            require(
                h.preserved(source) == before_source
                and h.preserved(target) == before_target
                and h.row(source)["status"] == "active",
                "Failed target hid or changed evidence",
            )
            if kind == "sqlite":
                with h.repo._transaction(write=True) as connection:
                    connection.execute("DROP TRIGGER reject_target")
                    connection.execute(
                        "CREATE TRIGGER reject_source BEFORE UPDATE ON memory_record WHEN new.status='superseded' BEGIN SELECT RAISE(ABORT,'fictional mark failure'); END"
                    )
                try:
                    h.merge(target, [source], MEMORY_XML)
                    raise AssertionError("Source failure not propagated")
                except sqlite3.DatabaseError:
                    pass
                require(
                    h.preserved(target) == before_target
                    and h.row(source)["status"] == "active",
                    "SQLite consolidation was not atomic",
                )
            else:
                require(
                    h.collection.events == [("upsert", (target,))],
                    "Target failure attempted source mutation",
                )
                second_source = h.store("second-source")
                second_before = h.preserved(second_source)
                h.collection.fail = second_source
                h.collection.events.clear()
                try:
                    h.merge(target, [source, second_source], MEMORY_XML)
                    raise AssertionError("Partial mark failure not propagated")
                except RuntimeError:
                    pass
                require(
                    h.collection.events
                    == [
                        ("upsert", (target,)),
                        ("update", (source,)),
                        ("update", (second_source,)),
                    ],
                    "Chroma target-first ordering changed",
                )
                require(
                    h.row(source)["status"] == "superseded"
                    and h.row(second_source)["status"] == "active"
                    and h.preserved(source) == before_source
                    and h.preserved(second_source) == second_before,
                    "Partial Chroma failure destroyed source",
                )
                require(
                    set(h.resolve(second_source)) == {source, second_source, target},
                    "One-sided provenance family not discoverable",
                )
                h.collection.fail = None
                h.collection.events.clear()
                require(
                    h.service.forget_ids([second_source], AGENT)["count"] == 3,
                    "Partial family erase failed",
                )
                require(
                    h.collection.events
                    == [("delete", tuple(sorted([source, second_source, target])))],
                    "Chroma family erase was not one bounded delete",
                )


def checks_worker_identity_and_invalid_actions():
    built = SimpleNamespace(
        document=MEMORY_XML, header="Fictional", memory_data=MEMORY_DATA
    )
    persistence = SimpleNamespace(
        readiness_error=lambda: None,
        persist=Mock(return_value="opaque:chosen#id"),
        consolidation_candidates=Mock(return_value=[]),
        apply_consolidation=Mock(),
    )
    worker = MemoryWorker(persistence=persistence)
    worker._builder = SimpleNamespace(build=AsyncMock(return_value=built))
    operation = {"agent_name": AGENT, "session_id": "session", "operation_id": "op"}
    asyncio.run(worker._store_conversation_internal(operation))
    persistence.consolidation_candidates.assert_called_once_with(
        operation, built, "opaque:chosen#id"
    )
    candidates = [{"id": "source", "document": MEMORY_XML}]
    payloads = [
        '<ACTION id="unknown" type="DISCARD"/>',
        '<ACTION id="source" type="DISCARD"/><ACTION id="unknown" type="KEEP"/>',
        "<ACTION>malformed</ACTION>",
        '<ACTION id="target" type="DISCARD"/>',
        '<ACTION id="source" type="KEEP"/><ACTION id="source" type="DISCARD"/>',
        '<ACTION id="source" type="MERGE"/>',
        '<ACTION id="source" type="BAD"/>',
        '<ACTION id="source"/>',
        '<ACTION id="source" type="DISCARD">unexpected</ACTION>',
        '<ACTION id="source" type="DISCARD" extra="unexpected"/>',
        '<ACTION id="source" type="DISCARD"/><UNEXPECTED/>',
        '<ACTION id="source" type="MERGE"/><MERGED_MEMORY for="source"><MEMORY/></MERGED_MEMORY>',
    ]
    for payload in payloads:
        text = f"<CONSOLIDATION_RESULT>{payload}</CONSOLIDATION_RESULT>"
        require(
            worker._parse_consolidation_result(text, candidates, AGENT, "target")
            == (None, []),
            "Invalid action produced a partial mutation plan",
        )
    keep = (
        '<CONSOLIDATION_RESULT><ACTION id="source" type="KEEP"/></CONSOLIDATION_RESULT>'
    )
    valid_merge = '<CONSOLIDATION_RESULT><ACTION id="source" type="MERGE"/><MERGED_MEMORY for="source"><MEMORY><HEAD>merged</HEAD></MEMORY></MERGED_MEMORY></CONSOLIDATION_RESULT>'
    document, sources = worker._parse_consolidation_result(
        valid_merge, candidates, AGENT, "target"
    )
    require(
        document is not None and sources == ["source"],
        "Valid merge action was rejected",
    )
    require(
        worker._parse_consolidation_result(keep, candidates, AGENT, "target")
        == (None, []),
        "KEEP changed candidate",
    )
    valid = '<CONSOLIDATION_RESULT><ACTION id="source" type="DISCARD"/></CONSOLIDATION_RESULT>'
    require(
        worker._parse_consolidation_result(valid, candidates, AGENT, "target")
        == (None, ["source"]),
        "DISCARD did not supersede",
    )
    persistence.persist.side_effect = p.MemoryFamilyError("Ambiguous identity")
    worker.current_conversation_context.clear()
    asyncio.run(worker._store_conversation_internal(operation))
    require(
        not worker.current_conversation_context,
        "Failed persistence mutated session context",
    )


def checks_backend_validation_and_legacy():
    require(
        p.is_active({})
        and not p.is_active({"superseded_by": ""})
        and not p.is_active({"superseded_by": False})
        and p.timestamp(True) is None,
        "Malformed linkage became a legacy active root",
    )
    for age in (None, True, float("nan"), "unknown"):
        require(
            p.FamilyIndex(
                [p.FamilyRecord("root", AGENT, "session", {}, age)], AGENT
            ).expired(NOW)
            == (),
            "Unknown root age was expired",
        )
    merged = p.merge_metadata(
        p.with_sources({"custom": "kept"}, ["b"]),
        p.with_sources({"other": "new"}, ["a", "b"]),
    )
    require(
        p.source_ids(merged) == ("a", "b") and merged["custom"] == "kept",
        "Ordinary provenance union lost history",
    )
    for malformed in (
        {"provenance_version": None, "consolidated_from_json": None},
        {"provenance_version": 2, "consolidated_from_json": "[]"},
        {"provenance_version": 1, "consolidated_from_json": "{}"},
        {"provenance_version": 1, "consolidated_from_json": '["a","a"]'},
    ):
        expect_family_error(lambda malformed=malformed: p.source_ids(malformed))
    for kind in ("sqlite", "chroma"):
        with backend(kind) as h:
            source, root = h.store("source"), h.store("root")
            foreign = h.store("foreign", agent="OtherAgent")
            before = {key: h.preserved(key) for key in (source, root, foreign)}
            for invalid in ([foreign], [root], [source, source], ["missing"]):
                expect_family_error(
                    lambda invalid=invalid, root=root: h.merge(
                        root, invalid, MEMORY_XML
                    )
                )
            require(
                all(h.preserved(key) == value for key, value in before.items()),
                "Invalid consolidation partially published a target",
            )
            operation, built = h.operations[root]
            candidate = {"id": source, "document": MEMORY_XML}
            invalid = f'<CONSOLIDATION_RESULT><ACTION id="{source}" type="DISCARD"/><ACTION id="unknown" type="KEEP"/></CONSOLIDATION_RESULT>'
            llm = SimpleNamespace(process_message=AsyncMock(return_value=invalid))
            worker = MemoryWorker(llm_service=llm, persistence=h.adapter)
            with (
                patch.object(
                    h.adapter, "consolidation_candidates", return_value=[candidate]
                ),
                patch.object(
                    h.adapter,
                    "apply_consolidation",
                    wraps=h.adapter.apply_consolidation,
                ) as apply,
            ):
                asyncio.run(
                    worker._consolidate_related_memories(operation, built, root)
                )
                apply.assert_not_called()
            require(
                all(h.preserved(key) == value for key, value in before.items()),
                "Invalid LLM result changed durable evidence",
            )
            h.mutate(source, status="superseded", parent=root)
            require(
                set(h.resolve(source)) == {source, root},
                "One-sided lifecycle linkage was rejected",
            )
            require(
                h.service.forget_ids([source], AGENT)["count"] == 2,
                "One-sided family erase failed",
            )
        with backend(kind) as h:
            source, root = h.store("fresh-source"), h.store("expired-root", stamp=OLD)
            h.merge(root, [source])
            h.store("expired-root", stamp=OLD)
            require(
                h.service.cleanup_old_memories() == 2,
                "Fresh superseded source prevented root-based expiry",
            )
    with backend("chroma") as h:
        legacy = h.store("legacy")
        h.collection.rows[legacy]["metadata"].pop("status")
        require(
            legacy
            in run_handler(
                h.service, query="alpha", memory_ids=[legacy], detail="full"
            ),
            "Legacy missing-status record was hidden",
        )
        require(
            len(h.service.list_memory_headers(agent_name=AGENT)) == 1,
            "Legacy header hidden",
        )
        require(
            len(h.service.get_agent_memory_corpus(AGENT)) == 1, "Legacy corpus hidden"
        )
        require(
            "MEMORY" in h.service.retrieve_memory("alpha", agent_name=AGENT),
            "Legacy retrieval hidden",
        )
        current = h.store("current")
        operation, built = h.operations[current]
        require(
            legacy
            in [
                row["id"]
                for row in h.adapter.consolidation_candidates(operation, built, current)
            ],
            "Legacy candidate hidden",
        )
        require(
            h.service.mark_memories_evolved([legacy], AGENT) == 1,
            "Legacy evolution hidden",
        )
        before = h.preserved(legacy)
        h.collection.fail = current
        h.collection.events.clear()
        try:
            h.merge(current, [legacy])
            raise AssertionError("DISCARD target failure not propagated")
        except RuntimeError:
            pass
        require(
            h.collection.events == [("update", (current,))]
            and h.row(legacy)["status"] == "active"
            and h.preserved(legacy) == before,
            "DISCARD marked a source before target publication",
        )


def checks_freeze_and_schema_contract():
    state = {
        "hashes": {
            key: hashlib.sha256((ROOT / key).read_bytes()).hexdigest()
            for key in phase4.FROZEN_FILE_HASHES
        },
        "methods": phase4._repository_methods_digest(),
        "policy": phase4.POLICY,
    }
    baseline = {
        "hashes": {
            key: hashlib.sha256(
                subprocess.check_output(["git", "show", f"91c3a0dc:{key}"], cwd=ROOT)
            ).hexdigest()
            for key in phase4.FROZEN_FILE_HASHES
        },
        "methods": "f9c56e3213561ba231ef4f64d7b087eb93d868cba53e0095f43d0d2f44045ac8",
        "policy": phase4.FROZEN_POLICY,
    }
    require(
        json.loads(json.dumps(state)) == baseline, "Slice 4 introduced freeze drift"
    )
    require(SCHEMA_VERSION == 1, "Schema version changed")
    original = subprocess.check_output(
        ["git", "show", "91c3a0dc:AgentCrew/modules/memory/benchmark.py"], cwd=ROOT
    )
    require(
        original == (ROOT / "AgentCrew/modules/memory/benchmark.py").read_bytes(),
        "Historical provenance anchors changed",
    )


def main():
    for check in (
        checks_lossless_supersession_and_visibility,
        checks_continuation_identity_and_metadata,
        checks_inactive_identity_collision,
        checks_chains_deletion_and_retention,
        checks_invalid_families_and_limits,
        checks_atomicity_and_chroma_partial_failures,
        checks_worker_identity_and_invalid_actions,
        checks_backend_validation_and_legacy,
        checks_freeze_and_schema_contract,
    ):
        check()
        print(f"PASS {check.__name__}")
    print(
        "PASS Slice 4 lossless consolidation; fictional SQLite and offline Chroma only"
    )


if __name__ == "__main__":
    main()
