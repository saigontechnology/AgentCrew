"""
C# language parser for code analysis.
"""

from typing import Any

from .base import BaseLanguageParser


class CSharpParser(BaseLanguageParser):
    """Parser for C# source code."""

    @property
    def language_name(self) -> str:
        return "csharp"

    def process_node(
        self, node, source_code: bytes, process_children_callback
    ) -> dict[str, Any] | None:
        result = self._create_base_result(node)

        if node.type == "class_declaration":
            self._handle_class_declaration(node, source_code, result)

        elif node.type == "method_declaration":
            self._handle_method_declaration(node, source_code, result)

        elif node.type == "property_declaration":
            self._handle_property_declaration(node, source_code, result)

        elif node.type == "field_declaration":
            self._handle_field_declaration(node, source_code, result)

        children = []
        for child in node.children:
            child_result = process_children_callback(child)
            if child_result and self._is_significant_node(child_result):
                children.append(child_result)

        if children:
            result["children"] = children

        return result

    def _handle_class_declaration(
        self, node, source_code: bytes, result: dict[str, Any]
    ) -> None:
        for child in node.children:
            if child.type == "identifier":
                result["name"] = self.extract_node_text(child, source_code)
            elif child.type == "base_list":
                if len(child.children) > 1:
                    result["base_class"] = self.extract_node_text(
                        child.children[1], source_code
                    )

    def _handle_method_declaration(
        self, node, source_code: bytes, result: dict[str, Any]
    ) -> None:
        method_name = None
        parameters = []
        access_modifiers = []

        for child in node.children:
            if child.type == "identifier":
                method_name = self.extract_node_text(child, source_code)
                result["name"] = method_name
            elif child.type == "parameter_list":
                for param in child.children:
                    if param.type == "parameter":
                        param_type = ""
                        param_name = None

                        type_node = param.child_by_field_name("type")
                        name_node = param.child_by_field_name("name")

                        if type_node:
                            param_type = self.extract_node_text(type_node, source_code)
                        if name_node:
                            param_name = self.extract_node_text(name_node, source_code)

                        if param_name:
                            parameters.append(param_type + " " + param_name)

                if parameters:
                    result["parameters"] = parameters
            elif child.type == "modifier":
                modifier = self.extract_node_text(child, source_code)
                access_modifiers.append(modifier)

        if access_modifiers:
            result["modifiers"] = access_modifiers

    def _handle_property_declaration(
        self, node, source_code: bytes, result: dict[str, Any]
    ) -> None:
        property_name = None
        property_type = None
        modifiers = []

        for child in node.children:
            if child.type == "modifier":
                modifiers.append(self.extract_node_text(child, source_code))
            elif child.type in [
                "predefined_type",
                "nullable_type",
                "generic_name",
                "array_type",
            ]:
                property_type = self.extract_node_text(child, source_code)
            elif child.type == "identifier":
                if property_type is None:
                    property_type = self.extract_node_text(child, source_code)
                else:
                    property_name = self.extract_node_text(child, source_code)

        if property_name:
            result["type"] = "property_declaration"
            if property_type:
                result["name"] = f"{property_type} {property_name}"
            else:
                result["name"] = property_name
            if modifiers:
                result["modifiers"] = modifiers

    def _handle_field_declaration(
        self, node, source_code: bytes, result: dict[str, Any]
    ) -> None:
        field_name = None
        field_type = None
        modifiers = []

        for child in node.children:
            if child.type == "modifier":
                modifiers.append(self.extract_node_text(child, source_code))
            elif child.type == "variable_declaration":
                for subchild in child.children:
                    if (
                        subchild.type
                        in [
                            "predefined_type",
                            "nullable_type",
                            "generic_name",
                            "array_type",
                        ]
                        or subchild.type == "identifier"
                        and field_type is None
                    ):
                        field_type = self.extract_node_text(subchild, source_code)
                    elif subchild.type == "variable_declarator":
                        for var_child in subchild.children:
                            if var_child.type == "identifier":
                                field_name = self.extract_node_text(
                                    var_child, source_code
                                )
                                break

        if field_name:
            result["type"] = "field_declaration"
            if field_type:
                result["name"] = f"{field_type} {field_name}"
            else:
                result["name"] = field_name
            if modifiers:
                result["modifiers"] = modifiers
