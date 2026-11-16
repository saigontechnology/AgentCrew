"""Fictional Phase 5 checks: SQLite write path, CWD capture, builder, concurrency.

No network, embeddings, Chroma dependency for SQLite paths, private memory, or
sealed Phase 3 artifacts are used.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
executable = Path(sys.executable)
if executable.is_symlink():
    executable = Path(os.path.abspath(sys.executable))
for site in (executable.parent.parent / "lib").glob("python*/site-packages"):
    site_path = str(site)
    if site_path not in sys.path:
        sys.path.append(site_path)

from AgentCrew.modules.events.hooks import HookPoints, HookRegistry
from AgentCrew.modules.memory.benchmark import POLICY
from AgentCrew.modules.memory.memory_record_builder import (
    MemoryRecordBuilder,
    build_consolidation_query,
)
from AgentCrew.modules.memory.memory_worker import ChromaMemoryPersistence
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.sqlite_service import (
    CONSOLIDATION_MIN_OVERLAP,
    SQLiteMemoryPersistence,
    SQLiteMemoryService,
)
from AgentCrew.modules.memory.types import MemoryRecord

MEMORY_DATA = {
    "MEMORY": {
        "DATE": "2026-11-12",
        "HEAD": "Fictional alpha headline",
        "FLOW": {"OUTCOME": "AlphaService handled the fictional alpha outcome"},
        "ENTITIES": {"ENTITY": {"NAME": "AlphaService", "TYPE": "service"}},
    }
}
MEMORY_XML = """<MEMORY><DATE>2026-11-12</DATE><HEAD>Fictional alpha headline</HEAD>
<FLOW><OUTCOME>AlphaService handled the fictional alpha outcome</OUTCOME></FLOW>
<ENTITIES><ENTITY><NAME>AlphaService</NAME><TYPE>service</TYPE></ENTITY></ENTITIES></MEMORY>"""

UNRELATED_XML = """<MEMORY><DATE>2026-11-12</DATE><HEAD>Unrelated gamma headline</HEAD>
<FLOW><OUTCOME>Completely different zeta topic</OUTCOME></FLOW>
<ENTITIES><ENTITY><NAME>ZetaService</NAME><TYPE>service</TYPE></ENTITY></ENTITIES></MEMORY>"""

RELATED_XML = """<MEMORY><DATE>2026-11-12</DATE><HEAD>Related beta headline</HEAD>
<FLOW><OUTCOME>AlphaService handled the fictional beta outcome</OUTCOME></FLOW>
<ENTITIES><ENTITY><NAME>AlphaService</NAME><TYPE>service</TYPE></ENTITY></ENTITIES></MEMORY>"""

FROZEN_POLICY = {
    "name": "qualified-sqlite-signals-v1",
    "fts_weights": [6, 4, 1, 8],
    "rrf_k": 60,
    "kind_weight": 1.1,
    "min_overlap": 0.35,
    "relative_coverage": 0.7,
    "sqlite_semantic": {
        "metric": "cosine_distance",
        "admission": "explicit-cutoff-before-rrf",
        "default_max_distance": None,
        "cross_source_tie": "diversity-then-stable-id",
    },
}


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FictionalLLM:
    def __init__(self, reply=MEMORY_XML, fail=False):
        self.reply = reply
        self.fail = fail
        self.prompts: list[str] = []

    async def process_message(self, prompt, temperature=0, model_id=None):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("fictional llm failure")
        return self.reply


class FictionalHooks:
    def __init__(self):
        self.before_calls = []
        self.after_calls = []
        self.cancel = False
        self.modify = None

    async def run_before(self, point, operation_data=None, **kwargs):
        self.before_calls.append(
            {"point": point, "operation_data": dict(operation_data or {})}
        )
        if self.cancel:
            return None
        return {"operation_data": operation_data}

    async def run_after(self, point, result=None, operation_data=None, **kwargs):
        self.after_calls.append(
            {"point": point, "result": result, "operation_data": operation_data}
        )
        if self.modify is not None:
            memory_data = self.modify(result.get("memory_data"))
            return {**result, "memory_data": memory_data}
        return None


def make_operation(
    operation_id="op-1",
    session_id="sess-1",
    agent_name="FictionalAgent",
    cwd="/fictional/cwd",
):
    return {
        "type": "store_conversation",
        "operation_id": operation_id,
        "user_message": "fictional user message",
        "assistant_messages": ["fictional assistant message"],
        "agent_name": agent_name,
        "session_id": session_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "cwd": cwd,
    }


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def direct_record(
    repository, memory_id, session_id, agent_name, document, created_at=None
):
    repository.upsert_record(
        MemoryRecord(
            id=memory_id,
            agent_name=agent_name,
            session_id=session_id,
            document=document,
            created_at=created_at
            if created_at is not None
            else datetime.now(UTC).timestamp(),
            updated_at=created_at
            if created_at is not None
            else datetime.now(UTC).timestamp(),
            metadata={"timestamp": datetime.now(UTC).isoformat()},
            cwd="/fictional/cwd",
            header="Fictional header",
        )
    )


def count_rows(repository, table, where="", values=()):
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    with repository._transaction() as connection:
        return connection.execute(sql, values).fetchone()[0]


def checks_policy_freeze():
    require(POLICY == FROZEN_POLICY, "Frozen retrieval policy changed")
    require(
        POLICY["min_overlap"] == CONSOLIDATION_MIN_OVERLAP,
        "Consolidation overlap gate diverged from frozen policy",
    )
    print("PASS checks_policy_freeze")


def checks_m500_cwd_capture():
    with tempfile.TemporaryDirectory() as tmp:
        original = HookRegistry.get_instance
        hooks = FictionalHooks()
        HookRegistry.get_instance = classmethod(lambda cls: hooks)
        try:
            service = SQLiteMemoryService(
                Path(tmp) / "memory.sqlite3", llm_service=FictionalLLM()
            )
            captured = {}
            service._worker.queue_store = lambda operation_data: (
                captured.update(operation_data) or True
            )
            os.chdir(tmp)
            try:
                service.store_conversation(
                    "fictional user",
                    ["fictional assistant"],
                    agent_name="FictionalAgent",
                    session_id="sess-cwd",
                )
            finally:
                os.chdir(ROOT)
            require(
                captured.get("cwd") == tmp,
                "CWD not captured synchronously at queue time",
            )
            require("cwd" in captured, "CWD missing from operation data")
            service.shutdown()
        finally:
            HookRegistry.get_instance = original

    persistence = ChromaMemoryPersistence()
    require(
        persistence.readiness_error() == "Memory worker: collection not set",
        "Chroma readiness message changed",
    )

    class FictionalCollection:
        def __init__(self):
            self.upserts = []

        def get(self, **kwargs):
            return {"ids": [], "metadatas": []}

        def upsert(self, ids, documents, embeddings, metadatas):
            self.upserts.append({"ids": ids, "metadatas": metadatas})

    class FictionalEmbedding:
        def __call__(self, texts):
            return [[0.1, 0.2] for _ in texts]

    collection = FictionalCollection()
    persistence.set_collection(collection)
    persistence.set_embedding_fn(FictionalEmbedding())
    from AgentCrew.modules.memory.memory_record_builder import BuiltMemoryRecord

    record = BuiltMemoryRecord(
        memory_data={"MEMORY": {"HEAD": "Fictional chroma headline"}},
        document=MEMORY_XML,
        header="Fictional chroma headline",
        timestamp=datetime.now(UTC).timestamp(),
    )
    persistence.persist(make_operation(cwd="/fictional/chroma-cwd"), record)
    metadata = collection.upserts[0]["metadatas"][0]
    require(
        metadata.get("cwd") == "/fictional/chroma-cwd",
        "Chroma metadata missing CWD for new stores",
    )
    print("PASS checks_m500_cwd_capture")


def checks_m501_builder():
    original = HookRegistry.get_instance
    hooks = FictionalHooks()
    HookRegistry.get_instance = classmethod(lambda cls: hooks)
    try:
        builder = MemoryRecordBuilder(FictionalLLM())
        record = asyncio.run(builder.build(make_operation(), ""))
        require(record is not None, "Builder returned no record")
        require(record.header == "Fictional alpha headline", "Builder header mismatch")
        require("MEMORY" in record.document, "Builder document missing MEMORY block")
        require(record.timestamp > 0, "Builder timestamp missing")
        require(len(hooks.before_calls) == 1, "Before hook not invoked once")
        require(
            hooks.before_calls[0]["point"] == HookPoints.MEMORY_STORE,
            "Before hook point mismatch",
        )
        require(len(hooks.after_calls) == 1, "After hook not invoked once")
        require(
            hooks.after_calls[0]["point"] == HookPoints.MEMORY_STORE,
            "After hook point mismatch",
        )
        first_prompt = builder.llm_service.prompts[0]
        require("fictional user message" in first_prompt, "Prompt missing user message")
        require(
            "fictional assistant message" in first_prompt,
            "Prompt missing assistant response",
        )
        require(
            "Create a new memory record" in first_prompt,
            "First-turn instructions missing",
        )
        require(
            "PREVIOUS_CONVERSATION_CONTEXT" not in first_prompt,
            "Merge instructions used without session context",
        )

        builder2 = MemoryRecordBuilder(FictionalLLM())
        asyncio.run(
            builder2.build(
                make_operation(), "<MEMORY><HEAD>existing context</HEAD></MEMORY>"
            )
        )
        merge_prompt = builder2.llm_service.prompts[0]
        require(
            "PREVIOUS_CONVERSATION_CONTEXT" in merge_prompt,
            "Merge instructions missing for session context",
        )

        failing = MemoryRecordBuilder(FictionalLLM(fail=True))
        fallback = asyncio.run(failing.build(make_operation(), ""))
        require(fallback is not None, "Fallback record missing")
        require(len(failing.llm_service.prompts) == 3, "Fallback retry count changed")
        notes = fallback.memory_data["MEMORY"].get("CONVERSATION_NOTES")
        require(notes is not None, "Fallback conversation notes missing")

        hooks.cancel = True
        cancelled = MemoryRecordBuilder(FictionalLLM())
        result = asyncio.run(cancelled.build(make_operation(), ""))
        require(result is None, "Cancelled store did not return None")

        hooks.cancel = False
        hooks.modify = lambda memory_data: {
            **memory_data,
            "MEMORY": {**memory_data["MEMORY"], "HEAD": "Modified fictional headline"},
        }
        modified_builder = MemoryRecordBuilder(FictionalLLM())
        modified = asyncio.run(modified_builder.build(make_operation(), ""))
        require(
            modified.header == "Modified fictional headline",
            "After hook modification not applied",
        )

        query = build_consolidation_query(
            {
                "MEMORY": {
                    "HEAD": "Alpha headline",
                    "FLOW": {"OUTCOME": "alpha outcome"},
                    "ENTITIES": {"ENTITY": {"NAME": "AlphaService"}},
                }
            }
        )
        require(
            "Alpha headline" in query
            and "alpha outcome" in query
            and "AlphaService" in query,
            "Consolidation query missing fields",
        )
    finally:
        HookRegistry.get_instance = original
    print("PASS checks_m501_builder")


def checks_m502_m503_m504_service():
    chroma_before = "chromadb" in sys.modules
    with tempfile.TemporaryDirectory() as tmp:
        service = SQLiteMemoryService(
            Path(tmp) / "memory.sqlite3", llm_service=FictionalLLM()
        )
        service.store_conversation(
            "fictional user alpha",
            ["fictional assistant alpha"],
            agent_name="FictionalAgent",
            session_id="sess-a",
        )
        service.store_conversation(
            "fictional user beta",
            ["fictional assistant beta"],
            agent_name="FictionalAgent",
            session_id="sess-b",
        )
        service.store_conversation(
            "fictional user other",
            ["fictional assistant other"],
            agent_name="OtherAgent",
            session_id="sess-c",
        )
        require(
            wait_for(
                lambda: (
                    service._repository.get_record(
                        "sess-b_FictionalAgent", "FictionalAgent"
                    )
                    is not None
                )
            ),
            "Worker did not persist store",
        )
        record = service._repository.get_record(
            "sess-a_FictionalAgent", "FictionalAgent"
        )
        require(
            record is not None and record.cwd == os.getcwd(),
            "CWD not persisted on SQLite record",
        )
        require(
            record is not None and record.header == "Fictional alpha headline",
            "Header not persisted",
        )

        result = service.retrieve_memory(
            "AlphaService fictional alpha", agent_name="FictionalAgent"
        )
        require("AlphaService" in result, "Lexical read missing stored memory")
        structured = service.search_structured(
            __import__(
                "AgentCrew.modules.memory.types", fromlist=["MemoryQuery"]
            ).MemoryQuery(
                query="AlphaService fictional alpha",
                agent_name="FictionalAgent",
                current_session_id="sess-z",
            )
        )
        require(len(structured.hits) > 0, "Structured search missing hits")
        documents = service.get_memory_documents(
            ["sess-a_FictionalAgent"], "FictionalAgent"
        )
        require(
            "sess-a_FictionalAgent" in documents, "Document expansion missing record"
        )

        service.session_id = "sess-a"
        headers = service.list_memory_headers(agent_name="FictionalAgent")
        require(len(headers) == 1, "Header listing did not exclude current session")
        require("Fictional alpha headline" in headers[0], "Header text missing")
        require("cwd" not in headers[0].lower(), "CWD leaked into headers")
        require("2026-" in headers[0], "Header date format missing")

        corpus = service.get_agent_memory_corpus("FictionalAgent", max_items=10)
        require(len(corpus) == 1, "Corpus did not exclude current session by default")
        require(
            corpus[0]["metadata"].get("cwd") == os.getcwd(),
            "Corpus metadata missing CWD",
        )

        marked = service.mark_memories_evolved(
            ["sess-b_FictionalAgent"], "FictionalAgent"
        )
        require(marked == 1, "Evolution marking count mismatch")
        evolved_record = service._repository.get_record(
            "sess-b_FictionalAgent", "FictionalAgent"
        )
        require(
            evolved_record is not None and evolved_record.evolved_at is not None,
            "evolved_at not persisted",
        )
        corpus_after = service.get_agent_memory_corpus(
            "FictionalAgent", max_items=10, exclude_session_id=None
        )
        require(
            all(
                record["metadata"].get("evolved_at") is None for record in corpus_after
            ),
            "Evolved memory not excluded from corpus",
        )

        service.load_conversation_context("sess-a", "FictionalAgent")
        require(
            "sess-a" in service._worker.current_conversation_context,
            "Session context not loaded",
        )
        service.clear_conversation_context()
        require(
            not service._worker.current_conversation_context,
            "Session context not cleared",
        )

        broken = SQLiteMemoryService(
            Path(tmp) / "broken.sqlite3", llm_service=FictionalLLM()
        )
        original_upsert = broken._repository.upsert_record
        broken._repository.upsert_record = lambda record: (_ for _ in ()).throw(
            RuntimeError("fictional storage failure")
        )
        returned = broken.store_conversation(
            "fictional user",
            ["fictional assistant"],
            agent_name="FictionalAgent",
            session_id="sess-x",
        )
        require(len(returned) == 1, "Fail-open storage did not return operation id")
        broken._repository.upsert_record = original_upsert
        broken.shutdown()
        service.shutdown()
    require(
        chroma_before or "chromadb" not in sys.modules,
        "SQLite-only flow imported Chroma",
    )
    print("PASS checks_m502_m503_m504_service")


def checks_m505_atomic_delete():
    with tempfile.TemporaryDirectory() as tmp:
        repository = SQLiteMemoryRepository(Path(tmp) / "memory.sqlite3")
        direct_record(repository, "mem-1", "sess-1", "FictionalAgent", MEMORY_XML)
        direct_record(repository, "mem-2", "sess-2", "FictionalAgent", RELATED_XML)
        require(
            count_rows(repository, "retrieval_unit") > 0, "Units missing before delete"
        )

        removed = repository.delete_records(["mem-1"], "FictionalAgent")
        require(removed == 1, "Physical delete count mismatch")
        require(
            repository.get_record("mem-1", "FictionalAgent") is None,
            "Canonical row survived delete",
        )
        require(
            count_rows(repository, "retrieval_unit", "memory_id=?", ("mem-1",)) == 0,
            "Units survived delete",
        )
        require(
            count_rows(repository, "memory_entity", "memory_id=?", ("mem-1",)) == 0,
            "Entities survived delete",
        )
        require(
            count_rows(repository, "memory_tag", "memory_id=?", ("mem-1",)) == 0,
            "Tags survived delete",
        )
        fts_left = count_rows(
            repository,
            "memory_fts",
            "rowid IN (SELECT rowid FROM retrieval_unit WHERE memory_id=?)",
            ("mem-1",),
        )
        require(fts_left == 0, "FTS rows survived delete")
        with repository._transaction() as connection:
            require(
                not connection.execute("PRAGMA foreign_key_check").fetchone(),
                "Foreign key check failed",
            )

        direct_record(repository, "mem-3", "sess-3", "FictionalAgent", MEMORY_XML)
        with repository._transaction(write=True) as connection:
            connection.execute(
                "CREATE TRIGGER fail_unit_delete BEFORE DELETE ON retrieval_unit "
                "BEGIN SELECT RAISE(ABORT, 'fictional trigger'); END"
            )
        try:
            repository.delete_records(["mem-2", "mem-3"], "FictionalAgent")
            raise AssertionError("Failing trigger did not abort delete transaction")
        except sqlite3.DatabaseError:
            pass
        require(
            repository.get_record("mem-2", "FictionalAgent") is not None,
            "Partial deletion left canonical rows missing",
        )
        require(
            repository.get_record("mem-3", "FictionalAgent") is not None,
            "Partial deletion removed later records",
        )
        require(
            count_rows(repository, "retrieval_unit", "memory_id=?", ("mem-2",)) > 0,
            "Partial deletion removed units",
        )
        with repository._transaction(write=True) as connection:
            connection.execute("DROP TRIGGER fail_unit_delete")

        removed = repository.delete_records(["mem-2"], "OtherAgent")
        require(removed == 0, "Agent scope not enforced on delete")
        removed = repository.delete_records_by_session("sess-3")
        require(removed == 1, "Session delete count mismatch")
        require(
            repository.get_record("mem-3", "FictionalAgent") is None,
            "Session delete left records",
        )

        direct_record(repository, "mem-4", "sess-4", "FictionalAgent", MEMORY_XML)
        removed = repository.delete_records_matching('"alphaservice"', "FictionalAgent")
        require(removed == 2, "FTS topic delete count mismatch")
        direct_record(repository, "mem-5", "sess-5", "FictionalAgent", MEMORY_XML)
        direct_record(repository, "mem-6", "sess-6", "OtherAgent", MEMORY_XML)
        removed = repository.delete_records_matching('"alphaservice"', "FictionalAgent")
        require(removed == 1, "FTS topic delete did not stay agent-scoped")
        require(
            repository.get_record("mem-6", "OtherAgent") is not None,
            "FTS topic delete crossed agent scope",
        )
    print("PASS checks_m505_atomic_delete")


def checks_m506_retention():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "memory.sqlite3"
        repository = SQLiteMemoryRepository(db_path)
        old_ts = (datetime.now(UTC) - timedelta(days=90)).timestamp()
        direct_record(
            repository,
            "old-1",
            "sess-old",
            "FictionalAgent",
            MEMORY_XML,
            created_at=old_ts,
        )
        direct_record(repository, "new-1", "sess-new", "FictionalAgent", MEMORY_XML)

        service = SQLiteMemoryService(db_path, llm_service=FictionalLLM())
        require(
            service._repository.get_record("old-1", "FictionalAgent") is not None,
            "Initialization deleted old memories",
        )
        removed = service.cleanup_old_memories(months=1)
        require(removed == 1, "Retention count not observable")
        require(
            service._repository.get_record("old-1", "FictionalAgent") is None,
            "Retention did not remove old memory",
        )
        require(
            service._repository.get_record("new-1", "FictionalAgent") is not None,
            "Retention removed fresh memory",
        )
        service.shutdown()
    print("PASS checks_m506_retention")


def checks_m507_consolidation_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        repository = SQLiteMemoryRepository(Path(tmp) / "memory.sqlite3")
        direct_record(
            repository,
            "sess-cur_FictionalAgent",
            "sess-cur",
            "FictionalAgent",
            MEMORY_XML,
        )
        direct_record(repository, "rel-1", "sess-rel", "FictionalAgent", RELATED_XML)
        direct_record(repository, "unr-1", "sess-unr", "FictionalAgent", UNRELATED_XML)
        direct_record(repository, "oth-1", "sess-oth", "OtherAgent", RELATED_XML)

        persistence = SQLiteMemoryPersistence(repository)
        operation_data = {"session_id": "sess-cur", "agent_name": "FictionalAgent"}
        built = SimpleNamespace(
            memory_data=MEMORY_DATA,
            header="Fictional alpha headline",
            document=MEMORY_XML,
        )
        candidates = persistence.consolidation_candidates(
            operation_data, built, "sess-cur_FictionalAgent"
        )
        ids = [candidate["id"] for candidate in candidates]
        require("rel-1" in ids, "Related memory not found via entity/FTS candidates")
        require("unr-1" not in ids, "Unrelated memory admitted as candidate")
        require("oth-1" not in ids, "Candidate query crossed agent scope")
        require(
            "sess-cur_FictionalAgent" not in ids,
            "Current record returned as its own candidate",
        )
        require(len(candidates) <= 5, "Consolidation candidates not bounded")

        for index in range(8):
            direct_record(
                repository,
                f"many-{index}",
                f"sess-many-{index}",
                "FictionalAgent",
                RELATED_XML,
            )
        bounded = persistence.consolidation_candidates(
            operation_data, built, "sess-cur_FictionalAgent"
        )
        require(len(bounded) <= 5, "Bounded candidate limit exceeded")

        with repository._transaction() as connection:
            vector_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_document_vector'"
            ).fetchone()
        require(vector_table is None, "Consolidation created document vectors")
    print("PASS checks_m507_consolidation_candidates")


def checks_m508_concurrency():
    with tempfile.TemporaryDirectory() as tmp:
        service = SQLiteMemoryService(
            Path(tmp) / "memory.sqlite3", llm_service=FictionalLLM()
        )
        thread = service._worker._memory_thread
        require(thread is not None and thread.is_alive(), "Worker thread not running")
        require(thread.name == "SQLiteMemoryWorker", "Worker thread name mismatch")

        errors = []

        def store_task(agent_name, session_id):
            try:
                service.store_conversation(
                    "fictional user",
                    ["fictional assistant"],
                    agent_name=agent_name,
                    session_id=session_id,
                )
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(
                target=store_task, args=(f"Agent{index % 2}", f"sess-{index}")
            )
            for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        require(not errors, "Concurrent stores raised errors")
        require(
            wait_for(lambda: count_rows(service._repository, "memory_record") >= 8),
            "Concurrent stores lost writes",
        )
        require(
            count_rows(service._repository, "memory_record") == 8,
            "Concurrent stores duplicated writes",
        )

        pending = [
            service.store_conversation(
                "fictional user",
                ["fictional assistant"],
                agent_name="DrainAgent",
                session_id=f"drain-{index}",
            )
            for index in range(4)
        ]
        require(all(len(ids) == 1 for ids in pending), "Pending stores not queued")
        service.shutdown()
        require(
            wait_for(
                lambda: (
                    count_rows(
                        service._repository,
                        "memory_record",
                        "agent_name=?",
                        ("DrainAgent",),
                    )
                    == 4
                )
            ),
            "Shutdown lost pending operations",
        )
        require(
            not service._worker._memory_thread.is_alive(),
            "Worker thread survived shutdown",
        )
    print("PASS checks_m508_concurrency")


def checks_phase5_gate():
    gate_modules_before = set(sys.modules)
    with tempfile.TemporaryDirectory() as tmp:
        service = SQLiteMemoryService(
            Path(tmp) / "memory.sqlite3", llm_service=FictionalLLM()
        )
        service.store_conversation(
            "fictional user alpha",
            ["fictional assistant"],
            agent_name="GateAgent",
            session_id="gate-1",
        )
        require(
            wait_for(
                lambda: (
                    service._repository.get_record("gate-1_GateAgent", "GateAgent")
                    is not None
                )
            ),
            "Gate write failed",
        )
        read = service.retrieve_memory("AlphaService fictional", agent_name="GateAgent")
        require("AlphaService" in read, "Gate read failed")
        headers = service.list_memory_headers(agent_name="GateAgent")
        require(len(headers) == 1, "Gate header listing failed")
        forgotten = service.forget_ids(["gate-1_GateAgent"], "GateAgent")
        require(forgotten["count"] == 1, "Gate forget failed")
        service.store_conversation(
            "fictional user beta",
            ["fictional assistant"],
            agent_name="GateAgent",
            session_id="gate-2",
        )
        require(
            wait_for(
                lambda: (
                    service._repository.get_record("gate-2_GateAgent", "GateAgent")
                    is not None
                )
            ),
            "Gate second write failed",
        )
        evolved = service.mark_memories_evolved(["gate-2_GateAgent"], "GateAgent")
        require(evolved == 1, "Gate evolution failed")
        deleted = service.delete_by_conversation_id("gate-2")
        require(deleted["count"] == 1, "Gate deletion failed")
        service.shutdown()
    require(
        "chromadb" not in set(sys.modules) - gate_modules_before
        or "chromadb" not in sys.modules,
        "Gate flow required Chroma",
    )
    print("PASS checks_phase5_gate")


def main():
    checks_policy_freeze()
    checks_m500_cwd_capture()
    checks_m501_builder()
    checks_m502_m503_m504_service()
    checks_m505_atomic_delete()
    checks_m506_retention()
    checks_m507_consolidation_candidates()
    checks_m508_concurrency()
    checks_phase5_gate()
    print(
        "PASS Phase 5 SQLite write path, CWD capture, builder, retention, atomic deletes, consolidation, concurrency and gate checks"
    )
    print(
        "Fictional data only; no network, embeddings, Chroma dependency, private memory or sealed Phase 3 artifacts were used."
    )


if __name__ == "__main__":
    main()
