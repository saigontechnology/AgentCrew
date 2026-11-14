"""Fictional Phase 6 checks: memory engine selection, optional Chroma extra, auto migration.

No network, embeddings, private memory, or sealed Phase 3 artifacts are used.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
executable = Path(sys.executable)
if executable.is_symlink():
    executable = Path(os.path.abspath(sys.executable))
for site in (executable.parent.parent / "lib").glob("python*/site-packages"):
    site_path = str(site)
    if site_path not in sys.path:
        sys.path.append(site_path)

import chromadb

from AgentCrew.modules.memory import engine_selection
from AgentCrew.modules.memory.benchmark import POLICY
from AgentCrew.modules.memory.sqlite_repository import SQLiteMemoryRepository
from AgentCrew.modules.memory.sqlite_service import SQLiteMemoryService

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
SQLITE_CHAIN = (
    "AgentCrew/modules/memory/engine_selection.py",
    "AgentCrew/modules/memory/sqlite_service.py",
    "AgentCrew/modules/memory/sqlite_repository.py",
    "AgentCrew/modules/memory/memory_worker.py",
    "AgentCrew/modules/memory/memory_record_builder.py",
    "AgentCrew/modules/memory/retrieval.py",
    "AgentCrew/modules/memory/record_parser.py",
    "AgentCrew/modules/memory/types.py",
    "AgentCrew/modules/memory/base_service.py",
)
MEMORY_XML = (
    "<MEMORY><HEAD>Fictional alpha headline</HEAD><FLOW>"
    "<OUTCOME>AlphaService handled the fictional alpha outcome</OUTCOME></FLOW></MEMORY>"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fictional_chroma_store(directory: Path, agent: str = "FictionalAgent") -> None:
    """Create a disposable offline Chroma store with explicit fictional vectors."""
    client = chromadb.PersistentClient(path=str(directory))
    collection = client.create_collection("conversation")
    collection.upsert(
        ids=[f"sess-1_{agent}"],
        documents=[MEMORY_XML],
        embeddings=[[0.1, 0.2, 0.3]],
        metadatas=[
            {
                "agent": agent,
                "date": time.time(),
                "session_id": "sess-1",
                "type": "conversation",
                "header": "Fictional alpha headline",
            }
        ],
    )
    with contextlib.suppress(Exception):
        client._system.stop()


def module_scope_chromadb_imports(relative: str) -> list[str]:
    """Runtime module-scope chromadb imports, ignoring TYPE_CHECKING guards."""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.If):
            continue
        if isinstance(node, ast.Import):
            bad.extend(a.name for a in node.names if a.name.split(".")[0] == "chromadb")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "chromadb"
            and node.level == 0
        ):
            bad.append(node.module)
    return bad


def checks_engine_config() -> None:
    require(
        engine_selection.resolve_memory_engine({}) == "sqlite",
        "Default engine must be sqlite",
    )
    require(
        engine_selection.resolve_memory_engine({"memory_engine": "chromadb"})
        == "chromadb",
        "chromadb engine must be selectable",
    )
    require(
        engine_selection.resolve_memory_engine({"memory_engine": " SQLite "})
        == "sqlite",
        "Engine value must be case-insensitive",
    )
    try:
        engine_selection.resolve_memory_engine({"memory_engine": "lancedb"})
        raise AssertionError("Invalid engine must fail fast")
    except engine_selection.MemoryEngineError as error:
        text = str(error)
        require("lancedb" in text, "Engine error must name the invalid value")
        require(
            "sqlite" in text and "chromadb" in text,
            "Engine error must name the valid values",
        )
    print("PASS checks_engine_config")


def checks_selection_wiring() -> None:
    source = (ROOT / "AgentCrew/setup.py").read_text(encoding="utf-8")
    require(
        "resolve_memory_engine(global_settings)" in source,
        "setup must resolve the engine from global_settings",
    )
    require("SQLiteMemoryService(" in source, "setup must construct the SQLite engine")
    require("ChromaMemoryService(" in source, "setup must construct the Chroma engine")
    require(
        "require_chromadb()" in source,
        "chromadb engine must require the extra at startup",
    )
    require(
        "ensure_sqlite_store(" in source, "setup must run the store decision matrix"
    )
    for relative in (
        "AgentCrew/modules/memory/chroma_service.py",
        "AgentCrew/modules/memory/sqlite_service.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        require("def shutdown(self)" in text, f"{relative} must keep a shutdown method")
    for relative in SQLITE_CHAIN:
        bad = module_scope_chromadb_imports(relative)
        require(
            not bad, f"Sqlite chain module-scope chromadb import in {relative}: {bad}"
        )
    print("PASS checks_selection_wiring")


def checks_sqlite_chain_chromadb_free() -> None:
    sentinel = sys.modules.get("chromadb")
    sys.modules["chromadb"] = None
    try:
        with tempfile.TemporaryDirectory(prefix="phase6-free-") as tmp:
            base = Path(tmp)
            db = base / "memory_db" / "agent_memory.sqlite3"
            work = base / "memory_db" / "migration-work"
            result = engine_selection.ensure_sqlite_store(db, base / "no-chroma", work)
            require(result == db, "Fresh start must return the configured db path")
            require(
                not db.exists(),
                "Fresh start must not create the db before service init",
            )
        try:
            engine_selection.require_chromadb()
            raise AssertionError("Blocked chromadb must fail the requirement")
        except engine_selection.MemoryEngineError as error:
            require(
                "uv sync --extra chromadb" in str(error),
                "Install hint must mention the uv extra",
            )
            require(
                "agentcrew-ai[chromadb]" in str(error),
                "Install hint must mention the pip extra",
            )
    finally:
        if sentinel is None:
            sys.modules.pop("chromadb", None)
        else:
            sys.modules["chromadb"] = sentinel
    print("PASS checks_sqlite_chain_chromadb_free")


def checks_auto_migration() -> None:
    from verification.memory.capture_baseline import fingerprint

    with tempfile.TemporaryDirectory(prefix="phase6-migrate-") as tmp:
        base = Path(tmp)
        src = base / "chroma-src"
        fictional_chroma_store(src)
        before = fingerprint(src)
        db = base / "g" / "agent_memory.sqlite3"
        work = base / "g" / "migration-work"
        engine_selection.ensure_sqlite_store(db, src, work)
        require(db.is_file(), "Migration must produce the SQLite store")
        report = json.loads((work / "report.json").read_text(encoding="utf-8"))
        require(report["status"] == "verified", "Migration report must be verified")
        require(
            report.get("inserted") == 1 and report.get("verified") == 1,
            "Migration counts must match the fictional record",
        )
        require(
            report.get("verified_canonical_fingerprint")
            == report.get("expected_canonical_fingerprint"),
            "Canonical checksums must match",
        )
        require(fingerprint(src) == before, "Chroma source must stay untouched")
        record = SQLiteMemoryRepository(db).get_record(
            "sess-1_FictionalAgent", "FictionalAgent"
        )
        require(
            record is not None and record.document == MEMORY_XML,
            "Migrated record must round-trip",
        )
        second = engine_selection.ensure_sqlite_store(db, src, work)
        require(second == db, "Completed migration must be reused without re-migration")
        service = SQLiteMemoryService(db_path=db, llm_service=None)
        try:
            require(
                service.list_memory_headers(agent_name="FictionalAgent"),
                "Migrated store must serve headers",
            )
        finally:
            service.shutdown()
    print("PASS checks_auto_migration")


def checks_fail_fast_without_chromadb() -> None:
    with tempfile.TemporaryDirectory(prefix="phase6-failfast-") as tmp:
        base = Path(tmp)
        src = base / "chroma-src"
        fictional_chroma_store(src)
        db = base / "g" / "agent_memory.sqlite3"
        work = base / "g" / "migration-work"
        sentinel = sys.modules.get("chromadb")
        sys.modules["chromadb"] = None
        try:
            try:
                engine_selection.ensure_sqlite_store(db, src, work)
                raise AssertionError("Chroma data without chromadb must fail fast")
            except engine_selection.MemoryEngineError as error:
                text = str(error)
                require(
                    "uv sync --extra chromadb" in text,
                    "Fail-fast must mention the uv extra",
                )
                require(
                    "agentcrew-ai[chromadb]" in text,
                    "Fail-fast must mention the pip extra",
                )
                require(
                    "automatic migration" in text,
                    "Fail-fast must explain the migration need",
                )
                require(
                    "migrate_chroma_memory_to_sqlite" in text,
                    "Fail-fast must mention the manual migration fallback",
                )
                require(
                    "memory_engine is 'chromadb'" not in text,
                    "Migration fail-fast must not claim the engine is chromadb",
                )
            require(
                not db.exists(), "Fail-fast must never silently start an empty store"
            )
        finally:
            if sentinel is None:
                sys.modules.pop("chromadb", None)
            else:
                sys.modules["chromadb"] = sentinel
    print("PASS checks_fail_fast_without_chromadb")


def checks_migration_failure_abort() -> None:
    with tempfile.TemporaryDirectory(prefix="phase6-abort-") as tmp:
        base = Path(tmp)
        src = base / "chroma-src"
        client = chromadb.PersistentClient(path=str(src))
        collection = client.create_collection("conversation")
        collection.upsert(
            ids=["sess-1_FictionalAgent"],
            documents=[MEMORY_XML],
            embeddings=[[0.1, 0.2, 0.3]],
            metadatas=[{"session_id": "sess-1", "date": time.time()}],
        )
        with contextlib.suppress(Exception):
            client._system.stop()
        db = base / "g" / "agent_memory.sqlite3"
        work = base / "g" / "migration-work"
        try:
            engine_selection.ensure_sqlite_store(db, src, work)
            raise AssertionError("Unmappable record must abort the migration")
        except Exception as error:
            require(
                "unmappable" in str(error)
                or "agent" in str(error)
                or "metadata" in str(error),
                f"Abort must carry the mapping reason: {error}",
            )
        report = json.loads((work / "report.json").read_text(encoding="utf-8"))
        require(
            report["status"] == "blocked",
            "Failed migration must record a blocked report",
        )
        require(report["reason"], "Blocked report must carry the reason")
    print("PASS checks_migration_failure_abort")


def checks_mid_apply_crash_resume() -> None:
    from AgentCrew.modules.memory import sqlite_repository as repo_mod

    with tempfile.TemporaryDirectory(prefix="phase6-crash-") as tmp:
        base = Path(tmp)
        src = base / "chroma-src"
        fictional_chroma_store(src)
        db = base / "g" / "agent_memory.sqlite3"
        work = base / "g" / "migration-work"
        original = repo_mod.SQLiteMemoryRepository.apply_migration_batch

        def crashing(
            self, records, source_fingerprint, resume=False, fail_on_parse_error=False
        ):
            original(
                self,
                records,
                source_fingerprint,
                resume=resume,
                fail_on_parse_error=fail_on_parse_error,
            )
            raise RuntimeError("fictional mid-apply crash")

        repo_mod.SQLiteMemoryRepository.apply_migration_batch = crashing
        try:
            try:
                engine_selection.ensure_sqlite_store(db, src, work)
                raise AssertionError("Simulated crash must propagate")
            except RuntimeError as error:
                require(
                    "fictional mid-apply crash" in str(error),
                    "Crash must abort startup",
                )
        finally:
            repo_mod.SQLiteMemoryRepository.apply_migration_batch = original
        require(db.is_file(), "Crashed migration may leave a partial store")
        engine_selection.ensure_sqlite_store(db, src, work)
        report = json.loads((work / "report.json").read_text(encoding="utf-8"))
        require(report["status"] == "verified", "Resume must complete the migration")
        require(
            report.get("skipped", 0) >= 1, "Resume must skip already-applied records"
        )
        record = SQLiteMemoryRepository(db).get_record(
            "sess-1_FictionalAgent", "FictionalAgent"
        )
        require(record is not None, "Resumed store must contain the record")
    print("PASS checks_mid_apply_crash_resume")


def checks_packaging() -> None:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [str(item) for item in data["project"]["dependencies"]]
    extras = data["project"]["optional-dependencies"]
    require(
        not any(item.startswith("chromadb") for item in dependencies),
        "chromadb must not be a hard dependency",
    )
    require(
        not any(item.startswith("voyageai") for item in dependencies),
        "voyageai must not be a hard dependency",
    )
    require("chromadb" in extras, "The chromadb extra must exist")
    extra = [str(item) for item in extras["chromadb"]]
    require(
        any(item.startswith("chromadb==") for item in extra),
        "The extra must pin chromadb",
    )
    require(
        any(item.startswith("voyageai==") for item in extra),
        "The extra must pin voyageai",
    )
    require("cpu" in extras and "gui" in extras, "Existing extras must stay intact")
    voyage_sources = []
    for path in (ROOT / "AgentCrew").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import voyageai" in text or "from voyageai" in text:
            voyage_sources.append(path.name)
    require(
        set(voyage_sources) == {"voyageai_ef.py"},
        f"voyageai must only be imported by the Chroma embedding path: {voyage_sources}",
    )
    print("PASS checks_packaging")


def checks_frozen_policy() -> None:
    require(POLICY == FROZEN_POLICY, "Frozen retrieval policy changed")
    print("PASS checks_frozen_policy")


def checks_mem604_surfaces() -> None:
    chroma_sites = []
    sqlite_sites = []
    for path in (ROOT / "AgentCrew").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ChromaMemoryService(" in text and "class ChromaMemoryService" not in text:
            chroma_sites.append(str(path.relative_to(ROOT)))
        if "SQLiteMemoryService(" in text and "class SQLiteMemoryService" not in text:
            sqlite_sites.append(str(path.relative_to(ROOT)))
    require(
        chroma_sites == ["AgentCrew/setup.py"],
        f"Chroma construction must stay centralized: {chroma_sites}",
    )
    require(
        sqlite_sites == ["AgentCrew/setup.py"],
        f"SQLite construction must stay centralized: {sqlite_sites}",
    )
    for surface in (
        "AgentCrew/modules/agents/local_agent.py",
        "AgentCrew/modules/agents/tools/delegate.py",
        "AgentCrew/modules/a2a/agent_executor.py",
        "AgentCrew/modules/acp/turn_executor.py",
        "AgentCrew/modules/agents/cursor_agent.py",
    ):
        text = (ROOT / surface).read_text(encoding="utf-8")
        require(
            "MemoryService(" not in text,
            f"{surface} must receive the service instead of constructing one",
        )
    print("PASS checks_mem604_surfaces")


def main() -> int:
    checks_engine_config()
    checks_selection_wiring()
    checks_sqlite_chain_chromadb_free()
    checks_auto_migration()
    checks_fail_fast_without_chromadb()
    checks_migration_failure_abort()
    checks_mid_apply_crash_resume()
    checks_packaging()
    checks_frozen_policy()
    checks_mem604_surfaces()
    print("Phase 6 verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
