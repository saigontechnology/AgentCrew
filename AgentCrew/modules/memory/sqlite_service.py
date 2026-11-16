"""SQLite/FTS5 memory service: the Phase 5 standalone write path (MEM-502).

Not wired into setup composition; the user-visible default backend remains
Chroma until the blocked Phase 6 cutover is approved.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from . import provenance
from .base_service import BaseMemoryService
from .memory_record_builder import BuiltMemoryRecord, build_consolidation_query
from .memory_worker import MemoryWorker
from .retrieval import MemorySearchCoordinator, _words, format_search_result
from .sqlite_repository import SQLiteMemoryRepository
from .types import MemoryQuery, MemoryRecord, MemorySearchResult

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

SQLITE_MEMORY_WORKER_THREAD_NAME = "SQLiteMemoryWorker"
FORGET_TOPIC_MATCH_LIMIT = 100
CONSOLIDATION_CANDIDATE_LIMIT = 5
CONSOLIDATION_MIN_OVERLAP = 0.35


def fts_or_expression(text: str, max_tokens: int = 12) -> str:
    """Quote lexical tokens for a bounded OR FTS5 match expression."""
    tokens = []
    for token in text.split():
        if token.strip():
            tokens.append('"' + token.strip().replace('"', '""') + '"')
        if len(tokens) >= max_tokens:
            break
    return " OR ".join(tokens)


class SQLiteMemoryPersistence:
    """SQLite storage adapter: canonical writes and lexical candidates (MEM-502/507)."""

    def __init__(self, repository: SQLiteMemoryRepository):
        self._repository = repository

    def readiness_error(self) -> str | None:
        return None

    def _record_id(self, operation_data: dict[str, Any]) -> str:
        return f"{operation_data['session_id']}_{operation_data['agent_name']}"

    def persist(self, operation_data: dict[str, Any], built: BuiltMemoryRecord) -> str:
        memory_record = MemoryRecord(
            id=self._record_id(operation_data),
            agent_name=operation_data["agent_name"],
            session_id=operation_data["session_id"],
            document=built.document,
            created_at=built.timestamp,
            updated_at=built.timestamp,
            metadata={
                "timestamp": operation_data.get("timestamp"),
                "cwd": operation_data.get("cwd"),
            },
            cwd=operation_data.get("cwd"),
            header=built.header,
        )
        return self._repository.persist_turn(
            memory_record, operation_data["operation_id"]
        )

    def consolidation_candidates(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        memory_id: str,
    ) -> list[dict[str, Any]]:
        query_text = build_consolidation_query(built.memory_data)
        expression = fts_or_expression(query_text)
        candidates = self._repository.consolidation_candidates(
            memory_id,
            operation_data["agent_name"],
            expression,
            CONSOLIDATION_CANDIDATE_LIMIT,
        )
        query_words = _words(query_text)
        if not query_words:
            return []
        gated = []
        for candidate in candidates:
            if candidate.get("source") == "exact":
                gated.append(candidate)
                continue
            evidence_words = _words(
                f"{candidate.get('header') or ''} {candidate.get('evidence') or ''}"
            )
            overlap = len(query_words & evidence_words) / len(query_words)
            if overlap >= CONSOLIDATION_MIN_OVERLAP:
                gated.append(candidate)
        return gated[:CONSOLIDATION_CANDIDATE_LIMIT]

    def apply_consolidation(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        memory_id: str,
        merged_document: str | None,
        ids_to_supersede: list[str],
    ):
        self._repository.apply_consolidation(
            memory_id, operation_data["agent_name"], merged_document, ids_to_supersede
        )


class SQLiteMemoryService(BaseMemoryService):
    """SQLite/FTS5 memory service fulfilling the full BaseMemoryService contract."""

    def __init__(
        self,
        db_path: str | Path,
        llm_service: BaseLLMService | None = None,
    ):
        self.db_path = Path(db_path)
        self.llm_service = llm_service
        self._repository = SQLiteMemoryRepository(self.db_path)
        self._coordinator = MemorySearchCoordinator(self._repository)
        self._persistence = SQLiteMemoryPersistence(self._repository)
        self._worker: MemoryWorker = MemoryWorker(
            llm_service=self.llm_service,
            persistence=self._persistence,
            thread_name=SQLITE_MEMORY_WORKER_THREAD_NAME,
        )
        self._worker.start()

    def store_conversation(
        self,
        user_message: str,
        assistant_messages: list[str],
        agent_name: str = "None",
        session_id: str | None = None,
    ) -> list[str]:
        operation_id = str(uuid.uuid4())
        operation_data = {
            "type": "store_conversation",
            "operation_id": operation_id,
            "user_message": user_message,
            "assistant_messages": assistant_messages,
            "agent_name": agent_name,
            "session_id": session_id or self.session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": os.getcwd(),
        }
        if self._worker.queue_store(operation_data):
            return [operation_id]
        return []

    def clear_conversation_context(self):
        self._worker.current_conversation_context = {}

    def load_conversation_context(self, session_id: str, agent_name: str = "None"):
        record = self._repository.active_session_record(session_id, agent_name)
        if record is not None:
            self._worker.current_conversation_context[session_id] = record.document

    def _memory_query(
        self,
        keywords: str,
        from_date: int | None,
        to_date: int | None,
        agent_name: str,
    ) -> MemoryQuery:
        parsed_from = (
            datetime.fromtimestamp(from_date, tz=UTC).date() if from_date else None
        )
        parsed_to = datetime.fromtimestamp(to_date, tz=UTC).date() if to_date else None
        return MemoryQuery(
            query=keywords,
            agent_name=agent_name,
            current_session_id=self.session_id or "unsessioned",
            from_date=parsed_from,
            to_date=parsed_to,
        )

    def retrieve_memory(
        self,
        keywords: str,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> str:
        result = self._coordinator.search(
            self._memory_query(keywords, from_date, to_date, agent_name)
        )
        if not result.hits:
            return "No relevant memories found."
        return format_search_result(result)

    def search_structured(self, query: MemoryQuery) -> MemorySearchResult:
        return self._coordinator.search(query)

    def get_memory_documents(
        self,
        memory_ids: list[str],
        agent_name: str = "None",
    ) -> dict[str, str]:
        """Expand already-selected hits directly; search remains the eligibility gate."""
        return self._repository.get_memory_documents(memory_ids, agent_name)

    def list_memory_headers(
        self,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> list[str]:
        exclude = self.session_id if self.session_id.strip() else None
        return self._repository.list_headers(
            agent_name, exclude, from_ts=from_date, to_ts=to_date
        )

    def cleanup_old_memories(self, months: int = 1) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=30 * months)
        removed = self._repository.delete_memory_families(cutoff_ts=cutoff.timestamp())
        if removed:
            self.clear_conversation_context()
        logger.info(
            f"SQLite retention removed {removed} memories older than {months} month(s)"
        )
        return removed

    def get_agent_memory_corpus(
        self,
        agent_name: str,
        max_items: int = 100,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not agent_name.strip() or max_items <= 0:
            return []
        exclude = exclude_session_id or self.session_id or None
        records = self._repository.agent_records(agent_name, max_items, exclude)
        corpus = []
        for record in records:
            corpus.append(
                {
                    "id": record.id,
                    "document": record.document,
                    "metadata": {
                        "date": record.created_at,
                        "timestamp": record.metadata.get("timestamp"),
                        "session_id": record.session_id,
                        "agent": record.agent_name,
                        "type": "conversation",
                        "cwd": record.cwd,
                        "header": record.header,
                        "evolved_at": record.evolved_at,
                    },
                }
            )
        return corpus

    def mark_memories_evolved(
        self,
        memory_ids: list[str],
        agent_name: str,
    ) -> int:
        marked = self._repository.mark_active_roots_evolved(
            memory_ids, agent_name, datetime.now(UTC).isoformat()
        )
        logger.info(
            f"Marked {marked}/{len(memory_ids)} memories as evolved for {agent_name}"
        )
        return marked

    def forget_topic(
        self,
        topic: str,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> dict[str, Any]:
        try:
            removed = self._repository.delete_memory_families(
                fts_expression=fts_or_expression(topic),
                agent_name=agent_name,
                from_ts=from_date,
                to_ts=to_date,
            )
            if removed == 0:
                return {
                    "success": False,
                    "message": f"No memories found related to '{topic}'",
                    "count": 0,
                }
            self.clear_conversation_context()
            return {
                "success": True,
                "message": f"Successfully removed {removed} memory chunks related to '{topic}'",
                "count": removed,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error forgetting topic: {e!s}",
                "count": 0,
            }

    def forget_ids(self, ids: list[str], agent_name: str = "None") -> dict[str, Any]:
        try:
            removed = self._repository.delete_memory_families(
                memory_ids=list(ids), agent_name=agent_name
            )
        except provenance.MemoryFamilyError:
            return {
                "success": False,
                "message": "Unsafe memory family deletion refused",
                "count": 0,
            }
        if removed:
            self.clear_conversation_context()
        return {
            "success": True,
            "message": f"Successfully removed {removed} memory chunks from {agent_name}",
            "count": removed,
        }

    def delete_by_conversation_id(self, conversation_id: str) -> dict[str, Any]:
        try:
            removed = self._repository.delete_memory_families(
                session_id=conversation_id
            )
            if removed:
                self.clear_conversation_context()
            if removed == 0:
                return {
                    "success": True,
                    "message": f"No memories found for conversation {conversation_id}",
                    "count": 0,
                }
            logger.info(
                f"Deleted {removed} memories for conversation {conversation_id}"
            )
            return {
                "success": True,
                "message": f"Successfully removed {removed} memories for conversation {conversation_id}",
                "count": removed,
            }
        except Exception as e:
            logger.error(
                f"Error deleting memories for conversation {conversation_id}: {e}"
            )
            return {
                "success": False,
                "message": f"Error deleting memories: {e!s}",
                "count": 0,
            }

    def shutdown(self):
        logger.info("Shutting down SQLite memory service...")
        self._worker.stop()
        logger.info("SQLite memory service shutdown complete")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:  # noqa: S110 - best-effort cleanup in __del__
            pass
