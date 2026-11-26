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

from . import provenance
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

    def family_snapshot(self, agent_name: str) -> provenance.FamilyIndex:
        fetched = self._collection.get(
            where={"agent": agent_name},
            include=["metadatas"],
            limit=provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS + 1,
        )
        if len(fetched["ids"]) != len(fetched["metadatas"]):
            raise provenance.MemoryFamilyError("Incomplete family snapshot")
        rows = []
        for key, metadata in zip(fetched["ids"], fetched["metadatas"]):
            metadata = metadata or {}
            rows.append(
                provenance.FamilyRecord(
                    key,
                    metadata.get("agent"),
                    metadata.get("session_id"),
                    metadata,
                    provenance.timestamp(
                        metadata.get(
                            "updated_at",
                            metadata.get("date", metadata.get("timestamp")),
                        )
                    ),
                )
            )
        return provenance.FamilyIndex(rows, agent_name)

    def persist(self, operation_data: dict[str, Any], built: BuiltMemoryRecord) -> str:
        agent, session = operation_data["agent_name"], operation_data["session_id"]
        fetched = self._collection.get(
            where={"$and": [{"agent": agent}, {"session_id": session}]},
            include=["metadatas"],
            limit=provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS + 1,
        )
        if len(fetched["ids"]) > provenance.MAX_MEMORY_FAMILY_SCAN_RECORDS or len(
            fetched["ids"]
        ) != len(fetched["metadatas"]):
            raise provenance.MemoryFamilyError(
                "Persistence identity scan limit or shape invalid"
            )
        records = [
            provenance.FamilyRecord(
                key,
                (meta or {}).get("agent"),
                (meta or {}).get("session_id"),
                meta or {},
            )
            for key, meta in zip(fetched["ids"], fetched["metadatas"])
        ]
        native = f"{session}_{agent}"
        memory_id = provenance.choose_identity(
            native, operation_data["operation_id"], agent, session, records
        )
        collision = self._collection.get(ids=[memory_id], include=["metadatas"])
        if collision["ids"]:
            meta = collision["metadatas"][0] or {}
            if (
                meta.get("agent") != agent
                or meta.get("session_id") != session
                or not provenance.is_active(meta)
            ):
                raise provenance.MemoryFamilyError("Persistence identity collision")
        existing = next((row for row in records if row.id == memory_id), None)
        metadata = provenance.merge_metadata(
            existing.metadata if existing else {}, self._metadata(operation_data, built)
        )
        metadata.update(status="active", updated_at=built.timestamp)
        conversation_embedding = self._embedding_function([built.document])
        self._collection.upsert(
            ids=[memory_id],
            documents=[built.document],
            embeddings=conversation_embedding,
            metadatas=[metadata],
        )
        self.context_embedding.append(conversation_embedding)
        if len(self.context_embedding) > 5:
            self.context_embedding.pop(0)
        return memory_id

    def delete_families(
        self, seeds: dict[str, list[str]], cutoff: float | None = None
    ) -> int:
        resolved = set()
        for agent, ids in seeds.items():
            if cutoff is None and (
                not ids
                or not self._collection.get(
                    ids=list(dict.fromkeys(ids)),
                    where={"agent": agent},
                    include=["metadatas"],
                )["ids"]
            ):
                continue
            try:
                index = self.family_snapshot(agent)
                found = (
                    index.expired(cutoff) if cutoff is not None else index.resolve(ids)
                )
            except provenance.MemoryFamilyError:
                if cutoff is None:
                    raise
                logger.warning("Retention skipped invalid or oversized agent snapshot")
                continue
            resolved.update(found)
            if len(resolved) > provenance.MAX_MEMORY_FAMILY_MEMBERS:
                raise provenance.MemoryFamilyError("Memory deletion set limit exceeded")
        if resolved:
            self._collection.delete(ids=sorted(resolved))
        return len(resolved)

    def consolidation_candidates(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        memory_id: str,
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
        budget = provenance.chroma_query_budget(
            self._collection, where_filter, CONSOLIDATION_CANDIDATE_LIMIT
        )
        if not budget:
            return []
        results = self._collection.query(
            query_texts=[search_query],
            n_results=budget,
            where=where_filter,
        )
        if not results["ids"] or not results["ids"][0]:
            return []
        candidates = []
        index = self.family_snapshot(agent_name)
        for i in range(len(results["ids"][0])):
            dist = results["distances"][0][i] if results["distances"] else 99
            if dist <= self._relevant_threashold:
                metadata = results["metadatas"][0][i] or {}
                if (
                    not provenance.is_active(metadata)
                    or results["ids"][0][i] == memory_id
                ):
                    continue
                try:
                    index.require_roots([results["ids"][0][i]])
                except provenance.MemoryFamilyError:
                    continue
                candidates.append(
                    {
                        "id": results["ids"][0][i],
                        "document": results["documents"][0][i],
                        "header": metadata.get("header"),
                    }
                )
        return candidates[:CONSOLIDATION_CANDIDATE_LIMIT]

    def apply_consolidation(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        memory_id: str,
        merged_document: str | None,
        ids_to_supersede: list[str],
    ):
        if not ids_to_supersede:
            return
        index = self.family_snapshot(operation_data["agent_name"])
        metadata = provenance.consolidation_metadata(index, memory_id, ids_to_supersede)
        metadata.update(status="active", updated_at=datetime.now(UTC).timestamp())
        if merged_document is None:
            self._collection.update(ids=[memory_id], metadatas=[metadata])
        else:
            embedding = self._embedding_function([merged_document])
            self._collection.upsert(
                ids=[memory_id],
                documents=[merged_document],
                embeddings=embedding,
                metadatas=[metadata],
            )
        for source in ids_to_supersede:
            self._collection.update(
                ids=[source],
                metadatas=[
                    {
                        **index.records[source].metadata,
                        "status": "superseded",
                        "superseded_by": memory_id,
                    }
                ],
            )


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
            if "action_trace" in operation_data:
                self._conversation_queue.put_nowait(operation_data)
            else:
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
        """Finalize worker-owned tasks and async generators before loop teardown."""
        with asyncio.Runner() as runner:
            while True:
                try:
                    operation_data = self._conversation_queue.get(
                        timeout=DEFAULT_QUEUE_TIMEOUT
                    )

                    if operation_data.get("type") == "shutdown":
                        break

                    runner.run(self._store_conversation_internal(operation_data))
                    self._conversation_queue.task_done()

                except queue.Empty:
                    if self._memory_stop_event.is_set():
                        break
                    continue
                except Exception as e:
                    logger.error(f"Memory worker error: {e}")

    async def _store_conversation_internal(self, operation_data: dict[str, Any]):
        receipt = operation_data.get("storage_receipt")
        if receipt is not None:
            receipt["status"] = "processing"
        try:
            if self._persistence is None:
                if receipt is not None:
                    receipt["status"] = "failed"
                logger.error("Memory worker: persistence backend not set")
                return
            readiness = self._persistence.readiness_error()
            if readiness:
                if receipt is not None:
                    receipt["status"] = "failed"
                logger.error(readiness)
                return

            session_id = operation_data["session_id"]
            built = await self._builder.build(
                operation_data,
                self.current_conversation_context.get(session_id, ""),
            )
            if built is None:
                if receipt is not None:
                    receipt["status"] = "skipped"
                return
            memory_id = self._persistence.persist(operation_data, built)
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise provenance.MemoryFamilyError(
                    "Persistence returned no canonical identity"
                )
            if receipt is not None:
                receipt["status"] = "stored"
                receipt["memory_id"] = memory_id
            self.current_conversation_context[session_id] = built.document

            logger.debug(f"Stored conversation: {operation_data['operation_id']}")

            count = self._store_count_by_session.get(session_id, 0)
            if count % CONSOLIDATION_EVERY_N == 0:
                await self._consolidate_related_memories(
                    operation_data, built, memory_id
                )
            self._store_count_by_session[session_id] = count + 1

        except Exception as e:
            if receipt is not None:
                receipt["status"] = "failed"
            logger.error(
                f"Failed to store conversation {operation_data.get('operation_id')}: {e}"
            )

    async def _consolidate_related_memories(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        memory_id: str,
    ):
        try:
            candidates = self._persistence.consolidation_candidates(
                operation_data, built, memory_id
            )
            if not candidates:
                return

            logger.info("Consolidation comparing {} candidates", len(candidates))

            merged_document, ids_to_supersede = await self._merge_with_candidates(
                operation_data, built, candidates, memory_id
            )
            if merged_document is not None or ids_to_supersede:
                from .resources import enforce_resources

                merged_document = enforce_resources(
                    merged_document or built.document,
                    (),
                    (
                        built.document,
                        *(
                            candidate["document"]
                            for candidate in candidates
                            if candidate["id"] in ids_to_supersede
                        ),
                    ),
                )
                self._persistence.apply_consolidation(
                    operation_data, built, memory_id, merged_document, ids_to_supersede
                )
            if merged_document is not None:
                session_id = operation_data.get("session_id", "")
                if session_id:
                    self.current_conversation_context[session_id] = merged_document
        except Exception:
            logger.warning("Consolidation failed (non-fatal)")

    async def _merge_with_candidates(
        self,
        operation_data: dict[str, Any],
        built: BuiltMemoryRecord,
        candidates: list[dict[str, Any]],
        memory_id: str,
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
            except Exception:
                logger.warning("Consolidation LLM attempt {}/3 failed", attempt + 1)
                continue

        if not result_text or "<CONSOLIDATION_RESULT>" not in result_text:
            return None, []

        return self._parse_consolidation_result(
            result_text, candidates, operation_data["agent_name"], memory_id
        )

    def _parse_consolidation_result(
        self,
        result_text: str,
        candidates: list[dict[str, Any]],
        agent_name: str,
        memory_id: str,
    ) -> tuple[str | None, list[str]]:
        try:
            parsed = parse_xml_block(result_text, "CONSOLIDATION_RESULT")
            if not parsed or not isinstance(parsed.get("CONSOLIDATION_RESULT"), dict):
                raise ValueError("Malformed consolidation result")
            result = parsed["CONSOLIDATION_RESULT"]
            if set(result) - {"ACTION", "MERGED_MEMORY"}:
                raise ValueError("Unexpected consolidation result fields")
            actions = result.get("ACTION") or []
            actions = [actions] if isinstance(actions, dict) else actions
            merged = result.get("MERGED_MEMORY") or []
            merged = [merged] if isinstance(merged, dict) else merged
            valid = {candidate["id"] for candidate in candidates}
            if (
                len(valid) != len(candidates)
                or memory_id in valid
                or not isinstance(actions, list)
                or not isinstance(merged, list)
            ):
                raise ValueError("Invalid consolidation candidates or actions")
            seen = set()
            supersede = []
            final_document = None
            merge_ids = set()
            documents = {}
            for item in merged:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"@for", "MEMORY"}
                    or item.get("@for") not in valid
                    or item.get("@for") in documents
                ):
                    raise ValueError("Invalid merged memory identity")
                memory = item.get("MEMORY")
                if (
                    not isinstance(memory, dict)
                    or not isinstance(memory.get("HEAD"), str)
                    or not memory["HEAD"].strip()
                ):
                    raise ValueError("Invalid merged memory document")
                documents[item["@for"]] = xmltodict.unparse(
                    {"MEMORY": memory}, pretty=True, full_document=False
                )
            for action in actions:
                if not isinstance(action, dict):
                    raise TypeError("Malformed consolidation action")
                if set(action) != {"@id", "@type"}:
                    raise ValueError("Unexpected consolidation action fields")
                key, kind = action.get("@id"), action.get("@type")
                if (
                    key not in valid
                    or key in seen
                    or kind not in {"KEEP", "MERGE", "DISCARD"}
                ):
                    raise ValueError("Invalid or conflicting consolidation action")
                seen.add(key)
                if kind == "MERGE":
                    if merge_ids:
                        raise ValueError("Multiple merge actions are not supported")
                    if key not in documents:
                        raise ValueError("Merge action has no valid document")
                    merge_ids.add(key)
                    final_document = documents[key]
                if kind != "KEEP":
                    supersede.append(key)
            if set(documents) != merge_ids:
                raise ValueError("Unmatched merged memory document")
            return final_document, supersede
        except Exception:
            logger.warning("Invalid consolidation result skipped without mutation")
            return None, []
