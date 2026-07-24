from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from AgentCrew.modules.code_analysis.tree_sitter_runtime import TreeSitterRuntime

if TYPE_CHECKING:
    from typing import Literal


@dataclass
class SyntaxError:
    """Represents a syntax error found by tree-sitter."""

    line: int
    column: int
    message: str
    node_type: str | None
    severity: Literal["error", "warning"]


@dataclass
class SyntaxCheckResult:
    """Result of syntax checking."""

    is_valid: bool
    errors: list[SyntaxError]
    language: str
    parse_tree_available: bool


class TreeSitterChecker:
    """
    Universal syntax checker using tree-sitter.

    Uses the shared TreeSitterRuntime for lazy parser access and
    consistent language support across the codebase.
    """

    def __init__(self):
        self._runtime = TreeSitterRuntime.get_instance()

    def check_syntax(self, file_path: str, content: str) -> SyntaxCheckResult:
        """
        Check syntax using tree-sitter parser.

        Args:
            file_path: Path to file (used to determine language)
            content: File content to check

        Returns:
            SyntaxCheckResult with errors and validation status
        """
        language = self._runtime.detect_language_for_file(file_path)

        if not language:
            return SyntaxCheckResult(
                is_valid=True, errors=[], language="unknown", parse_tree_available=False
            )

        if not self._runtime.is_in_manifest(language):
            return SyntaxCheckResult(
                is_valid=True, errors=[], language=language, parse_tree_available=False
            )

        try:
            parser = self._runtime.get_parser(language)
        except Exception:
            return SyntaxCheckResult(
                is_valid=True, errors=[], language=language, parse_tree_available=False
            )

        tree = parser.parse(bytes(content, "utf8"))

        errors = self._extract_errors(tree, content)

        return SyntaxCheckResult(
            is_valid=len(errors) == 0,
            errors=errors,
            language=language,
            parse_tree_available=True,
        )

    def _extract_errors(self, tree, content: str) -> list[SyntaxError]:
        """
        Extract syntax errors from tree-sitter parse tree.

        Tree-sitter creates ERROR nodes for syntax errors and marks nodes as MISSING
        for incomplete syntax.
        """
        errors = []

        def visit_node(node):
            """Recursively visit tree nodes to find errors."""
            if node.is_error:
                line = node.start_point[0] + 1
                column = node.start_point[1]

                error_text = content[node.start_byte : node.end_byte]
                error_preview = (
                    error_text[:100] + "..." if len(error_text) > 100 else error_text
                )

                errors.append(
                    SyntaxError(
                        line=line,
                        column=column,
                        message=f"Syntax error near: {error_preview!r}",
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

    def get_supported_languages(self) -> list[str]:
        """Return list of languages available in the pack manifest."""
        return self._runtime.get_manifest_languages()

    def is_language_supported(self, file_path: str) -> bool:
        """Check if file type is supported by tree-sitter."""
        language = self._runtime.detect_language_for_file(file_path)
        return self._runtime.is_in_manifest(language) if language else False
