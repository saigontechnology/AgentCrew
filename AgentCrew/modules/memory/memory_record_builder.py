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
from typing import TYPE_CHECKING, Annotated, Any

import xmltodict
from loguru import logger
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from AgentCrew.modules.prompts.constants import (
    FIRST_TURN_INSTRUCTIONS,
    MERGE_INSTRUCTIONS,
    PRE_ANALYZE_PROMPT,
)

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

EXTRACTION_RETRIES = 3
MAX_PREVIOUS_MEMORY_CHARACTERS = 30_000
MAX_ASSISTANT_EVIDENCE_CHARACTERS = 40_000
MAX_USER_EVIDENCE_CHARACTERS = 12_000
MAX_EXTRACTION_CHARACTERS = 12_000
MAX_MINIMAL_EXTRACTION_CHARACTERS = 4_000


class ExtractionFailure(StrEnum):
    """Stable, content-free failure categories shared by extraction diagnostics."""

    PROVIDER_ERROR = "provider_error"
    EMPTY_RESPONSE = "empty_response"
    MISSING_MEMORY_BLOCK = "missing_memory_block"
    MALFORMED_XML = "malformed_xml"
    INVALID_MEMORY_ROOT = "invalid_memory_root"
    OUTPUT_TRUNCATED = "output_truncated"
    RESOURCE_ENFORCEMENT_FAILED = "resource_enforcement_failed"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    UNKNOWN_RESOURCE_ID = "unknown_resource_id"
    OUTPUT_TOO_LARGE = "output_too_large"


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


def _valid_xml_character(character: str) -> bool:
    value = ord(character)
    return (
        value in (9, 10, 13)
        or 0x20 <= value <= 0xD7FF
        or 0xE000 <= value <= 0xFFFD
        or 0x10000 <= value <= 0x10FFFF
    )


def _xml_text(value: str) -> str:
    if not all(_valid_xml_character(character) for character in value):
        raise ValueError("invalid_xml_character")
    return value


MemoryText = Annotated[str, Field(max_length=1200), AfterValidator(_xml_text)]
MemoryName = Annotated[str, Field(max_length=256), AfterValidator(_xml_text)]


class _ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ExtractionFlow(_ExtractionModel):
    intent: MemoryText
    exploration: MemoryText
    outcome: MemoryText


class ExtractionEntity(_ExtractionModel):
    name: Annotated[MemoryName, Field(min_length=1, pattern=r"\S")]
    desc: MemoryText = ""
    update_existing: bool = False


class ExtractionEntityRef(_ExtractionModel):
    name: Annotated[MemoryName, Field(min_length=1, pattern=r"\S")]
    relation: Annotated[MemoryName, Field(min_length=1, max_length=128, pattern=r"\S")]


class ExtractionResource(_ExtractionModel):
    id: Annotated[str, Field(pattern=r"^r(?:[1-9]|[12][0-9]|3[0-2])$")]
    name: MemoryName = ""
    entity_refs: list[ExtractionEntityRef] = Field(default_factory=list, max_length=8)


class MinimalMemoryExtraction(_ExtractionModel):
    head: MemoryName
    notes: list[MemoryText] = Field(max_length=8)


class MemoryExtraction(MinimalMemoryExtraction):
    flow: ExtractionFlow
    insights: list[MemoryText] = Field(default_factory=list, max_length=8)
    entities: list[ExtractionEntity] = Field(default_factory=list, max_length=24)
    domains: list[MemoryName] = Field(default_factory=list, max_length=8)
    resources: list[ExtractionResource] = Field(default_factory=list, max_length=32)


class ExtractionValidationError(ValueError):
    def __init__(self, category: ExtractionFailure):
        super().__init__(category.value)
        self.category = category


def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[Evidence shortened]\n"
    remaining = limit - len(marker)
    head = remaining // 2
    return text[:head] + marker + text[-(remaining - head) :]


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid_json_constant")


