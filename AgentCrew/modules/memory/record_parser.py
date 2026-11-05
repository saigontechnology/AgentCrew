"""Deterministic source-preserving XML unit derivation; no model extraction."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from xml.etree import ElementTree

from .types import MemoryRecord, ParsedMemory, RetrievalUnit


MAX_DOCUMENT_CHARACTERS = 1_000_000
MAX_INDEX_CHARACTERS = 100_000
MAX_UNITS = 512
TECHNICAL_IDENTIFIER = re.compile(
    r"https?://[^\s<>\"']+|(?:/?[\w.-]+/)+[\w.-]+"
    r"|\b\w+(?:[_.-]\w+)+\b|\b[A-Z][A-Z0-9]{1,}\b"
)


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identifiers(text: str) -> tuple[str, ...]:
    values = (match.group().rstrip(".,;:!?)") for match in TECHNICAL_IDENTIFIER.finditer(text))
    return tuple(dict.fromkeys(value for value in values if value))


def derive_memory_units(record: MemoryRecord) -> ParsedMemory:
    """Keep invalid/oversized source verbatim in its parent and index a fallback."""
    warnings: list[str] = []
    fields: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...]]] = []
    structured: dict[str, list[str]] = {}
    try:
        if len(record.document) > MAX_DOCUMENT_CHARACTERS:
            raise ValueError("document_size_limit")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", record.document, re.IGNORECASE):
            raise ValueError("xml_declaration_rejected")
        root = ElementTree.fromstring(record.document)
        if root.tag != "MEMORY":
            raise ValueError("unexpected_xml_root")
        preferred_kind = record.kinds[0] if record.kinds else None
        mapping = (
            ("HEAD", "headline"),
            ("FLOW/INTENT", "episode"),
            ("FLOW/EXPLORATION", "episode"),
            ("FLOW/OUTCOME", preferred_kind or "outcome"),
            ("INSIGHTS/INSIGHT", preferred_kind or "fact"),
            ("CONVERSATION_NOTES/NOTE", preferred_kind or "note"),
            ("ENTITIES/ENTITY", "entity"),
            ("DOMAINS/DOMAIN", "tag"),
            ("RESOURCES/RESOURCE", "resource"),
        )
        for source_field, kind in mapping:
            for node in root.findall(source_field):
                text = " ".join(" ".join(node.itertext()).split())
                if not text:
                    continue
                entities: tuple[str, ...] = ()
                tags: tuple[str, ...] = ()
                if kind == "entity":
                    name = node.findtext("NAME") or text
                    entities = (name.strip(),)
                elif kind == "resource":
                    entities = (text,)
                elif kind == "tag":
                    tags = (text,)
                fields.append((source_field, kind, text, entities, tags))
                structured.setdefault(source_field, []).append(text)
        if not fields:
            raise ValueError("no_supported_xml_fields")
    except ElementTree.ParseError:
        warnings.append("malformed_xml_fallback")
    except ValueError as error:
        warnings.append(str(error))
    if warnings:
        fields = [("BODY", "body", record.document, (), ())]
        structured = {}
    units = []
    seen = set()
    for source_field, kind, text, entities, tags in fields:
        if len(units) >= MAX_UNITS:
            warnings.append("retrieval_unit_limit_reached")
            break
        if len(text) > MAX_INDEX_CHARACTERS:
            text = text[:MAX_INDEX_CHARACTERS]
            warnings.append("indexed_text_truncated")
        digest = checksum(normalize_text(text))
        identity = (source_field, digest)
        if identity in seen:
            continue
        seen.add(identity)
        unit_id = checksum(json.dumps([record.id, source_field, digest]))
        units.append(RetrievalUnit(
            id=unit_id, memory_id=record.id, kind=kind,
            source_field=source_field, text=text, ordinal=len(units), checksum=digest,
            entities=tuple(dict.fromkeys((*entities, *_identifiers(text)))), tags=tags,
        ))
    return ParsedMemory(
        units=tuple(units),
        structured_json=json.dumps(structured, ensure_ascii=False, sort_keys=True) if structured else None,
        warnings=tuple(dict.fromkeys(warnings)),
    )
