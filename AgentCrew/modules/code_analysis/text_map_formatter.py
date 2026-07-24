from typing import Any

MAX_ITEMS_OUT = 40


class TextMapFormatter:
    """Formats code analysis results into a hierarchical text map."""

    def generate_text_map(self, analysis_results: list[dict[str, Any]]) -> str:
        """Generate a hierarchical text representation of the code structure analysis."""

        sorted_results = sorted(analysis_results, key=lambda x: x["path"])

        results_by_path = {result["path"]: result for result in sorted_results}

        tree: dict[str, Any] = {}
        for result in sorted_results:
            path = result["path"].replace("\\", "/")
            parts = path.split("/")
            current = tree
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    current[part] = {
                        "__is_file__": True,
                        "__path__": result["path"],
                    }
                else:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

        output_lines = []

        def format_tree(node: dict[str, Any], indent: str = "") -> None:
            items = sorted(node.keys())
            for name in items:
                child = node[name]
                if isinstance(child, dict) and child.get("__is_file__"):
                    output_lines.append(f"{indent}{name}")
                    file_path = child["__path__"]
                    if file_path in results_by_path:
                        file_content = self._get_file_code_content(
                            results_by_path[file_path], indent
                        )
                        output_lines.extend(file_content)
                elif isinstance(child, dict):
                    output_lines.append(f"{indent}{name}/")
                    format_tree(child, indent + "  ")

        format_tree(tree)

        return (
            "\n".join(output_lines)
            if output_lines
            else "No significant code structure found."
        )

    def _format_node(
        self, node: dict[str, Any], prefix: str = "", is_last: bool = True
    ) -> list[str]:
        lines = []

        node_type = node.get("type", "")
        node_name = node.get("name", "")
        node_lines = f" #L: {node.get('start_line', '')}-{node.get('end_line', '')}"

        if node_type == "decorated_definition" and "children" in node:
            for child in node.get("children", []):
                if child.get("type") in {
                    "function_definition",
                    "method_definition",
                    "member_function_definition",
                }:
                    return self._format_node(child, prefix, is_last)

        if not node_name and node_type in {
            "class_body",
            "block",
            "declaration_list",
            "body",
            "namespace_declaration",
            "lexical_declaration",
            "variable_declarator",
        }:
            return self._process_children(node.get("children", []), prefix, is_last)
        elif not node_name:
            return lines

        branch = "  "
        if node_type in {
            "class_definition",
            "class_declaration",
            "class_specifier",
            "class",
            "interface_declaration",
            "struct_specifier",
            "struct_declaration",
            "struct_item",
            "trait_item",
            "trait_declaration",
            "module",
            "type_declaration",
        }:
            node_info = f"class {node_name}{node_lines}"
        elif node_type in {
            "function_definition",
            "function_declaration",
            "method_definition",
            "method_declaration",
            "fn_item",
            "method",
            "singleton_method",
            "constructor_declaration",
            "member_function_definition",
            "constructor",
            "destructor",
            "public_method_definition",
            "private_method_definition",
            "protected_method_definition",
            "arrow_function",
            "lexical_declaration",
        }:
            if "first_line" in node:
                node_info = node["first_line"] + node_lines
            else:
                params = []
                modfilers = ""
                if node.get("parameters"):
                    params = node["parameters"]
                elif "children" in node:
                    for child in node["children"]:
                        if child.get("type") in {
                            "parameter_list",
                            "parameters",
                            "formal_parameters",
                            "argument_list",
                        }:
                            for param in child.get("children", []):
                                if param.get("type") in {"identifier", "parameter"}:
                                    param_name = param.get("name", "")
                                    if param_name:
                                        params.append(param_name)

                params_str = ", ".join(params) if params else ""
                params_str = params_str.replace("\n", "")
                if "modifiers" in node:
                    modfilers = " ".join(node["modifiers"]) + " "
                node_info = f"{modfilers}{node_name}({params_str}){node_lines}"
        else:
            if "first_line" in node:
                node_info = node["first_line"]
            else:
                node_info = node_name

        if len(node_info) > 300:
            node_info = node_info[:297] + "... (REDACTED due to long content)"

        lines.append(f"{prefix}{branch}{node_info}")

        if "children" in node:
            new_prefix = prefix + "  "
            child_lines = self._process_children(node["children"], new_prefix, is_last)
            if child_lines:
                lines.extend(child_lines)

        return lines

    def _process_children(
        self, children: list[dict], prefix: str, is_last: bool
    ) -> list[str]:
        if not children:
            return []

        lines = []
        significant_children = [
            child
            for child in children
            if child.get("type")
            in {
                "arrow_function",
                "call_expression",
                "lexical_declaration",
                "decorated_definition",
                "class_definition",
                "class_declaration",
                "class_specifier",
                "class",
                "interface_declaration",
                "struct_specifier",
                "struct_declaration",
                "struct_item",
                "trait_item",
                "trait_declaration",
                "module",
                "type_declaration",
                "impl_item",
                "function_definition",
                "function_declaration",
                "method_definition",
                "method_declaration",
                "fn_item",
                "method",
                "singleton_method",
                "constructor_declaration",
                "member_function_definition",
                "constructor",
                "destructor",
                "public_method_definition",
                "private_method_definition",
                "protected_method_definition",
                "class_body",
                "block",
                "declaration_list",
                "body",
                "impl_block",
                "property_declaration",
                "field_declaration",
                "variable_declaration",
                "const_declaration",
            }
        ]

        for i, child in enumerate(significant_children):
            is_last_child = i == len(significant_children) - 1
            child_lines = self._format_node(child, prefix, is_last_child)
            if child_lines:
                lines.extend(child_lines)
            if i >= MAX_ITEMS_OUT:
                lines.append(
                    f"{prefix}  ...({len(significant_children) - MAX_ITEMS_OUT} more items)"
                )
                break

        return lines

    def _get_file_code_content(
        self, result: dict[str, Any], file_indent: str
    ) -> list[str]:
        """Generate code structure content for a single file."""
        lines = []
        structure = result.get("structure")
        if not structure:
            return lines

        if not structure.get("children"):
            if structure.get("type"):
                return [f"{file_indent}  {structure['type']}"]
            return lines

        significant_nodes = [
            child
            for child in structure["children"]
            if child.get("type")
            in {
                "arrow_function",
                "lexical_declaration",
                "call_expression",
                "decorated_definition",
                "class_definition",
                "class_declaration",
                "class_specifier",
                "class",
                "interface_declaration",
                "struct_specifier",
                "struct_declaration",
                "struct_item",
                "trait_item",
                "trait_declaration",
                "module",
                "type_declaration",
                "impl_item",
                "function_definition",
                "function_declaration",
                "method_definition",
                "method_declaration",
                "fn_item",
                "method",
                "singleton_method",
                "constructor_declaration",
                "member_function_definition",
                "constructor",
                "destructor",
                "public_method_definition",
                "private_method_definition",
                "protected_method_definition",
                "property_declaration",
                "field_declaration",
                "variable_declaration",
                "const_declaration",
                "namespace_declaration",
            }
        ]

        for i, node in enumerate(significant_nodes):
            is_last = i == len(significant_nodes) - 1
            node_lines = self._format_node(node, file_indent, is_last)
            if node_lines:
                lines.extend(node_lines)
            if i >= MAX_ITEMS_OUT:
                lines.append(
                    f"{file_indent}  ...({len(significant_nodes) - MAX_ITEMS_OUT} more items)"
                )
                break
        return lines