def parse_extraction(text: str, *, minimal: bool = False) -> MinimalMemoryExtraction:
    """Provider-neutral JSON boundary; never repair or echo invalid model output."""
    limit = MAX_MINIMAL_EXTRACTION_CHARACTERS if minimal else MAX_EXTRACTION_CHARACTERS
    if len(text) > limit:
        raise ExtractionValidationError(ExtractionFailure.OUTPUT_TOO_LARGE)
    fenced = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise ExtractionValidationError(ExtractionFailure.MALFORMED_JSON) from exc
    try:
        schema = MinimalMemoryExtraction if minimal else MemoryExtraction
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionValidationError(
            ExtractionFailure.SCHEMA_VALIDATION_FAILED
        ) from exc


def _extracted_memory(
    payload: MinimalMemoryExtraction, captured: tuple
) -> dict[str, Any]:
    """Map validated semantic fields to canonical XML keys; URIs are app-owned."""
    memory: dict[str, Any] = {
        "HEAD": payload.head,
        "DATE": datetime.now(UTC).strftime("%Y-%m-%d"),
        "CONVERSATION_NOTES": {"NOTE": payload.notes},
    }
    if isinstance(payload, MemoryExtraction):
        allowed = {f"r{index}": item for index, item in enumerate(captured[:32], 1)}
        resources = []
        for proposal in payload.resources:
            if proposal.id not in allowed:
                raise ExtractionValidationError(ExtractionFailure.UNKNOWN_RESOURCE_ID)
            resources.append(
                {
                    "@uri": allowed[proposal.id].uri,
                    "@name": proposal.name,
                    "ENTITY_REF": [
                        {"@relation": ref.relation, "#text": ref.name}
                        for ref in proposal.entity_refs
                    ],
                }
            )
        memory.update(
            {
                "FLOW": {
                    key.upper(): value
                    for key, value in payload.flow.model_dump().items()
                },
                "INSIGHTS": {"INSIGHT": payload.insights},
                "ENTITIES": {
                    "ENTITY": [
                        {"NAME": entity.name, "DESC": entity.desc}
                        for entity in payload.entities
                    ]
                },
                "DOMAINS": {"DOMAIN": payload.domains},
                "RESOURCES": {"RESOURCE": resources},
            }
        )
    return {"MEMORY": memory}


def _merge_entities(old_items: list, new_items: list, updates: tuple[str, ...]) -> list:
    """Apply explicit unique-name updates, never infer identity from a name collision."""
    from collections import Counter

    from .record_parser import normalize_text

    def name(item: Any) -> str:
        value = item.get("NAME") if isinstance(item, dict) else None
        return normalize_text(value) if isinstance(value, str) else ""

    update_keys = {normalize_text(value) for value in updates}
    old_counts = Counter(map(name, old_items))
    new_counts = Counter(map(name, new_items))
    positions = {name(item): index for index, item in enumerate(old_items)}
    retained = list(old_items)
    for item in new_items:
        key = name(item)
        if key and key in update_keys and old_counts[key] == new_counts[key] == 1:
            position = positions[key]
            target = dict(retained[position])
            for field, value in item.items():
                if field == "NAME" or not value:
                    continue
                previous = target.get(field)
                if not previous:
                    target[field] = value
                elif previous != value:
                    target[field] = (
                        previous + "\n\n" + value
                        if isinstance(previous, str) and isinstance(value, str)
                        else [previous, value]
                    )
            retained[position] = target
        else:
            retained.append(item)
    return retained


