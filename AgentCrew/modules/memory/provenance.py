"""Bounded consolidation identity, provenance and same-agent lifecycle graphs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

MAX_MEMORY_FAMILY_SCAN_RECORDS = 10_000
MAX_MEMORY_FAMILY_MEMBERS = 10_000
PROVENANCE_VERSION = 1


class MemoryFamilyError(ValueError):
    """Unsafe or ambiguous lifecycle operation; messages never contain documents."""


def is_active(metadata: dict[str, Any]) -> bool:
    """Missing legacy status is active; a linkage always hides a source."""
    return (
        metadata.get("status", "active") == "active"
        and metadata.get("superseded_by") is None
    )


def source_ids(metadata: dict[str, Any]) -> tuple[str, ...]:
    """Validate scalar-compatible direct-source provenance without rewriting IDs."""
    version = metadata.get("provenance_version")
    encoded = metadata.get("consolidated_from_json")
    if (
        "provenance_version" not in metadata
        and "consolidated_from_json" not in metadata
    ):
        return ()
    if (
        type(version) is not int
        or version != PROVENANCE_VERSION
        or not isinstance(encoded, str)
    ):
        raise MemoryFamilyError("Invalid provenance representation")
    try:
        values = json.loads(encoded)
    except (ValueError, TypeError, RecursionError) as error:
        raise MemoryFamilyError("Malformed provenance JSON") from error
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise MemoryFamilyError("Invalid provenance source IDs")
    if len(values) > MAX_MEMORY_FAMILY_MEMBERS or len(set(values)) != len(values):
        raise MemoryFamilyError("Duplicate or oversized provenance")
    return tuple(values)


def with_sources(metadata: dict[str, Any], sources: list[str]) -> dict[str, Any]:
    """Union direct sources, preserving other metadata and deterministic encoding."""
    if any(not isinstance(value, str) or not value.strip() for value in sources):
        raise MemoryFamilyError("Invalid provenance union IDs")
    values = sorted(set(source_ids(metadata)).union(sources))
    if len(values) > MAX_MEMORY_FAMILY_MEMBERS or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise MemoryFamilyError("Invalid or oversized provenance union")
    return {
        **metadata,
        "provenance_version": PROVENANCE_VERSION,
        "consolidated_from_json": json.dumps(
            values, ensure_ascii=False, separators=(",", ":")
        ),
    }


def merge_metadata(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Ordinary writes retain provenance rather than rebuilding it away."""
    sources = list(source_ids(existing)) + list(source_ids(incoming))
    result = {**existing, **incoming}
    if "provenance_version" in existing or "provenance_version" in incoming:
        result.pop("provenance_version", None)
        result.pop("consolidated_from_json", None)
        result = with_sources(result, sources)
    return result


