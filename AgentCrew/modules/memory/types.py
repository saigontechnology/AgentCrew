"""Non-breaking, request-local types for the SQLite retrieval prototype."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateSource(StrEnum):
    MEMORY_ID = "exact_memory_id"
    ENTITY = "exact_entity_or_resource"
    TAG = "exact_tag"
    FTS = "weighted_fts5"
    SEMANTIC = "semantic_document_vector"
    KIND_HINT = "explicit_kind_hint"


DOCUMENT_VECTOR_DIMENSIONS = 1024


@dataclass(frozen=True)
class SQLiteSemanticPolicy:
    """Explicit cosine-distance admission; unset means disabled, not calibrated."""

    max_distance: float | None = None

    def __post_init__(self) -> None:
        if self.max_distance is not None and (
            type(self.max_distance) not in (int, float)
            or not 0 <= self.max_distance <= 2
            or not math.isfinite(self.max_distance)
        ):
            raise ValueError(
                "SQLite semantic cutoff must be finite within cosine distance [0, 2]"
            )

    def qualifies(self, distance: float | None) -> bool:
        return (
            self.max_distance is not None
            and type(distance) in (int, float)
            and 0 <= distance <= self.max_distance
            and math.isfinite(distance)
        )


@dataclass(frozen=True)
class MemoryQuery:
    query: str
    agent_name: str
    current_session_id: str
    from_date: date | None = None
    to_date: date | None = None
    memory_ids: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    limit: int = 5
    candidate_limit: int = 100
    as_of: int | None = None
    query_vector: tuple[float, ...] | None = None
    semantic_policy: SQLiteSemanticPolicy = field(default_factory=SQLiteSemanticPolicy)

    def __post_init__(self) -> None:
        for value in (self.query, self.agent_name, self.current_session_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "Query, agent and explicit current session are required"
                )
        if len(self.query) > 4096 or not any(c.isalnum() for c in self.query):
            raise ValueError(
                "Query must contain meaningful text within 4096 characters"
            )
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise ValueError("Result limit must be between 1 and 20")
        if (
            type(self.candidate_limit) is not int
            or not 1 <= self.candidate_limit <= 500
        ):
            raise ValueError("Candidate limit must be between 1 and 500")
        for value in (self.from_date, self.to_date):
            if value is not None and type(value) is not date:
                raise ValueError("Date filters must be calendar dates")
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("Date range is inverted")
        for values in (self.memory_ids, self.kinds):
            if not isinstance(values, tuple) or len(values) > 50:
                raise ValueError("ID and kind hints must be bounded immutable tuples")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError("ID and kind hints must contain nonempty strings")
        if self.as_of is not None and type(self.as_of) is not int:
            raise ValueError("Validity time must be an explicit Unix timestamp")
        if not isinstance(self.semantic_policy, SQLiteSemanticPolicy):
            raise TypeError("SQLite semantic policy must be explicit and immutable")
        if self.query_vector is not None and (
            not isinstance(self.query_vector, tuple)
            or len(self.query_vector) != DOCUMENT_VECTOR_DIMENSIONS
            or any(
                type(value) not in (int, float)
                or isinstance(value, float) and math.isnan(value)
                or value in (float("inf"), float("-inf"))
                for value in self.query_vector
            )
        ):
            raise ValueError("Query vector must be an explicit 1024-float tuple")


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    agent_name: str
    session_id: str | None
    document: str
    created_at: int | float
    updated_at: int | float
    metadata: dict[str, Any] = field(default_factory=dict)
    cwd: str | None = None
    header: str | None = None
    status: str = "active"
    superseded_by: str | None = None
    subject_key: str | None = None
    valid_from: int | float | None = None
    valid_to: int | float | None = None
    evolved_at: str | None = None
    document_format: str = "xml"
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalUnit:
    id: str
    memory_id: str
    kind: str
    source_field: str
    text: str
    ordinal: int
    checksum: str
    entities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedMemory:
    units: tuple[RetrievalUnit, ...]
    structured_json: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchReason:
    source: CandidateSource
    rank: int
    detail: str
    distance: float | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    record: MemoryRecord
    unit: RetrievalUnit
    reason: MatchReason
    parse_warnings: tuple[str, ...] = ()
    source_headline: str | None = None


@dataclass(frozen=True)
class CandidateBatch:
    candidates: tuple[MemoryCandidate, ...]
    conflicting_subjects: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MemorySearchHit:
    memory_id: str
    unit_id: str
    date: str
    cwd: str | None
    kind: str
    headline: str | None
    excerpt: str
    reasons: tuple[MatchReason, ...]
    confidence: ConfidenceBand
    score: float
    conflict: bool = False
    parse_warnings: tuple[str, ...] = ()

    @property
    def citation(self) -> str:
        return f"memory:{self.memory_id}#{self.unit_id}"


@dataclass(frozen=True)
class MemorySearchResult:
    hits: tuple[MemorySearchHit, ...] = ()
    warnings: tuple[str, ...] = ()