def _preserve_previous(
    memory_data: dict[str, Any], session_context: str, updates: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Retain existing semantic data even when prompt bounds or fallback omit it."""
    if not session_context:
        return memory_data
    try:
        previous = xmltodict.parse(session_context).get("MEMORY")
    except Exception:
        return memory_data
    if not isinstance(previous, dict):
        return memory_data
    incoming = memory_data["MEMORY"]
    merged = {**previous, **incoming}
    for container, tag in (
        ("INSIGHTS", "INSIGHT"),
        ("ENTITIES", "ENTITY"),
        ("DOMAINS", "DOMAIN"),
        ("CONVERSATION_NOTES", "NOTE"),
    ):
        old_container = previous.get(container) or {}
        new_container = incoming.get(container) or {}
        old = (
            old_container.get(tag) if isinstance(old_container, dict) else old_container
        )
        new = (
            new_container.get(tag) if isinstance(new_container, dict) else new_container
        )
        old_items = old if isinstance(old, list) else [old] if old is not None else []
        new_items = new if isinstance(new, list) else [new] if new is not None else []
        if container == "ENTITIES":
            retained = _merge_entities(old_items, new_items, updates)
        else:
            retained = list(old_items)
            seen = {json.dumps(item, sort_keys=True) for item in retained}
            for item in new_items:
                identity = json.dumps(item, sort_keys=True)
                if identity not in seen:
                    retained.append(item)
                    seen.add(identity)
        merged[container] = {
            **(old_container if isinstance(old_container, dict) else {}),
            tag: retained,
        }
    old_flow = previous.get("FLOW") or {}
    new_flow = incoming.get("FLOW") or {}
    if isinstance(old_flow, str):
        old_flow = {"EXPLORATION": old_flow}
    if isinstance(new_flow, str):
        new_flow = {"EXPLORATION": new_flow}
    old_flow = old_flow if isinstance(old_flow, dict) else {}
    new_flow = new_flow if isinstance(new_flow, dict) else {}
    flow: dict[str, Any] = {**old_flow, **new_flow}
    merged["FLOW"] = flow
    for key in ("INTENT", "EXPLORATION", "OUTCOME"):
        old_value, new_value = old_flow.get(key), new_flow.get(key)
        values = [value for value in (old_value, new_value) if value]
        if all(isinstance(value, str) for value in values):
            flow[key] = "\n\n".join(dict.fromkeys(values))
        else:
            flow[key] = values
    if not incoming.get("HEAD"):
        merged["HEAD"] = previous.get("HEAD")
    return {"MEMORY": merged}


def _fallback_memory(
    user_message: str, assistant_response_for_memory: str
) -> dict[str, Any]:
    return {
        "MEMORY": {
            "DATE": datetime.now(UTC).strftime("%Y-%m-%d"),
            "CONVERSATION_NOTES": {
                "NOTE": [
                    "".join(
                        character if _valid_xml_character(character) else "\ufffd"
                        for character in text
                    )
                    for text in (
                        _bounded_text(user_message, MAX_USER_EVIDENCE_CHARACTERS),
                        _bounded_text(
                            assistant_response_for_memory,
                            MAX_ASSISTANT_EVIDENCE_CHARACTERS,
                        ),
                    )
                ]
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
        attempt = 0
        response_characters = 0
        failure = None
        exception_class = "none"
        entity_updates: tuple[str, ...] = ()
        if self.llm_service:
            for attempt in range(1, EXTRACTION_RETRIES + 1):
                category = ExtractionFailure.PROVIDER_ERROR
                response_characters = 0
                try:
                    minimal = attempt == EXTRACTION_RETRIES
                    schema = MinimalMemoryExtraction if minimal else MemoryExtraction
                    instructions = (
                        FIRST_TURN_INSTRUCTIONS
                        if not session_context
                        else MERGE_INSTRUCTIONS
                    )
                    previous = (
                        "<PREVIOUS_CONVERSATION_CONTEXT>\n"
                        + _bounded_text(session_context, MAX_PREVIOUS_MEMORY_CHARACTERS)
                        + "\n</PREVIOUS_CONVERSATION_CONTEXT>"
                        if session_context
                        else ""
                    )
                    evidence = json.dumps(
                        {
                            "user": _bounded_text(
                                user_message, MAX_USER_EVIDENCE_CHARACTERS
                            ),
                            "assistant": _bounded_text(
                                assistant_response_for_memory,
                                MAX_ASSISTANT_EVIDENCE_CHARACTERS,
                            ),
                        },
                        ensure_ascii=False,
                    )
                    analyzed_prompt = PRE_ANALYZE_PROMPT.format(
                        current_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        context_instructions=instructions,
                        conversation_context=previous,
                        conversation_turn=evidence,
                        output_schema=json.dumps(
                            schema.model_json_schema(), ensure_ascii=False
                        ),
                        output_limit=MAX_MINIMAL_EXTRACTION_CHARACTERS
                        if minimal
                        else MAX_EXTRACTION_CHARACTERS,
                        accessed_resources=json.dumps(
                            [
                                {"id": f"r{index}", "uri": item.uri, "name": item.name}
                                for index, item in enumerate(captured[:32], 1)
                            ],
                            ensure_ascii=False,
                        ),
                    )
                    if failure is not None:
                        analyzed_prompt += (
                            "\n<RETRY_GUIDANCE>Previous attempt failed: "
                            + failure.value
                            + ". Return a shorter JSON object matching the supplied schema exactly; no XML, commentary, unknown fields or resource URIs."
                            + (
                                " Use only head and notes on this final attempt."
                                if minimal
                                else ""
                            )
                            + "</RETRY_GUIDANCE>"
                        )
                    analyzed_text = await self.llm_service.process_message(
                        analyzed_prompt
                    )
                    response_characters = (
                        len(analyzed_text) if isinstance(analyzed_text, str) else 0
                    )
                    category = ExtractionFailure.EMPTY_RESPONSE
                    if not isinstance(analyzed_text, str) or not analyzed_text.strip():
                        raise ExtractionValidationError(category)
                    category = ExtractionFailure.SCHEMA_VALIDATION_FAILED
                    payload = parse_extraction(analyzed_text, minimal=minimal)
                    memory_data = _extracted_memory(payload, captured)
                    entity_updates = (
                        tuple(
                            entity.name
                            for entity in payload.entities
                            if entity.update_existing
                        )
                        if isinstance(payload, MemoryExtraction)
                        else ()
                    )
                    failure = None
                    break
                except Exception as exc:
                    from AgentCrew.modules.llm.base import LLMResponseError

                    if isinstance(exc, ExtractionValidationError):
                        category = exc.category
                    elif (
                        category == ExtractionFailure.PROVIDER_ERROR
                        and isinstance(exc, LLMResponseError)
                        and exc.output_truncated
                    ):
                        category = ExtractionFailure.OUTPUT_TRUNCATED
                    memory_data = None
                    failure = category
                    exception_class = type(exc).__name__
                    _extraction_diagnostic(
                        self.llm_service,
                        category=category,
                        attempt=attempt,
                        response_characters=response_characters,
                        exception_class=exception_class,
                    )
                    if (
                        category == ExtractionFailure.PROVIDER_ERROR
                        and attempt < EXTRACTION_RETRIES
                    ):
                        import asyncio

                        await asyncio.sleep(0.1 * attempt)

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

        memory_data = _preserve_previous(memory_data, session_context, entity_updates)
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
            memory_data = _preserve_previous(
                _fallback_memory(user_message, assistant_response_for_memory),
                session_context,
            )
            conversation_document = enforce_resources(
                xmltodict.unparse(memory_data, pretty=True, full_document=False),
                captured,
                (session_context,) if session_context else (),
            )
            memory_header = memory_data["MEMORY"].get("HEAD")
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
        memory_header = memory_data["MEMORY"].get("HEAD")
        return BuiltMemoryRecord(
            memory_data=memory_data,
            document=conversation_document,
            header=memory_header,
            timestamp=timestamp,
        )
