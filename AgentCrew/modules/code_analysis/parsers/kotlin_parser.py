"""
Kotlin language parser for code analysis.
"""

from typing import Any

from .base import BaseLanguageParser


class KotlinParser(BaseLanguageParser):
    """Parser for Kotlin source code."""

    @property
    def language_name(self) -> str:
        return "kotlin"

    def process_node(
        self, node, source_code: bytes, process_children_callback
    ) -> dict[str, Any] | None:
        result = self._create_base_result(node)

        if node.type == "class_declaration":
            for child in node.children:
                if child.type in ["simple_identifier", "type_identifier"]:
                    result["name"] = self.extract_node_text(child, source_code)
                elif child.type == "class_body":
                    children = []
                    for body_child in child.children:
                        child_result = process_children_callback(body_child)
                        if child_result and self._is_significant_node(child_result):
                            children.append(child_result)
                    if children:
                        result["children"] = children
            return result

        elif node.type == "function_declaration":
            for child in node.children:
                if child.type == "simple_identifier":
                    result["name"] = self.extract_node_text(child, source_code)
                    return result
            return result

        elif node.type in ["property_declaration", "variable_declaration"]:
            return self._handle_property_declaration(node, source_code, result)

        children = []
        for child in node.children:
            child_result = process_children_callback(child)
            if child_result and self._is_significant_node(child_result):
                children.append(child_result)

        if children:
            result["children"] = children

        return result

    def _handle_property_declaration(
        self, node, source_code: bytes, result: dict[str, Any]
    ) -> dict[str, Any]:
        prop_name = None
        prop_type = None

        for child in node.children:
            if child.type == "variable_declaration":
                for subchild in child.children:
                    if subchild.type == "simple_identifier" and prop_name is None:
                        prop_name = self.extract_node_text(subchild, source_code)
                    elif subchild.type == "user_type" or subchild.type in [
                        "nullable_type",
                        "type_identifier",
                    ]:
                        prop_type = self.extract_node_text(subchild, source_code)
            elif child.type == "simple_identifier" and prop_name is None:
                prop_name = self.extract_node_text(child, source_code)
            elif child.type == "user_type":
                prop_type = self.extract_node_text(child, source_code)

        if prop_name:
            result["type"] = "property_declaration"
            if prop_type:
                result["name"] = f"{prop_name}: {prop_type}"
            else:
                result["name"] = prop_name

        return result
