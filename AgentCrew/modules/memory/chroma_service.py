from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from . import provenance
from .base_service import BaseMemoryService
from .memory_worker import ChromaMemoryPersistence, MemoryWorker
from .types import (
    CandidateSource,
    ConfidenceBand,
    MatchReason,
    MemoryQuery,
    MemorySearchHit,
    MemorySearchResult,
)

if TYPE_CHECKING:
    from chromadb import Collection

    from AgentCrew.modules.llm.base import BaseLLMService

MEMORY_DB_PATH = "./memory_db"
RELEVANT_THRESHOLD = 0.30


class ChromaMemoryService(BaseMemoryService):
    """Service for storing and retrieving conversation memory using ChromaDB."""

    def __init__(
        self,
        collection_name="conversation",
        llm_service: BaseLLMService | None = None,
    ):
        self.db_path = os.getenv("MEMORYDB_PATH", MEMORY_DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.llm_service = llm_service
        if self.llm_service:
            if self.llm_service.provider_name == "google":
                self.llm_service.model = "gemini-2.5-flash-lite"
            elif self.llm_service.provider_name == "claude":
                self.llm_service.model = "claude-3-5-haiku-latest"
            elif self.llm_service.provider_name == "openai":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "deepinfra":
                self.llm_service.model = "google/gemma-4-31B-it"
            elif self.llm_service.provider_name == "fireworks":
                self.llm_service.model = "accounts/fireworks/models/gemma-4-31b-it"
            elif self.llm_service.provider_name == "github_copilot":
                self.llm_service.model = "claude-haiku-4.5"
            elif (
                self.llm_service.provider_name == "copilot_response"
                or self.llm_service.provider_name == "openai_codex"
            ):
                self.llm_service.model = "gpt-6-luna"
            elif self.llm_service.provider_name == "together":
                self.llm_service.model = "Qwen/Qwen3.5-9B"
            elif self.llm_service.provider_name == "opencode_go":
                self.llm_service.model = "deepseek-v4-flash"
            elif self.llm_service.provider_name == "commandcode":
                self.llm_service.model = "deepseek/deepseek-v4-flash"

        self._collection = None
        self.collection_name = collection_name
        self._worker: MemoryWorker = MemoryWorker(
            llm_service=self.llm_service, persistence=ChromaMemoryPersistence()
        )
        self._worker.start()

    def ensure_initialized(self) -> None:
        """Pre-initialize the collection on the current thread.

        Call this from the main thread before the Qt event loop starts to
        avoid macOS bus errors caused by NumPy/OpenBLAS initialization in
        QThread contexts.  No-op if already initialized.
        """
        self._initialize_collection()

    def _initialize_collection(self) -> Collection:
        import chromadb
        from chromadb.config import Settings
        from chromadb.utils import embedding_functions

        if self._collection is not None:
            return self._collection

        self.client = chromadb.PersistentClient(
            path=self.db_path, settings=Settings(anonymized_telemetry=False)
        )
        if os.getenv("VOYAGE_API_KEY"):
            from .voyageai_ef import VOYAGE_RELEVANT_THRESHOLD, VoyageEmbeddingFunction

            voyage_ef = VoyageEmbeddingFunction(
                api_key=os.getenv("VOYAGE_API_KEY"),
                model_name="voyage-4",
            )
            self.embedding_function = voyage_ef
            self.relevant_threshold = VOYAGE_RELEVANT_THRESHOLD
        else:
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            self.relevant_threshold = RELEVANT_THRESHOLD

        self._collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,  # type:ignore
            configuration={"hnsw": {"space": "cosine", "ef_construction": 200}},
        )

        ## Re-create memory collection to prevent incompatible issue
        if (
            self._collection.configuration.get("hnsw")
            and self._collection.configuration.get("hnsw").get("space") != "cosine"  # type:ignore
        ):
            self.client.delete_collection(self.collection_name)
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function,  # type:ignore
                configuration={"hnsw": {"space": "cosine", "ef_construction": 200}},
            )

        self._worker.set_collection(self._collection)
        self._worker.set_embedding_fn(self.embedding_function)
        self._worker.set_relevant_threshold(self.relevant_threshold)

        self.cleanup_old_memories(months=1)
        return self._collection

    def store_conversation(
        self,
        user_message: str,
        assistant_messages: list[str],
        agent_name: str = "None",
        session_id: str | None = None,
    ) -> list[str]:
        self._initialize_collection()

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
        self._worker.context_embedding = []

    def load_conversation_context(self, session_id: str, agent_name: str = "None"):
        collection = self._initialize_collection()
        latest_memory = collection.get(
            where={"$and": [{"session_id": session_id}, {"agent": agent_name}]}
        )
        active = [
            document
            for document, metadata in zip(
                latest_memory["documents"], latest_memory["metadatas"]
            )
            if provenance.is_active(metadata or {})
        ]
        if len(active) > 1:
            raise provenance.MemoryFamilyError("Multiple active session contexts")
        if active:
            self._worker.current_conversation_context[session_id] = active[0]

    def list_memory_headers(
        self,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "None",
    ) -> list[str]:
        collection = self._initialize_collection()

        and_conditions: list[dict[str, Any]] = []

        if self.session_id.strip():
            and_conditions.append({"session_id": {"$ne": self.session_id}})
        if agent_name.strip():
            and_conditions.append({"agent": agent_name})
        if from_date:
            and_conditions.append({"date": {"$gte": from_date}})
        if to_date:
            and_conditions.append({"date": {"$lte": to_date}})

        list_memory = collection.get(
            where={"$and": and_conditions}
            if len(and_conditions) >= 2
            else and_conditions[0]
            if and_conditions
            else None,
            include=["metadatas"],
        )
        headers = []
        if list_memory and list_memory["metadatas"]:
            for metadata in list_memory["metadatas"]:
                if provenance.is_active(metadata) and metadata.get("header", None):
                    timestamp = float(metadata.get("date", 0))  # type: ignore
                    headers.append(
                        f"{metadata.get('header')} ({datetime.fromtimestamp(timestamp, tz=UTC).strftime('%Y-%m-%d %H:%M')})"
                        if timestamp > 0
                        else metadata.get("header")
                    )
        return list(reversed(headers))[:20]

    def retrieve_memory(
        self,
        keywords: str,
        from_date: int | None = None,
        to_date: int | None = None,
        agent_name: str = "",
    ) -> str:
        collection = self._initialize_collection()

        and_conditions: list[dict[str, Any]] = []

        if self.session_id.strip():
            and_conditions.append({"session_id": {"$ne": self.session_id}})
        if agent_name.strip():
            and_conditions.append({"agent": agent_name})
        if from_date:
            and_conditions.append({"date": {"$gte": from_date}})
        if to_date:
            and_conditions.append({"date": {"$lte": to_date}})

        results = collection.query(
            query_texts=[keywords],
            n_results=10,
            where={"$and": and_conditions}
            if len(and_conditions) >= 2
            else and_conditions[0]
            if and_conditions
            else None,
        )

        if not results["documents"] or not results["documents"][0]:
            return "No relevant memories found."

        conversation_chunks = []
        for i, (id, doc, metadata) in enumerate(
            zip(results["ids"][0], results["documents"][0], results["metadatas"][0])  # type:ignore
        ):
            if not provenance.is_active(metadata or {}):
                continue
            conversation_chunks.append(
                {
                    "id": id,
                    "document": doc,
                    "timestamp": metadata.get("date", None)
                    or metadata.get("timestamp", "unknown"),
                    "relevance": results["distances"][0][i]
                    if results["distances"]
                    else 1,
                }
            )

        sorted_conversations = sorted(conversation_chunks, key=lambda x: x["relevance"])

        output = []
        for conv_data in sorted_conversations[:3]:
            conversation_text = conv_data["document"]
            if conv_data["relevance"] > self.relevant_threshold:
                continue
            timestamp = "Unknown time"
            if conv_data["timestamp"] != "unknown":
                try:
                    try:
                        dt = datetime.fromtimestamp(conv_data["timestamp"], tz=UTC)
                        timestamp = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        dt = datetime.fromisoformat(conv_data["timestamp"])
                        timestamp = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    timestamp = conv_data["timestamp"]

            output.append(
                f"---Date:{timestamp} (Similarity: {1 - conv_data['relevance']:.2f})---\n{conversation_text}\n---"
            )

        memories = "\n\n".join(output)
        return memories

    def search_structured(self, query: MemoryQuery) -> MemorySearchResult:
        """Structured Chroma search with server-side scope and bounded results."""
        collection = self._initialize_collection()

        and_conditions: list[dict[str, Any]] = []
        if self.session_id.strip():
            and_conditions.append({"session_id": {"$ne": self.session_id}})
        if query.agent_name.strip():
            and_conditions.append({"agent": query.agent_name})
        if query.from_date is not None:
            and_conditions.append(
                {
                    "date": {
                        "$gte": int(
                            datetime.combine(query.from_date, time.min).timestamp()
                        )
                    }
                }
            )
        if query.to_date is not None:
            and_conditions.append(
                {
                    "date": {
                        "$lte": int(
                            datetime.combine(query.to_date, time.min).timestamp()
                        )
                        + 86_399
                    }
                }
            )
        where = (
            {"$and": and_conditions}
            if len(and_conditions) >= 2
            else and_conditions[0]
            if and_conditions
            else None
        )

        if query.memory_ids:
            requested = list(dict.fromkeys(query.memory_ids))
            fetched = collection.get(
                ids=requested,
                where=where,
                include=["documents", "metadatas"],
            )
            documents = dict(zip(fetched["ids"], fetched["documents"]))
            metadatas = dict(zip(fetched["ids"], fetched["metadatas"]))
            hits = [
                self._structured_hit(
                    memory_id,
                    documents[memory_id],
                    metadatas.get(memory_id) or {},
                    None,
                )
                for memory_id in requested
                if memory_id in documents
                and provenance.is_active(metadatas.get(memory_id) or {})
            ]
            return MemorySearchResult(tuple(hits[: query.limit]))

        results = collection.query(
            query_texts=[query.query],
            n_results=max(10, min(query.limit, 20)),
            where=where,
        )
        hits = []
        for memory_id, document, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if (
                not provenance.is_active(metadata or {})
                or distance is None
                or distance > self.relevant_threshold
            ):
                continue
            hits.append(
                self._structured_hit(memory_id, document, metadata or {}, distance)
            )
        kind_hints = {kind.lower() for kind in query.kinds}
        if kind_hints:
            hits.sort(key=lambda hit: hit.kind.lower() not in kind_hints)
        return MemorySearchResult(tuple(hits[: query.limit]))

    def _structured_hit(
        self,
        memory_id: str,
        document: str,
        metadata: dict[str, Any],
        distance: float | None,
    ) -> MemorySearchHit:
        kind = str(metadata.get("type") or "memory")
        timestamp = metadata.get("date")
        try:
            date_text = (
                datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
                if isinstance(timestamp, (int, float))
                else str(timestamp or "unknown")
            )
        except Exception:
            date_text = "unknown"
        if distance is None:
            reasons = (
                MatchReason(CandidateSource.MEMORY_ID, 0, "exact memory id lookup"),
            )
            confidence = ConfidenceBand.HIGH
            score = 1.0
        else:
            reasons = (
                MatchReason(
                    CandidateSource.SEMANTIC,
                    0,
                    f"vector similarity {1 - distance:.2f}",
                    distance,
                ),
            )
            confidence = (
                ConfidenceBand.HIGH
                if distance <= self.relevant_threshold * 0.5
                else ConfidenceBand.MEDIUM
                if distance <= self.relevant_threshold * 0.8
                else ConfidenceBand.LOW
            )
            score = 1.0 - distance
        header = metadata.get("header")
        return MemorySearchHit(
            memory_id=str(memory_id),
            unit_id=kind,
            date=date_text,
            cwd=None,
            kind=kind,
            headline=str(header)[:180] if header else None,
            excerpt=(document or "")[:360],
            reasons=reasons,
            confidence=confidence,
            score=score,
        )

    def get_memory_documents(
        self,
        memory_ids: list[str],
        agent_name: str = "None",
    ) -> dict[str, str]:
        """Return full documents for known memory IDs, scoped to one agent."""
        collection = self._initialize_collection()
        requested = [
            str(memory_id)
            for memory_id in dict.fromkeys(memory_ids)
            if str(memory_id).strip()
        ]
        if not requested:
            return {}
        fetched = collection.get(
            ids=requested,
            where={"agent": agent_name} if agent_name.strip() else None,
            include=["documents"],
        )
        return {
            str(memory_id): document
            for memory_id, document in zip(fetched["ids"], fetched["documents"])
        }

    def _selection_agents(
        self, conversation_id: str | None = None
    ) -> dict[str, list[str]]:
        records = self._initialize_collection().get(
            where={"session_id": conversation_id}
            if conversation_id is not None
            else None,
            include=["metadatas"],
            limit=provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS + 1,
        )
        if len(records["ids"]) > provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS or len(
            records["ids"]
        ) != len(records["metadatas"]):
            raise provenance.MemoryFamilyError(
                "Memory lifecycle selection limit or shape invalid"
            )
        selected = {}
        for key, metadata in zip(records["ids"], records["metadatas"]):
            agent = (metadata or {}).get("agent")
            if not isinstance(agent, str) or not agent.strip():
                raise provenance.MemoryFamilyError("Missing lifecycle agent scope")
            selected.setdefault(agent, []).append(key)
        return selected

    def cleanup_old_memories(self, months: int = 1) -> int:
        try:
            selected = self._selection_agents()
            cutoff = (datetime.now(UTC) - timedelta(days=30 * months)).timestamp()
            removed = self._worker._persistence.delete_families(selected, cutoff=cutoff)
            if removed:
                self.clear_conversation_context()
            return removed
        except provenance.MemoryFamilyError:
            logger.warning("Retention skipped unsafe memory lifecycle selection")
            return 0

    def get_agent_memory_corpus(
        self,
        agent_name: str,
        max_items: int = 100,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._initialize_collection()

        if not agent_name.strip() or max_items <= 0:
            return []

        and_conditions: list[dict[str, Any]] = [{"agent": agent_name}]
        session_to_exclude = exclude_session_id or self.session_id
        if session_to_exclude.strip():
            and_conditions.append({"session_id": {"$ne": session_to_exclude}})

        records = collection.get(
            where={"$and": and_conditions}
            if len(and_conditions) > 1
            else and_conditions[0],
            include=["documents", "metadatas"],
        )

        ids = records.get("ids") or []
        documents = records.get("documents") or []
        metadatas = records.get("metadatas") or []

        corpus: list[dict[str, Any]] = []
        for item_id, document, metadata in zip(ids, documents, metadatas):
            metadata = metadata or {}
            if not provenance.is_active(metadata) or metadata.get("evolved_at"):
                continue
            sort_key = metadata.get("date") or metadata.get("timestamp") or ""
            corpus.append(
                {
                    "id": item_id,
                    "document": document,
                    "metadata": metadata,
                    "sort_key": sort_key,
                }
            )

        corpus.sort(key=lambda item: item["sort_key"], reverse=True)
        return [
            {
                "id": item["id"],
                "document": item["document"],
                "metadata": item["metadata"],
            }
            for item in corpus[:max_items]
        ]

    def mark_memories_evolved(
        self,
        memory_ids: list[str],
        agent_name: str,
    ) -> int:
        if not memory_ids:
            return 0

        collection = self._initialize_collection()
        timestamp = datetime.now(UTC).isoformat()
        marked = 0

        existing = collection.get(
            ids=memory_ids,
            include=["documents", "metadatas", "embeddings"],
        )

        index = self._worker._persistence.family_snapshot(agent_name)
        for i, mid in enumerate(existing["ids"]):
            if existing["metadatas"]:
                meta = dict(existing["metadatas"][i] or {})
                if meta.get("agent") != agent_name or not provenance.is_active(meta):
                    continue
                try:
                    index.require_roots([mid])
                except provenance.MemoryFamilyError:
                    continue
                meta["evolved_at"] = timestamp
                collection.update(
                    ids=[mid],
                    metadatas=[meta],
                )
                marked += 1

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
            collection = self._initialize_collection()
            and_conditions: list[dict[str, Any]] = []

            if agent_name.strip():
                and_conditions.append({"agent": agent_name})

            if from_date:
                and_conditions.append({"date": {"$gte": from_date}})
            if to_date:
                and_conditions.append({"date": {"$lte": to_date}})
            results = collection.query(
                query_texts=[topic],
                n_results=100,
                where={"$and": and_conditions}
                if len(and_conditions) >= 2
                else and_conditions[0]
                if and_conditions
                else None,
            )

            if not results["documents"] or not results["documents"][0]:
                return {
                    "success": False,
                    "message": f"No memories found related to '{topic}'",
                    "count": 0,
                }

            ids_to_remove = results["ids"][0]

            removed = self._worker._persistence.delete_families(
                {agent_name: ids_to_remove}
            )
            if removed:
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
        self._initialize_collection()
        try:
            removed = self._worker._persistence.delete_families({agent_name: ids})
            if removed:
                self.clear_conversation_context()
            return {
                "success": True,
                "message": f"Successfully removed {removed} memory chunks",
                "count": removed,
            }
        except provenance.MemoryFamilyError:
            return {
                "success": False,
                "message": "Unsafe memory family deletion refused",
                "count": 0,
            }

    def delete_by_conversation_id(self, conversation_id: str) -> dict[str, Any]:
        try:
            removed = self._worker._persistence.delete_families(
                self._selection_agents(conversation_id)
            )
            if removed:
                self.clear_conversation_context()
            return {
                "success": True,
                "message": f"Successfully removed {removed} memories for conversation",
                "count": removed,
            }
        except Exception:
            logger.warning("Memory conversation family deletion failed")
            return {
                "success": False,
                "message": "Memory family deletion failed",
                "count": 0,
            }

    def shutdown(self):
        logger.info("Shutting down memory service...")
        self._worker.stop()
        logger.info("Memory service shutdown complete")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:  # noqa: S110 - best-effort cleanup in __del__
            pass
