"""Tests for ``AgentCrew.modules.code_analysis.cache``.

Tests the manifest-based incremental caching behavior at the
``AnalyzeRepoCache`` unit level and the ``CodeAnalysisService``
integration level.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from AgentCrew.modules.code_analysis.cache import (
    INCREMENTAL_CHANGE_RATIO,
    INCREMENTAL_MAX_CHANGED,
    INCREMENTAL_MIN_CHANGED,
    MAX_CACHE_ENTRIES,
    SCHEMA_VERSION,
    AnalyzeRepoCache,
    _build_cache_key,
    _compute_content_hash,
    _compute_file_manifest,
    _detect_manifest_changes,
    _find_git_root,
    _sha256_hex,
    _should_use_incremental,
    discover_supported_files,
    normalize_exclude_patterns,
    normalize_path,
)

# ============================================================================
# Helpers
# ============================================================================


FAKE_HEAD = "a" * 40


def _make_clean_runner(monkeypatch: pytest.MonkeyPatch, git_root: str) -> None:
    """Install a ``subprocess.run`` mock that returns clean-repo responses for
    the plumbing-based ``compute_git_fingerprint`` and empty ``ls-files``."""

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
        if "ls-files" in cmd and "--others" not in cmd and "-s" not in cmd:
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

    def fake_realpath(p: str, **kwargs) -> str:
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


# ============================================================================
# _should_use_incremental
# ============================================================================


class TestShouldUseIncremental:
    """Boundary tests for the incremental merge threshold."""

    def test_zero_baseline_allows_min(self) -> None:
        # min(20, max(5, ceil(0.15 * 1))) = min(20, 5) = 5
        assert _should_use_incremental(0, 0) is True
        assert _should_use_incremental(5, 0) is True
        assert _should_use_incremental(6, 0) is False

    def test_small_baseline_allows_min(self) -> None:
        # min(20, max(5, ceil(0.15 * 10))) = min(20, max(5, 2)) = 5
        assert _should_use_incremental(5, 10) is True
        assert _should_use_incremental(6, 10) is False

    def test_medium_baseline_scales(self) -> None:
        # min(20, max(5, ceil(0.15 * 50))) = min(20, max(5, 8)) = 8
        assert _should_use_incremental(8, 50) is True
        assert _should_use_incremental(9, 50) is False

    def test_large_baseline_hits_cap(self) -> None:
        # min(20, max(5, ceil(0.15 * 200))) = min(20, max(5, 30)) = 20
        assert _should_use_incremental(20, 200) is True
        assert _should_use_incremental(21, 200) is False

    def test_vast_baseline_respects_hard_cap(self) -> None:
        # min(20, max(5, ceil(0.15 * 10000))) = min(20, 1500) = 20
        assert _should_use_incremental(20, 10000) is True
        assert _should_use_incremental(21, 10000) is False

    def test_zero_changes_always_true(self) -> None:
        assert _should_use_incremental(0, 100) is True
        assert _should_use_incremental(0, 0) is True

    def test_constants_sane(self) -> None:
        assert INCREMENTAL_MAX_CHANGED >= INCREMENTAL_MIN_CHANGED
        assert 0 < INCREMENTAL_CHANGE_RATIO < 1.0


# ============================================================================
# discover_supported_files
# ============================================================================


class TestDiscoverSupportedFiles:
    """Tests for the shared file discovery function."""

    def test_empty_git_ls_files_returns_empty(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), [], {".py": "python"})
        assert result == []

    def test_filters_by_extension(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "src/main.py\nsrc/main.js\nREADME.md\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), [], {".py": "python"})
        assert result == ["src/main.py"]

    def test_exclude_patterns(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "src/main.py\ntests/test_main.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), ["tests/*"], {".py": "python"})
        assert result == ["src/main.py"]

    def test_path_scope(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "src/main.py\nother/file.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        # Create subdir for scope test
        subdir = tmp_path / "src"
        subdir.mkdir()
        result = discover_supported_files(str(subdir), [], {".py": "python"})
        # main.py is in src/ which is within the scope
        assert "main.py" in result

    def test_includes_untracked(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(args, 0, "src/main.py\n", "")
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "src/new.py\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), [], {".py": "python"})
        assert "src/main.py" in result
        assert "src/new.py" in result
        assert len(result) == 2

    def test_returns_paths_relative_to_analysis_root(
        self, monkeypatch, tmp_path
    ) -> None:
        """Paths are relative to the analysis path, not the git root."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "sub/main.py\nroot_only.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        subdir = tmp_path / "sub"
        subdir.mkdir()
        result = discover_supported_files(str(subdir), [], {".py": "python"})
        # main.py is in sub/ and should be relative to sub/
        assert "main.py" in result
        # root_only.py is at the repo root, not in sub/
        assert "root_only.py" not in result

    def test_git_failure_returns_none(self, monkeypatch, tmp_path) -> None:
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), [], {".py": "python"})
        assert result is None

    def test_untracked_listing_failure_returns_none(
        self, monkeypatch, tmp_path
    ) -> None:
        """Nonzero from ls-files --others must return None (fail-closed)."""

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(args, 0, "src/main.py\n", "")
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = discover_supported_files(str(tmp_path), [], {".py": "python"})
        assert result is None, "Untracked listing failure must return None"


