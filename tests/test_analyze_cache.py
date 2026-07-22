"""Tests for ``AgentCrew.modules.code_analysis.cache``.

These test at the ``AnalyzeRepoCache`` unit level and at the
``CodeAnalysisService`` integration level.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from AgentCrew.modules.code_analysis.cache import (
    AnalyzeRepoCache,
    MAX_CACHE_ENTRIES,
    _build_cache_key,
    _find_git_root,
    compute_git_fingerprint,
    normalize_exclude_patterns,
    normalize_path,
)


# ============================================================================
# Helpers
# ============================================================================


FAKE_HEAD = "a" * 40


def _make_clean_runner(monkeypatch: pytest.MonkeyPatch, git_root: str) -> None:
    """Install a ``subprocess.run`` mock that returns clean-repo responses for
    the plumbing-based ``compute_git_fingerprint``."""

    def fake_run(args, **kwargs):
        cmd = " ".join(args)
        if "rev-parse --show-toplevel" in cmd:
            return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
        if "rev-parse HEAD" in cmd:
            return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
        if "diff-index --binary --full-index --patch HEAD" in cmd:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "diff-files --binary --full-index --patch" in cmd:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "ls-files --others --exclude-standard" in cmd:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "hash-object" in cmd:
            return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
        if "ls-files -s" in cmd:
            return subprocess.CompletedProcess(args, 0, "", "")
        # Fallback for rev-parse (no --show-toplevel or HEAD)
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
        raise RuntimeError(f"Unexpected subprocess call: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)


def _patch_realpath(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``realpath`` a no-op so test paths stay as-is."""

    def fake_realpath(p: str) -> str:
        return p

    monkeypatch.setattr(os.path, "realpath", fake_realpath)


# ============================================================================
# normalize_exclude_patterns
# ============================================================================


class TestNormalizeExcludePatterns:
    def test_none_returns_empty(self) -> None:
        assert normalize_exclude_patterns(None) == []

    def test_empty_list_returns_empty(self) -> None:
        assert normalize_exclude_patterns([]) == []

    def test_deduplicates(self) -> None:
        assert normalize_exclude_patterns(["*.py", "*.js", "*.py"]) == [
            "*.js",
            "*.py",
        ]

    def test_sorts(self) -> None:
        assert normalize_exclude_patterns(["*.z", "*.a", "*.m"]) == [
            "*.a",
            "*.m",
            "*.z",
        ]


# ============================================================================
# normalize_path
# ============================================================================


class TestNormalizePath:
    def test_expands_user_and_realpath(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os.path, "realpath", lambda p: p.replace("~", "/home/user"))
        monkeypatch.setattr(
            os.path, "expanduser", lambda p: p.replace("~", "/home/user")
        )
        result = normalize_path("~/project")
        assert result == "/home/user/project"


# ============================================================================
# _build_cache_key
# ============================================================================


class TestBuildCacheKey:
    def test_deterministic(self) -> None:
        k1 = _build_cache_key("/a", ["*.py"], "auth", True)
        k2 = _build_cache_key("/a", ["*.py"], "auth", True)
        assert k1 == k2

    def test_different_path_differs(self) -> None:
        k1 = _build_cache_key("/a", ["*.py"], "auth", True)
        k2 = _build_cache_key("/b", ["*.py"], "auth", True)
        assert k1 != k2

    def test_different_exclusions_differs(self) -> None:
        k1 = _build_cache_key("/a", ["*.py"], "auth", True)
        k2 = _build_cache_key("/a", ["*.js"], "auth", True)
        assert k1 != k2

    def test_different_scope_differs(self) -> None:
        k1 = _build_cache_key("/a", [], "auth", True)
        k2 = _build_cache_key("/a", [], "db", True)
        assert k1 != k2

    def test_different_deep_differs(self) -> None:
        k1 = _build_cache_key("/a", [], None, True)
        k2 = _build_cache_key("/a", [], None, False)
        assert k1 != k2

    def test_exclusion_order_same_after_normalization(self) -> None:
        from AgentCrew.modules.code_analysis.cache import normalize_exclude_patterns

        k1 = _build_cache_key(
            "/a", normalize_exclude_patterns(["*.z", "*.a"]), None, True
        )
        k2 = _build_cache_key(
            "/a", normalize_exclude_patterns(["*.a", "*.z"]), None, True
        )
        assert k1 == k2

    def test_scope_none_vs_missing(self) -> None:
        k1 = _build_cache_key("/a", [], None, True)
        k2 = _build_cache_key("/a", [], None, True)
        assert k1 == k2


