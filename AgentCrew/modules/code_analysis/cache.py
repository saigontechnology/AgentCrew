"""Cache for ``analyze_repo`` tool results.

Stores cached analysis results under ``<git_root>/.agentcrew/analyze_repo_cache/``.
Uses SHA-256 cache keys based on normalized arguments, LRU eviction (max 5
entries per project), and per-file content-hash manifest for change detection.

The cache uses a unified ``files`` map that serves simultaneously as:
* The content manifest (every supported path has a ``hash``).
* The analyzed set (records with ``analysis`` or ``error``).
* The skipped set (hash-only records for unselected files in
  repositories over the analysis limit).

No raw Tree-sitter output (``analysis_results``, AST structures) or complete
formatted ``analysis_text`` is persisted.  The repository response is
reconstructed from per-file compact analysis/error records and derived metadata.

All file paths stored in cache entries are relative to the *requested analysis
path* (not the Git root).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

CACHE_DIR_NAME = "analyze_repo_cache"
"""Subdirectory inside ``.agentcrew/`` that holds cache entries."""

MAX_CACHE_ENTRIES = 5
"""Maximum number of cache entries kept per project."""

SCHEMA_VERSION = 2
"""Schema/format version for cache entries.

Entries without the current version trigger a one-time full rebuild.
Increment this when the structured output format changes in
backwards-incompatible ways.

Version history:
    1 - Original schema with ``analysis_results``, ``manifest``,
        ``analyzed_relative_paths``, etc.
    2 - Unified ``files`` map.  Each supported path stores a content
        hash and optionally compact formatted ``analysis`` or an ``error``.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_hex(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _sha256_label(value: bytes | str) -> str:
    return f"sha256:{_sha256_hex(value)}"


def normalize_path(path: str) -> str:
    """Expand ``~`` and resolve to a canonical absolute path."""
    return os.path.realpath(os.path.expanduser(path))


def normalize_exclude_patterns(patterns: list[str] | None) -> list[str]:
    """Deduplicate and sort exclude patterns so ordering does not affect keys."""
    if not patterns:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return sorted(result)


def _find_git_root(path: str) -> str | None:
    """Return the git root directory for *path*, or *None*."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,  # returncode checked manually below
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Git helpers  (plumbing-based, no porcelain parsing)
# ---------------------------------------------------------------------------


def _git_capture(git_root: str, *args: str) -> tuple[int, str]:
    """Run ``git <args>`` in *git_root* and return ``(returncode, stdout)``.

    Raises ``RuntimeError`` on subprocess-level failure (not found, timeout).
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=git_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,  # returncode returned to caller for manual handling
        )
        return result.returncode, result.stdout
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(f"git {' '.join(args)} failed") from exc


# ---------------------------------------------------------------------------
# Per-file manifest for change detection
# ---------------------------------------------------------------------------


def discover_supported_files(
    path: str,
    exclude_patterns: list[str] | None = None,
    language_map: dict[str, str] | None = None,
) -> list[str] | None:
    """Discover all supported source files (tracked + untracked) under *path*.

    Returns sorted file paths relative to *path* (the requested analysis
    root), or *None* if git file listing fails.  This is the single shared
    discovery function used by both manifest computation and full analysis
    to ensure baseline consistency.

    Filters:
    * Files within the requested analysis *path*.
    * Not matching any *exclude_patterns*.
    * Having a supported language extension (from *language_map*).
    """
    if exclude_patterns is None:
        exclude_patterns = []
    if language_map is None:
        language_map = {}

    git_root = _find_git_root(normalize_path(path))
    if git_root is None:
        return None

    try:
        rc, out = _git_capture(git_root, "ls-files")
        if rc != 0:
            return None
        files = [f for f in out.splitlines() if f.strip()]

        rc2, ut_out = _git_capture(
            git_root, "ls-files", "--others", "--exclude-standard"
        )
        if rc2 != 0:
            return None
        files.extend(f for f in ut_out.splitlines() if f.strip())

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_files: list[str] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique_files.append(f)

        norm_path = normalize_path(path)
        path_prefix = Path(norm_path).resolve()
        git_root_path = Path(git_root).resolve()

        result: list[str] = []
        for rel_from_root in unique_files:
            full_path = git_root_path / rel_from_root

            # Must be within the requested analysis path
            try:
                full_path.relative_to(path_prefix)
            except ValueError:
                continue

            # Compute path relative to the analysis root (not git root)
            rel_from_analysis = os.path.relpath(full_path, path_prefix)

            # Normalize to forward slashes for cross-platform consistency
            # On Windows, os.path.relpath may return backslash separators;
            # always normalize so manifest keys match across platforms.
            rel_from_analysis = rel_from_analysis.replace("\\", "/")

            # Must not match exclude patterns
            excluded = False
            for pattern in exclude_patterns:
                if fnmatch.fnmatch(rel_from_analysis, pattern):
                    excluded = True
                    break
            if excluded:
                continue

            # Must have a supported language extension
            ext = os.path.splitext(rel_from_analysis)[1].lower()
            if ext not in language_map:
                continue

            result.append(rel_from_analysis)

        return sorted(result)

    except Exception as exc:
        logger.warning(f"Failed to list relevant files: {exc}")
        return None