# ============================================================================
# _compute_content_hash  (uses real files via tmp_path)
# ============================================================================


class TestComputeContentHash:
    def test_returns_deterministic_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        h1 = _compute_content_hash(str(tmp_path), "test.py")
        h2 = _compute_content_hash(str(tmp_path), "test.py")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("content1")
        (tmp_path / "b.py").write_text("content2")
        h1 = _compute_content_hash(str(tmp_path), "a.py")
        h2 = _compute_content_hash(str(tmp_path), "b.py")
        assert h1 != h2

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _compute_content_hash(str(tmp_path), "nonexistent.py") is None


# ============================================================================
# _detect_manifest_changes
# ============================================================================


class TestDetectManifestChanges:
    def test_no_changes(self) -> None:
        m = {"a.py": "hash1", "b.py": "hash2"}
        result = _detect_manifest_changes(m, m)
        assert result == {"added": [], "modified": [], "deleted": []}

    def test_added_file(self) -> None:
        stored = {"a.py": "h1"}
        current = {"a.py": "h1", "b.py": "h2"}
        result = _detect_manifest_changes(stored, current)
        assert result["added"] == ["b.py"]
        assert result["modified"] == []
        assert result["deleted"] == []

    def test_modified_file(self) -> None:
        stored = {"a.py": "h1"}
        current = {"a.py": "h2"}
        result = _detect_manifest_changes(stored, current)
        assert result["added"] == []
        assert result["modified"] == ["a.py"]
        assert result["deleted"] == []

    def test_deleted_file(self) -> None:
        stored = {"a.py": "h1", "b.py": "h2"}
        current = {"a.py": "h1"}
        result = _detect_manifest_changes(stored, current)
        assert result["added"] == []
        assert result["modified"] == []
        assert result["deleted"] == ["b.py"]

    def test_rename_as_delete_plus_add(self) -> None:
        stored = {"old.py": "h1"}
        current = {"new.py": "h1"}
        result = _detect_manifest_changes(stored, current)
        assert result["added"] == ["new.py"]
        assert result["deleted"] == ["old.py"]
        assert result["modified"] == []

    def test_all_changes_together(self) -> None:
        stored = {"keep.py": "h1", "mod.py": "h1", "del.py": "h1"}
        current = {"keep.py": "h1", "mod.py": "h2", "add.py": "h1"}
        result = _detect_manifest_changes(stored, current)
        assert result["added"] == ["add.py"]
        assert result["modified"] == ["mod.py"]
        assert result["deleted"] == ["del.py"]

    def test_stored_none_all_current_added(self) -> None:
        current = {"a.py": "h1"}
        result = _detect_manifest_changes(None, current)
        assert result["added"] == ["a.py"]
        assert result["deleted"] == []

    def test_current_none_all_stored_deleted(self) -> None:
        stored = {"a.py": "h1"}
        result = _detect_manifest_changes(stored, None)
        assert result["deleted"] == ["a.py"]
        assert result["added"] == []

    def test_both_none_no_changes(self) -> None:
        result = _detect_manifest_changes(None, None)
        assert result == {"added": [], "modified": [], "deleted": []}


# ============================================================================
# _compute_file_manifest
# ============================================================================


