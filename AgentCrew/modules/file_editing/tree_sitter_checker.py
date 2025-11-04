from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import dataclass
from pathlib import Path

from tree_sitter_language_pack import get_parser

if TYPE_CHECKING:
    from tree_sitter_language_pack import SupportedLanguage
    from typing import List, Optional, Literal, Dict


@dataclass
class SyntaxError:
    """Represents a syntax error found by tree-sitter."""

    line: int
    column: int
    message: str
    node_type: Optional[str]
    severity: Literal["error", "warning"]


@dataclass
class SyntaxCheckResult:
    """Result of syntax checking."""

    is_valid: bool
    errors: List[SyntaxError]
    language: str
    parse_tree_available: bool


class TreeSitterChecker:
    """
    Universal syntax checker using tree-sitter.

    Supports 30+ languages including Python, JavaScript, TypeScript, Java, C/C++, Go, Rust, etc.
    """

    # Map file extensions to tree-sitter language names
    EXTENSION_TO_LANGUAGE: Dict[str, SupportedLanguage] = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".lua": "lua",
        ".r": "r",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".sh": "bash",
        ".bash": "bash",
        ".vim": "vim",
        ".el": "elisp",
        ".clj": "clojure",
    }

    def __init__(self):
        """Initialize tree-sitter parsers."""
        self._parsers = {}
        self._load_parsers()

    def _load_parsers(self):
        """Load all available tree-sitter language parsers."""
        for _, lang_name in self.EXTENSION_TO_LANGUAGE.items():
            try:
                parser = get_parser(lang_name)
                self._parsers[lang_name] = parser
            except Exception:
                # Language not available, skip silently
                pass

    def check_syntax(self, file_path: str, content: str) -> SyntaxCheckResult:
        """
        Check syntax using tree-sitter parser.

        Args:
            file_path: Path to file (used to determine language)
            content: File content to check

        Returns:
            SyntaxCheckResult with errors and validation status
        """
        # Determine language from extension
        ext = Path(file_path).suffix.lower()
        language = self.EXTENSION_TO_LANGUAGE.get(ext)

        if not language:
            return SyntaxCheckResult(
                is_valid=True, errors=[], language="unknown", parse_tree_available=False
            )

        if language not in self._parsers:
            return SyntaxCheckResult(
                is_valid=True, errors=[], language=language, parse_tree_available=False
            )

        parser = self._parsers[language]
        tree = parser.parse(bytes(content, "utf8"))

        errors = self._extract_errors(tree, content)

        return SyntaxCheckResult(
            is_valid=len(errors) == 0,
            errors=errors,
            language=language,
            parse_tree_available=True,
        )

    def _extract_errors(self, tree, content: str) -> List[SyntaxError]:
        """
        Extract syntax errors from tree-sitter parse tree.

        Tree-sitter creates ERROR nodes for syntax errors and marks nodes as MISSING
        for incomplete syntax.
        """
        errors = []

        def visit_node(node):
            """Recursively visit tree nodes to find errors."""
            if node.type == "ERROR":
                line = node.start_point[0] + 1  # tree-sitter uses 0-indexed lines
                column = node.start_point[1]

                error_text = content[node.start_byte : node.end_byte]
                error_preview = (
                    error_text[:50] + "..." if len(error_text) > 50 else error_text
                )

                errors.append(
                    SyntaxError(
                        line=line,
                        column=column,
                        message=f"Syntax error near: {repr(error_preview)}",
                        node_type="ERROR",
                        severity="error",
                    )
                )

            if node.is_missing:
                line = node.start_point[0] + 1
                column = node.start_point[1]

                errors.append(
                    SyntaxError(
                        line=line,
                        column=column,
                        message=f"Missing {node.type}",
                        node_type=node.type,
                        severity="error",
                    )
                )

            for child in node.children:
                visit_node(child)

        visit_node(tree.root_node)

        return errors

    def get_supported_languages(self) -> List[str]:
        """Return list of supported languages."""
        return list(self._parsers.keys())

    def is_language_supported(self, file_path: str) -> bool:
        """Check if file type is supported."""
        ext = Path(file_path).suffix.lower()
        language = self.EXTENSION_TO_LANGUAGE.get(ext)
        return language in self._parsers if language else False
