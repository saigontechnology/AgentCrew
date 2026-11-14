"""Queue-backed memory store worker with backend-neutral record building.

The builder owns extraction (MEM-501); a persistence backend owns storage;
this worker owns queue/thread lifecycle and the bounded consolidation flow.
"""

from __future__ import annotations

import asyncio
import queue
from datetime import UTC, datetime
from threading import Event, Thread
from typing import TYPE_CHECKING, Any

import xmltodict
from loguru import logger

from AgentCrew.modules.prompts.constants import CONSOLIDATION_PROMPT

from .memory_record_builder import (
    BuiltMemoryRecord,
    MemoryRecordBuilder,
    build_consolidation_query,
    parse_xml_block,
)

if TYPE_CHECKING:
    from chromadb import Collection, EmbeddingFunction

    from AgentCrew.modules.llm.base import BaseLLMService

DEFAULT_QUEUE_TIMEOUT = 5.0
MAX_QUEUE_SIZE = 1000
WORKER_THREAD_NAME = "ChromaMemoryWorker"
CONSOLIDATION_EVERY_N = 5
CONSOLIDATION_CANDIDATE_LIMIT = 5


class ChromaMemoryPersistence:
    """Chroma storage adapter: embeddings, metadata and vector candidates."""

    def __init__(self):
        self._collection: Collection | None = None
        self._embedding_function: EmbeddingFunction | None = None
        self._relevant_threashold: float = 1
        self.context_embedding: list = []

    def set_collection(self, collection: Collection):
        self._collection = collection

    def set_embedding_fn(self, embedding_fn: EmbeddingFunction):
        self._embedding_function = embedding_fn

    def set_relevant_threshold(self, relevant_threashold: float):
        self._relevant_threashold = relevant_threashold

    def readiness_error(self) -> str | None:
        if self._collection is None:
            return "Memory worker: collection not set"
        if self._embedding_function is None:
            return "Cannot find embedding_function"
        return None

    def _metadata(
        self, operation_data: dict[str, Any], built: BuiltMemoryRecord
    ) -> dict[str, Any]:
        metadata = {
            "date": built.timestamp,
            "session_id": operation_data["session_id"],
            "agent": operation_data["agent_name"],
            "type": "conversation",
        }
        if built.header:
            metadata["header"] = built.header
        cwd = operation_data.get("cwd")
        if cwd:
            metadata["cwd"] = cwd
        return metadata

    def persist(self, operation_data: dict[str, Any], built: BuiltMemoryRecord):
        conversation_embedding = self._embedding_function([built.document])
        self.context_embedding.append(conversation_embedding)
        if len(self.context_embedding) > 5:
            self.context_embedding.pop(0)
        self._collection.upsert(
            ids=[f"{operation_data['session_id']}_{operation_data['agent_name']}"],
            documents=[built.document],
            embeddings=conversation_embedding,
            metadatas=[self._metadata(operation_data, built)],
        )

    def consolidation_candidates(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
    ) -> list[dict[str, Any]]:
        current_session_id = operation_data["session_id"]
        agent_name = operation_data["agent_name"]
        where_filter: dict[str, Any] = {
            "$and": [
                {"agent": agent_name},
                {"session_id": {"$ne": current_session_id}},
            ]
        }
        search_query = build_consolidation_query(built.memory_data) or built.document
        results = self._collection.query(
            query_texts=[search_query],
            n_results=CONSOLIDATION_CANDIDATE_LIMIT,
            where=where_filter,
        )
        if not results["ids"] or not results["ids"][0]:
            return []
        candidates = []
        for i in range(len(results["ids"][0])):
            dist = results["distances"][0][i] if results["distances"] else 99
            if dist <= self._relevant_threashold:
                metadata = results["metadatas"][0][i] or {}
                candidates.append(
                    {
                        "id": results["ids"][0][i],
                        "document": results["documents"][0][i],
                        "header": metadata.get("header"),
                    }
                )
        return candidates

    def apply_consolidation(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        merged_document: str | None,
        ids_to_delete: list[str],
    ):
        if merged_document is not None:
            if self._embedding_function is None:
                logger.error("Cannot find embedding_function")
                return
            merged_embedding = self._embedding_function([merged_document])
            self._collection.upsert(
                ids=[f"{operation_data['session_id']}_{operation_data['agent_name']}"],
                documents=[merged_document],
                embeddings=merged_embedding,
                metadatas=[self._metadata(operation_data, built)],
            )
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)