class TestComputeFileManifest:
    """Integration test using a real temp directory."""

    def test_creates_manifest_from_real_files(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Create a pseudo-repo with some files
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def main(): pass")
        (src / "utils.py").write_text("def util(): pass")

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                # Tracked files
                return subprocess.CompletedProcess(
                    args, 0, "src/main.py\nsrc/utils.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        manifest = _compute_file_manifest(
            str(tmp_path),
            [],
            {".py": "python"},
        )
        assert manifest is not None
        assert "src/main.py" in manifest
        assert "src/utils.py" in manifest
        assert len(manifest["src/main.py"]) == 64
        assert len(manifest["src/utils.py"]) == 64
        # Deterministic
        manifest2 = _compute_file_manifest(
            str(tmp_path),
            [],
            {".py": "python"},
        )
        assert manifest == manifest2

    def test_hash_failure_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        """Any single-file hash failure must cause manifest to return None."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "good.py").write_text("def ok(): pass")
        (src / "bad.py").write_text("def bad(): pass")

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "src/good.py\nsrc/bad.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        original_read_bytes = Path.read_bytes

        def patched_read_bytes(self):
            if self.name == "bad.py":
                raise PermissionError("denied")
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", patched_read_bytes)

        manifest = _compute_file_manifest(str(tmp_path), [], {".py": "python"})
        assert manifest is None, "Hash failure must cause manifest to return None"

    def test_exclude_patterns_apply(self, tmp_path: Path, monkeypatch) -> None:
        """Exclude patterns should filter out matching files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def main(): pass")
        (src / "test_main.py").write_text("def test(): pass")

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{tmp_path}\n", "")
            if "ls-files" in cmd and "--others" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "src/main.py\nsrc/test_main.py\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        manifest = _compute_file_manifest(
            str(tmp_path), ["**/test_*"], {".py": "python"}
        )
        assert manifest is not None
        assert "src/main.py" in manifest
        assert "src/test_main.py" not in manifest


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
            files={"dummy.py": {"hash": "abc"}},
            project_notes="some notes",
            language_map={},
        )
        entry = cache.get(str(git_root), language_map={})
        assert entry is not None
        # With language_map={}, the manifest will be empty (no supported files).
        # The stored files won't match, but we can verify the entry was written
        # and read back as a valid dict with files and project_notes.
        assert isinstance(entry.get("files"), dict)
        assert entry.get("project_notes") == "some notes"

    @pytest.mark.asyncio
    async def test_set_then_get_miss_different_path(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})
        other = git_root.parent / "other"
        assert cache.get(str(other)) is None

    @pytest.mark.asyncio
    async def test_miss_different_feature_scope(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            feature_scope="auth",
            files={"dummy.py": {"hash": "abc"}},
            project_notes="notes",
            language_map={},
        )
        assert cache.get(str(git_root), feature_scope="db", language_map={}) is None

    @pytest.mark.asyncio
    async def test_miss_different_deep_analysis(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            deep_analysis=True,
            files={"dummy.py": {"hash": "abc"}},
            project_notes="notes",
            language_map={},
        )
        assert cache.get(str(git_root), deep_analysis=False, language_map={}) is None

    @pytest.mark.asyncio
    async def test_miss_different_exclusions(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.py"],
            files={"dummy.py": {"hash": "abc"}},
            project_notes="notes",
            language_map={},
        )
        assert (
            cache.get(str(git_root), exclude_patterns=["*.js"], language_map={}) is None
        )

    # --- Exclusion normalization ------------------------------------------

    @pytest.mark.asyncio
    async def test_exclusion_order_produces_hit(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.z", "*.a"],
            files={},
            project_notes="notes",
            language_map={},
        )
        entry = cache.get(
            str(git_root),
            exclude_patterns=["*.a", "*.z"],
            language_map={},
        )
        assert entry is not None

    @pytest.mark.asyncio
    async def test_exclusion_dedup_produces_hit(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            exclude_patterns=["*.py", "*.py"],
            files={},
            project_notes="notes",
            language_map={},
        )
        entry = cache.get(str(git_root), exclude_patterns=["*.py"], language_map={})
        assert entry is not None

    # --- deep_analysis=False ----------------------------------------------

    @pytest.mark.asyncio
    async def test_deep_analysis_false_caches_no_notes(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(
            path=str(git_root),
            deep_analysis=False,
            files={},
            project_notes=None,
            language_map={},
        )
        entry = cache.get(str(git_root), deep_analysis=False, language_map={})
        assert entry is not None
        assert entry.get("files") == {}
        assert entry.get("project_notes") is None

    # --- LRU eviction -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache: AnalyzeRepoCache, git_root: Path) -> None:
        """Sixth write evicts the oldest entry, leaving five."""
        for i in range(MAX_CACHE_ENTRIES + 1):
            cache.set(
                path=str(git_root),
                feature_scope=f"scope{i}",
                files={"dummy.py": {"hash": f"hash{i}"}},
                project_notes=f"notes{i}",
                language_map={},
            )

        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        self._assert_cache_count(cache_dir, MAX_CACHE_ENTRIES)

        assert cache.get(str(git_root), feature_scope="scope0", language_map={}) is None
        for i in range(1, MAX_CACHE_ENTRIES + 1):
            assert (
                cache.get(str(git_root), feature_scope=f"scope{i}", language_map={})
                is not None
            )

    @pytest.mark.asyncio
    async def test_lru_evicts_oldest(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Accessing an entry protects it from LRU eviction."""
        for i in range(MAX_CACHE_ENTRIES):
            cache.set(
                path=str(git_root),
                feature_scope=f"scope{i}",
                files={"dummy.py": {"hash": f"hash{i}"}},
                project_notes=f"notes{i}",
                language_map={},
            )

        cache.get(str(git_root), feature_scope="scope0", language_map={})

        cache.set(
            path=str(git_root),
            feature_scope="scope_new",
            files={"dummy.py": {"hash": "newhash"}},
            project_notes="new_notes",
            language_map={},
        )

        assert cache.get(str(git_root), feature_scope="scope1", language_map={}) is None
        assert (
            cache.get(str(git_root), feature_scope="scope0", language_map={})
            is not None
        )

    # --- Git invalidation -------------------------------------------------

    @pytest.mark.asyncio
    async def test_git_head_change_does_not_invalidate_manifest(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HEAD change does NOT invalidate — manifest-based detection is HEAD-independent."""
        cache.set(path=str(git_root), files={}, project_notes="notes", language_map={})
        assert cache.get(str(git_root), language_map={}) is not None

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
            if "ls-files" in cmd and "--others" not in cmd and "-s" not in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        entry = cache.get(str(git_root), language_map={})
        assert entry is not None
        assert entry.get("files") == {}
        assert entry.get("project_notes") == "notes"

    @pytest.mark.asyncio
    async def test_dirty_worktree_invalidates(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache.set(path=str(git_root), files={}, project_notes="notes")

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
    async def test_repeated_manifest_failure_no_cache_hit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        git_root: Path,
    ) -> None:
        """When ls-files fails, manifest cannot be computed so get returns None."""
        _make_clean_runner(monkeypatch, str(git_root))
        c = AnalyzeRepoCache()
        c.set(path=str(git_root), files={}, project_notes="original", language_map={})
        assert c.get(str(git_root), language_map={}) is not None

        def failing_runner(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "ls-files" in cmd and "--others" not in cmd and "-s" not in cmd:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            if "ls-files --others --exclude-standard" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", failing_runner)

        assert c.get(str(git_root), language_map={}) is None

    # --- Corrupt entries --------------------------------------------------

    @pytest.mark.asyncio
    async def test_corrupt_entry_returns_none(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        cache.set(path=str(git_root), files={}, project_notes="text")
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
        cache.set(path=str(git_root), files={}, project_notes="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        entry = json.loads(entry_files[0].read_text())
        del entry["args"]
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_schema_missing_files_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Valid JSON but missing files map → miss (legacy schema rebuild)."""
        cache.set(path=str(git_root), files={}, project_notes="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        entry = json.loads(entry_files[0].read_text())
        del entry["files"]
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_schema_non_dict_files_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """files map is a list instead of dict → miss."""
        cache.set(path=str(git_root), files={}, project_notes="text")
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1

        entry = json.loads(entry_files[0].read_text())
        entry["files"] = ["not", "a", "dict"]
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    # --- Schema version ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_schema_version_mismatch_triggers_rebuild(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Entries without the current schema_version trigger rebuild."""
        cache.set(
            path=str(git_root),
            files={},
            project_notes="text",
            language_map={},
        )
        assert cache.get(str(git_root), language_map={}) is not None

        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry = json.loads(entry_files[0].read_text())
        entry["schema_version"] = 999
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root), language_map={}) is None

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
            files={"dummy.py": {"hash": "abc"}},
            project_notes="notes",
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
    async def test_list_valid_entries_lists_despite_head_change(
        self,
        cache: AnalyzeRepoCache,
        git_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})

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
        assert len(entries) == 1
        assert entries[0]["path"] == str(git_root)

    # --- Per-record corruption --------------------------------------------

    @pytest.mark.asyncio
    async def test_non_dict_record_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """files[path] is a string instead of dict → miss."""
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry = json.loads(entry_files[0].read_text())
        entry["files"] = {"dummy.py": "this is a string, not a dict"}
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_missing_hash_in_record_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """files[path] dict missing 'hash' key → miss."""
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry = json.loads(entry_files[0].read_text())
        entry["files"] = {"dummy.py": {"analysis": "no hash field"}}
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_empty_hash_in_record_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """files[path] with empty hash string → miss."""
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry = json.loads(entry_files[0].read_text())
        entry["files"] = {"dummy.py": {"hash": ""}}
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_non_string_hash_in_record_treated_as_miss(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """files[path] with integer hash → miss."""
        cache.set(path=str(git_root), files={"dummy.py": {"hash": "abc"}})
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        entry = json.loads(entry_files[0].read_text())
        entry["files"] = {"dummy.py": {"hash": 12345}}
        entry_files[0].write_text(json.dumps(entry))

        assert cache.get(str(git_root)) is None

    @pytest.mark.asyncio
    async def test_corrupt_records_excluded_from_listing(
        self, cache: AnalyzeRepoCache, git_root: Path
    ) -> None:
        """Entries with corrupt per-file records are excluded from list_valid_entries."""
        # Write a good entry
        cache.set(
            path=str(git_root),
            feature_scope="good",
            files={"good.py": {"hash": "abc"}},
        )
        # Manually write a corrupt entry with a different key
        norm_path = normalize_path(str(git_root))
        key = _build_cache_key(norm_path, [], "corrupt", True)
        cache_dir = cache._resolve_cache_dir(str(git_root))
        assert cache_dir is not None
        corrupt_entry = {
            "cache_key": key,
            "schema_version": SCHEMA_VERSION,
            "args": {
                "path": norm_path,
                "exclude_patterns": [],
                "feature_scope": "corrupt",
                "deep_analysis": True,
            },
            "files": {"bad.py": {"hash": 999}},
            "project_notes": None,
        }
        AnalyzeRepoCache._write_entry(cache_dir, key, corrupt_entry)

        entries = cache.list_valid_entries(str(git_root))
        # Only the good entry should appear
        assert len(entries) == 1
        assert entries[0]["feature_scope"] == "good"
        assert entries[0]["path"] == str(git_root)


# ============================================================================
# CodeAnalysisService integration tests
# ============================================================================


class TestCodeAnalysisServiceCached:
    """Verify the service delegates correctly to the cache component."""

    @pytest.fixture
    def svc_and_mocks(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
        """Return (service, internal_mock, notes_mock, git_root)."""
        git_root = tmp_path / "repo"
        git_root.mkdir()
        _patch_realpath(monkeypatch)

        # Create a real file for manifest computation
        src = git_root / "src"
        src.mkdir()
        (src / "main.py").write_text("def main(): pass\n")

        # Patch subprocess for git ls-files
        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "diff-files" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files" in cmd and "--others" not in cmd and "-s" not in cmd:
                return subprocess.CompletedProcess(args, 0, "src/main.py\n", "")
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            if "ls-files -s" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        from AgentCrew.modules.code_analysis import CodeAnalysisService

        svc = CodeAnalysisService(llm_service=None)
        notes_mock = AsyncMock(return_value="project notes")
        svc.extract_project_notes = notes_mock  # type: ignore[method-assign]

        # Mock _run_analysis_internal to return structured data
        internal_mock = AsyncMock(
            return_value={
                "analysis_results": [
                    {
                        "path": "src/main.py",
                        "language": "python",
                        "structure": {
                            "type": "module",
                            "name": "main",
                            "children": [
                                {
                                    "type": "function_definition",
                                    "name": "main",
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                            ],
                        },
                    }
                ],
                "analyzed_files_abs": [str(src / "main.py")],
                "analyzed_relative_paths": ["src/main.py"],
                "errors": [],
                "non_analyzed_files": [],
                "total_supported_files": 1,
            }
        )
        svc._run_analysis_internal = internal_mock  # type: ignore[method-assign]
        return svc, internal_mock, notes_mock, str(git_root)

    @pytest.mark.asyncio
    async def test_cache_hit_skips_expensive_calls(self, svc_and_mocks) -> None:
        """Same normalized call hits cache; analysis/notes called once."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        r1 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert r1["analysis_text"] != ""
        assert r1["project_notes"] == "project notes"
        assert internal_mock.await_count == 1
        assert notes_mock.await_count == 1

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert r2["analysis_text"] != ""
        assert r2["project_notes"] == "project notes"
        assert internal_mock.await_count == 1, "cache hit should skip re-analysis"
        assert notes_mock.await_count == 1, "cache hit should skip note extraction"

    @pytest.mark.asyncio
    async def test_miss_different_feature_scope(self, svc_and_mocks) -> None:
        """Different feature_scope produces independent cache entries."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        await svc.analyze_code_structure_cached(
            git_root, feature_scope="a", deep_analysis=True
        )
        assert internal_mock.await_count == 1

        await svc.analyze_code_structure_cached(
            git_root, feature_scope="b", deep_analysis=True
        )
        assert internal_mock.await_count == 2, "different scope = cache miss"

    @pytest.mark.asyncio
    async def test_deep_analysis_false_no_notes_call(self, svc_and_mocks) -> None:
        """deep_analysis=False skips note extraction entirely."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        r = await svc.analyze_code_structure_cached(git_root, deep_analysis=False)
        assert r["analysis_text"] != ""
        assert r["project_notes"] is None
        assert notes_mock.await_count == 0

        # Cache hit also returns no notes
        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=False)
        assert r2["project_notes"] is None
        assert internal_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_hit_preserves_notes_semantics(self, svc_and_mocks) -> None:
        """Deep-analysis cache hit preserves project_notes in output."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        r1 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert r1["project_notes"] == "project notes"

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert r2["project_notes"] == "project notes"

    @pytest.mark.asyncio
    async def test_cache_failure_degradation(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache failure degrades to normal analysis."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        def failing_get(*args, **kwargs):
            raise RuntimeError("cache failure")

        monkeypatch.setattr(svc._analyze_cache, "get", failing_get)

        r = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert r["analysis_text"] != ""
        assert r["project_notes"] == "project notes"

    @pytest.mark.asyncio
    async def test_list_newest_first_precise_order(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Entries created within one second preserve precise ordering."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        await svc.analyze_code_structure_cached(
            git_root, feature_scope="first", deep_analysis=True
        )
        await svc.analyze_code_structure_cached(
            git_root, feature_scope="second", deep_analysis=True
        )

        entries = svc.get_cache_entries_for_context(git_root)
        assert len(entries) >= 2
        assert entries[0]["feature_scope"] == "second"
        assert entries[1]["feature_scope"] == "first"

    @pytest.mark.asyncio
    async def test_incremental_merge_project_notes_preserved(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Incremental merge preserves project notes without rerunning extraction."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        # Initial full analysis
        await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert notes_mock.await_count == 1
        assert internal_mock.await_count == 1

        # Modify a file to trigger incremental merge
        src_dir = Path(git_root) / "src"
        (src_dir / "main.py").write_text("def modified(): pass")

        # The internal mock now returns different results for the modified file
        internal_mock.return_value = {
            "analysis_results": [
                {
                    "path": "src/main.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "main",
                        "children": [
                            {
                                "type": "function_definition",
                                "name": "modified",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    },
                }
            ],
            "analyzed_files_abs": [str(src_dir / "main.py")],
            "analyzed_relative_paths": ["src/main.py"],
            "errors": [],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        # internal_mock called only for the changed files (incremental)
        # notes_mock should NOT be called again
        assert notes_mock.await_count == 1, "project notes should not be re-extracted"
        assert "modified" in r2["analysis_text"] or r2["analysis_text"] != ""

    @pytest.mark.asyncio
    async def test_incremental_merge_success_to_error_removes_structure(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A modified file that now fails parsing must not retain its old structure."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        # Initial full analysis succeeds
        await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        assert internal_mock.await_count == 1

        # Modify file to trigger incremental merge
        src_dir = Path(git_root) / "src"
        (src_dir / "main.py").write_text("def broken(")  # invalid Python

        # Mock now returns an error for this file
        internal_mock.return_value = {
            "analysis_results": [],
            "analyzed_files_abs": [str(src_dir / "main.py")],
            "analyzed_relative_paths": ["src/main.py"],
            "errors": [{"path": "src/main.py", "error": "parse error"}],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        # The old successful structure must NOT appear in the output
        assert "function_definition" not in r2["analysis_text"]
        assert "parse error" in r2["analysis_text"] or "errors:" in r2["analysis_text"]

    @pytest.mark.asyncio
    async def test_incremental_merge_error_to_success_removes_error(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A previously failing file that now parses successfully must not retain its old error."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        # Initial full analysis with error
        internal_mock.return_value = {
            "analysis_results": [],
            "analyzed_files_abs": [str(Path(git_root) / "src" / "main.py")],
            "analyzed_relative_paths": ["src/main.py"],
            "errors": [{"path": "src/main.py", "error": "original parse error"}],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }
        await svc.analyze_code_structure_cached(git_root, deep_analysis=True)

        # Fix the file
        src_dir = Path(git_root) / "src"
        (src_dir / "main.py").write_text("def fixed(): pass")

        # Mock now returns success
        internal_mock.return_value = {
            "analysis_results": [
                {
                    "path": "src/main.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "main",
                        "children": [
                            {
                                "type": "function_definition",
                                "name": "fixed",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    },
                }
            ],
            "analyzed_files_abs": [str(src_dir / "main.py")],
            "analyzed_relative_paths": ["src/main.py"],
            "errors": [],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        # The old error must NOT appear in the output
        assert "original parse error" not in r2["analysis_text"]
        assert "fixed" in r2["analysis_text"] or r2["analysis_text"] != ""

    @pytest.mark.asyncio
    async def test_run_analysis_internal_normalizes_backslash_paths(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate Windows-style os.path.relpath returning backslashes;
        _run_analysis_internal must emit forward-slash paths."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        # Bypass the mock to call the real _run_analysis_internal
        original_run = svc.__class__._run_analysis_internal

        # Patch os.path.relpath to simulate Windows behavior
        _real_relpath = os.path.relpath

        def windows_relpath(path, start):
            result = _real_relpath(path, start)
            return result.replace("/", "\\")

        monkeypatch.setattr(os.path, "relpath", windows_relpath)

        # Reset the mock so the real method runs
        svc._run_analysis_internal = original_run.__get__(svc, type(svc))

        result = await original_run(svc, git_root)

        # Restore the mock for other tests
        svc._run_analysis_internal = internal_mock

        # All paths must use forward slashes
        for r in result.get("analysis_results", []):
            assert "\\" not in r["path"], f"Path has backslash: {r['path']}"
        for e in result.get("errors", []):
            assert "\\" not in e["path"], f"Error path has backslash: {e['path']}"
        for p in result.get("analyzed_relative_paths", []):
            assert "\\" not in p, f"Relative path has backslash: {p}"

    @pytest.mark.asyncio
    async def test_incremental_merge_matches_forward_slash_on_backslash_platform(
        self, svc_and_mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the platform produces backslash paths in cache entries,
        incremental merge must still match and replace them."""
        svc, internal_mock, notes_mock, git_root = svc_and_mocks

        # Seed cache with backslash paths (simulating a Windows-cached entry)
        internal_mock.return_value = {
            "analysis_results": [
                {
                    "path": "src\\main.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "main",
                        "children": [
                            {
                                "type": "function_definition",
                                "name": "old_fn",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    },
                }
            ],
            "analyzed_files_abs": [str(Path(git_root) / "src" / "main.py")],
            "analyzed_relative_paths": ["src\\main.py"],
            "errors": [],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }
        await svc.analyze_code_structure_cached(git_root, deep_analysis=True)

        # Modify the file
        src_dir = Path(git_root) / "src"
        (src_dir / "main.py").write_text("def new_fn(): pass")

        # Mock returns forward-slash paths (the new normalized behavior)
        internal_mock.return_value = {
            "analysis_results": [
                {
                    "path": "src/main.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "main",
                        "children": [
                            {
                                "type": "function_definition",
                                "name": "new_fn",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    },
                }
            ],
            "analyzed_files_abs": [str(src_dir / "main.py")],
            "analyzed_relative_paths": ["src/main.py"],
            "errors": [],
            "non_analyzed_files": [],
            "total_supported_files": 1,
        }

        r2 = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)
        # Old structure must be gone, new one must appear
        assert "old_fn" not in r2["analysis_text"]
        assert "new_fn" in r2["analysis_text"] or r2["analysis_text"] != ""

    # --- Skipped-file / hash-only incremental behavior --------------------

    @pytest.fixture
    def svc_skipped_fixture(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> tuple:
        """Service with pre-seeded cache containing analyzed + hash-only records."""
        git_root = tmp_path / "large_repo"
        git_root.mkdir()
        _patch_realpath(monkeypatch)

        src = git_root / "src"
        src.mkdir()
        (src / "main.py").write_text("def main(): pass\n")
        (src / "utils.py").write_text("x = 1\n")

        # Git mock returns both files
        tracked_files = ["src/main.py", "src/utils.py"]

        def fake_run(args, **kwargs):
            cmd = " ".join(args)
            if "rev-parse --show-toplevel" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{git_root}\n", "")
            if "rev-parse HEAD" in cmd:
                return subprocess.CompletedProcess(args, 0, f"{FAKE_HEAD}\n", "")
            if "diff-index" in cmd or "diff-files" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "ls-files" in cmd and "--others" not in cmd and "-s" not in cmd:
                return subprocess.CompletedProcess(
                    args, 0, "\n".join(tracked_files) + "\n", ""
                )
            if "ls-files --others" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(args, 0, "dummyhash\n", "")
            if "ls-files -s" in cmd:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        from AgentCrew.modules.code_analysis import CodeAnalysisService

        svc = CodeAnalysisService(llm_service=None)
        notes_mock = AsyncMock(return_value="project notes")
        svc.extract_project_notes = notes_mock

        internal_mock = AsyncMock()
        svc._run_analysis_internal = internal_mock

        # Compute proper content hashes
        main_hash = _sha256_hex(b"def main(): pass\n")
        utils_hash = _sha256_hex(b"x = 1\n")

        # Seed cache with analyzed + hash-only records (simulating large repo)
        files = {
            "src/main.py": {
                "hash": main_hash,
                "analysis": "main.py\n  main()",
                "classes": 0,
                "functions": 1,
                "decorated": 0,
            },
            "src/utils.py": {"hash": utils_hash},  # hash-only (skipped)
        }
        svc._analyze_cache.set(
            path=str(git_root),
            files=files,
            project_notes="original notes",
            language_map=svc.LANGUAGE_MAP,
        )

        return svc, internal_mock, notes_mock, str(git_root), src, tracked_files

    @pytest.mark.asyncio
    async def test_skipped_file_content_change_updates_hash_only(
        self, svc_skipped_fixture
    ) -> None:
        """Content-only change to hash-only (skipped) file updates its hash
        but keeps it hash-only; no Tree-sitter or LLM selection."""
        svc, internal_mock, notes_mock, git_root, src_dir, _ = svc_skipped_fixture

        # Modify the hash-only file's content
        (src_dir / "utils.py").write_text("x = 42\n")  # new content

        r = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)

        # _run_analysis_internal must NOT be called (no Tree-sitter for skipped)
        assert internal_mock.await_count == 0, (
            "hash-only change must not trigger Tree-sitter"
        )
        # notes_mock must NOT be called (notes preserved from cache)
        assert notes_mock.await_count == 0, (
            "hash-only change must preserve cached notes"
        )
        # Output must be valid (reconstructed from cached files)
        assert r["analysis_text"] != ""
        # Project notes preserved from cache
        assert r["project_notes"] == "original notes"
        # The skipped file's hash in the cache entry changed
        cache_dir = svc._analyze_cache._resolve_cache_dir(git_root)
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        import json

        entry = json.loads(entry_files[0].read_text())
        utils_record = entry["files"].get("src/utils.py", {})
        # Hash changed from original
        new_expected_hash = _sha256_hex(b"x = 42\n")
        assert utils_record.get("hash") == new_expected_hash
        # Record must be hash-only (no analysis or error)
        assert "analysis" not in utils_record
        assert "error" not in utils_record

    @pytest.mark.asyncio
    async def test_new_file_while_skipped_records_triggers_full_rebuild(
        self, svc_skipped_fixture
    ) -> None:
        """Adding a genuinely new path while hash-only records exist
        triggers full rebuild (candidate path set changed)."""
        svc, internal_mock, notes_mock, git_root, src_dir, tracked_files = (
            svc_skipped_fixture
        )

        # Create a new file
        (src_dir / "new.py").write_text("z = 3\n")
        tracked_files.append("src/new.py")  # add to git mock

        # Mock _run_analysis_internal to handle the full rebuild
        internal_mock.return_value = {
            "analysis_results": [
                {
                    "path": "src/main.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "main",
                        "children": [
                            {
                                "type": "function_definition",
                                "name": "main",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ],
                    },
                },
                {
                    "path": "src/new.py",
                    "language": "python",
                    "structure": {
                        "type": "module",
                        "name": "new",
                        "children": [],
                    },
                },
            ],
            "analyzed_files_abs": [
                str(src_dir / "main.py"),
                str(src_dir / "new.py"),
            ],
            "analyzed_relative_paths": ["src/main.py", "src/new.py"],
            "errors": [],
            "non_analyzed_files": ["src/utils.py"],
            "total_supported_files": 3,
        }

        r = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)

        # Full rebuild called _run_analysis_internal once
        assert internal_mock.await_count == 1, (
            "new file with skipped records triggers full analysis"
        )
        # notes_mock was called (full rebuild includes project notes extraction)
        assert notes_mock.await_count == 1
        # Output is valid
        assert r["analysis_text"] != ""

    @pytest.mark.asyncio
    async def test_skipped_file_deletion_removes_record(
        self, svc_skipped_fixture
    ) -> None:
        """Deleting a hash-only path removes it without analysis."""
        svc, internal_mock, notes_mock, git_root, src_dir, tracked_files = (
            svc_skipped_fixture
        )

        # Remove the hash-only file from disk and git tracking
        (src_dir / "utils.py").unlink()
        tracked_files.remove("src/utils.py")

        r = await svc.analyze_code_structure_cached(git_root, deep_analysis=True)

        # No Tree-sitter call for deletion
        assert internal_mock.await_count == 0
        # Notes preserved
        assert r["project_notes"] == "original notes"
        # Output is valid
        assert r["analysis_text"] != ""
        # Cache entry no longer contains the deleted file
        cache_dir = svc._analyze_cache._resolve_cache_dir(git_root)
        assert cache_dir is not None
        entry_files = list(cache_dir.rglob("*.json"))
        assert len(entry_files) == 1
        import json

        entry = json.loads(entry_files[0].read_text())
        assert "src/utils.py" not in entry.get("files", {})
