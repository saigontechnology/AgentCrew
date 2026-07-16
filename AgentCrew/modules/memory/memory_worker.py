from __future__ import annotations
import queue
import asyncio
from typing import TYPE_CHECKING
from datetime import datetime
from threading import Thread, Event
from loguru import logger
import xmltodict

from AgentCrew.modules.prompts.constants import (
    PRE_ANALYZE_PROMPT,
    MERGE_INSTRUCTIONS,
    FIRST_TURN_INSTRUCTIONS,
    CONSOLIDATION_PROMPT,
)

if TYPE_CHECKING:
    from typing import Any
    from chromadb import Collection, EmbeddingFunction
    from AgentCrew.modules.llm.base import BaseLLMService

DEFAULT_QUEUE_TIMEOUT = 5.0
MAX_QUEUE_SIZE = 1000
WORKER_THREAD_NAME = "ChromaMemoryWorker"
CONSOLIDATION_EVERY_N = 5


class MemoryWorker:
    def __init__(
        self,
        embedding_fn: EmbeddingFunction | None = None,
        llm_service: BaseLLMService | None = None,
        relevant_threashold: float = 1,
    ):
        self._embedding_function = embedding_fn
        self.llm_service = llm_service
        self._collection: Collection | None = None
        self._relevant_threashold = relevant_threashold

        self.context_embedding: list = []
        self.current_conversation_context: dict[str, Any] = {}
        self._store_count_by_session: dict[str, int] = {}

        self._conversation_queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._memory_thread: Thread | None = None
        self._memory_stop_event = Event()

    def set_collection(self, collection: Collection):
        self._collection = collection

    def set_relevant_threshold(self, relevant_threashold: float):
        self._relevant_threashold = relevant_threashold

    def set_embedding_fn(self, embedding_fn: EmbeddingFunction):
        self._embedding_function = embedding_fn

    def _parse_xml_block(self, text: str, tag_name: str) -> dict[str, Any] | None:
        open_tag = f"<{tag_name}>"
        close_tag = f"</{tag_name}>"
        if open_tag not in text or close_tag not in text:
            return None
        start = text.index(open_tag)
        end = text.index(close_tag) + len(close_tag)
        xml_block = text[start:end]
        xml_block = xml_block.replace("&", "&amp;")
        return xmltodict.parse(xml_block)

    def start(self):
        if self._memory_thread is None or not self._memory_thread.is_alive():
            self._memory_stop_event.clear()
            self._memory_thread = Thread(
                target=self._worker_loop, name=WORKER_THREAD_NAME, daemon=True
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
        while not self._memory_stop_event.is_set():
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
                continue
            except Exception as e:
                logger.error(f"Memory worker error: {e}")
        loop.close()

    async def _store_conversation_internal(self, operation_data: dict[str, Any]):
        try:
            collection = self._collection
            if collection is None:
                logger.error("Memory worker: collection not set")
                return

            if self._embedding_function is None:
                logger.error("Cannot find embedding_function")
                return

            # ── memory.store.before hook ────────────────────────────────
            from AgentCrew.modules.events.hooks import (
                HookRegistry,
                HookPoints,
            )

            _hooks = HookRegistry.get_instance()
            _before_ctx = await _hooks.run_before(
                HookPoints.MEMORY_STORE,
                operation_data=operation_data,
            )
            if _before_ctx is None:
                logger.info(
                    "memory.store cancelled by before hook: %s",
                    operation_data.get("operation_id", ""),
                )
                return
            operation_data = _before_ctx["operation_data"]
            # ─────────────────────────────────────────────────────────────

            user_message = operation_data["user_message"]
            assistant_messages = operation_data.get("assistant_messages") or []
            assistant_messages = [
                message.strip()
                for message in assistant_messages
                if isinstance(message, str) and message.strip()
            ]
            assistant_response_for_memory = "\n\n".join(
                f"[Assistant message {index}]\n{message}"
                for index, message in enumerate(assistant_messages, start=1)
            )
            agent_name = operation_data["agent_name"]
            session_id = operation_data["session_id"]

            memory_data = None
            retried = 0
            if self.llm_service:
                while retried < 3:
                    analyzed_text = None
                    try:
                        if self.current_conversation_context.get(session_id, ""):
                            analyzed_prompt = PRE_ANALYZE_PROMPT.replace(
                                "{context_instructions}",
                                MERGE_INSTRUCTIONS,
                            ).replace(
                                "{conversation_context}",
                                f"""<PREVIOUS_CONVERSATION_CONTEXT>
        {self.current_conversation_context[session_id]}
        </PREVIOUS_CONVERSATION_CONTEXT>""",
                            )
                        else:
                            analyzed_prompt = PRE_ANALYZE_PROMPT.replace(
                                "{context_instructions}",
                                FIRST_TURN_INSTRUCTIONS,
                            ).replace("{conversation_context}", "")
                        analyzed_prompt = (
                            analyzed_prompt.replace(
                                "{current_date}",
                                datetime.today().strftime("%Y-%m-%d %H:%M:%S"),
                            )
                            .replace("{user_message}", user_message)
                            .replace(
                                "{assistant_response}", assistant_response_for_memory
                            )
                        )
                        analyzed_text = await self.llm_service.process_message(
                            analyzed_prompt
                        )
                        if (
                            "<MEMORY>" not in analyzed_text
                            or "</MEMORY>" not in analyzed_text
                        ):
                            raise ValueError(
                                "LLM response missing <MEMORY>...</MEMORY> block"
                            )
                        memory_data = self._parse_xml_block(analyzed_text, "MEMORY")
                        if memory_data is None:
                            raise ValueError("Failed to parse <MEMORY> XML block")
                        logger.debug(f"Memory data: {memory_data}")
                        break
                    except Exception as e:
                        analyzed_preview = None
                        if isinstance(analyzed_text, str):
                            analyzed_preview = analyzed_text[:500]
                        logger.warning(
                            f"Error processing conversation with LLM on retry {retried + 1}/3: {str(e)} | analyzed_preview={analyzed_preview}",
                        )
                        retried += 1
                        continue

            if memory_data is None:
                memory_data = {
                    "MEMORY": {
                        "DATE": datetime.today().strftime("%Y-%m-%d"),
                        "CONVERSATION_NOTES": {
                            "NOTE": [user_message, assistant_response_for_memory]
                        },
                    }
                }

            # ── memory.store.after hook ─────────────────────────────────
            # Run immediately after final memory_data creation (LLM or
            # fallback) and BEFORE deriving any state from it so that
            # after-hook mutations affect all derived values: header,
            # serialized document, embedding, cache, and metadata.
            _after_envelope: dict[str, Any] = {
                "memory_data": memory_data,
            }
            _modified = await _hooks.run_after(
                HookPoints.MEMORY_STORE,
                result=_after_envelope,
                operation_data=operation_data,
            )
            if isinstance(_modified, dict) and isinstance(
                _modified.get("memory_data"), dict
            ):
                memory_data = _modified["memory_data"]
            # ─────────────────────────────────────────────────────────────

            timestamp = datetime.now().timestamp()

            memory_header = memory_data["MEMORY"].get("HEAD", None)
            conversation_document = xmltodict.unparse(
                memory_data, pretty=True, full_document=False
            )
            self.current_conversation_context[session_id] = conversation_document

            conversation_embedding = self._embedding_function([conversation_document])
            self.context_embedding.append(conversation_embedding)
            if len(self.context_embedding) > 5:
                self.context_embedding.pop(0)

            metadata = {
                "date": timestamp,
                "session_id": session_id,
                "agent": agent_name,
                "type": "conversation",
            }
            if memory_header:
                metadata["header"] = memory_header

            collection.upsert(
                ids=[f"{session_id}_{agent_name}"],
                documents=[conversation_document],
                embeddings=conversation_embedding,
                metadatas=[metadata],
            )

            logger.debug(f"Stored conversation: {operation_data['operation_id']}")

            if session_id not in self._store_count_by_session:
                self._store_count_by_session[session_id] = 0
            count = self._store_count_by_session[session_id]
            if count % CONSOLIDATION_EVERY_N == 0:
                await self._consolidate_related_memories(
                    current_document=conversation_document,
                    current_id=f"{session_id}_{agent_name}",
                    current_metadata=metadata,
                    agent_name=agent_name,
                    collection=collection,
                )
            self._store_count_by_session[session_id] += 1

        except Exception as e:
            logger.error(
                f"Failed to store conversation {operation_data['operation_id']}: {e}"
            )

    async def _consolidate_related_memories(
        self,
        current_document: str,
        current_id: str,
        current_metadata: dict[str, Any],
        agent_name: str,
        collection: Collection,
    ):
        try:
            current_session_id = current_metadata.get("session_id", "")
            where_filter: dict[str, Any] = {
                "$and": [
                    {"agent": agent_name},
                    {"session_id": {"$ne": current_session_id}},
                ]
            }

            search_query = current_document
            try:
                mem_dict = xmltodict.parse(current_document)
                mem = mem_dict.get("MEMORY", {})
                query_parts = []
                if mem.get("HEAD"):
                    query_parts.append(mem["HEAD"])
                flow = mem.get("FLOW", {})
                if isinstance(flow, dict):
                    for flow_field in ("INTENT", "EXPLORATION", "OUTCOME"):
                        if flow.get(flow_field):
                            query_parts.append(flow[flow_field])
                elif isinstance(flow, str) and flow:
                    query_parts.append(flow)
                context = mem.get("CONTEXT")
                if context and not query_parts:
                    query_parts.append(context)
                entities = mem.get("ENTITIES", {}) or {}
                entity_list = entities.get("ENTITY", [])
                if isinstance(entity_list, dict):
                    entity_list = [entity_list]
                for entity in entity_list:
                    if isinstance(entity, dict) and entity.get("NAME"):
                        query_parts.append(entity["NAME"])
                if query_parts:
                    search_query = " ".join(query_parts)
            except Exception:
                pass

            results = collection.query(
                query_texts=[search_query],
                n_results=5,
                where=where_filter,
            )

            if not results["ids"] or not results["ids"][0]:
                return

            candidates = []
            for i in range(len(results["ids"][0])):
                dist = results["distances"][0][i] if results["distances"] else 99
                if dist <= self._relevant_threashold:
                    candidates.append(
                        {
                            "id": results["ids"][0][i],
                            "document": results["documents"][0][i],  # type: ignore
                            "metadata": results["metadatas"][0][i],  # type: ignore
                            "distance": dist,
                        }
                    )

            if not candidates:
                return

            current_headline = current_metadata.get("header", "<no headline>")
            candidate_labels = [
                f'"{c["metadata"].get("header", "<no headline>")}" ({c["id"]})'
                for c in candidates
            ]
            logger.info(
                f"Consolidation candidates for {agent_name}: "
                f'current="{current_headline}" -> comparing against '
                f"{len(candidates)} memories: [{', '.join(candidate_labels)}]"
            )

            await self._merge_with_candidates(
                current_document,
                current_id,
                current_metadata,
                candidates,
                agent_name,
                collection,
            )
        except Exception as e:
            logger.warning(f"Consolidation failed (non-fatal): {e}")

    async def _merge_with_candidates(
        self,
        current_document: str,
        current_id: str,
        current_metadata: dict[str, Any],
        candidates: list[dict[str, Any]],
        agent_name: str,
        collection: Collection,
    ):
        if not self.llm_service:
            return

        candidates_xml = "\n".join(
            f'<EXISTING_MEMORY id="{c["id"]}">\n{c["document"]}\n</EXISTING_MEMORY>'
            for c in candidates
        )

        prompt = (
            CONSOLIDATION_PROMPT.replace(
                "{current_date}", datetime.today().strftime("%Y-%m-%d")
            )
            .replace("{current_memory}", current_document)
            .replace("{existing_memories}", candidates_xml)
            .replace("{agent_name}", agent_name)
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
            return

        await self._apply_consolidation(
            result_text,
            current_id,
            current_metadata,
            candidates,
            agent_name,
            collection,
        )

    async def _apply_consolidation(
        self,
        result_text: str,
        current_id: str,
        current_metadata: dict[str, Any],
        candidates: list[dict[str, Any]],
        agent_name: str,
        collection: Collection,
    ):

        if self._embedding_function is None:
            logger.error("Cannot find embedding_function")
            return

        try:
            parsed = self._parse_xml_block(result_text, "CONSOLIDATION_RESULT")
            if parsed is None:
                logger.warning("Failed to parse <CONSOLIDATION_RESULT> XML block")
                return

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
                c["id"]: c["metadata"].get("header", "<no headline>")
                for c in candidates
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
                merged_embedding = self._embedding_function([final_merged_doc])
                collection.upsert(
                    ids=[current_id],
                    documents=[final_merged_doc],
                    embeddings=merged_embedding,
                    metadatas=[current_metadata],
                )
                session_id = current_metadata.get("session_id", "")
                if session_id:
                    self.current_conversation_context[session_id] = final_merged_doc

            if ids_to_delete:
                collection.delete(ids=ids_to_delete)

            logger.info(
                f"Consolidation for {agent_name}: "
                f"merged {merge_count}, discarded {discard_count}, kept {keep_count}"
            )
        except Exception as e:
            logger.warning(f"Failed to apply consolidation (non-fatal): {e}")