# ============================================================================
# _find_git_root
# ============================================================================


class TestFindGitRoot:
    def test_returns_git_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "/home/user/project\n", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _find_git_root("/some/path") == "/home/user/project"

    def test_returns_none_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repo")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _find_git_root("/some/path") is None


# ============================================================================
# compute_git_fingerprint  —  plumbing-based fingerprint
# ============================================================================


class TestComputeGitFingerprint:
    def test_clean_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Clean repo: no staged, unstaged, or untracked changes."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = compute_git_fingerprint("/repo")
        assert result is not None
        head, fp = result
        assert head == FAKE_HEAD
        # fp is a SHA-256 hex digest (64 hex chars)
        assert len(fp) == 64
        import re

        assert re.fullmatch(r"[0-9a-f]{64}", fp), f"Expected 64 hex chars, got {fp!r}"

        # Deterministic: same state produces same fingerprint
        result2 = compute_git_fingerprint("/repo")
        assert result2 is not None
        assert result2[1] == fp

    def test_dirty_file_changes_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editing an already-dirty file changes the fingerprint."""

        diff_patch_content = (
            "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-original\n+modified\n"
        )

        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, diff_patch_content, "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        fp1 = compute_git_fingerprint("/repo")
        assert fp1 is not None

        # Change the diff output (editing the already-dirty file)
        diff_patch_content = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-original\n+modified again\n"

        call_count[0] = 0
        fp2 = compute_git_fingerprint("/repo")
        assert fp2 is not None

        assert fp1 != fp2, "Editing an already-dirty file must change the fingerprint"

    def test_tracked_deletion_invalidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting a tracked file (worktree deletion) changes fingerprint."""

        def fake_run_clean(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_clean)
        fp_clean = compute_git_fingerprint("/repo")
        assert fp_clean is not None

        # Now simulate a tracked file deletion (diff-files shows the deletion)
        def fake_run_dirty(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--- a/src/main.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-content\n",
                    "",
                )
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_dirty)
        fp_dirty = compute_git_fingerprint("/repo")
        assert fp_dirty is not None

        assert fp_clean != fp_dirty, "Tracked file deletion must change the fingerprint"

    def test_staged_addition_invalidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Staging a new file (index vs HEAD change) changes fingerprint."""

        def fake_run_clean(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_clean)
        fp_clean = compute_git_fingerprint("/repo")
        assert fp_clean is not None

        # Now simulate a staged addition (diff-index has output)
        def fake_run_staged(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+new file content\n",
                    "",
                )
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_staged)
        fp_staged = compute_git_fingerprint("/repo")
        assert fp_staged is not None

        assert fp_clean != fp_staged, "Staged addition must change the fingerprint"

    def test_staged_deletion_invalidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Staging a deletion (git rm) changes fingerprint."""

        def fake_run_clean(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_clean)
        fp_clean = compute_git_fingerprint("/repo")
        assert fp_clean is not None

        # Staged deletion: diff-index shows file removal
        def fake_run_deleted(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--- a/src/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old content\n",
                    "",
                )
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_deleted)
        fp_deleted = compute_git_fingerprint("/repo")
        assert fp_deleted is not None

        assert fp_clean != fp_deleted, "Staged deletion must change the fingerprint"

    def test_rename_invalidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Renaming a tracked file changes both staged and unstaged diffs."""

        def fake_run_clean(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_clean)
        fp_clean = compute_git_fingerprint("/repo")
        assert fp_clean is not None

        # Rename appears as staged diff (delete old, add new)
        def fake_run_rename(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--- a/src/old_name.py\n+++ b/src/new_name.py\n@@ -1 +1 @@\n-same content\n+same content\n",
                    "",
                )
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_rename)
        fp_rename = compute_git_fingerprint("/repo")
        assert fp_rename is not None

        assert fp_clean != fp_rename, "Renaming a file must change the fingerprint"

    # --- Fail-closed: nonzero git commands return None --------------------

    def test_diff_index_failure_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nonzero from diff-index makes fingerprint return None."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal error")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert compute_git_fingerprint("/repo") is None

    def test_diff_files_failure_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nonzero from diff-files makes fingerprint return None."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal error")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert compute_git_fingerprint("/repo") is None

    def test_ls_files_others_failure_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nonzero from ls-files --others makes fingerprint return None."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal error")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert compute_git_fingerprint("/repo") is None

    def test_orphan_ls_files_s_failure_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Orphan branch: nonzero from ls-files -s makes fingerprint return None."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal: bad revision")
            if "ls-files -s" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal error")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert compute_git_fingerprint("/repo") is None

    # --- Binary content sensitivity --------------------------------------

    def test_binary_modified_file_changes_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two different modifications to an already-dirty binary produce
        different fingerprints via ``--binary --full-index`` index hashes."""

        diff_patch_1 = (
            "diff --git a/data.bin b/data.bin\n"
            "index 0000000..1111111 100644\n"
            "Binary files a/data.bin and b/data.bin differ\n"
        )
        diff_patch_2 = (
            "diff --git a/data.bin b/data.bin\n"
            "index 0000000..2222222 100644\n"
            "Binary files a/data.bin and b/data.bin differ\n"
        )

        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, diff_patch_1, "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        fp1 = compute_git_fingerprint("/repo")
        assert fp1 is not None

        # Change the diff output to simulate different binary content
        call_count[0] = 0

        def fake_run_2(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, diff_patch_2, "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run_2)
        fp2 = compute_git_fingerprint("/repo")
        assert fp2 is not None

        assert fp1 != fp2, (
            "Different binary content must produce different fingerprints"
        )


# ============================================================================
# AnalyzeRepoCache — unit tests
# ============================================================================


class TestAnalyzeRepoCache:
    """Tests using a temporary directory as the fake git root."""

    @pytest.fixture
    def git_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "project"
        root.mkdir()
        return root

    @pytest.fixture
    def cache(
        self, monkeypatch: pytest.MonkeyPatch, git_root: Path
    ) -> AnalyzeRepoCache:
        """Return a cache instance with patched subprocess pointing at *git_root*."""
        _patch_realpath(monkeypatch)
        _make_clean_runner(monkeypatch, str(git_root))
        return AnalyzeRepoCache()

    def _assert_cache_count(self, cache_dir: Path, expected: int) -> None:
        actual = list(cache_dir.rglob("*.json"))
        assert len(actual) == expected, (
            f"Expected {expected} entries, got {len(actual)}"
        )

    # --- Hit / Miss -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, cache: AnalyzeRepoCache) -> None:
        assert cache.get("/unknown/path") is None

    @pytest.mark.asyncio
    async def test_set_then_get_hit(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            analysis_text="struct analysis",
            project_notes="some notes",
        )
        entry = cache.get(str(git_root))
        assert entry is not None
        result = entry["result"]
        assert result["analysis_text"] == "struct analysis"
        assert result["project_notes"] == "some notes"

    @pytest.mark.asyncio
    async def test_set_then_get_miss_different_path(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), analysis_text="text")
        other = git_root.parent / "other"
        assert cache.get(str(other)) is None

    @pytest.mark.asyncio
    async def test_miss_different_feature_scope(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), feature_scope="auth", analysis_text="text")
        assert cache.get(str(git_root), feature_scope="db") is None

    @pytest.mark.asyncio
    async def test_miss_different_deep_analysis(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), deep_analysis=True, analysis_text="text")
        assert cache.get(str(git_root), deep_analysis=False) is None

    @pytest.mark.asyncio
    async def test_miss_different_exclusions(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), exclude_patterns=["*.py"], analysis_text="text")
        assert cache.get(str(git_root), exclude_patterns=["*.js"]) is None

    # --- Exclusion normalization ------------------------------------------

    @pytest.mark.asyncio
    async def test_exclusion_order_produces_hit(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.z", "*.a"],
            analysis_text="text",
        )
        entry = cache.get(
            str(git_root),
            exclude_patterns=["*.a", "*.z"],
        )
        assert entry is not None

    @pytest.mark.asyncio
    async def test_exclusion_dedup_produces_hit(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.py", "*.py"],
            analysis_text="text",
        )
        entry = cache.get(str(git_root), exclude_patterns=["*.py"])
        assert entry is not None

    # --- deep_analysis=False ----------------------------------------------

    @pytest.mark.asyncio
    async def test_deep_analysis_false_caches_no_notes(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            deep_analysis=False,
            analysis_text="text only",
        )
        entry = cache.get(str(git_root), deep_analysis=False)
        assert entry is not None
        assert entry["result"]["analysis_text"] == "text only"
        assert entry["result"]["project_notes"] is None

    # --- LRU eviction -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache: AnalyzeRepoCache, git_root: Path) -> None:
        """Sixth write evicts the oldest entry, leaving five."""
        for i in range(MAX_CACHE_ENTRIES + 1):
            cache.set(
                path=str(git_root),
                feature_scope=f"scope{i}",
                analysis_text=f"text{i}",
            )

        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        self._assert_cache_count(cache_dir, MAX_CACHE_ENTRIES)

        assert cache.get(str(git_root), feature_scope="scope0") is None
        for i in range(1, MAX_CACHE_ENTRIES + 1):
            assert cache.get(str(git_root), feature_scope=f"scope{i}") is not None

    @pytest.mark.asyncio
    async def test_lru_evicts_oldest(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Accessing an entry protects it from LRU eviction."""
        for i in range(MAX_CACHE_ENTRIES):
            cache.set(
                path=str(git_root),
                feature_scope=f"scope{i}",
                analysis_text=f"text{i}",
            )

        cache.get(str(git_root), feature_scope="scope0")

        cache.set(
            path=str(git_root),
            feature_scope="scope_new",
            analysis_text="new",
        )

        assert cache.get(str(git_root), feature_scope="scope1") is None
        assert cache.get(str(git_root), feature_scope="scope0") is not None

    # --- Git invalidation -------------------------------------------------

    @pytest.mark.asyncio
    async def test_git_head_change_invalidates(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache.set(path=str(git_root), analysis_text="text")
        assert cache.get(str(git_root)) is not None

        different_head = "b" * 40

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{different_head}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_dirty_worktree_invalidates(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache.set(path=str(git_root), analysis_text="text")

        diff_content = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n"

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, diff_content, "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_repeated_failure_no_cache_hit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_root: Path,
    ) -> None:
        """Repeated Git command failure prevents both cache writes and
        cache hits."""
        # First use a clean runner to populate the cache
        _make_clean_runner(monkeypatch, str(git_root))
        c = AnalyzeRepoCache()
        c.set(path=str(git_root), analysis_text="original")
        assert c.get(str(git_root)) is not None

        # Now make diff-files fail persistently
        def failing_runner(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", failing_runner)

        # Existing entry can't be validated during get
        assert c.get(str(git_root)) is None, (
            "Repeated Git failure must prevent cache hits"
        )

        # A new write also cannot produce a reusable entry
        c.set(path=str(git_root), analysis_text="newer")
        assert c.get(str(git_root)) is None, (
            "Repeated Git failure must prevent new writes from being usable"
        )

    # --- Corrupt entries --------------------------------------------------

    @pytest.mark.asyncio
    async def test_corrupt_entry_returns_none(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), analysis_text="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None

        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry_files[0].write_text("{not valid json}")

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_schema_missing_args_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Valid JSON + matching key but missing args dict → miss."""
        cache.set(path=str(git_root), analysis_text="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        # Read, strip args, write back
        entry = json.loads(entry_files[0].read_text())
        del entry["args"]
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_schema_missing_analysis_text_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Valid JSON + matching key but missing result.analysis_text → miss."""
        cache.set(path=str(git_root), analysis_text="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        entry = json.loads(entry_files[0].read_text())
        entry["result"] = {"project_notes": "orphan notes"}
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_schema_non_dict_result_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Valid JSON but result is a string instead of dict → miss."""
        cache.set(path=str(git_root), analysis_text="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        entry = json.loads(entry_files[0].read_text())
        entry["result"] = "just a string"
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    # --- list_valid_entries -----------------------------------------------

    @pytest.mark.asyncio
    async def test_list_valid_entries_returns_args(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.js"],
            feature_scope="auth",
            deep_analysis=True,
            analysis_text="text",
        )
        entries = cache.list_valid_entries(str(git_root))
        assert len(entries) == 1
        entry = entries[0]
        assert entry["path"] == str(git_root)
        assert entry["exclude_patterns"] == ["*.js"]
        assert entry["feature_scope"] == "auth"
        assert entry["deep_analysis"] is True
        assert "cache_key" in entry

    @pytest.mark.asyncio
    async def test_list_valid_entries_empty_when_none(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        assert cache.list_valid_entries(str(git_root)) == []

    @pytest.mark.asyncio
    async def test_list_valid_entries_skips_invalid(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache.set(path=str(git_root), analysis_text="text")

        different_head = "c" * 40

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{different_head}\n", "")
            if "diff-index --binary --full-index --patch HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files --binary --full-index --patch" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        entries = cache.list_valid_entries(str(git_root))
        assert entries == []


# ============================================================================
# CodeAnalysisService integration tests
# ============================================================================


class TestCodeAnalysisServiceCached:
    """Verify the service delegates correctly to the cache component."""

    @pytest.fixture
    def svc_and_mocks(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
        """Return (service, struct_mock, notes_mock) with a tmp_path-backed git root."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        _patch_realpath(monkeypatch)
        _make_clean_runner(monkeypatch, str(git_root))

        from AgentCrew.modules.code_analysis import CodeAnalysisService

        svc = CodeAnalysisService(llm_service=None)
        struct_mock = AsyncMock(return_value="structure output")
        notes_mock = AsyncMock(return_value="project notes")
        svc.analyze_code_structure = struct_mock  # type: ignore[method-assign]
        svc.extract_project_notes = notes_mock  # type: ignore[method-assign]
        return svc, struct_mock, notes_mock

    @pytest.mark.asyncio
    async def test_cache_hit_skips_expensive_calls(self, svc_and_mocks) -> None:
        """Same normalized call hits cache; analysis/notes called once."""
        svc, struct_mock, notes_mock = svc_and_mocks

        r1 = await svc.analyze_code_structure_cached(".", deep_analysis=True)
        assert r1["analysis_text"] == "structure output"
        assert r1["project_notes"] == "project notes"
        assert struct_mock.await_count == 1
        assert notes_mock.await_count == 1

        r2 = await svc.analyze_code_structure_cached(".", deep_analysis=True)
        assert r2["analysis_text"] == "structure output"
        assert r2["project_notes"] == "project notes"
        assert struct_mock.await_count == 1, "cache hit should skip re-analysis"
        assert notes_mock.await_count == 1, "cache hit should skip note extraction"

    @pytest.mark.asyncio
    async def test_miss_different_feature_scope(self, svc_and_mocks) -> None:
        """Different feature_scope produces independent cache entries."""
        svc, struct_mock, notes_mock = svc_and_mocks

        await svc.analyze_code_structure_cached(
            ".", feature_scope="a", deep_analysis=True
        )
        assert struct_mock.await_count == 1

        await svc.analyze_code_structure_cached(
            ".", feature_scope="b", deep_analysis=True
        )
        assert struct_mock.await_count == 2, "different scope = cache miss"

    @pytest.mark.asyncio
    async def test_deep_analysis_false_no_notes_call(self, svc_and_mocks) -> None:
        """deep_analysis=False skips note extraction entirely."""
        svc, struct_mock, notes_mock = svc_and_mocks

        r = await svc.analyze_code_structure_cached(".", deep_analysis=False)
        assert r["analysis_text"] == "structure output"
        assert r["project_notes"] is None
        assert notes_mock.await_count == 0

        # Cache hit also returns no notes
        r2 = await svc.analyze_code_structure_cached(".", deep_analysis=False)
        assert r2["project_notes"] is None
        assert struct_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_hit_preserves_notes_semantics(self, svc_and_mocks) -> None:
        """Deep-analysis cache hit preserves project_notes in output."""
        svc, struct_mock, notes_mock = svc_and_mocks

        r1 = await svc.analyze_code_structure_cached(".", deep_analysis=True)
        assert r1["project_notes"] == "project notes"

        r2 = await svc.analyze_code_structure_cached(".", deep_analysis=True)
        assert r2["project_notes"] == "project notes"

    @pytest.mark.asyncio
    async def test_cache_failure_degradation(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache failure degrades to normal analysis."""
        svc, struct_mock, notes_mock = svc_and_mocks

        def failing_get(*args, **kwargs):
            raise RuntimeError("cache failure")

        monkeypatch.setattr(svc._analyze_cache, "get", failing_get)

        r = await svc.analyze_code_structure_cached(".", deep_analysis=True)
        assert r["analysis_text"] == "structure output"
        assert r["project_notes"] == "project notes"
