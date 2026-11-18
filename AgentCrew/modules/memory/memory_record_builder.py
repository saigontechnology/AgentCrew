"""Backend-neutral memory record builder for store operations (MEM-501).

Extraction prompts, store hooks, the deterministic fallback and session
context handling are shared by every persistence backend; only storage
differs. The consolidation query builder gives Chroma and SQLite one
lexical candidate source (MEM-507).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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


class ExtractionFailure(StrEnum):
    """Stable, content-free failure categories shared by extraction diagnostics."""

    PROVIDER_ERROR = "provider_error"
    EMPTY_RESPONSE = "empty_response"
    MISSING_MEMORY_BLOCK = "missing_memory_block"
    MALFORMED_XML = "malformed_xml"
    INVALID_MEMORY_ROOT = "invalid_memory_root"
    OUTPUT_TRUNCATED = "output_truncated"
    RESOURCE_ENFORCEMENT_FAILED = "resource_enforcement_failed"


def _extraction_diagnostic(
    service: BaseLLMService | None,
    *,
    category: ExtractionFailure,
    attempt: int,
    response_characters: int,
    exception_class: str,
    final: bool = False,
) -> None:
    """Log only identity, counters and categories, never exception text or output."""
    from AgentCrew.modules.llm.service_manager import ServiceManager

    fields = {
        **ServiceManager.describe_service(service),
        "event": "memory_extraction_fallback" if final else "memory_extraction_attempt",
        "category": category.value,
        "attempt": attempt,
        "retry_count": max(0, attempt - 1),
        "response_characters": response_characters,
        "exception_class": exception_class
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", exception_class)
        else "unknown",
        "final_fallback_reason": category.value if final else None,
    }
    logger.bind(**fields).log(
        "ERROR" if final else "WARNING",
        "Memory extraction {}: category={} attempt={} retry_count={} "
        "response_characters={} exception_class={} provider={} "
        "service_family={} service_class={} model={}",
        "fallback" if final else "attempt failed",
        fields["category"],
        fields["attempt"],
        fields["retry_count"],
        fields["response_characters"],
        fields["exception_class"],
        fields["provider"],
        fields["service_family"],
        fields["service_class"],
        fields["qualified_model"],
    )


def parse_xml_block(text: str, tag_name: str) -> dict[str, Any] | None:
    open_tag = f"<{tag_name}>"
    close_tag = f"</{tag_name}>"
    if open_tag not in text or close_tag not in text:
        return None
    start = text.index(open_tag)
    end = text.index(close_tag) + len(close_tag)
    xml_block = text[start:end]
    xml_block = re.sub(
        r"&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)", "&amp;", xml_block
    )
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


def _fallback_memory(
    user_message: str, assistant_response_for_memory: str
) -> dict[str, Any]:
    return {
        "MEMORY": {
            "DATE": datetime.now(UTC).strftime("%Y-%m-%d"),
            "CONVERSATION_NOTES": {
                "NOTE": [user_message, assistant_response_for_memory]
            },
        }
    }


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

        from .resources import enforce_resources

        captured = operation_data.get("resources", ())
        memory_data = None
        retried = 0
        attempt = 0
        response_characters = 0
        failure = None
        exception_class = "none"
        if self.llm_service:
            while retried < EXTRACTION_RETRIES:
                analyzed_text = None
                attempt = retried + 1
                category = ExtractionFailure.PROVIDER_ERROR
                response_characters = 0
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
                        .replace("{assistant_response}", assistant_response_for_memory)
                    )
                    analyzed_prompt += (
                        "\n<ACCESSED_RESOURCES>"
                        + json.dumps(
                            [item.uri for item in captured], ensure_ascii=False
                        )
                        + "</ACCESSED_RESOURCES>\nUse only these exact URIs or resources from previous context. You may propose RESOURCE uri and name attributes and ENTITY_REF relation links only when the conversation explicitly supports a unique named entity. Ambiguous resources must remain unlinked."
                    )
                    analyzed_text = await self.llm_service.process_message(
                        analyzed_prompt
                    )
                    response_characters = (
                        len(analyzed_text) if isinstance(analyzed_text, str) else 0
                    )
                    category = ExtractionFailure.EMPTY_RESPONSE
                    if not isinstance(analyzed_text, str) or not analyzed_text.strip():
                        raise ValueError("empty_response")
                    category = ExtractionFailure.MISSING_MEMORY_BLOCK
                    if (
                        "<MEMORY>" not in analyzed_text
                        or "</MEMORY>" not in analyzed_text
                    ):
                        raise ValueError(
                            "LLM response missing <MEMORY>...</MEMORY> block"
                        )
                    category = ExtractionFailure.MALFORMED_XML
                    memory_data = parse_xml_block(analyzed_text, "MEMORY")
                    category = ExtractionFailure.INVALID_MEMORY_ROOT
                    if memory_data is None or not isinstance(
                        memory_data.get("MEMORY"), dict
                    ):
                        memory_data = None
                        raise ValueError("Failed to parse <MEMORY> XML block")
                    failure = None
                    break
                except Exception as e:
                    from AgentCrew.modules.llm.base import LLMResponseError

                    if (
                        category == ExtractionFailure.PROVIDER_ERROR
                        and isinstance(e, LLMResponseError)
                        and e.output_truncated
                    ):
                        category = ExtractionFailure.OUTPUT_TRUNCATED
                    memory_data = None
                    failure = category
                    exception_class = type(e).__name__
                    _extraction_diagnostic(
                        self.llm_service,
                        category=category,
                        attempt=attempt,
                        response_characters=response_characters,
                        exception_class=exception_class,
                    )
                    retried += 1
                    continue

        if memory_data is None:
            memory_data = _fallback_memory(user_message, assistant_response_for_memory)

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
        try:
            conversation_document = xmltodict.unparse(
                memory_data, pretty=True, full_document=False
            )
            conversation_document = enforce_resources(
                conversation_document,
                captured,
                (session_context,) if session_context else (),
            )
        except Exception as exc:
            failure = ExtractionFailure.RESOURCE_ENFORCEMENT_FAILED
            exception_class = type(exc).__name__
            _extraction_diagnostic(
                self.llm_service,
                category=failure,
                attempt=attempt,
                response_characters=response_characters,
                exception_class=exception_class,
                final=True,
            )
            failure = None
            memory_data = _fallback_memory(user_message, assistant_response_for_memory)
            conversation_document = enforce_resources(
                xmltodict.unparse(memory_data, pretty=True, full_document=False),
                captured,
                (session_context,) if session_context else (),
            )
            memory_header = None
        if failure is not None:
            _extraction_diagnostic(
                self.llm_service,
                category=failure,
                attempt=attempt,
                response_characters=response_characters,
                exception_class=exception_class,
                final=True,
            )
        memory_data = xmltodict.parse(conversation_document)
        return BuiltMemoryRecord(
            memory_data=memory_data,
            document=conversation_document,
            header=memory_header,
            timestamp=timestamp,
        )