def timestamp(value: Any) -> float | None:
    """Legacy numeric or ISO timestamps; unknown ages are never expired."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            parsed = datetime.fromisoformat(value)
            result = parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class FamilyRecord:
    id: str
    agent: str
    session: str | None
    metadata: dict[str, Any]
    updated_at: float | None = None


@dataclass(frozen=True)
class MemoryFamily:
    root: str
    members: tuple[str, ...]


def choose_identity(
    native_id: str,
    operation_id: str,
    agent: str,
    session: str,
    records: list[FamilyRecord],
) -> str:
    """Reuse one active exact-session identity; never overwrite an inactive ID."""
    if any(row.agent != agent or row.session != session for row in records):
        raise MemoryFamilyError("Persistence identity scope mismatch")
    active = [row for row in records if is_active(row.metadata)]
    if len(active) > 1:
        raise MemoryFamilyError("Multiple active persistence identities")
    if active:
        source_ids(active[0].metadata)
        return active[0].id
    if not records:
        return native_id
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise MemoryFamilyError("Missing continuation operation identity")
    return f"continuation:{operation_id}"


def consolidation_metadata(
    index: FamilyIndex, target: str, sources: list[str]
) -> dict[str, Any]:
    """Validate the complete proposed graph before either backend mutates data."""
    if target in sources or len(set(sources)) != len(sources):
        raise MemoryFamilyError("Invalid consolidation source set")
    index.require_roots([target])
    direct = source_ids(index.records[target].metadata)
    for key in sources:
        if key not in index.records:
            raise MemoryFamilyError("Missing or out-of-scope consolidation source")
        family = index._family(index._members(key))
        if family.root != key and not (family.root == target and key in direct):
            raise MemoryFamilyError("Consolidation source is not a root")
    metadata = with_sources(index.records[target].metadata, sources)
    proposed = []
    for row in index.records.values():
        updated = metadata if row.id == target else row.metadata
        if row.id in sources:
            updated = {**updated, "status": "superseded", "superseded_by": target}
        proposed.append(
            FamilyRecord(row.id, row.agent, row.session, updated, row.updated_at)
        )
    FamilyIndex(proposed, index.records[target].agent).resolve([target])
    return metadata


class FamilyIndex:
    """One bounded exact-agent snapshot; invalid unrelated components stay isolated."""

    def __init__(self, records: list[FamilyRecord], agent: str):
        if len(records) > MAX_MEMORY_FAMILY_SCAN_RECORDS:
            raise MemoryFamilyError("Memory family scan limit exceeded")
        if any(row.agent != agent for row in records):
            raise MemoryFamilyError("Memory family snapshot scope mismatch")
        if any(not isinstance(row.id, str) or not row.id.strip() for row in records):
            raise MemoryFamilyError("Invalid memory family identity")
        self.records = {row.id: row for row in records}
        if len(self.records) != len(records):
            raise MemoryFamilyError("Duplicate memory family identity")
        self.parents: dict[str, set[str]] = {key: set() for key in self.records}
        self.adjacent: dict[str, set[str]] = {key: set() for key in self.records}
        self.provenance_edges: set[tuple[str, str]] = set()
        self.invalid: set[str] = set()
        for row in records:
            try:
                sources = source_ids(row.metadata)
            except MemoryFamilyError:
                self.invalid.add(row.id)
                sources = ()
            for source in sources:
                self._edge(source, row.id, row.id, provenance=True)
            parent = row.metadata.get("superseded_by")
            if parent is not None:
                self._edge(row.id, parent, row.id)

    def _edge(self, source, target, owner, *, provenance=False):
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in self.records
            or target not in self.records
            or source == target
        ):
            self.invalid.add(owner)
            return
        self.parents[source].add(target)
        self.adjacent[source].add(target)
        self.adjacent[target].add(source)
        if provenance:
            self.provenance_edges.add((source, target))

    def _members(self, seed: str) -> set[str]:
        members: set[str] = set()
        pending = [seed]
        while pending:
            current = pending.pop()
            if current in members:
                continue
            members.add(current)
            if len(members) > MAX_MEMORY_FAMILY_MEMBERS:
                raise MemoryFamilyError("Memory family member limit exceeded")
            pending.extend(self.adjacent[current] - members)
        return members

    def _family(self, members: set[str]) -> MemoryFamily:
        if members & self.invalid:
            raise MemoryFamilyError("Malformed or out-of-scope family endpoint")
        roots = []
        for key in members:
            parents = self.parents[key]
            row = self.records[key]
            if len(parents) > 1:
                raise MemoryFamilyError("Conflicting memory family parents")
            if not parents:
                if not is_active(row.metadata):
                    raise MemoryFamilyError("Inactive memory family root")
                roots.append(key)
            elif row.metadata.get("status", "active") == "active":
                parent = next(iter(parents))
                if (
                    row.metadata.get("superseded_by")
                    or (key, parent) not in self.provenance_edges
                ):
                    raise MemoryFamilyError("Invalid active non-root memory")
            elif row.metadata.get("status") != "superseded":
                raise MemoryFamilyError("Invalid source lifecycle")
        if len(roots) != 1:
            raise MemoryFamilyError("Rootless or multiple-root memory family")
        finished: set[str] = set()
        for key in members:
            trail: set[str] = set()
            cursor = key
            while cursor not in finished:
                if cursor in trail:
                    raise MemoryFamilyError("Cyclic memory family")
                trail.add(cursor)
                parents = self.parents[cursor]
                if not parents:
                    break
                cursor = next(iter(parents))
            finished.update(trail)
        return MemoryFamily(roots[0], tuple(sorted(members)))

    def resolve(self, seeds: list[str]) -> tuple[str, ...]:
        """Resolve every found component before returning any deletion IDs."""
        resolved: set[str] = set()
        for seed in dict.fromkeys(seeds):
            if seed in self.records and seed not in resolved:
                resolved.update(self._family(self._members(seed)).members)
        return tuple(sorted(resolved))

    def require_roots(self, ids: list[str]) -> None:
        for key in dict.fromkeys(ids):
            if key not in self.records or self._family(self._members(key)).root != key:
                raise MemoryFamilyError("Consolidation requires active family roots")

    def expired(self, cutoff: float) -> tuple[str, ...]:
        seen: set[str] = set()
        expired: set[str] = set()
        for key in self.records:
            if key in seen:
                continue
            try:
                members = self._members(key)
                seen.update(members)
                family = self._family(members)
                age = timestamp(self.records[family.root].updated_at)
                if age is None:
                    logger.warning("Retention skipped unknown memory root age")
                elif age < cutoff:
                    expired.update(family.members)
            except MemoryFamilyError:
                logger.warning("Retention skipped invalid memory family")
        return tuple(sorted(expired))
