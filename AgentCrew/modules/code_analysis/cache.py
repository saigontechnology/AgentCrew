"""Cache for ``analyze_repo`` tool results.

Stores cached analysis results under ``<git_root>/.agentcrew/analyze_repo_cache/``.
Uses SHA-256 cache keys based on normalized arguments, LRU eviction (max 5
entries per project), and Git-based invalidation via HEAD + dirty-worktree
content fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


CACHE_DIR_NAME = "analyze_repo_cache"
"""Subdirectory inside ``.agentcrew/`` that holds cache entries."""

MAX_CACHE_ENTRIES = 5
"""Maximum number of cache entries kept per project."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        )
        return result.returncode, result.stdout
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(f"git {' '.join(args)} failed") from exc


# ---------------------------------------------------------------------------
# Git-based invalidation
# ---------------------------------------------------------------------------


def compute_git_fingerprint(git_root: str) -> tuple[str, str] | None:
    """Return ``(HEAD_sha, fingerprint)`` or *None* if git commands fail.

    The fingerprint captures every repository state change relevant to cache
    invalidation using Git plumbing:

    * ``git rev-parse HEAD`` — current commit.
    * ``git diff-index --binary --full-index --patch HEAD`` — staged changes
      (index vs HEAD), including binary content.
    * ``git diff-files --binary --full-index --patch`` — unstaged changes
      (worktree vs index), including binary content.
    * ``git ls-files --others --exclude-standard`` + ``git hash-object``
      per file — untracked file existence and content.

    **Fail-closed**: any required Git command returning nonzero causes
    ``None`` to be returned so that cache reads miss and cache writes do
    not produce reusable entries during degraded repository states.
    The sole exception is ``rev-parse HEAD`` returning nonzero, which is
    treated as an orphan branch (no commits yet).
    """
    try:
        # --- HEAD ---
        rc, head_out = _git_capture(git_root, "rev-parse", "HEAD")
        if rc != 0:
            head = ""  # orphan branch or no commits yet
        else:
            head = head_out.strip()

        parts: list[str] = [f"HEAD:{head or '(empty)'}"]

        # --- Staged changes (index vs HEAD) ---
        if head:
            rc, staged_out = _git_capture(
                git_root, "diff-index", "--binary", "--full-index", "--patch", "HEAD"
            )
            if rc != 0:
                logger.debug(
                    f"git diff-index --binary --full-index --patch HEAD "
                    f"failed (rc={rc}) for {git_root}"
                )
                return None
            parts.append(f"staged:{_sha256_hex(staged_out)}")
        else:
            # No HEAD yet — capture the entire index as a proxy for staged
            rc, index_out = _git_capture(git_root, "ls-files", "-s")
            if rc != 0:
                logger.debug(f"git ls-files -s failed (rc={rc}) for {git_root}")
                return None
            parts.append(f"staged:{_sha256_hex(index_out)}")

        # --- Unstaged changes (worktree vs index) ---
        rc, unstaged_out = _git_capture(
            git_root, "diff-files", "--binary", "--full-index", "--patch"
        )
        if rc != 0:
            logger.debug(
                f"git diff-files --binary --full-index --patch "
                f"failed (rc={rc}) for {git_root}"
            )
            return None
        parts.append(f"unstaged:{_sha256_hex(unstaged_out)}")

        # --- Untracked files ---
        rc, ut_out = _git_capture(
            git_root, "ls-files", "--others", "--exclude-standard"
        )
        if rc != 0:
            logger.debug(
                f"git ls-files --others --exclude-standard "
                f"failed (rc={rc}) for {git_root}"
            )
            return None
        if ut_out.strip():
            ut_list = sorted(f for f in ut_out.splitlines() if f.strip())
            ut_parts: list[str] = []
            for f in ut_list:
                rc2, f_hash = _git_capture(git_root, "hash-object", f)
                if rc2 == 0:
                    ut_parts.append(f"{f}:{f_hash.strip()}")
                else:
                    # Per-file hash-object failure is non-fatal (file may
                    # have been deleted between ls-files and hash-object)
                    ut_parts.append(f"{f}:?")
            parts.append(f"untracked:{_sha256_hex('\n'.join(ut_parts))}")
        else:
            parts.append("untracked:none")

        fingerprint = _sha256_hex("\n".join(parts))
        return head or "(empty)", fingerprint

    except Exception as exc:
        logger.warning(f"Failed to compute git fingerprint: {exc}")
        return None


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


