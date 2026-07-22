from __future__ import annotations

import os
import fnmatch
import subprocess
from typing import Any, Tuple, Union, TYPE_CHECKING

from .tree_sitter_runtime import TreeSitterRuntime, EXTENSION_TO_LANGUAGE
from .parsers import get_parser_for_language, BaseLanguageParser, LANGUAGE_PARSER_MAP
from .text_map_formatter import TextMapFormatter
from .file_tree_formatter import FileTreeFormatter
from .result_formatter import ResultFormatter
from .project_notes import ProjectNotesExtractor
from .file_selector import FileSelector, MAX_FILES_TO_ANALYZE
from .cache import AnalyzeRepoCache
from loguru import logger
import mimetypes

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService


class CodeAnalysisService:
    """Service for analyzing code structure using tree-sitter."""

    LANGUAGE_MAP = EXTENSION_TO_LANGUAGE

    CUSTOM_PARSER_LANGUAGES = set(LANGUAGE_PARSER_MAP.keys())

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
            elif self.llm_service.provider_name == "copilot_response":
                self.llm_service.model = "gpt-5.4-mini"
            elif self.llm_service.provider_name == "openai_codex":
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
            return {"error": f"Error analyzing file: {str(e)}"}

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

        Args:
            path: Root directory to analyze (must be a git repository)

        Returns:
            Dictionary containing analysis results for each file or formatted string
        """
        try:
            if exclude_patterns is None:
                exclude_patterns = []

            if not os.path.exists(path):
                return {"error": f"Path does not exist: {path}"}

            try:
                result = subprocess.run(
                    ["git", "ls-files"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                files = result.stdout.strip().split("\n")
            except subprocess.CalledProcessError:
                return {
                    "error": f"Failed to run git ls-files on {path}. Make sure it's a git repository."
                }

            supported_files_rel = []
            for file_path in files:
                excluded = False
                if file_path.strip():
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(file_path, pattern):
                            excluded = True
                            break
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in self.LANGUAGE_MAP and not excluded:
                        supported_files_rel.append(file_path)

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

            supported_files = [os.path.join(path, f) for f in files_to_analyze]

            analysis_results = []
            errors = []
            for file_path in supported_files:
                rel_path = os.path.relpath(file_path, path)
                try:
                    language = self._detect_language(file_path)

                    if language == "config":
                        if os.path.basename(file_path) == "package-lock.json":
                            continue
                        result = {"type": "config", "name": os.path.basename(file_path)}
                    else:
                        result = self._analyze_file(file_path)

                    if result and isinstance(result, dict) and "error" not in result:
                        analysis_results.append(
                            {
                                "path": rel_path,
                                "language": language,
                                "structure": result,
                            }
                        )
                    elif result and isinstance(result, dict) and "error" in result:
                        errors.append({"path": rel_path, "error": result["error"]})
                except Exception as e:
                    errors.append({"path": rel_path, "error": str(e)})

            if not analysis_results:
                return "Analysis completed but no valid results. This may due to excluded patterns is not correct"
            return self._format_analysis_results(
                analysis_results,
                supported_files,
                errors,
                non_analyzed_files,
                len(supported_files_rel),
            )

        except Exception as e:
            return {"error": f"Error analyzing directory: {str(e)}"}

    def _generate_text_map(self, analysis_results: list[dict[str, Any]]) -> str:
        """Generate a hierarchical text representation of the code structure analysis."""
        return self._text_map_formatter.generate_text_map(analysis_results)

    async def get_file_content(
        self,
        file_path,
        start_line=None,
        end_line=None,
    ) -> Union[Tuple[str, str], Tuple[str, dict[str, Any]]]:
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
            FileHandler,
            ALLOWED_MIME_TYPES,
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

        with open(file_path, "rb") as file:
            content = file.read()

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
            if end_line > total_lines:
                end_line = total_lines

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

        On cache hit returns previously stored results without running
        tree-sitter analysis or LLM calls.  On cache miss runs the full
        analysis pipeline, caches the result, and returns it.

        Returns:
            A dict with keys:
            - ``analysis_text`` (str) — the structural analysis output
            - ``project_notes`` (str | None) — extracted project notes if
              *deep_analysis* was True
            - ``error`` (str | None) — set only when analysis itself failed
        """
        # Try cache first
        try:
            cached = self._analyze_cache.get(
                path, exclude_patterns, feature_scope, deep_analysis
            )
            if cached is not None:
                result_data = cached.get("result", {})
                return {
                    "analysis_text": result_data.get("analysis_text", ""),
                    "project_notes": result_data.get("project_notes"),
                }
        except Exception as exc:
            logger.warning(f"analyze_repo cache lookup failed, falling through: {exc}")

        # Cache miss or error — run real analysis
        analysis_text = await self.analyze_code_structure(
            path, exclude_patterns, feature_scope=feature_scope
        )
        if isinstance(analysis_text, dict):
            # Error case: do not cache
            return {
                "analysis_text": "",
                "project_notes": None,
                "error": analysis_text.get("error", "Unknown error"),
            }

        project_notes: str | None = None
        if deep_analysis:
            project_notes = await self.extract_project_notes(
                analysis_text, path, feature_scope=feature_scope
            )

        # Best-effort cache write
        try:
            self._analyze_cache.set(
                path=path,
                exclude_patterns=exclude_patterns,
                feature_scope=feature_scope,
                deep_analysis=deep_analysis,
                analysis_text=analysis_text,
                project_notes=project_notes,
            )
        except Exception as exc:
            logger.warning(f"Failed to cache analyze_repo result: {exc}")

        return {
            "analysis_text": analysis_text,
            "project_notes": project_notes,
        }

    def get_cache_entries_for_context(self, cwd: str) -> list[dict[str, Any]]:
        """Return metadata for valid cache entries in the project at *cwd*.

        Used by the adaptive context system to inform the agent about
        previously cached ``analyze_repo`` calls.  Performs no LLM work.
        """
        return self._analyze_cache.list_valid_entries(cwd)

    def _format_analysis_results(
        self,
        analysis_results: list[dict[str, Any]],
        analyzed_files: list[str],
        errors: list[dict[str, str]],
        non_analyzed_files: list[str] = [],
        total_supported_files: int = 0,
    ) -> str:
        """Format the analysis results into a clear text format."""
        return self._result_formatter.format_analysis_results(
            analysis_results,
            analyzed_files,
            errors,
            non_analyzed_files,
            total_supported_files,
        )
