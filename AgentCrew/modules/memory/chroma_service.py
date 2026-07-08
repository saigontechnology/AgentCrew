from __future__ import annotations
import os
import uuid
from typing import TYPE_CHECKING, Any
from datetime import datetime, timedelta
from loguru import logger

from .base_service import BaseMemoryService
from .memory_worker import MemoryWorker

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
            elif self.llm_service.provider_name == "copilot_response":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "openai_codex":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "together":
                self.llm_service.model = "Qwen/Qwen3.5-9B"
            elif self.llm_service.provider_name == "opencode_go":
                self.llm_service.model = "deepseek-v4-flash"
            elif self.llm_service.provider_name == "commandcode":
                self.llm_service.model = "deepseek/deepseek-v4-flash"
            elif self.llm_service.provider_name == "crofai":
                self.llm_service.model = "deepseek-v4-flash"

        self._collection = None
        self.collection_name = collection_name
        self._worker: MemoryWorker = MemoryWorker(llm_service=self.llm_service)
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
        import chromadb.utils.embedding_functions as embedding_functions
        from chromadb.config import Settings

        if self._collection is not None:
            return self._collection

        self.client = chromadb.PersistentClient(
            path=self.db_path, settings=Settings(anonymized_telemetry=False)
        )
        if os.getenv("VOYAGE_API_KEY"):
            from .voyageai_ef import VoyageEmbeddingFunction, VOYAGE_RELEVANT_THRESHOLD

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
            "timestamp": datetime.now().isoformat(),
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
            where={
                "session_id": session_id,
            },
        )
        if latest_memory["documents"]:
            self._worker.current_conversation_context[session_id] = latest_memory[
                "documents"
            ][-1]

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
                if metadata.get("header", None):
                    timestamp = float(metadata.get("date", 0))  # type: ignore
                    headers.append(
                        f"{metadata.get('header')} ({datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')})"
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
                        dt = datetime.fromtimestamp(conv_data["timestamp"])
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

    def cleanup_old_memories(self, months: int = 1) -> int:
        collection = self._initialize_collection()
        cutoff_date = datetime.now() - timedelta(days=30 * months)

        all_memories = collection.get()

        ids_to_remove = []
        if all_memories["metadatas"]:
            for i, metadata in enumerate(all_memories["metadatas"]):
                timestamp_str = str(
                    metadata.get("timestamp", datetime.now().isoformat())
                )
                try:
                    timestamp_dt = datetime.fromisoformat(timestamp_str)
                    if timestamp_dt < cutoff_date:
                        ids_to_remove.append(all_memories["ids"][i])
                except ValueError:
                    ids_to_remove.append(all_memories["ids"][i])

        if ids_to_remove:
            collection.delete(ids=ids_to_remove)

        return len(ids_to_remove)

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
            if metadata.get("evolved_at"):
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
        timestamp = datetime.now().isoformat()
        marked = 0

        existing = collection.get(
            ids=memory_ids,
            include=["documents", "metadatas", "embeddings"],
        )

        for i, mid in enumerate(existing["ids"]):
            if existing["metadatas"]:
                meta = dict(existing["metadatas"][i] or {})
                if meta.get("agent") != agent_name:
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

            if ids_to_remove:
                collection.delete(ids=ids_to_remove)

            return {
                "success": True,
                "message": f"Successfully removed {len(ids_to_remove)} memory chunks related to '{topic}'",
                "count": len(ids_to_remove),
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"Error forgetting topic: {str(e)}",
                "count": 0,
            }

    def forget_ids(self, ids: list[str], agent_name: str = "None") -> dict[str, Any]:
        collection = self._initialize_collection()
        collection.delete(ids=ids, where={"agent": agent_name})

        return {
            "success": True,
            "message": f"Successfully removed {len(ids)} memory chunks from {agent_name}",
            "count": len(ids),
        }

    def delete_by_conversation_id(self, conversation_id: str) -> dict[str, Any]:
        try:
            collection = self._initialize_collection()

            results = collection.get(
                where={"session_id": conversation_id},
                include=["metadatas"],
            )

            if not results["ids"]:
                return {
                    "success": True,
                    "message": f"No memories found for conversation {conversation_id}",
                    "count": 0,
                }

            ids_to_remove = results["ids"]
            collection.delete(ids=ids_to_remove)

            if conversation_id in self._worker.current_conversation_context:
                del self._worker.current_conversation_context[conversation_id]

            logger.info(
                f"Deleted {len(ids_to_remove)} memories for conversation {conversation_id}"
            )

            return {
                "success": True,
                "message": f"Successfully removed {len(ids_to_remove)} memories for conversation {conversation_id}",
                "count": len(ids_to_remove),
            }
        except Exception as e:
            logger.error(
                f"Error deleting memories for conversation {conversation_id}: {e}"
            )
            return {
                "success": False,
                "message": f"Error deleting memories: {str(e)}",
                "count": 0,
            }

    def shutdown(self):
        logger.info("Shutting down memory service...")
        self._worker.stop()
        logger.info("Memory service shutdown complete")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