def _compute_content_hash(base_path: str, rel_path: str) -> str | None:
    """Return SHA-256 hex digest of a file's content, or *None* on error."""
    full_path = Path(base_path) / rel_path
    try:
        content = full_path.read_bytes()
        return _sha256_hex(content)
    except (OSError, PermissionError) as exc:
        logger.debug(f"Cannot hash {rel_path}: {exc}")
        return None


def _compute_file_manifest(
    path: str,
    exclude_patterns: list[str] | None = None,
    language_map: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Build a ``{relative_path: sha256_hex}`` manifest for relevant files.

    Paths are relative to *path* (the requested analysis root), matching
    the namespace used by ``analysis_results`` and ``analyzed_relative_paths``.

    Returns *None* if git file listing fails or no supported files exist.
    """
    try:
        supported = discover_supported_files(path, exclude_patterns, language_map)
        if supported is None:
            return None
        norm_path = normalize_path(path)
        manifest: dict[str, str] = {}
        for rel_path in supported:
            h = _compute_content_hash(norm_path, rel_path)
            if h is None:
                logger.warning(f"Cannot hash {rel_path}; aborting manifest computation")
                return None
            manifest[rel_path] = h
        return manifest
    except Exception as exc:
        logger.warning(f"Failed to compute file manifest: {exc}")
        return None


def _detect_manifest_changes(
    stored: dict[str, str] | None,
    current: dict[str, str] | None,
) -> dict[str, list[str]]:
    """Compare two manifests and return added / modified / deleted files.

    Returns a dict with keys ``"added"``, ``"modified"``, ``"deleted"``
    each containing a list of relative paths.

    If either manifest is *None*, all current files are considered added and
    all stored files deleted (full rebuild signal).
    """
    if stored is None and current is None:
        return {"added": [], "modified": [], "deleted": []}
    if stored is None:
        return {"added": sorted(current or {}), "modified": [], "deleted": []}
    if current is None:
        return {"added": [], "modified": [], "deleted": sorted(stored)}

    stored_set = set(stored)
    current_set = set(current)

    added = sorted(current_set - stored_set)
    deleted = sorted(stored_set - current_set)
    modified = sorted(p for p in stored_set & current_set if stored[p] != current[p])

    return {"added": added, "modified": modified, "deleted": deleted}


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def _build_cache_key(
    path: str,
    exclude_patterns: list[str],
    feature_scope: str | None,
    deep_analysis: bool,
) -> str:
    """Deterministic SHA-256 cache key from normalized tool arguments."""
    payload = {
        "path": path,
        "exclude_patterns": exclude_patterns,
        "feature_scope": feature_scope,
        "deep_analysis": deep_analysis,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256_label(serialized)


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


# -- Incremental merge thresholds ------------------------------------------

INCREMENTAL_MAX_CHANGED = 20
"""Hard cap on changed files for incremental merge."""

INCREMENTAL_MIN_CHANGED = 5
"""Minimum threshold floor — always allow at least this many changes."""

INCREMENTAL_CHANGE_RATIO = 0.15
"""Fraction of baseline analyzed file count used as adaptive threshold."""


def _should_use_incremental(changed_count: int, baseline_analyzed_count: int) -> bool:
    """Return ``True`` when incremental merge is safe given change volume.

    The threshold is the smaller of:
    * ``INCREMENTAL_MAX_CHANGED`` (hard cap)
    * ``max(INCREMENTAL_MIN_CHANGED, ceil(INCREMENTAL_CHANGE_RATIO * max(1, baseline_analyzed_count)))``

    Args:
        changed_count: Total added + modified + deleted files detected.
        baseline_analyzed_count: Number of files in the baseline analyzed set.

    Returns:
        True if the change count is within the threshold.
    """
    from math import ceil

    threshold = min(
        INCREMENTAL_MAX_CHANGED,
        max(
            INCREMENTAL_MIN_CHANGED,
            ceil(INCREMENTAL_CHANGE_RATIO * max(1, baseline_analyzed_count)),
        ),
    )
    return changed_count <= threshold


class AnalyzeRepoCache:
    """Project-local, file-backed cache for ``analyze_repo`` results.

    Cache entries are individual JSON files stored under
    ``<git_root>/.agentcrew/analyze_repo_cache/``.

    The cache uses a unified ``files`` map as both the content manifest
    and the stored analysis/error records:

    * ``files[path] = {"hash": "...", "analysis": "..."}`` — analyzed file
      with compact per-file formatted result.
    * ``files[path] = {"hash": "...", "error": "..."}`` — file that produced
      a parse error.
    * ``files[path] = {"hash": "..."}`` — supported but not analyzed file
      (hash-only; over the selection limit).

    Change detection compares the current content hashes against the
    stored ``files`` hashes.  All paths are relative to the *requested
    analysis path* (not the Git root).

    **Thread safety:** individual read/write operations use atomic temp-file
    replaces; concurrent writers to different keys are safe, but concurrent
    LRU eviction may temporarily exceed the limit.
    """

    # -- Public API ---------------------------------------------------------

    def get(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        deep_analysis: bool = True,
        language_map: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Look up a cached result with per-file manifest change detection.

        Returns the full entry dict on hit (no changes), or with
        ``_cache_info`` metadata for incremental merge.  Returns *None* on
        miss / too many changes / legacy schema / corruption.
        """
        cache_dir = self._resolve_cache_dir(path)
        if cache_dir is None:
            return None

        norm_path = normalize_path(path)
        norm_exclusions = normalize_exclude_patterns(exclude_patterns)
        key = _build_cache_key(norm_path, norm_exclusions, feature_scope, deep_analysis)

        entry = self._read_entry(cache_dir, key)
        if entry is None:
            return None

        # Schema version check
        stored_version = entry.get("schema_version")
        if stored_version != SCHEMA_VERSION:
            logger.info(
                f"Cache entry {key} schema_version={stored_version} "
                f"!= current {SCHEMA_VERSION}; triggering full rebuild"
            )
            self._delete_entry(cache_dir, key)
            return None

        # Must have a files map (schema v2+)
        stored_files = entry.get("files")
        if stored_files is None or not isinstance(stored_files, dict):
            logger.info(f"Cache entry {key} has no files map; triggering full rebuild")
            self._delete_entry(cache_dir, key)
            return None

        # Compute current manifest and detect changes
        git_root = _find_git_root(norm_path)
        if git_root is None or language_map is None:
            return None

        current_manifest = _compute_file_manifest(path, norm_exclusions, language_map)
        if current_manifest is None:
            return None

        # Use stored files as the previous manifest (extract hash values)
        stored_manifest = {
            p: v["hash"] if isinstance(v, dict) and "hash" in v else str(v)
            for p, v in stored_files.items()
        }
        changes = _detect_manifest_changes(stored_manifest, current_manifest)
        changed_count = (
            len(changes["added"]) + len(changes["modified"]) + len(changes["deleted"])
        )

        # Derive baseline analyzed count from files records
        baseline_count = sum(
            1 for v in stored_files.values() if "analysis" in v or "error" in v
        )

        if changed_count == 0:
            # Direct hit — no changes, touch LRU
            entry["last_accessed_at"] = _utc_now_iso()
            self._write_entry(cache_dir, key, entry)
            return entry

        if _should_use_incremental(changed_count, baseline_count):
            # Small change set — return entry with merge info
            entry["last_accessed_at"] = _utc_now_iso()
            result_copy = dict(entry)
            result_copy["_cache_info"] = {
                "action": "incremental_merge",
                "changes": changes,
                "changed_count": changed_count,
                "baseline_analyzed_count": baseline_count,
                "current_manifest": current_manifest,
            }
            self._write_entry(cache_dir, key, entry)
            return result_copy

        # Too many changes — invalidate and trigger full rebuild
        logger.info(
            f"Cache entry {key}: {changed_count} changes exceeds threshold "
            f"for baseline {baseline_count}, triggering full rebuild"
        )
        self._delete_entry(cache_dir, key)
        return None

    def set(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        deep_analysis: bool = True,
        files: dict[str, dict] | None = None,
        project_notes: str | None = None,
        language_map: dict[str, str] | None = None,
    ) -> None:
        """Persist a new cache entry with a unified ``files`` map.

        The ``files`` dict maps relative paths to records:

        * ``{"hash": "...", "analysis": "..."}`` — analyzed file.
        * ``{"hash": "...", "error": "..."}`` — parse error.
        * ``{"hash": "..."}`` — hash-only (supported but not analyzed).

        Args:
            path: Normalized analysis path.
            exclude_patterns: Normalized exclusion patterns.
            feature_scope: Optional feature scope string.
            deep_analysis: Whether deep analysis was performed.
            files: Unified per-file map.  Each entry must have a ``hash``.
            project_notes: Optional extracted project notes.
            language_map: Extension-to-language map for manifest computation.
        """
        cache_dir = self._resolve_cache_dir(path)
        if cache_dir is None:
            return

        norm_path = normalize_path(path)
        norm_exclusions = normalize_exclude_patterns(exclude_patterns)
        key = _build_cache_key(norm_path, norm_exclusions, feature_scope, deep_analysis)

        now = _utc_now_iso()
        entry: dict[str, Any] = {
            "cache_key": key,
            "created_at": now,
            "last_accessed_at": now,
            "schema_version": SCHEMA_VERSION,
            "args": {
                "path": norm_path,
                "exclude_patterns": norm_exclusions,
                "feature_scope": feature_scope,
                "deep_analysis": deep_analysis,
            },
            "files": files or {},
            "project_notes": project_notes,
        }

        self._write_entry(cache_dir, key, entry)
        self._evict_lru(cache_dir)

    def list_valid_entries(self, cwd: str) -> list[dict[str, Any]]:
        """Return metadata for all readable cache entries in the project at *cwd*.

        Returns at most :data:`MAX_CACHE_ENTRIES` entries, sorted by
        ``last_accessed_at`` descending (newest/LRU-first).  Each entry
        contains the exact arguments (``path``, ``exclude_patterns``,
        ``feature_scope``, ``deep_analysis``) and the opaque ``cache_key``.

        Corrupt or schema-invalid entries (missing args, missing files map,
        non-dict structure) are silently skipped.

        Performs no LLM or analysis work.
        """
        cache_dir = self._resolve_cache_dir(cwd)
        if cache_dir is None or not cache_dir.exists():
            return []

        candidates: list[tuple[str, dict[str, Any]]] = []
        for entry_path in sorted(cache_dir.rglob("*.json")):
            try:
                with entry_path.open("r", encoding="utf-8") as f:
                    entry = json.load(f)
                if not isinstance(entry, dict):
                    continue
                args = entry.get("args")
                if not isinstance(args, dict):
                    continue
                # Schema v2+: must have a validated files map
                if not AnalyzeRepoCache._validate_entry_files(entry.get("files")):
                    continue
                last_at = entry.get("last_accessed_at", "")
                candidates.append((last_at, entry))
            except (json.JSONDecodeError, KeyError, OSError, ValueError, TypeError):
                logger.debug("Skipping corrupted cache entry: %s", entry_path)
                continue

        def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, str]:
            ts_str, e = item
            try:
                dt = datetime.fromisoformat(ts_str)
                return (-dt.timestamp(), e.get("cache_key", ""))
            except (ValueError, TypeError):
                return (0.0, e.get("cache_key", ""))

        candidates.sort(key=_sort_key)

        results: list[dict[str, Any]] = []
        for _, entry in candidates[:MAX_CACHE_ENTRIES]:
            args = entry.get("args", {})
            results.append(
                {
                    "path": args.get("path", "?"),
                    "exclude_patterns": args.get("exclude_patterns", []),
                    "feature_scope": args.get("feature_scope"),
                    "deep_analysis": args.get("deep_analysis", True),
                    "cache_key": entry.get("cache_key", ""),
                }
            )

        return results

    # -- Internal helpers ---------------------------------------------------

    def _resolve_cache_dir(self, path: str) -> Path | None:
        """Determine the cache directory for the git project containing *path*."""
        norm = normalize_path(path)
        git_root = _find_git_root(norm)
        if git_root is None:
            return None
        return Path(git_root) / ".agentcrew" / CACHE_DIR_NAME

    @staticmethod
    def _validate_entry_files(files: Any) -> bool:
        """Validate all per-file records in the ``files`` map.

        Each record must be a dict with a non-empty string ``hash``.
        Optional ``analysis`` or ``error`` fields, if present, must be
        strings.  Corrupt records cause the entire entry to be treated as
        a cache miss rather than raising at lookup time.
        """
        if not isinstance(files, dict):
            return False
        for path_key, record in files.items():
            if not isinstance(path_key, str):
                return False
            if not isinstance(record, dict):
                return False
            h = record.get("hash")
            if not isinstance(h, str) or not h:
                return False
            analysis = record.get("analysis")
            if analysis is not None and not isinstance(analysis, str):
                return False
            error = record.get("error")
            if error is not None and not isinstance(error, str):
                return False
            # Must have at least hash; analysis and error are optional
        return True

    @staticmethod
    def _entry_path(cache_dir: Path, key: str) -> Path:
        key_hash = key.split(":", 1)[-1]
        return cache_dir / key_hash[:2] / f"{key_hash}.json"

    def _read_entry(self, cache_dir: Path, key: str) -> dict[str, Any] | None:
        path = self._entry_path(cache_dir, key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                entry = json.load(f)
            if not isinstance(entry, dict) or entry.get("cache_key") != key:
                logger.warning(f"Corrupt cache entry at {path}, ignoring")
                return None
            if not isinstance(entry.get("args"), dict):
                logger.warning(
                    f"Cache entry {path} has missing or invalid args, ignoring"
                )
                return None
            # Schema v2+: must have a validated files map
            files = entry.get("files")
            if not self._validate_entry_files(files):
                logger.warning(
                    f"Cache entry {path} has corrupt per-file records, ignoring"
                )
                return None
            return entry
        except Exception as exc:
            logger.warning(f"Failed to read cache entry {path}: {exc}")
            return None

    @staticmethod
    def _write_entry(cache_dir: Path, key: str, entry: dict[str, Any]) -> None:
        path = AnalyzeRepoCache._entry_path(cache_dir, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp_path.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to write cache entry {path}: {exc}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _delete_entry(cache_dir: Path, key: str) -> None:
        path = AnalyzeRepoCache._entry_path(cache_dir, key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Failed to delete cache entry {path}: {exc}")

    def _evict_lru(self, cache_dir: Path) -> None:
        """Remove oldest entries when count exceeds :data:`MAX_CACHE_ENTRIES`."""
        if not cache_dir.exists():
            return
        entries: list[tuple[float, Path]] = []
        for p in cache_dir.rglob("*.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                last_at = data.get("last_accessed_at", "")
                dt = (
                    datetime.fromisoformat(last_at)
                    if last_at
                    else datetime.min.replace(tzinfo=UTC)
                )
                ts = dt.timestamp() if dt.tzinfo else dt.replace(tzinfo=UTC).timestamp()
                entries.append((ts, p))
            except Exception:
                entries.append((0.0, p))

        entries.sort(key=lambda x: x[0], reverse=True)

        for _, path in entries[MAX_CACHE_ENTRIES:]:
            try:
                path.unlink(missing_ok=True)
                logger.debug(f"Evicted LRU cache entry: {path}")
            except OSError as exc:
                logger.warning(f"Failed to evict cache entry {path}: {exc}")
