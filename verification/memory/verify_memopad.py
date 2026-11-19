"""Offline memopad verification using fictional data and disposable SQLite stores.

Run: uv run python verification/memory/verify_memopad.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.base_service import BaseMemoryService
from AgentCrew.modules.memory.memopad import (
    MemopadCandidate,
    format_memopad_stats,
    parse_memopad_candidates,
)
from AgentCrew.modules.memory.memory_record_builder import (
    BuiltMemoryRecord,
    MemoryRecordBuilder,
    parse_extraction,
)
from AgentCrew.modules.memory.project_identity import resolve_project_identity
from AgentCrew.modules.memory.sqlite_repository import (
    SCHEMA_VERSION,
    SQLiteMemoryError,
    SQLiteMemoryRepository,
)
from AgentCrew.modules.memory.sqlite_service import (
    SQLiteMemoryPersistence,
    SQLiteMemoryService,
)
from AgentCrew.modules.memory.types import MemoryRecord

MEMOPAD_BASE_COMMIT = "661df4b0b43be32f6cb10265c806161a43a1253c"
PROTECTED_MEMORY_PATHS = (
    "AgentCrew/modules/agents/context_manager.py",
    "AgentCrew/modules/agents/memory_coordinator.py",
    "AgentCrew/modules/memory/retrieval.py",
    "AgentCrew/modules/memory/memory_worker.py",
)
STATEMENT = "Use bounded retries for fictional requests."
DOCUMENT = f"<MEMORY><HEAD>Fictional retry policy</HEAD><CONVERSATION_NOTES><NOTE>{STATEMENT}</NOTE></CONVERSATION_NOTES></MEMORY>"
PROPOSAL = {
    "kind": "constraint",
    "title": "Bounded retries",
    "statement": STATEMENT,
    "subject_key": "retry-policy",
}
PAYLOAD = {
    "head": "Fictional policy",
    "flow": {"intent": "Plan", "exploration": "Checked", "outcome": "Agreed"},
    "notes": [STATEMENT],
    "memopad": [PROPOSAL],
}


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class MemopadVerification(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = SQLiteMemoryRepository(self.root / "memory.sqlite3")
        self.project = resolve_project_identity(self.root)
        self.candidate = MemopadCandidate.model_validate(PROPOSAL)
        self.now = datetime.now(UTC).timestamp()

    def record(self, key="m1", agent="FictionalAgent", timestamp=None):
        timestamp = self.now if timestamp is None else timestamp
        record = MemoryRecord(
            id=key,
            agent_name=agent,
            session_id=key,
            document=DOCUMENT,
            created_at=timestamp,
            updated_at=timestamp,
            metadata={},
        )
        self.repository.upsert_record(record)
        return record

    def collect(self, key="m1", agent="FictionalAgent", project=None, candidate=None):
        return self.repository.collect_memopad(
            key, agent, project or self.project, [candidate or self.candidate]
        )

    def rows(self, table):
        with sqlite3.connect(self.repository.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    def downgrade(self, version):
        with sqlite3.connect(self.repository.path) as connection:
            connection.execute("DROP TRIGGER memopad_source_deleted")
            connection.execute("DROP TABLE memopad_evidence")
            connection.execute("DROP TABLE memopad")
            if version == 1:
                connection.execute("DROP TABLE memory_resource_entity")
                connection.execute("DROP TABLE memory_resource")
            connection.execute("UPDATE schema_version SET version=?", (version,))
            connection.execute(f"PRAGMA user_version={version}")

    def test_explicit_identity_is_stable_bounded_and_side_effect_free(self):
        marker = self.root / ".agentcrew"
        marker.mkdir()
        config = marker / "project.json"
        config.write_text(
            json.dumps({"id": "fictional-project-123", "name": "Fictional project"})
        )
        nested = self.root / "src"
        nested.mkdir()
        before = config.read_bytes()
        identity = resolve_project_identity(nested)
        self.assertEqual(identity, resolve_project_identity(self.root))
        self.assertEqual(identity.name, "Fictional project")
        self.assertNotIn(str(self.root), identity.key)
        self.assertEqual(config.read_bytes(), before)
        config.write_text("x" * 4097)
        fallback = resolve_project_identity(self.root)
        self.assertTrue(fallback.key.startswith("workspace:"))
        self.assertEqual(fallback, resolve_project_identity(self.root))
        config.unlink()
        resolve_project_identity(self.root)
        self.assertFalse(config.exists())

    def test_git_worktrees_and_normalized_remote_identity(self):
        repository = self.root / "repo"
        repository.mkdir()
        git(repository, "init", "-q")
        git(
            repository,
            "-c",
            "user.name=Fiction",
            "-c",
            "user.email=fiction@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        )
        worktree = self.root / "linked"
        git(repository, "worktree", "add", "-qb", "fictional-branch", str(worktree))
        self.assertEqual(
            resolve_project_identity(repository), resolve_project_identity(worktree)
        )
        git(
            repository,
            "remote",
            "add",
            "origin",
            "https://fiction:credential@example.test/team/repo.git",
        )
        first = resolve_project_identity(repository)
        git(repository, "remote", "set-url", "origin", "git@example.test:team/repo.git")
        self.assertEqual(first, resolve_project_identity(worktree))
        git(
            repository,
            "remote",
            "set-url",
            "origin",
            "https://example.test:443/team/repo.git",
        )
        self.assertEqual(first, resolve_project_identity(repository))
        git(
            repository,
            "remote",
            "set-url",
            "origin",
            "https://example.test:8443/team/repo.git",
        )
        self.assertNotEqual(first.key, resolve_project_identity(repository).key)
        self.assertNotIn("credential", first.key)
        self.assertNotIn(str(repository), first.key)
        marker = repository / ".agentcrew"
        marker.mkdir()
        (marker / "project.json").write_text(
            json.dumps({"id": "explicit-fixture", "name": "Explicit"})
        )
        self.assertEqual(
            resolve_project_identity(repository), resolve_project_identity(worktree)
        )

    def test_separate_git_directories_do_not_collide_under_a_shared_parent(self):
        identities = []
        for name in ("first", "second"):
            repository = self.root / name
            repository.mkdir()
            git(
                repository,
                "init",
                "-q",
                f"--separate-git-dir={self.root / (name + '-metadata')}",
            )
            identities.append(resolve_project_identity(repository))
        self.assertNotEqual(identities[0].key, identities[1].key)

    def test_git_failure_falls_back_without_path_key(self):
        with patch(
            "AgentCrew.modules.memory.project_identity.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 1),
        ):
            identity = resolve_project_identity(self.root)
        self.assertTrue(identity.key.startswith("workspace:"))
        self.assertNotIn(str(self.root), identity.key)

    def test_schema_upgrade_preserves_records_fts_and_does_not_backfill(self):
        self.record()
        original = self.rows("memory_record")
        units = self.rows("retrieval_unit")
        self.downgrade(2)
        with self.assertRaises(SQLiteMemoryError):
            SQLiteMemoryRepository(self.repository.path, read_only=True)
        self.repository = SQLiteMemoryRepository(self.repository.path)
        self.assertEqual(original, self.rows("memory_record"))
        self.assertEqual(units, self.rows("retrieval_unit"))
        self.assertEqual(self.rows("schema_version"), [{"version": SCHEMA_VERSION}])
        self.assertEqual(self.repository.memopad_stats()["total"], 0)
        self.repository.check_integrity()
        SQLiteMemoryRepository(
            self.repository.path, read_only=True
        ).check_read_only_integrity()

    def test_schema_one_upgrade_also_preserves_canonical_memory(self):
        self.record()
        original = self.rows("memory_record")[0]
        self.downgrade(1)
        self.repository = SQLiteMemoryRepository(self.repository.path)
        current = self.rows("memory_record")[0]
        for field in (
            "id",
            "document",
            "document_checksum",
            "metadata_json",
            "metadata_checksum",
        ):
            self.assertEqual(current[field], original[field])
        self.assertEqual(self.repository.memopad_stats()["total"], 0)
        self.repository.check_integrity()

    def test_failed_schema_upgrade_rolls_back_non_destructively(self):
        from AgentCrew.modules.memory import sqlite_repository as module

        self.record()
        original = self.rows("memory_record")
        self.downgrade(2)
        with (
            patch.object(
                module, "MEMOPAD_SCHEMA", (*module.MEMOPAD_SCHEMA, "INVALID SQL")
            ),
            self.assertRaises(sqlite3.OperationalError),
        ):
            SQLiteMemoryRepository(self.repository.path)
        self.assertEqual(self.rows("schema_version"), [{"version": 2}])
        self.assertEqual(self.rows("memory_record"), original)
        with sqlite3.connect(self.repository.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='memopad'"
                ).fetchone()
            )
        self.repository = SQLiteMemoryRepository(self.repository.path)
        self.repository.check_integrity()

    def test_optional_candidates_fail_closed_without_losing_canonical_extraction(self):
        invalids = [
            [PROPOSAL] * 9,
            [{**PROPOSAL, "kind": "unsupported"}],
            [{**PROPOSAL, "confidence": 1}],
            [{**PROPOSAL, "statement": "x" * 1201}],
            [{**PROPOSAL, "title": " "}],
            [{**PROPOSAL, "subject_key": "bad\u0000"}],
            [{**PROPOSAL, "evidence": ["unknown-id"]}],
            None,
            "not a list",
        ]
        for value in invalids:
            with self.subTest(value_type=type(value).__name__):
                parsed = parse_extraction(json.dumps({**PAYLOAD, "memopad": value}))
                self.assertEqual(parsed.head, PAYLOAD["head"])
                self.assertEqual(parsed.memopad, [])
        self.assertEqual(len(parse_memopad_candidates([PROPOSAL] * 8)), 8)
        self.assertEqual(
            parse_memopad_candidates(
                [{**PROPOSAL, "statement": "api_key=fictional-secret"}]
            ),
            [],
        )
        self.assertEqual(
            parse_extraction(
                json.dumps(
                    {key: value for key, value in PAYLOAD.items() if key != "memopad"}
                )
            ).memopad,
            [],
        )

    def test_persistence_idempotency_provenance_and_reindex(self):
        record = self.record()
        self.collect()
        self.collect()
        item = self.rows("memopad")[0]
        evidence = self.rows("memopad_evidence")[0]
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["support_count"], 1)
        self.assertEqual(item["confidence"], 0.35)
        self.assertEqual(evidence["memory_id"], record.id)
        self.assertTrue(evidence["memory_checksum"])
        self.assertTrue(evidence["unit_id"])
        self.assertTrue(evidence["unit_checksum"])
        self.repository.upsert_record(
            replace(record, document=DOCUMENT.replace("policy", "updated policy"))
        )
        self.assertEqual(len(self.rows("memopad_evidence")), 1)
        self.assertEqual(self.repository.memopad_stats()["total"], 1)
        self.repository.check_integrity()

    def test_scope_and_case_sensitive_statement_identity(self):
        self.record()
        with self.assertRaises(SQLiteMemoryError):
            self.collect(agent="OtherAgent")
        self.record("m2", "OtherAgent")
        self.collect()
        self.collect("m2", "OtherAgent")
        other = self.root / "other"
        other.mkdir()
        project = resolve_project_identity(other)
        self.collect(project=project)
        self.collect(
            candidate=MemopadCandidate.model_validate(
                {**PROPOSAL, "statement": STATEMENT.upper()}
            )
        )
        self.assertEqual(self.repository.memopad_stats()["total"], 4)
        self.assertEqual(self.repository.memopad_stats("OtherAgent")["total"], 1)
        self.assertEqual(
            self.repository.memopad_stats(project_key=project.key)["total"], 1
        )

    def test_multiple_evidence_sources_and_final_source_deletion_purges_content(self):
        self.record()
        self.record("m2")
        self.collect()
        self.collect("m2")
        stats = self.repository.memopad_stats()
        self.assertEqual(stats["source_memories"], 2)
        self.assertEqual(stats["corroborated"], 1)
        self.assertEqual(self.rows("memopad")[0]["support_count"], 2)
        self.repository.delete_record("m1", "FictionalAgent")
        self.assertEqual(self.rows("memopad")[0]["support_count"], 1)
        self.repository.delete_record("m2", "FictionalAgent")
        self.assertEqual(self.repository.memopad_stats()["total"], 0)
        self.assertEqual(self.rows("memopad_evidence"), [])
        self.assertEqual(self.repository.memopad_stats()["orphans"], 0)

    def test_soft_delete_cleans_evidence_and_orphan(self):
        record = self.record()
        self.collect()
        with sqlite3.connect(self.repository.path) as connection:
            connection.execute("UPDATE memopad SET status='active'")
        self.repository.upsert_record(replace(record, status="deleted"))
        self.assertEqual(self.repository.memopad_stats()["total"], 0)

    def test_service_lifecycle_family_forget_topic_conversation_and_retention(self):
        service = SQLiteMemoryService(self.repository.path)
        service._worker.stop()
        self.addCleanup(service.shutdown)
        for mode in ("id", "topic", "conversation", "retention"):
            with self.subTest(mode=mode):
                old = (datetime.now(UTC) - timedelta(days=90)).timestamp()
                self.record("source", timestamp=old)
                self.record("target", timestamp=old)
                self.collect("source")
                self.collect("target")
                self.repository.apply_consolidation(
                    "target", "FictionalAgent", DOCUMENT, ["source"]
                )
                self.assertEqual(self.repository.memopad_stats()["source_memories"], 2)
                if mode == "id":
                    result = service.forget_ids(["source"], "FictionalAgent")
                elif mode == "topic":
                    result = service.forget_topic(
                        "fictional", agent_name="FictionalAgent"
                    )
                elif mode == "conversation":
                    result = service.delete_by_conversation_id("source")
                else:
                    with sqlite3.connect(self.repository.path) as connection:
                        connection.execute(
                            "UPDATE memory_record SET updated_at=?", (old,)
                        )
                    result = {"count": service.cleanup_old_memories(1)}
                self.assertEqual(result["count"], 2)
                self.assertEqual(service.get_memopad_stats()["total"], 0)
                self.repository.check_integrity()

    def test_canonical_persistence_survives_projection_failure_and_worker_continues(
        self,
    ):
        from AgentCrew.modules.memory.memory_worker import MemoryWorker

        persistence = SQLiteMemoryPersistence(self.repository)
        worker = MemoryWorker(persistence=persistence)
        built = BuiltMemoryRecord(
            {}, DOCUMENT, "Fictional policy", self.now, (self.candidate,)
        )
        worker._builder.build = AsyncMock(return_value=built)
        worker._consolidate_related_memories = AsyncMock()
        operation = {
            "session_id": "fixture",
            "agent_name": "FictionalAgent",
            "operation_id": "op",
            "cwd": str(self.root),
        }
        with patch.object(
            self.repository,
            "collect_memopad",
            side_effect=RuntimeError("fictional failure"),
        ):
            asyncio.run(worker._store_conversation_internal(operation))
        self.assertIsNotNone(
            self.repository.get_record("fixture_FictionalAgent", "FictionalAgent")
        )
        self.assertEqual(worker.current_conversation_context["fixture"], DOCUMENT)
        self.assertEqual(worker._store_count_by_session["fixture"], 1)
        operation["operation_id"] = "op-next"
        asyncio.run(worker._store_conversation_internal(operation))
        self.assertEqual(self.repository.memopad_stats()["total"], 1)

    def test_projection_transaction_failure_does_not_rollback_canonical_store(self):
        with sqlite3.connect(self.repository.path) as connection:
            connection.execute("""CREATE TRIGGER fictional_failure BEFORE INSERT ON memopad_evidence
                BEGIN SELECT RAISE(ABORT,'fictional failure'); END""")
        built = BuiltMemoryRecord(
            {}, DOCUMENT, "Fictional policy", self.now, (self.candidate,)
        )
        operation = {
            "session_id": "fixture",
            "agent_name": "FictionalAgent",
            "operation_id": "op",
            "cwd": str(self.root),
        }
        memory_id = SQLiteMemoryPersistence(self.repository).persist(operation, built)
        self.assertIsNotNone(self.repository.get_record(memory_id, "FictionalAgent"))
        self.assertEqual(self.repository.memopad_stats()["total"], 0)
        self.assertEqual(self.rows("memopad_evidence"), [])

    def test_builder_invalid_memopad_still_persists_canonical_memory(self):
        from verification.memory.verify_phase5 import FictionalHooks

        llm = SimpleNamespace(
            process_message=AsyncMock(
                return_value=json.dumps({**PAYLOAD, "memopad": [PROPOSAL] * 9})
            )
        )
        operation = {
            "user_message": "Fictional policy",
            "assistant_messages": ["Agreed"],
            "operation_id": "op",
            "session_id": "fixture",
            "agent_name": "FictionalAgent",
            "cwd": str(self.root),
        }
        with patch(
            "AgentCrew.modules.events.hooks.HookRegistry.get_instance",
            return_value=FictionalHooks(),
        ):
            built = asyncio.run(MemoryRecordBuilder(llm).build(operation))
        memory_id = SQLiteMemoryPersistence(self.repository).persist(operation, built)
        self.assertIsNotNone(self.repository.get_record(memory_id, "FictionalAgent"))
        self.assertEqual(built.memopad_candidates, ())
        self.assertEqual(self.repository.memopad_stats()["total"], 0)
        self.assertEqual(llm.process_message.call_count, 1)

    def test_builder_collects_without_inserting_memopad_into_canonical_document(self):
        from verification.memory.verify_phase5 import FictionalHooks

        llm = SimpleNamespace(
            process_message=AsyncMock(return_value=json.dumps(PAYLOAD))
        )
        operation = {
            "user_message": "Fictional policy",
            "assistant_messages": ["Agreed"],
            "operation_id": "op",
        }
        with patch(
            "AgentCrew.modules.events.hooks.HookRegistry.get_instance",
            return_value=FictionalHooks(),
        ):
            built = asyncio.run(MemoryRecordBuilder(llm).build(operation))
        self.assertEqual(len(built.memopad_candidates), 1)
        self.assertNotIn("memopad", built.document.lower())
        self.assertNotIn("memopad", json.dumps(built.memory_data).lower())
        self.assertEqual(llm.process_message.call_count, 1)

    def test_read_only_stats_command_and_unsupported_backend(self):
        from AgentCrew.modules.chat.message.command_processor import CommandProcessor
        from AgentCrew.modules.events import AppEvents

        self.record()
        self.collect()
        read_only = SQLiteMemoryRepository(self.repository.path, read_only=True)
        before = self.rows("memopad")
        stats = read_only.memopad_stats()
        self.assertEqual(stats["status"], {"candidate": 1})
        self.assertEqual(stats["kind"], {"constraint": 1})
        self.assertEqual(stats["agent"], {"FictionalAgent": 1})
        self.assertEqual(stats["project"], {self.project.key: 1})
        text = format_memopad_stats(stats)
        self.assertNotIn(STATEMENT, text)
        self.assertNotIn(str(self.root), text)
        handler = SimpleNamespace(
            bus=Mock(),
            memory_service=SimpleNamespace(get_memopad_stats=read_only.memopad_stats),
        )
        processor = CommandProcessor(handler)
        result = asyncio.run(processor.process_command("/memopad stats"))
        self.assertTrue(result.handled)
        handler.bus.emit_sync.assert_called_with(AppEvents.SYSTEM_MESSAGE, message=text)
        with self.assertRaises(NotImplementedError):
            BaseMemoryService.get_memopad_stats(object())
        handler.memory_service.get_memopad_stats = Mock(
            side_effect=NotImplementedError("SQLite required")
        )
        asyncio.run(processor.process_command("/memopad stats"))
        handler.bus.emit_sync.assert_called_with(
            AppEvents.ERROR, message="SQLite required"
        )
        self.assertEqual(before, self.rows("memopad"))

    def test_context_injection_files_unchanged(self):
        for revisions in ((MEMOPAD_BASE_COMMIT, "HEAD"), ("HEAD",)):
            changed = subprocess.check_output(
                [
                    "git",
                    "diff",
                    "--name-only",
                    *revisions,
                    "--",
                    *PROTECTED_MEMORY_PATHS,
                ],
                cwd=ROOT,
                text=True,
            ).splitlines()
            self.assertEqual(changed, [], "Protected memory boundary changed")
        for path in PROTECTED_MEMORY_PATHS:
            self.assertNotIn("memopad", (ROOT / path).read_text().lower(), path)

    def test_context_guard_rejects_committed_boundary_changes(self):
        for path in PROTECTED_MEMORY_PATHS:
            with (
                self.subTest(path=path),
                patch("subprocess.check_output", return_value=path + "\n"),
                self.assertRaises(AssertionError),
            ):
                self.test_context_injection_files_unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