class AnalyzeRepoCache:
    """Project-local, file-backed cache for ``analyze_repo`` results.

    Cache entries are individual JSON files stored under
    ``<git_root>/.agentcrew/analyze_repo_cache/``.

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
    ) -> dict[str, Any] | None:
        """Look up a cached result.

        Returns the full entry dict (with ``"result"`` sub-dict containing
        ``analysis_text`` and optionally ``project_notes``) on hit, or *None*
        on miss / invalidation / corruption.
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

        # Git-based invalidation
        if not self._is_entry_valid(entry, norm_path):
            self._delete_entry(cache_dir, key)
            return None

        # Touch last_accessed_at for LRU
        entry["last_accessed_at"] = _utc_now_iso()
        self._write_entry(cache_dir, key, entry)
        return entry

    def set(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        deep_analysis: bool = True,
        analysis_text: str = "",
        project_notes: str | None = None,
    ) -> None:
        """Persist a new cache entry and enforce LRU eviction."""
        cache_dir = self._resolve_cache_dir(path)
        if cache_dir is None:
            return

        norm_path = normalize_path(path)
        norm_exclusions = normalize_exclude_patterns(exclude_patterns)
        key = _build_cache_key(norm_path, norm_exclusions, feature_scope, deep_analysis)

        git_root = _find_git_root(norm_path)
        git_head = ""
        git_fingerprint = ""
        if git_root:
            info = compute_git_fingerprint(git_root)
            if info:
                git_head, git_fingerprint = info

        now = _utc_now_iso()
        entry: dict[str, Any] = {
            "cache_key": key,
            "created_at": now,
            "last_accessed_at": now,
            "git_head": git_head,
            "git_fingerprint": git_fingerprint,
            "args": {
                "path": norm_path,
                "exclude_patterns": norm_exclusions,
                "feature_scope": feature_scope,
                "deep_analysis": deep_analysis,
            },
            "result": {
                "analysis_text": analysis_text,
                "project_notes": project_notes,
            },
        }

        self._write_entry(cache_dir, key, entry)
        self._evict_lru(cache_dir)

    def list_valid_entries(self, cwd: str) -> list[dict[str, Any]]:
        """Return metadata for currently valid cache entries in the project at *cwd*.

        Returns at most :data:`MAX_CACHE_ENTRIES` entries, each containing the
        exact arguments (``path``, ``exclude_patterns``, ``feature_scope``,
        ``deep_analysis``) and the opaque ``cache_key``.

        Performs no LLM or analysis work.  Invalid/stale entries are silently
        skipped.
        """
        cache_dir = self._resolve_cache_dir(cwd)
        if cache_dir is None or not cache_dir.exists():
            return []

        git_root = _find_git_root(normalize_path(cwd))
        if git_root is None:
            return []
        git_info = compute_git_fingerprint(git_root)
        if git_info is None:
            return []
        head, fingerprint = git_info

        results: list[dict[str, Any]] = []
        for entry_path in sorted(cache_dir.rglob("*.json")):
            if len(results) >= MAX_CACHE_ENTRIES:
                break
            try:
                with entry_path.open("r", encoding="utf-8") as f:
                    entry = json.load(f)
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("git_head") != head
                    or entry.get("git_fingerprint") != fingerprint
                ):
                    continue
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
            except Exception as exc:
                logger.debug(f"Skipping invalid cache entry {entry_path}: {exc}")
                continue

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
            # Schema validation: must have args dict and result with analysis_text str
            if not isinstance(entry.get("args"), dict):
                logger.warning(
                    f"Cache entry {path} has missing or invalid args, ignoring"
                )
                return None
            result = entry.get("result")
            if not isinstance(result, dict) or not isinstance(
                result.get("analysis_text"), str
            ):
                logger.warning(
                    f"Cache entry {path} has missing or invalid result, ignoring"
                )
                return None
            return entry
        except Exception as exc:
            logger.warning(f"Failed to read cache entry {path}: {exc}")
            return None

    def _is_entry_valid(self, entry: dict[str, Any], norm_path: str) -> bool:
        """Check Git HEAD and worktree fingerprint match the entry."""
        git_root = _find_git_root(norm_path)
        if git_root is None:
            return False
        info = compute_git_fingerprint(git_root)
        if info is None:
            return False
        head, fingerprint = info
        return (
            entry.get("git_head") == head
            and entry.get("git_fingerprint") == fingerprint
        )

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
                    else datetime.min.replace(tzinfo=timezone.utc)
                )
                ts = (
                    dt.timestamp()
                    if dt.tzinfo
                    else dt.replace(tzinfo=timezone.utc).timestamp()
                )
                entries.append((ts, p))
            except Exception:
                entries.append((0.0, p))

        entries.sort(key=lambda x: x[0], reverse=True)  # newest first

        for _, path in entries[MAX_CACHE_ENTRIES:]:
            try:
                path.unlink(missing_ok=True)
                logger.debug(f"Evicted LRU cache entry: {path}")
            except OSError as exc:
                logger.warning(f"Failed to evict cache entry {path}: {exc}")
