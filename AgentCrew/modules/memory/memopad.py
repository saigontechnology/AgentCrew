"""Bounded, untrusted memopad proposals; never used for context or authorization."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

MemopadKind = Literal[
    "decision",
    "lesson",
    "constraint",
    "correction",
    "workaround",
    "unresolved",
    "verified_fact",
    "project_preference",
]


class MemopadCandidate(BaseModel):
    """Model proposes content only; status, confidence and ownership are app-owned."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        frozen=True,
        revalidate_instances="always",
    )

    kind: MemopadKind
    title: str = Field(min_length=1, max_length=160, pattern=r"\S")
    statement: str = Field(min_length=1, max_length=1200, pattern=r"\S")
    subject_key: str = Field(min_length=1, max_length=128, pattern=r"\S")


_CANDIDATES = TypeAdapter(Annotated[list[MemopadCandidate], Field(max_length=8)])
_SENSITIVE = re.compile(
    r"(?i)(?:\b(?:password|secret|api[_-]?key|(?:access|refresh)[_-]?token|authorization|cookie)"
    r"[\s\"']*[:=]|\bbearer\s+|-----BEGIN [\w ]*PRIVATE KEY-----|https?://[^/\s]+@)"
)


def parse_memopad_candidates(value: Any) -> list[MemopadCandidate]:
    """Reject the entire optional projection on error, not its canonical memory."""
    try:
        candidates = _CANDIDATES.validate_python(value, strict=True)
        if any(
            (ord(character) < 32 and character not in "\n\t\r")
            or 0xD800 <= ord(character) <= 0xDFFF
            for candidate in candidates
            for text in (candidate.title, candidate.statement, candidate.subject_key)
            for character in text
        ):
            return []
        return [
            candidate
            for candidate in candidates
            if not any(
                _SENSITIVE.search(text)
                for text in (
                    candidate.title,
                    candidate.statement,
                    candidate.subject_key,
                )
            )
        ]
    except (ValidationError, ValueError, TypeError):
        return []


def format_memopad_stats(stats: dict[str, Any]) -> str:
    """Aggregate counts only: no statements, evidence payloads or raw workspace paths."""
    lines = [
        "Memopad collection (read-only; no context injection)",
        f"Total items: {stats['total']}",
        f"Source memories represented: {stats['source_memories']}",
        f"Orphans (no evidence): {stats['orphans']}",
        f"Mean confidence: {stats['mean_confidence']:.2f}",
        f"Items with multiple source memories: {stats['corroborated']}",
    ]
    for field in ("status", "kind", "project", "agent"):
        lines.append(f"By {field}:")
        lines.extend(f"  {key}: {count}" for key, count in stats[field].items())
        if not stats[field]:
            lines.append("  (none)")
    return "\n".join(lines)
