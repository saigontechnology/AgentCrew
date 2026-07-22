from __future__ import annotations

import fnmatch
import os
import subprocess
from typing import Any

from .tree_sitter_runtime import EXTENSION_TO_LANGUAGE, TreeSitterRuntime


class SymbolLookupError(Exception):
    """Raised when a syntax-based symbol lookup request is invalid."""


class SymbolLookupService:
    """Find candidate symbol definitions and references with Tree-sitter."""

    _instance: SymbolLookupService | None = None

    DEFAULT_MAX_RESULTS = 50
    MAX_RESULTS = 500
    DEFAULT_EXCLUDE_PATTERNS = [
        ".git/*",
        ".agentcrew/*",
        "**/__pycache__/*",
    ]

    IDENTIFIER_TYPES = {
        "constant",
        "field_identifier",
        "identifier",
        "name",
        "property_identifier",
        "simple_identifier",
        "type_identifier",
        "variable_name",
    }

    DECLARATION_KINDS = {
        "class": {
            "class",
            "class_declaration",
            "class_definition",
            "class_specifier",
            "object_declaration",
        },
        "interface": {"interface_declaration"},
        "struct": {"struct_declaration", "struct_item", "struct_specifier"},
        "trait": {"trait_declaration", "trait_item"},
        "function": {
            "def_definition",
            "fn_item",
            "function_declaration",
            "function_definition",
        },
        "method": {
            "constructor_declaration",
            "method",
            "method_declaration",
            "method_definition",
            "singleton_method",
        },
        "variable": {
            "const_item",
            "const_spec",
            "property_declaration",
            "property_element",
            "static_item",
            "variable_declarator",
            "variable_declaration",
            "var_spec",
        },
    }

    ASSIGNMENT_TYPES = {
        "annotated_assignment",
        "assignment",
        "augmented_assignment",
        "let_declaration",
        "short_var_declaration",
    }

    PARAMETER_TYPES = {
        "default_parameter",
        "formal_parameter",
        "optional_parameter",
        "parameter",
        "required_parameter",
        "typed_default_parameter",
        "typed_parameter",
    }

    NAME_FIELDS = ("name", "declarator")
    ASSIGNMENT_FIELDS = ("left", "pattern")

    @classmethod
    def get_instance(cls) -> SymbolLookupService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._runtime = TreeSitterRuntime.get_instance()
        self._node_kind = {
            node_type: kind
            for kind, node_types in self.DECLARATION_KINDS.items()
            for node_type in node_types
        }

    def find_definitions(
        self,
        symbol: str,
        path: str = ".",
        exclude_patterns: list[str] | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> dict[str, Any]:
        """Return syntax-based candidate declarations for an exact symbol name."""
        symbol, scope, max_results = self._validate_request(symbol, path, max_results)
        matches: list[dict[str, Any]] = []

        for file_path, display_path in self._select_files(scope, exclude_patterns):
            source, root, language = self._parse_file(file_path)
            for identifier, kind in self._collect_declarations(root, source):
                if self._node_text(identifier, source) != symbol:
                    continue
                matches.append(
                    self._create_match(
                        identifier, source, display_path, language, symbol, kind
                    )
                )
                if len(matches) > max_results:
                    return self._result(
                        "definition", symbol, scope, matches[:max_results], True
                    )

        return self._result("definition", symbol, scope, matches, False)

    def find_references(
        self,
        symbol: str,
        path: str = ".",
        exclude_patterns: list[str] | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        include_definitions: bool = False,
    ) -> dict[str, Any]:
        """Return exact identifier usages, excluding recognized declarations by default."""
        symbol, scope, max_results = self._validate_request(symbol, path, max_results)
        matches: list[dict[str, Any]] = []

        for file_path, display_path in self._select_files(scope, exclude_patterns):
            source, root, language = self._parse_file(file_path)
            declarations = self._collect_declarations(root, source)
            declaration_ranges = {
                (node.start_byte, node.end_byte): kind for node, kind in declarations
            }

            for node in self._walk(root):
                if node.type not in self.IDENTIFIER_TYPES:
                    continue
                if self._node_text(node, source) != symbol:
                    continue
                node_range = (node.start_byte, node.end_byte)
                declaration_kind = declaration_ranges.get(node_range)
                if declaration_kind and not include_definitions:
                    continue
                kind = declaration_kind or "reference"
                matches.append(
                    self._create_match(
                        node, source, display_path, language, symbol, kind
                    )
                )
                if len(matches) > max_results:
                    return self._result(
                        "reference", symbol, scope, matches[:max_results], True
                    )

        return self._result("reference", symbol, scope, matches, False)

    def _validate_request(
        self, symbol: str, path: str, max_results: int
    ) -> tuple[str, str, int]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise SymbolLookupError("Symbol must be a non-empty string")
        if not isinstance(path, str) or not path.strip():
            raise SymbolLookupError("Path must be a non-empty string")
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise SymbolLookupError("max_results must be an integer")
        if max_results < 1 or max_results > self.MAX_RESULTS:
            raise SymbolLookupError(
                f"max_results must be between 1 and {self.MAX_RESULTS}"
            )

        scope = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(scope):
            raise SymbolLookupError(f"Path does not exist: {path}")
        if not os.path.isfile(scope) and not os.path.isdir(scope):
            raise SymbolLookupError(f"Path is not a file or directory: {path}")
        if not os.access(scope, os.R_OK):
            raise SymbolLookupError(f"Permission denied: Cannot read '{path}'")
        return symbol.strip(), scope, max_results

    def _select_files(
        self, scope: str, exclude_patterns: list[str] | None
    ) -> list[tuple[str, str]]:
        patterns = [*self.DEFAULT_EXCLUDE_PATTERNS, *(exclude_patterns or [])]
        if os.path.isfile(scope):
            language = self._runtime.detect_language_for_file(scope)
            if not language or not self._runtime.is_in_manifest(language):
                raise SymbolLookupError(f"Unsupported source file: {scope}")
            return [(scope, os.path.basename(scope))]

        relative_paths = self._git_files(scope)
        if relative_paths is None:
            relative_paths = []
            for root, directories, files in os.walk(scope):
                directories[:] = sorted(
                    directory
                    for directory in directories
                    if not self._is_excluded(
                        os.path.relpath(os.path.join(root, directory), scope) + "/",
                        patterns,
                    )
                )
                for filename in sorted(files):
                    relative_paths.append(
                        os.path.relpath(os.path.join(root, filename), scope)
                    )

        selected = []
        for relative_path in sorted(set(relative_paths)):
            normalized_path = relative_path.replace(os.sep, "/")
            if self._is_excluded(normalized_path, patterns):
                continue
            language = EXTENSION_TO_LANGUAGE.get(
                os.path.splitext(relative_path)[1].lower()
            )
            if not language or not self._runtime.is_in_manifest(language):
                continue
            selected.append((os.path.join(scope, relative_path), normalized_path))
        return selected

    @staticmethod
    def _git_files(scope: str) -> list[str] | None:
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=scope,
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return [line for line in result.stdout.splitlines() if line]

    @staticmethod
    def _is_excluded(path: str, patterns: list[str]) -> bool:
        normalized = path.replace(os.sep, "/")
        return any(
            fnmatch.fnmatch(normalized, pattern)
            or fnmatch.fnmatch(normalized.removesuffix("/"), pattern.removesuffix("/*"))
            for pattern in patterns
        )

    def _parse_file(self, file_path: str):
        try:
            with open(file_path, "rb") as source_file:
                source = source_file.read()
            language = self._runtime.detect_language_for_file(file_path)
            if not language:
                raise SymbolLookupError(f"Unsupported source file: {file_path}")
            root = self._runtime.get_parser(language).parse(source).root_node
            return source, root, language
        except (OSError, UnicodeError) as error:
            raise SymbolLookupError(f"Failed to read '{file_path}': {error}") from error

    def _collect_declarations(self, root, source: bytes) -> list[tuple[Any, str]]:
        declarations: dict[tuple[int, int], tuple[Any, str]] = {}
        for node in self._walk(root):
            kind = self._node_kind.get(node.type)
            if kind:
                for identifier in self._declaration_names(node):
                    declarations[(identifier.start_byte, identifier.end_byte)] = (
                        identifier,
                        kind,
                    )
            if node.type in self.ASSIGNMENT_TYPES:
                for identifier in self._assignment_names(node):
                    declarations[(identifier.start_byte, identifier.end_byte)] = (
                        identifier,
                        "variable",
                    )
            if node.type in self.PARAMETER_TYPES:
                name = node.child_by_field_name("name")
                if name and name.type in self.IDENTIFIER_TYPES:
                    declarations[(name.start_byte, name.end_byte)] = (name, "parameter")

            if node.type in {"parameters", "formal_parameters", "parameter_list"}:
                for child in node.named_children:
                    if child.type in self.IDENTIFIER_TYPES:
                        declarations[(child.start_byte, child.end_byte)] = (
                            child,
                            "parameter",
                        )

        return sorted(declarations.values(), key=lambda item: item[0].start_byte)

    def _declaration_names(self, node) -> list[Any]:
        for field in self.NAME_FIELDS:
            candidate = node.child_by_field_name(field)
            names = self._pattern_identifiers(candidate)
            if names:
                return names
        return []

    def _assignment_names(self, node) -> list[Any]:
        for field in self.ASSIGNMENT_FIELDS:
            names = self._pattern_identifiers(node.child_by_field_name(field))
            if names:
                return names
        if node.type == "let_declaration":
            return self._pattern_identifiers(node.child_by_field_name("pattern"))
        return []

    def _pattern_identifiers(self, node) -> list[Any]:
        if node is None:
            return []
        if node.type in self.IDENTIFIER_TYPES:
            return [node]
        if node.type in {
            "expression_list",
            "list_pattern",
            "pattern",
            "tuple_pattern",
        }:
            return [
                identifier
                for child in node.named_children
                for identifier in self._pattern_identifiers(child)
            ]
        return []

    @staticmethod
    def _walk(root):
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.named_children))

    @staticmethod
    def _node_text(node, source: bytes) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _create_match(
        self,
        node,
        source: bytes,
        path: str,
        language: str,
        symbol: str,
        kind: str,
    ) -> dict[str, Any]:
        lines = source.decode("utf-8", errors="replace").splitlines()
        line_index = node.start_point[0]
        snippet = lines[line_index].strip() if line_index < len(lines) else ""
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        return {
            "path": path,
            "language": language,
            "symbol": symbol,
            "kind": kind,
            "range": {
                "start": {
                    "line": node.start_point[0] + 1,
                    "column": node.start_point[1] + 1,
                },
                "end": {
                    "line": node.end_point[0] + 1,
                    "column": node.end_point[1] + 1,
                },
            },
            "snippet": snippet,
        }

    @staticmethod
    def _result(
        lookup: str,
        symbol: str,
        scope: str,
        matches: list[dict[str, Any]],
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "lookup": lookup,
            "symbol": symbol,
            "scope": scope,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
            "semantics": "syntax-based candidates; not type-aware or import-aware",
        }
