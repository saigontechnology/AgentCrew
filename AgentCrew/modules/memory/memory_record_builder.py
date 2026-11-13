"""Backend-neutral memory record builder for store operations (MEM-501).

Extraction prompts, store hooks, the deterministic fallback and session
context handling are shared by every persistence backend; only storage
differs. The consolidation query builder gives Chroma and SQLite one
lexical candidate source (MEM-507).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import xmltodict
from loguru import logger

from AgentCrew.modules.prompts.constants import (
    FIRST_TURN_INSTRUCTIONS,
    MERGE_INSTRUCTIONS,
    PRE_ANALYZE_PROMPT,
)

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

EXTRACTION_RETRIES = 3


def parse_xml_block(text: str, tag_name: str) -> dict[str, Any] | None:
    open_tag = f"<{tag_name}>"
    close_tag = f"</{tag_name}>"
    if open_tag not in text or close_tag not in text:
        return None
    start = text.index(open_tag)
    end = text.index(close_tag) + len(close_tag)
    xml_block = text[start:end]
    xml_block = xml_block.replace("&", "&amp;")
    return xmltodict.parse(xml_block)


@dataclass(frozen=True)
class BuiltMemoryRecord:
    memory_data: dict[str, Any]
    document: str
    header: str | None
    timestamp: float


def build_consolidation_query(memory_data: dict[str, Any]) -> str:
    """Lexical consolidation query from extracted memory fields (MEM-507)."""
    mem = memory_data.get("MEMORY", {}) or {}
    query_parts: list[str] = []
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
        return " ".join(query_parts)
    return ""


class MemoryRecordBuilder:
    """Builds memory records from queued store operations (MEM-501)."""

    def __init__(self, llm_service: BaseLLMService | None = None):
        self.llm_service = llm_service

    async def build(
        self,
        operation_data: dict[str, Any],
        session_context: str = "",
    ) -> BuiltMemoryRecord | None:
        """Return the built record, or None when a before hook cancels the store."""
        from AgentCrew.modules.events.hooks import HookPoints, HookRegistry

        hooks = HookRegistry.get_instance()
        before_ctx = await hooks.run_before(
            HookPoints.MEMORY_STORE,
            operation_data=operation_data,
        )
        if before_ctx is None:
            logger.info(
                "memory.store cancelled by before hook: %s",
                operation_data.get("operation_id", ""),
            )
            return None
        operation_data = before_ctx["operation_data"]

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

        memory_data = None
        retried = 0
        if self.llm_service:
            while retried < EXTRACTION_RETRIES:
                analyzed_text = None
                try:
                    if session_context:
                        analyzed_prompt = PRE_ANALYZE_PROMPT.replace(
                            "{context_instructions}",
                            MERGE_INSTRUCTIONS,
                        ).replace(
                            "{conversation_context}",
                            f"""<PREVIOUS_CONVERSATION_CONTEXT>
        {session_context}
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
                            datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
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
                    memory_data = parse_xml_block(analyzed_text, "MEMORY")
                    if memory_data is None:
                        raise ValueError("Failed to parse <MEMORY> XML block")
                    logger.debug(f"Memory data: {memory_data}")
                    break
                except Exception as e:
                    analyzed_preview = None
                    if isinstance(analyzed_text, str):
                        analyzed_preview = analyzed_text[:500]
                    logger.warning(
                        f"Error processing conversation with LLM on retry {retried + 1}/3: {e!s} | analyzed_preview={analyzed_preview}",
                    )
                    retried += 1
                    continue

        if memory_data is None:
            memory_data = {
                "MEMORY": {
                    "DATE": datetime.now(UTC).strftime("%Y-%m-%d"),
                    "CONVERSATION_NOTES": {
                        "NOTE": [user_message, assistant_response_for_memory]
                    },
                }
            }

        after_envelope: dict[str, Any] = {
            "memory_data": memory_data,
        }
        modified = await hooks.run_after(
            HookPoints.MEMORY_STORE,
            result=after_envelope,
            operation_data=operation_data,
        )
        if isinstance(modified, dict) and isinstance(modified.get("memory_data"), dict):
            memory_data = modified["memory_data"]

        timestamp = datetime.now(UTC).timestamp()
        memory_header = memory_data["MEMORY"].get("HEAD", None)
        conversation_document = xmltodict.unparse(
            memory_data, pretty=True, full_document=False
        )
        return BuiltMemoryRecord(
            memory_data=memory_data,
            document=conversation_document,
            header=memory_header,
            timestamp=timestamp,
        )
