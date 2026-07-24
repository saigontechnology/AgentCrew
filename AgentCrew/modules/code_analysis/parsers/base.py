"""
Base class for language-specific parsers.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseLanguageParser(ABC):
    """Abstract base class for language-specific code parsers."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Return the language name for this parser."""

    @staticmethod
    def extract_node_text(node, source_code: bytes) -> str:
        """Extract text from a tree-sitter node."""
        return source_code[node.start_byte : node.end_byte].decode("utf-8")

    @abstractmethod
    def process_node(
        self, node, source_code: bytes, process_children_callback
    ) -> dict[str, Any] | None:
        """
        Process a tree-sitter node and extract relevant information.

        Args:
            node: The tree-sitter node to process
            source_code: The source code as bytes
            process_children_callback: Callback to process children nodes recursively

        Returns:
            Dictionary with node information or None if not relevant
        """

    def _create_base_result(self, node) -> dict[str, Any]:
        """Create the base result dictionary with common fields."""
        return {
            "type": node.type,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        }

    def _process_children_default(
        self, node, source_code: bytes, process_children_callback
    ) -> list[dict[str, Any]]:
        """Default implementation for processing children nodes."""
        children = []
        for child in node.children:
            child_result = process_children_callback(child)
            if child_result and self._is_significant_node(child_result):
                children.append(child_result)
        return children

    def _is_significant_node(self, result: dict[str, Any]) -> bool:
        """Check if a node result is significant and should be included."""
        significant_types = {
            "class_definition",
            "function_definition",
            "class_declaration",
            "method_definition",
            "function_declaration",
            "interface_declaration",
            "method_declaration",
            "constructor_declaration",
            "class_specifier",
            "struct_specifier",
            "struct_declaration",
            "class",
            "method",
            "singleton_method",
            "module",
            "type_declaration",
            "struct_item",
            "impl_item",
            "fn_item",
            "trait_item",
            "trait_declaration",
            "property_declaration",
            "field_declaration",
            "public_field_definition",
            "const_declaration",
            "object_definition",
            "trait_definition",
            "def_definition",
            "variable_declaration",
            "arrow_function",
        }
        return result.get("type") in significant_types or "children" in result
