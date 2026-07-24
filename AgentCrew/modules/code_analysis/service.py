from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from .cache import (
    AnalyzeRepoCache,
    _compute_file_manifest,
    _sha256_hex,
    discover_supported_files,
    normalize_exclude_patterns,
)
from .file_selector import MAX_FILES_TO_ANALYZE, FileSelector
from .file_tree_formatter import FileTreeFormatter
from .parsers import LANGUAGE_PARSER_MAP, BaseLanguageParser, get_parser_for_language
from .project_notes import ProjectNotesExtractor
from .result_formatter import (
    ResultFormatter,
    format_single_file_analysis,
    reconstruct_from_files,
)
from .text_map_formatter import TextMapFormatter
from .tree_sitter_runtime import EXTENSION_TO_LANGUAGE, TreeSitterRuntime

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService


def _read_file_sync(path: str) -> bytes:
    """Synchronous helper: read file as bytes."""
    with open(path, "rb") as file:
        return file.read()


class CodeAnalysisService:
    """Service for analyzing code structure using tree-sitter."""

    LANGUAGE_MAP: ClassVar[dict[str, str | None]] = EXTENSION_TO_LANGUAGE

    CUSTOM_PARSER_LANGUAGES: ClassVar[set[str]] = set(LANGUAGE_PARSER_MAP.keys())

    def __init__(self, llm_service: BaseLLMService | None = None):
        """Initialize the code analysis service with tree-sitter.

        Args:
            llm_service: Optional LLM service for intelligent file selection when
                        analyzing large repositories (>500 files).
        """
        self.llm_service = llm_service
        self.file_handler = None
        if self.llm_service:
            if self.llm_service.provider_name == "google":
                self.llm_service.model = "gemini-2.5-flash-lite"
            elif self.llm_service.provider_name == "claude":
                self.llm_service.model = "claude-3-5-haiku-latest"
            elif self.llm_service.provider_name == "openai":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "deepinfra":
                self.llm_service.model = "google/gemma-4-31B-it"
            elif self.llm_service.provider_name == "fireworks":
                self.llm_service.model = "accounts/fireworks/models/gemma-4-31b-it"
            elif self.llm_service.provider_name == "github_copilot":
                self.llm_service.model = "claude-haiku-4.5"
            elif (
                self.llm_service.provider_name == "copilot_response"
                or self.llm_service.provider_name == "openai_codex"
            ):
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "together":
                self.llm_service.model = "Qwen/Qwen3.5-9B"
            elif self.llm_service.provider_name == "opencode_go":
                self.llm_service.model = "deepseek-v4-flash"
            elif self.llm_service.provider_name == "commandcode":
                self.llm_service.model = "deepseek/deepseek-v4-flash"
            elif self.llm_service.provider_name == "crofai":
                self.llm_service.model = "deepseek-v4-flash"

        self._runtime = TreeSitterRuntime.get_instance()
        self._language_parser_cache: dict[str, BaseLanguageParser] = {}

        self.class_types = {
            "class_definition",
            "class_declaration",
            "class_specifier",
            "struct_specifier",
            "struct_declaration",
            "struct_item",
            "interface_declaration",
            "object_declaration",
        }

        self.function_types = {
            "function_definition",
            "function_declaration",
            "method_definition",
            "method_declaration",
            "constructor_declaration",
            "arrow_function",
            "fn_item",
            "method",
            "singleton_method",
            "primary_constructor",
        }

        self._text_map_formatter = TextMapFormatter()
        self._file_tree_formatter = FileTreeFormatter()
        self._result_formatter = ResultFormatter(
            text_map_formatter=self._text_map_formatter,
            file_tree_formatter=self._file_tree_formatter,
            class_types=self.class_types,
            function_types=self.function_types,
            max_files_to_analyze=MAX_FILES_TO_ANALYZE,
        )
        self._project_notes_extractor = ProjectNotesExtractor(
            llm_service=self.llm_service
        )
        self._file_selector = FileSelector(llm_service=self.llm_service)
        self._analyze_cache = AnalyzeRepoCache()

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language based on file extension."""
        lang = self._runtime.detect_language_for_file(file_path)
        return lang if lang else "unknown"

    def _has_custom_parser(self, language: str) -> bool:
        """Check if a custom rich parser exists for this language."""
        resolved = self._runtime._resolve_name(language)
        return (
            resolved in self.CUSTOM_PARSER_LANGUAGES
            or language in self.CUSTOM_PARSER_LANGUAGES
        )

    def _get_tree_sitter_parser(self, language: str):
        """Get the appropriate tree-sitter parser for a language (lazy, cached)."""
        return self._runtime.get_parser(language)

    def _get_language_parser(self, language: str) -> BaseLanguageParser:
        """Get the appropriate language parser for processing nodes."""
        if language not in self._language_parser_cache:
            self._language_parser_cache[language] = get_parser_for_language(language)
        return self._language_parser_cache[language]

    def _analyze_file(self, file_path: str) -> dict[str, Any] | None:
        """Analyze a single file using tree-sitter."""
        try:
            with open(file_path, "rb") as f:
                source_code = f.read()

            language = self._detect_language(file_path)
            if language == "unknown":
                return {
                    "error": f"Unsupported file type: {os.path.splitext(file_path)[1]}"
                }

            if not self._runtime.is_in_manifest(language):
                return {
                    "error": f"Language '{language}' not available in tree-sitter pack"
                }

            tree_sitter_parser = self._get_tree_sitter_parser(language)

            tree = tree_sitter_parser.parse(source_code)
            root_node = tree.root_node

            if not root_node:
                return {"error": "Failed to parse file - no root node"}

            language_parser = self._get_language_parser(language)

            def process_node(node) -> dict[str, Any] | None:
                if not node:
                    return None
                return language_parser.process_node(node, source_code, process_node)

            return process_node(root_node)

        except Exception as e:
            return {"error": f"Error analyzing file: {e!s}"}

    def _count_nodes(self, structure: dict[str, Any], node_types: set[str]) -> int:
        """Recursively count nodes of specific types in the tree structure."""
        count = 0

        if structure.get("type") in node_types:
            count += 1

        for child in structure.get("children", []):
            count += self._count_nodes(child, node_types)

        return count

    async def _select_files_with_llm(
        self,
        files: list[str],
        max_files: int = MAX_FILES_TO_ANALYZE,
        feature_scope: str | None = None,
    ) -> list[str]:
        """Use LLM to intelligently select files for analysis. Delegates to FileSelector."""
        return await self._file_selector.select_files_with_llm(
            files, max_files, feature_scope=feature_scope
        )

    async def extract_project_notes(
        self,
        analysis_result: str,
        repo_path: str,
        feature_scope: str | None = None,
    ) -> str:
        """Extract project notes, rules, and conventions from the analysis result.

        Delegates to ProjectNotesExtractor."""
        return await self._project_notes_extractor.extract_project_notes(
            analysis_result, repo_path, feature_scope=feature_scope
        )

    async def analyze_code_structure(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
    ) -> dict[str, Any] | str:
        """Build a tree-sitter based structural map of source code files in a git repository.

        Delegates to ``_run_analysis_internal`` for shared structured logic,
        then formats the results.  This method is the public entry point;
        the incremental merge path uses ``_run_analysis_internal`` directly.

        Args:
            path: Root directory to analyze (must be a git repository)

        Returns:
            Formatted analysis string on success, or error dict on failure.
        """
        try:
            result = await self._run_analysis_internal(
                path=path,
                exclude_patterns=exclude_patterns or [],
                feature_scope=feature_scope,
            )
            if "error" in result:
                return {"error": result["error"]}

            analysis_results = result["analysis_results"]
            analyzed_files_abs = result["analyzed_files_abs"]
            errors = result["errors"]
            non_analyzed_files = result["non_analyzed_files"]
            total_supported_files = result["total_supported_files"]

            if not analysis_results:
                return "Analysis completed but no valid results. This may due to excluded patterns is not correct"

            return self._format_analysis_results(
                analysis_results,
                analyzed_files_abs,
                errors,
                non_analyzed_files,
                total_supported_files,
            )

        except Exception as e:
            return {"error": f"Error analyzing directory: {e!s}"}

    async def _run_analysis_internal(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        analyze_only_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Shared structured analysis pipeline used by full analysis and incremental merge.

        Args:
            path: Root directory to analyze.
            exclude_patterns: Glob patterns to exclude.
            feature_scope: Optional feature scope for LLM file selection.
            analyze_only_paths: If provided, only analyze these specific
                relative paths (used by incremental merge for changed files).
                When *None*, discovers all relevant files via ``git ls-files``.

        Returns:
            A dict with keys:
            - ``analysis_results`` (list[dict]): per-file structured results.
            - ``analyzed_files_abs`` (list[str]): absolute paths of analyzed files.
            - ``analyzed_relative_paths`` (list[str]): relative paths of analyzed files.
            - ``errors`` (list[dict]): per-file errors.
            - ``non_analyzed_files`` (list[str]): supported files not analyzed.
            - ``total_supported_files`` (int): count of supported files.
            - ``error`` (str | None): set on fatal errors.
        """
        if exclude_patterns is None:
            exclude_patterns = []

        if not os.path.exists(path):
            return {"error": f"Path does not exist: {path}"}

        if analyze_only_paths is not None:
            # Partial analysis: only analyze the specified relative paths
            files_to_analyze = sorted(analyze_only_paths)
            non_analyzed_files: list[str] = []
            supported_files_rel = files_to_analyze
        else:
            # Full analysis: discover all relevant files (tracked + untracked)
            # using the shared discovery function to stay consistent with manifest
            supported_files_rel = discover_supported_files(
                path, exclude_patterns, self.LANGUAGE_MAP
            )
            if supported_files_rel is None:
                return {
                    "error": f"Failed to run git ls-files on {path}. Make sure it's a git repository."
                }

            non_analyzed_files = []
            files_to_analyze = supported_files_rel

            if len(supported_files_rel) > MAX_FILES_TO_ANALYZE:
                selected_files = await self._select_files_with_llm(
                    supported_files_rel,
                    MAX_FILES_TO_ANALYZE,
                    feature_scope=feature_scope,
                )
                non_analyzed_files = [
                    f for f in supported_files_rel if f not in selected_files
                ]
                files_to_analyze = selected_files

        supported_files_abs = [os.path.join(path, f) for f in files_to_analyze]

        analysis_results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for file_path in supported_files_abs:
            rel_path = os.path.relpath(file_path, path)
            # Normalize to forward slashes for cross-platform consistency,
            # matching the canonical representation from discover_supported_files().
            rel_path = rel_path.replace("\\", "/")
            try:
                language = self._detect_language(file_path)

                if language == "config":
                    if os.path.basename(file_path) == "package-lock.json":
                        continue
                    parsed = {"type": "config", "name": os.path.basename(file_path)}
                else:
                    parsed = self._analyze_file(file_path)

                if parsed and isinstance(parsed, dict) and "error" not in parsed:
                    analysis_results.append(
                        {
                            "path": rel_path,
                            "language": language,
                            "structure": parsed,
                        }
                    )
                elif parsed and isinstance(parsed, dict) and "error" in parsed:
                    errors.append({"path": rel_path, "error": parsed["error"]})
            except Exception as e:
                errors.append({"path": rel_path, "error": str(e)})

        return {
            "analysis_results": analysis_results,
            "analyzed_files_abs": supported_files_abs,
            "analyzed_relative_paths": files_to_analyze,
            "errors": errors,
            "non_analyzed_files": non_analyzed_files,
            "total_supported_files": len(supported_files_rel),
        }

    async def _incremental_merge(
        self,
        cached_entry: dict[str, Any],
        changes: dict[str, list[str]],
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        current_manifest: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Merge changed files into cached ``files`` map.

        Operates on the unified ``files`` map (schema v2+).

        Preserves skipped-file status incrementally:
        * A content-only change to an existing hash-only (skipped) file
          updates only its stored hash; no Tree-sitter or LLM selection.
        * A genuinely *new* path while selection-limit/hash-only records
          exist triggers a full rebuild (candidate path set changed).
        * Deleted hash-only paths are simply removed from the map.
        * Changed analyzed files are reparsed and their records replaced.

        Args:
            cached_entry: The existing cache entry with ``files`` map.
            changes: Dict with ``added``, ``modified``, ``deleted`` file lists.
            path: The analysis root path.
            exclude_patterns: Exclusion patterns.
            feature_scope: Optional feature scope.
            current_manifest: Current ``{path: hash}`` manifest, used to
                update hash-only records without reading files ad-hoc.

        Returns:
            Updated dict with merged ``files`` and ``project_notes``,
            or ``{"_needs_full_rebuild": True}`` on safety failure.
        """
        cached_files: dict[str, dict] = cached_entry.get("files", {})

        added = changes.get("added", [])
        modified = changes.get("modified", [])
        deleted = changes.get("deleted", [])

        added_set = set(added)
        modified_set = set(modified)

        # --- Classify modified paths ---
        # Modified paths that are currently hash-only (skipped) need only
        # their hash refreshed; they remain hash-only.
        hash_only_modified = {
            p
            for p in modified_set
            if p in cached_files
            and "analysis" not in cached_files[p]
            and "error" not in cached_files[p]
        }
        # Modified paths that currently have analysis or error need
        # Tree-sitter reanalysis.
        analyzed_modified = modified_set - hash_only_modified

        # --- 150-file selection safety check ---
        # Only *genuinely new* paths (added, not modified) that are
        # outside the analyzed set trigger full rebuild when selection
        # limit was active.
        has_skipped = any(
            "analysis" not in v and "error" not in v for v in cached_files.values()
        )
        analyzed_paths = {
            p for p, v in cached_files.items() if "analysis" in v or "error" in v
        }
        genuinely_new_outside = added_set - analyzed_paths
        if genuinely_new_outside and has_skipped:
            logger.info(
                f"New file(s) {sorted(genuinely_new_outside)} outside previously "
                f"selected set; triggering full rebuild (selection limit was active)"
            )
            return {"_needs_full_rebuild": True}

        # --- Step 1: Preserve unchanged records, remove stale/deleted ---
        # Stale = analyzed_modified (will be replaced) + added (will be added)
        stale_or_deleted = analyzed_modified | added_set | set(deleted)
        merged_files = {
            p: v for p, v in cached_files.items() if p not in stale_or_deleted
        }

        # --- Step 2: Update hash-only modified files with new hash ---
        for path_key in hash_only_modified:
            new_hash = (current_manifest or {}).get(path_key, "")
            if new_hash:
                merged_files[path_key] = {"hash": new_hash}

        # --- Step 3: Re-analyze added and analyzed-modified files ---
        changed_paths = sorted(analyzed_modified | added_set)
        if changed_paths:
            partial_result = await self._run_analysis_internal(
                path=path,
                exclude_patterns=exclude_patterns,
                feature_scope=feature_scope,
                analyze_only_paths=changed_paths,
            )

            for result_item in partial_result.get("analysis_results", []):
                rel_path = result_item["path"]
                structure = result_item.get("structure", {})

                # Use manifest hash when available to avoid ad-hoc reads
                content_hash = (current_manifest or {}).get(rel_path, "")
                if not content_hash:
                    norm_path = os.path.normpath(path)
                    full_path = os.path.join(norm_path, rel_path)
                    try:
                        content_hash = _sha256_hex(
                            Path(full_path).read_bytes()
                            if os.path.exists(full_path)
                            else b""
                        )
                    except (OSError, PermissionError):
                        content_hash = _sha256_hex(b"")

                classes = self._count_nodes(structure, self.class_types)
                functions = self._count_nodes(structure, self.function_types)
                decorated = self._count_nodes(structure, {"decorated_definition"})

                compact = format_single_file_analysis(
                    rel_path, structure, self.class_types, self.function_types
                )

                merged_files[rel_path] = {
                    "hash": content_hash,
                    "analysis": compact,
                    "classes": classes,
                    "functions": functions,
                    "decorated": decorated,
                }

            # Handle errors
            for err_item in partial_result.get("errors", []):
                rel_path = err_item["path"]
                content_hash = (current_manifest or {}).get(rel_path, "")
                if not content_hash:
                    norm_path = os.path.normpath(path)
                    full_path = os.path.join(norm_path, rel_path)
                    try:
                        content_hash = _sha256_hex(
                            Path(full_path).read_bytes()
                            if os.path.exists(full_path)
                            else b""
                        )
                    except (OSError, PermissionError):
                        content_hash = _sha256_hex(b"")
                merged_files[rel_path] = {
                    "hash": content_hash,
                    "error": err_item["error"],
                }

        # --- Step 4: Reconstruct output from merged files map ---
        merged_analysis_text = reconstruct_from_files(
            merged_files,
            file_tree_formatter=self._file_tree_formatter,
            max_files_to_analyze=MAX_FILES_TO_ANALYZE,
        )

        # Preserve project notes (no deep extraction)
        cached_notes = cached_entry.get("project_notes")

        return {
            "files": merged_files,
            "project_notes": cached_notes,
            "analysis_text": merged_analysis_text,
        }

    def _generate_text_map(self, analysis_results: list[dict[str, Any]]) -> str:
        """Generate a hierarchical text representation of the code structure analysis."""
        return self._text_map_formatter.generate_text_map(analysis_results)

    async def get_file_content(
        self,
        file_path,
        start_line=None,
        end_line=None,
    ) -> tuple[str, str] | tuple[str, dict[str, Any]]:
        """Return the content of a file, optionally reading only a specific line range.

        For document files (PDF, DOCX, XLSX, PPTX), uses Docling to convert
        to text/markdown and ignores start_line/end_line parameters.
        For image files, returns base64 encoded data in image_url format.

        Args:
            file_path: Path to the file to read
            start_line: Optional starting line number (1-indexed) - ignored for document files
            end_line: Optional ending line number (1-indexed, inclusive) - ignored for document files

        Returns:
            Tuple of (file_path, content) where content is either:
            - str: text content for text/document files
            - dict: {"type": "image_url", "image_url": {"url": "data:mime;base64,..."}} for images
        """

        from AgentCrew.modules.utils.file_handler import (
            ALLOWED_MIME_TYPES,
            FileHandler,
        )

        mime_type, _ = mimetypes.guess_type(file_path)

        if not mime_type:
            mime_type = FileHandler.guess_mime_by_extension(file_path)

        if mime_type and mime_type in ALLOWED_MIME_TYPES:
            if self.file_handler is None:
                self.file_handler = FileHandler()
            result = await self.file_handler.async_process_file(file_path)
            if result and "text" in result:
                return file_path, result["text"]
            if result and "image_url" in result:
                return file_path, result
            elif result is None:
                raise ValueError(f"Failed to process document file: {file_path}")

        content = await asyncio.to_thread(_read_file_sync, file_path)
        decoded_content = content.decode("utf-8")

        if start_line is not None and end_line is not None:
            if start_line < 1:
                raise ValueError("start_line must be >= 1")
            if end_line < start_line:
                raise ValueError("end_line must be >= start_line")

            lines = decoded_content.split("\n")
            total_lines = len(lines)

            if start_line > total_lines:
                raise ValueError(
                    f"start_line {start_line} exceeds file length ({total_lines} lines)"
                )
            end_line = min(end_line, total_lines)

            selected_lines = lines[start_line - 1 : end_line]
            return file_path, "\n".join(
                line[:1000] + "..." if len(line) > 1000 else line
                for line in selected_lines
            )

        return file_path, decoded_content

    def _build_file_tree(self, file_paths: list[str]) -> dict[str, Any]:
        """Build a hierarchical tree structure from flat file paths."""
        return self._file_tree_formatter.build_file_tree(file_paths)

    def _format_file_tree(self, tree: dict[str, Any], indent: str = "") -> list[str]:
        """Format a file tree dictionary into indented lines."""
        return self._file_tree_formatter.format_file_tree(tree, indent)

    # ------------------------------------------------------------------
    # Cached analysis (wraps analyze_code_structure + extract_project_notes)
    # ------------------------------------------------------------------

    async def analyze_code_structure_cached(
        self,
        path: str,
        exclude_patterns: list[str] | None = None,
        feature_scope: str | None = None,
        deep_analysis: bool = True,
    ) -> dict[str, Any]:
        """Analyze code structure with project-local caching.

        Uses a compact per-file cache (schema v2) via ``AnalyzeRepoCache``.
        The cache stores a unified ``files`` map; no raw Tree-sitter AST or
        complete ``analysis_text`` is persisted.  The repository response
        is reconstructed from per-file compact analysis/error records.

        On cache hit (no file changes) returns previously stored results
        without running tree-sitter analysis or LLM calls.

        On a small change set (within threshold), performs an incremental
        merge: only re-analyses added/modified files and updates the
        ``files`` map.  Project notes are preserved without rerunning deep
        extraction.

        On cache miss, too many changes, or legacy entries, runs the full
        analysis pipeline, caches the result, and returns it.

        Returns:
            A dict with keys:
            - ``analysis_text`` (str) — the structural analysis output
            - ``project_notes`` (str | None) — extracted project notes if
              *deep_analysis* was True
            - ``error`` (str | None) — set only when analysis itself failed
        """
        # Try cache first with manifest-based change detection
        try:
            cached = self._analyze_cache.get(
                path,
                exclude_patterns,
                feature_scope,
                deep_analysis,
                language_map=self.LANGUAGE_MAP,
            )
            if cached is not None:
                cache_info = cached.get("_cache_info")
                if cache_info and cache_info.get("action") == "incremental_merge":
                    # Incremental merge path
                    changes = cache_info["changes"]
                    current_manifest = cache_info.get("current_manifest")
                    merged = await self._incremental_merge(
                        cached_entry=cached,
                        changes=changes,
                        path=path,
                        exclude_patterns=exclude_patterns,
                        feature_scope=feature_scope,
                        current_manifest=current_manifest,
                    )
                    if merged.get("_needs_full_rebuild"):
                        logger.info(
                            "Incremental merge unsafe (outside-selection change); "
                            "falling back to full analysis"
                        )
                    else:
                        merged_files = merged.get("files", {})
                        merged_text = merged.get("analysis_text", "")
                        merged_notes = merged.get("project_notes")
                        try:
                            self._analyze_cache.set(
                                path=path,
                                exclude_patterns=exclude_patterns,
                                feature_scope=feature_scope,
                                deep_analysis=deep_analysis,
                                files=merged_files,
                                project_notes=merged_notes,
                                language_map=self.LANGUAGE_MAP,
                            )
                        except Exception as exc:
                            logger.warning(
                                f"Failed to cache incremental merge result: {exc}"
                            )

                        return {
                            "analysis_text": merged_text,
                            "project_notes": merged_notes,
                        }
                else:
                    # Direct hit — no changes; reconstruct output from files map
                    stored_files = cached.get("files", {})
                    stored_notes = cached.get("project_notes")
                    analysis_text = reconstruct_from_files(
                        stored_files,
                        file_tree_formatter=self._file_tree_formatter,
                        max_files_to_analyze=MAX_FILES_TO_ANALYZE,
                    )
                    return {
                        "analysis_text": analysis_text,
                        "project_notes": stored_notes,
                    }
        except Exception as exc:
            logger.warning(f"analyze_repo cache lookup failed, falling through: {exc}")

        # Cache miss, incremental unsafe, or error — run full analysis
        structured = await self._run_analysis_internal(
            path=path,
            exclude_patterns=exclude_patterns or [],
            feature_scope=feature_scope,
        )
        if "error" in structured:
            return {
                "analysis_text": "",
                "project_notes": None,
                "error": structured["error"],
            }

        analysis_results = structured["analysis_results"]
        structured["analyzed_files_abs"]
        structured["analyzed_relative_paths"]
        errors = structured["errors"]
        structured["non_analyzed_files"]
        structured["total_supported_files"]

        # Build unified files map: combine manifest hashes with formatted results
        norm_path = os.path.normpath(path)
        manifest = _compute_file_manifest(
            path,
            normalize_exclude_patterns(exclude_patterns) if exclude_patterns else [],
            self.LANGUAGE_MAP,
        )
        files: dict[str, dict] = {}
        if manifest:
            for rel_path, content_hash in manifest.items():
                files[rel_path] = {"hash": content_hash}

        # Overlay analyzed file results (compact formatted text + counts)
        for result_item in analysis_results:
            rel_path = result_item["path"]
            structure = result_item.get("structure", {})
            classes = self._count_nodes(structure, self.class_types)
            functions = self._count_nodes(structure, self.function_types)
            decorated = self._count_nodes(structure, {"decorated_definition"})

            compact = format_single_file_analysis(
                rel_path, structure, self.class_types, self.function_types
            )

            # Compute content hash (or use manifest hash)
            content_hash = manifest.get(rel_path, "")
            if not content_hash:
                full_path = os.path.join(norm_path, rel_path)
                try:
                    content_hash = _sha256_hex(
                        Path(full_path).read_bytes()
                        if os.path.exists(full_path)
                        else b""
                    )
                except (OSError, PermissionError):
                    content_hash = _sha256_hex(b"")

            files[rel_path] = {
                "hash": content_hash,
                "analysis": compact,
                "classes": classes,
                "functions": functions,
                "decorated": decorated,
            }

        # Overlay error results
        for err_item in errors:
            rel_path = err_item["path"]
            content_hash = manifest.get(rel_path, "")
            if not content_hash:
                try:
                    content_hash = _sha256_hex(
                        Path(os.path.join(norm_path, rel_path)).read_bytes()
                    )
                except (OSError, PermissionError):
                    content_hash = _sha256_hex(b"")
            files[rel_path] = {
                "hash": content_hash,
                "error": err_item["error"],
            }

        # Reconstruct output from files map
        if not analysis_results:
            analysis_text = (
                "Analysis completed but no valid results. "
                "This may due to excluded patterns is not correct"
            )
        else:
            analysis_text = reconstruct_from_files(
                files,
                file_tree_formatter=self._file_tree_formatter,
                max_files_to_analyze=MAX_FILES_TO_ANALYZE,
            )

        project_notes: str | None = None
        if deep_analysis:
            project_notes = await self.extract_project_notes(
                analysis_text, path, feature_scope=feature_scope
            )

        # Best-effort cache write with compact files map
        try:
            self._analyze_cache.set(
                path=path,
                exclude_patterns=exclude_patterns,
                feature_scope=feature_scope,
                deep_analysis=deep_analysis,
                files=files,
                project_notes=project_notes,
                language_map=self.LANGUAGE_MAP,
            )
        except Exception as exc:
            logger.warning(f"Failed to cache analyze_repo result: {exc}")

        return {
            "analysis_text": analysis_text,
            "project_notes": project_notes,
        }

    def get_cache_entries_for_context(self, cwd: str) -> list[dict[str, Any]]:
        """Return metadata for all readable cache entries in the project at *cwd*.

        Used by the adaptive context system to inform the agent about
        previously cached ``analyze_repo`` calls.  Returns all readable
        entries regardless of file changes, sorted newest-first (max 5).
        Performs no LLM work.
        """
        return self._analyze_cache.list_valid_entries(cwd)

    def _format_analysis_results(
        self,
        analysis_results: list[dict[str, Any]],
        analyzed_files: list[str],
        errors: list[dict[str, str]],
        non_analyzed_files: list[str] | None = None,
        total_supported_files: int = 0,
    ) -> str:
        """Format the analysis results into a clear text format."""
        if non_analyzed_files is None:
            non_analyzed_files = []
        return self._result_formatter.format_analysis_results(
            analysis_results,
            analyzed_files,
            errors,
            non_analyzed_files,
            total_supported_files,
        )