class MemoryWorker:
    def __init__(
        self,
        llm_service: BaseLLMService | None = None,
        persistence: Any | None = None,
        thread_name: str = WORKER_THREAD_NAME,
    ):
        self.llm_service = llm_service
        self._persistence = persistence
        self._builder = MemoryRecordBuilder(llm_service)
        self._thread_name = thread_name

        self.current_conversation_context: dict[str, Any] = {}
        self._store_count_by_session: dict[str, int] = {}

        self._conversation_queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._memory_thread: Thread | None = None
        self._memory_stop_event = Event()

    @property
    def context_embedding(self) -> list:
        return getattr(self._persistence, "context_embedding", [])

    @context_embedding.setter
    def context_embedding(self, value: list) -> None:
        if hasattr(self._persistence, "context_embedding"):
            self._persistence.context_embedding = value

    def set_collection(self, collection: Collection):
        self._persistence.set_collection(collection)

    def set_embedding_fn(self, embedding_fn: EmbeddingFunction):
        self._persistence.set_embedding_fn(embedding_fn)

    def set_relevant_threshold(self, relevant_threashold: float):
        self._persistence.set_relevant_threshold(relevant_threashold)

    def start(self):
        if self._memory_thread is None or not self._memory_thread.is_alive():
            self._memory_stop_event.clear()
            self._memory_thread = Thread(
                target=self._worker_loop, name=self._thread_name, daemon=True
            )
            self._memory_thread.start()

    def stop(self):
        if self._memory_thread and self._memory_thread.is_alive():
            self._memory_stop_event.set()
            try:
                self._conversation_queue.put({"type": "shutdown"}, timeout=1.0)
            except queue.Full:
                logger.warning("Could not send shutdown signal to memory worker")
            self._memory_thread.join(timeout=10.0)

    def shutdown(self):
        self.stop()

    def queue_store(self, operation_data: dict) -> bool:
        try:
            self._conversation_queue.put(operation_data, timeout=1.0)
            logger.debug(
                f"Queued conversation storage: {operation_data.get('operation_id', '')}"
            )
            return True
        except queue.Full:
            logger.warning("Memory queue full, dropping conversation storage")
            return False

    def get_queue_status(self) -> dict[str, Any]:
        return {
            "queue_size": self._conversation_queue.qsize(),
            "worker_alive": self._memory_thread.is_alive()
            if self._memory_thread
            else False,
            "max_queue_size": MAX_QUEUE_SIZE,
        }

    def _worker_loop(self):
        loop = asyncio.new_event_loop()
        while True:
            try:
                operation_data = self._conversation_queue.get(
                    timeout=DEFAULT_QUEUE_TIMEOUT
                )

                if operation_data.get("type") == "shutdown":
                    break

                loop.run_until_complete(
                    self._store_conversation_internal(operation_data)
                )
                self._conversation_queue.task_done()

            except queue.Empty:
                if self._memory_stop_event.is_set():
                    break
                continue
            except Exception as e:
                logger.error(f"Memory worker error: {e}")
        loop.close()

    async def _store_conversation_internal(self, operation_data: dict[str, Any]):
        try:
            if self._persistence is None:
                logger.error("Memory worker: persistence backend not set")
                return
            readiness = self._persistence.readiness_error()
            if readiness:
                logger.error(readiness)
                return

            session_id = operation_data["session_id"]
            built = await self._builder.build(
                operation_data,
                self.current_conversation_context.get(session_id, ""),
            )
            if built is None:
                return
            self.current_conversation_context[session_id] = built.document

            self._persistence.persist(operation_data, built)

            logger.debug(f"Stored conversation: {operation_data['operation_id']}")

            count = self._store_count_by_session.get(session_id, 0)
            if count % CONSOLIDATION_EVERY_N == 0:
                await self._consolidate_related_memories(operation_data, built)
            self._store_count_by_session[session_id] = count + 1

        except Exception as e:
            logger.error(
                f"Failed to store conversation {operation_data.get('operation_id')}: {e}"
            )

    async def _consolidate_related_memories(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
    ):
        try:
            candidates = self._persistence.consolidation_candidates(
                operation_data, built
            )
            if not candidates:
                return

            current_headline = built.header or "<no headline>"
            agent_name = operation_data["agent_name"]
            candidate_labels = []
            for candidate in candidates:
                label = candidate.get("header") or "<no headline>"
                candidate_labels.append(f'"{label}" ({candidate["id"]})')
            logger.info(
                f"Consolidation candidates for {agent_name}: "
                f'current="{current_headline}" -> comparing against '
                f"{len(candidates)} memories: [{', '.join(candidate_labels)}]"
            )

            merged_document, ids_to_delete = await self._merge_with_candidates(
                operation_data, built, candidates
            )
            if merged_document is not None or ids_to_delete:
                self._persistence.apply_consolidation(
                    operation_data, built, merged_document, ids_to_delete
                )
            if merged_document is not None:
                session_id = operation_data.get("session_id", "")
                if session_id:
                    self.current_conversation_context[session_id] = merged_document
        except Exception as e:
            logger.warning(f"Consolidation failed (non-fatal): {e}")

    async def _merge_with_candidates(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        candidates: list[dict[str, Any]],
    ) -> tuple[str | None, list[str]]:
        if not self.llm_service:
            return None, []

        candidates_xml = "\n".join(
            f'<EXISTING_MEMORY id="{candidate["id"]}">\n{candidate["document"]}\n</EXISTING_MEMORY>'
            for candidate in candidates
        )

        prompt = (
            CONSOLIDATION_PROMPT.replace(
                "{current_date}", datetime.now(UTC).strftime("%Y-%m-%d")
            )
            .replace("{current_memory}", built.document)
            .replace("{existing_memories}", candidates_xml)
            .replace("{agent_name}", operation_data["agent_name"])
        )

        result_text = None
        for attempt in range(3):
            try:
                result_text = await self.llm_service.process_message(prompt)
                if "<CONSOLIDATION_RESULT>" not in result_text:
                    raise ValueError("Missing <CONSOLIDATION_RESULT> block")
                break
            except Exception as e:
                logger.warning(f"Consolidation LLM attempt {attempt + 1}/3 failed: {e}")
                continue

        if not result_text or "<CONSOLIDATION_RESULT>" not in result_text:
            return None, []

        return self._parse_consolidation_result(
            result_text, candidates, operation_data["agent_name"]
        )

    def _parse_consolidation_result(
        self,
        result_text: str,
        candidates: list[dict[str, Any]],
        agent_name: str,
    ) -> tuple[str | None, list[str]]:
        try:
            parsed = parse_xml_block(result_text, "CONSOLIDATION_RESULT")
            if parsed is None:
                logger.warning("Failed to parse <CONSOLIDATION_RESULT> XML block")
                return None, []

            actions = parsed["CONSOLIDATION_RESULT"].get("ACTION", [])
            if isinstance(actions, dict):
                actions = [actions]

            merged_memories = parsed["CONSOLIDATION_RESULT"].get("MERGED_MEMORY", [])
            if isinstance(merged_memories, dict):
                merged_memories = [merged_memories]

            ids_to_delete = []
            merge_count = 0
            discard_count = 0
            keep_count = 0
            final_merged_doc = None
            candidate_headers = {
                candidate["id"]: candidate.get("header") or "<no headline>"
                for candidate in candidates
            }
            decision_labels = []

            for action in actions:
                action_id = action.get("@id", "")
                action_type = action.get("@type", "KEEP").upper()

                if action_type == "DISCARD":
                    ids_to_delete.append(action_id)
                    discard_count += 1
                    decision_labels.append(
                        f'DISCARD "{candidate_headers.get(action_id, action_id)}"'
                    )
                elif action_type == "MERGE":
                    ids_to_delete.append(action_id)
                    merge_count += 1
                    decision_labels.append(
                        f'MERGE "{candidate_headers.get(action_id, action_id)}"'
                    )
                    for merged in merged_memories:
                        if merged.get("@for") == action_id:
                            final_merged_doc = xmltodict.unparse(
                                {"MEMORY": merged["MEMORY"]},
                                pretty=True,
                                full_document=False,
                            )
                            break
                else:
                    keep_count += 1
                    decision_labels.append(
                        f'KEEP "{candidate_headers.get(action_id, action_id)}"'
                    )

            if decision_labels:
                logger.info(f"Consolidation decisions: {', '.join(decision_labels)}")

            if final_merged_doc is not None:
                logger.info(f"Consolidated memory preview: {final_merged_doc[:300]}...")

            logger.info(
                f"Consolidation for {agent_name}: "
                f"merged {merge_count}, discarded {discard_count}, kept {keep_count}"
            )
            return final_merged_doc, ids_to_delete
        except Exception as e:
            logger.warning(f"Failed to apply consolidation (non-fatal): {e}")
            return None, []
