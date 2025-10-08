import os
import fnmatch
import subprocess
from typing import Any, Dict, List, Optional
from tree_sitter_language_pack import get_parser
from tree_sitter import Parser


class CodeAnalysisService:
    """Service for analyzing code structure using tree-sitter."""

    # Map of file extensions to language names
    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".cc": "cpp",
        ".hh": "cpp",
        ".cxx": "cpp",
        ".hxx": "cpp",
        ".rb": "ruby",
        ".rake": "ruby",
        ".go": "go",
        ".rs": "rust",
        ".php": "php",
        ".cs": "c-sharp",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".json": "config",
        ".toml": "config",
        ".yaml": "config",
        # Add more languages as needed
    }

    def __init__(self):
        """Initialize the code analysis service with tree-sitter parsers."""
        try:
            self._parser_cache = {
                "python": get_parser("python"),
                "javascript": get_parser("javascript"),
                "typescript": get_parser("typescript"),
                "java": get_parser("java"),
                "cpp": get_parser("cpp"),
                "ruby": get_parser("ruby"),
                "go": get_parser("go"),
                "rust": get_parser("rust"),
                "php": get_parser("php"),
                "c-sharp": get_parser("csharp"),
                "kotlin": get_parser("kotlin"),
            }
            # Define node types for different categories
            self.class_types = {
                "class_definition",
                "class_declaration",
                "class_specifier",
                "struct_specifier",
                "struct_item",
                "interface_declaration",
                "object_declaration",  # Kotlin object declarations
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
                "primary_constructor",  # Kotlin primary constructors
            }
        except Exception as e:
            raise RuntimeError(f"Failed to initialize languages: {e}")

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language based on file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        return self.LANGUAGE_MAP.get(ext, "unknown")

    def _get_language_parser(self, language: str) -> Parser:
        """Get the appropriate tree-sitter parser for a language."""
        if language not in self._parser_cache:
            raise ValueError(f"Unsupported language: {language}")
        return self._parser_cache[language]

    def _extract_node_text(self, node, source_code: bytes) -> str:
        """Extract text from a node."""
        return source_code[node.start_byte : node.end_byte].decode("utf-8")

    def _analyze_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Analyze a single file using tree-sitter."""
        try:
            with open(file_path, "rb") as f:
                source_code = f.read()

            language = self._detect_language(file_path)
            if language == "unknown":
                return {
                    "error": f"Unsupported file type: {os.path.splitext(file_path)[1]}"
                }

            parser = self._get_language_parser(language)
            if isinstance(parser, dict) and "error" in parser:
                return parser

            tree = parser.parse(source_code)
            root_node = tree.root_node

            # Check if we got a valid root node
            if not root_node:
                return {"error": "Failed to parse file - no root node"}

            def process_node(node) -> Optional[Dict[str, Any]]:
                if not node:
                    return None

                result = {
                    "type": node.type,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                }

                # Process child nodes based on language-specific patterns
                if language == "python":
                    if node.type in ["class_definition", "function_definition"]:
                        for child in node.children:
                            if child.type == "identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                            elif child.type == "parameters":
                                params = []
                                for param in child.children:
                                    if (
                                        "parameter" in param.type
                                        or param.type == "identifier"
                                    ):
                                        params.append(
                                            self._extract_node_text(param, source_code)
                                        )
                                if params:
                                    result["parameters"] = params
                    elif node.type == "assignment":
                        # Handle global variable assignments
                        for child in node.children:
                            if child.type == "identifier":
                                result["type"] = "variable_declaration"
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                            # Break after first identifier to avoid capturing right-hand side
                            break
                elif language == "javascript" or language == "typescript":
                    if (
                        node.type
                        in [
                            "class_declaration",
                            "method_definition",
                            "class",
                            "method_declaration",
                            "function_declaration",
                            "interface_declaration",
                            "export_statement",  # Handle exported items
                            "arrow_function",  # Add support for arrow functions
                            "lexical_declaration",  # Add support for const/let declarations with arrow functions
                        ]
                    ):
                        # Handle export statements by looking at their children
                        if node.type == "export_statement":
                            # Process the declaration that's being exported
                            for child in node.children:
                                if child.type in [
                                    "class_declaration",
                                    "function_declaration",
                                    "interface_declaration",
                                    "variable_statement",
                                    "lexical_declaration",
                                    "method_definition",
                                ]:
                                    # Recursively process the exported declaration
                                    exported_result = process_node(child)

                                    if exported_result:
                                        # Mark as exported
                                        exported_result["exported"] = True
                                        # Return the exported item's result
                                        return exported_result

                        # Handle arrow functions - extract name from parent variable declarator
                        elif node.type == "arrow_function":
                            parent = node.parent
                            if parent and parent.type == "variable_declarator":
                                for sibling in parent.children:
                                    if sibling.type == "identifier":
                                        result["type"] = "arrow_function"
                                        result["name"] = self._extract_node_text(
                                            sibling, source_code
                                        )

                            # Process arrow function parameters
                            for child in node.children:
                                if child.type == "formal_parameters":
                                    params = []
                                    for param in child.children:
                                        if param.type in [
                                            "required_parameter",
                                            "optional_parameter",
                                            "identifier",
                                        ]:
                                            param_text = self._extract_node_text(
                                                param, source_code
                                            )
                                            params.append(param_text)

                                    if params:
                                        result["parameters"] = params

                        # Handle lexical declarations with arrow functions (const/let)
                        elif node.type == "lexical_declaration":
                            for child in node.children:
                                if child.type == "variable_declarator":
                                    # Find the identifier (name)
                                    var_name = None
                                    has_arrow_function = False
                                    for declarator_child in child.children:
                                        if declarator_child.type == "identifier":
                                            var_name = self._extract_node_text(
                                                declarator_child, source_code
                                            )
                                        elif declarator_child.type == "arrow_function":
                                            has_arrow_function = True

                                    if var_name and has_arrow_function:
                                        result["type"] = "arrow_function"
                                        result["name"] = var_name
                                        # Recursively process the arrow function to get parameters
                                        for declarator_child in child.children:
                                            if (
                                                declarator_child.type
                                                == "arrow_function"
                                            ):
                                                arrow_result = process_node(
                                                    declarator_child
                                                )
                                                if (
                                                    arrow_result
                                                    and "parameters" in arrow_result
                                                ):
                                                    result["parameters"] = arrow_result[
                                                        "parameters"
                                                    ]
                                    else:
                                        result["type"] = "variable_declaration"
                                        result["name"] = var_name
                                        result["first_line"] = (
                                            self._extract_node_text(node, source_code)
                                            .split("\n")[0]
                                            .strip("{")
                                        )

                        # Handle regular declarations
                        elif node.type in [
                            "class",
                            "class_declaration",
                            "function_declaration",
                            "method_declaration",
                            "interface_declaration",
                            "method_definition",
                        ]:
                            for child in node.children:
                                if (
                                    child.type == "identifier"
                                    or child.type == "type_identifier"
                                    or child.type == "property_identifier"
                                ):
                                    result["name"] = self._extract_node_text(
                                        child, source_code
                                    )
                                # Process function parameters for function declarations
                                elif (
                                    child.type == "formal_parameters"
                                    and node.type
                                    in [
                                        "function_declaration",
                                        "method_declaration",
                                        "method_definition",
                                    ]
                                ):
                                    params = []
                                    for param in child.children:
                                        if param.type in [
                                            "required_parameter",
                                            "optional_parameter",
                                            "identifier",
                                        ]:
                                            param_name = None
                                            param_type = None

                                            # For simple identifiers
                                            if param.type == "identifier":
                                                param_name = self._extract_node_text(
                                                    param, source_code
                                                )
                                                params.append(param_name)
                                                continue

                                            # For parameters with type annotations
                                            for param_child in param.children:
                                                if (
                                                    param_child.type == "identifier"
                                                    or param_child.type
                                                    == "object_pattern"
                                                ):
                                                    param_name = (
                                                        self._extract_node_text(
                                                            param_child, source_code
                                                        )
                                                    )
                                                elif (
                                                    param_child.type
                                                    == "type_annotation"
                                                ):
                                                    # Extract the type from type annotation
                                                    for (
                                                        type_child
                                                    ) in param_child.children:
                                                        if (
                                                            type_child.type != ":"
                                                        ):  # Skip the colon
                                                            param_type = (
                                                                self._extract_node_text(
                                                                    type_child,
                                                                    source_code,
                                                                )
                                                            )

                                            if param_name:
                                                if param_type:
                                                    params.append(
                                                        f"{param_name}: {param_type}"
                                                    )
                                                else:
                                                    params.append(param_name)

                                    if params:
                                        result["parameters"] = params

                    elif node.type in [
                        "variable_statement",
                        "property_declaration",
                        "variable_declaration",
                    ]:
                        # Handle variable declarations and property declarations
                        for child in node.children:
                            if child.type == "variable_declaration_list":
                                for declarator in child.children:
                                    if declarator.type == "variable_declarator":
                                        var_name = None
                                        has_arrow_function = False

                                        for declarator_child in declarator.children:
                                            if declarator_child.type == "identifier":
                                                var_name = self._extract_node_text(
                                                    declarator_child, source_code
                                                )
                                            elif (
                                                declarator_child.type
                                                == "arrow_function"
                                            ):
                                                has_arrow_function = True

                                        if var_name:
                                            if has_arrow_function:
                                                result["type"] = "arrow_function"
                                                result["name"] = var_name
                                                # Find parameters
                                                for (
                                                    declarator_child
                                                ) in declarator.children:
                                                    if (
                                                        declarator_child.type
                                                        == "arrow_function"
                                                    ):
                                                        arrow_result = process_node(
                                                            declarator_child
                                                        )
                                                        if (
                                                            arrow_result
                                                            and "parameters"
                                                            in arrow_result
                                                        ):
                                                            result["parameters"] = (
                                                                arrow_result[
                                                                    "parameters"
                                                                ]
                                                            )
                                            else:
                                                result["type"] = "variable_declaration"
                                                result["name"] = var_name

                                            return result
                            elif child.type == "identifier":
                                result["type"] = "variable_declaration"
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result

                elif language == "java":
                    if node.type in ["class_declaration", "interface_declaration"]:
                        # Handle class and interface declarations
                        for child in node.children:
                            if child.type == "identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                            elif child.type in ["class_body", "interface_body"]:
                                result["children"] = [
                                    process_node(c) for c in child.children
                                ]

                    elif node.type == "method_declaration":
                        # Handle method declarations
                        method_name = None
                        parameters = []
                        return_type = None

                        for child in node.children:
                            if child.type == "identifier":
                                method_name = self._extract_node_text(
                                    child, source_code
                                )
                                result["name"] = method_name
                            elif child.type == "formal_parameters":
                                for param in child.children:
                                    if param.type == "parameter":
                                        param_name = self._extract_node_text(
                                            param.child_by_field_name("name"),
                                            source_code,
                                        )
                                        param_type = self._extract_node_text(
                                            param.child_by_field_name("type"),
                                            source_code,
                                        )
                                        parameters.append(f"{param_type} {param_name}")
                                result["parameters"] = parameters
                            elif child.type == "type":
                                return_type = self._extract_node_text(
                                    child, source_code
                                )
                                result["return_type"] = return_type

                    elif node.type == "field_declaration":
                        # Handle field declarations
                        for child in node.children:
                            if child.type == "variable_declarator":
                                var_name = self._extract_node_text(
                                    child.child_by_field_name("name"), source_code
                                )
                                var_type = self._extract_node_text(
                                    child.child_by_field_name("type"), source_code
                                )
                                result["name"] = var_name
                                result["variable_type"] = var_type
                                result["type"] = "field_declaration"

                    elif node.type == "annotation":
                        # Handle annotations
                        annotation_name = self._extract_node_text(node, source_code)
                        result["name"] = annotation_name
                        result["type"] = "annotation"

                    elif node.type == "lambda_expression":
                        # Handle lambda expressions
                        result["type"] = "lambda_expression"
                        # Additional processing for lambda parameters and body can be added here

                    # Recursively process children for nested classes or other constructs
                    children = [process_node(child) for child in node.children]
                    if children:
                        result["children"] = children

                    return result

                elif language == "cpp":
                    if node.type in [
                        "class_specifier",
                        "function_definition",
                        "struct_specifier",
                    ]:
                        for child in node.children:
                            if child.type == "identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result
                    elif node.type in ["declaration", "variable_declaration"]:
                        # Handle C++ global variables and declarations
                        for child in node.children:
                            if (
                                child.type == "init_declarator"
                                or child.type == "declarator"
                            ):
                                for subchild in child.children:
                                    if subchild.type == "identifier":
                                        result["type"] = "variable_declaration"
                                        result["name"] = self._extract_node_text(
                                            subchild, source_code
                                        )
                                        return result
                        return result

                elif language == "ruby":
                    if node.type in ["class", "method", "singleton_method", "module"]:
                        for child in node.children:
                            if child.type == "identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result
                    elif node.type == "assignment" or node.type == "global_variable":
                        # Handle Ruby global variables and assignments
                        for child in node.children:
                            if (
                                child.type == "identifier"
                                or child.type == "global_variable"
                            ):
                                result["type"] = "variable_declaration"
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result

                elif language == "go":
                    if node.type in [
                        "type_declaration",
                        "function_declaration",
                        "method_declaration",
                        "interface_declaration",
                    ]:
                        for child in node.children:
                            if (
                                child.type == "identifier"
                                or child.type == "field_identifier"
                            ):
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                result["first_line"] = (
                                    self._extract_node_text(node, source_code)
                                    .split("\n")[0]
                                    .strip("{")
                                )
                                return result
                        return result
                    elif (
                        node.type == "var_declaration"
                        or node.type == "const_declaration"
                    ):
                        # Handle Go variable and constant declarations
                        for child in node.children:
                            if child.type == "var_spec" or child.type == "const_spec":
                                for subchild in child.children:
                                    if subchild.type == "identifier":
                                        result["type"] = "variable_declaration"
                                        result["name"] = self._extract_node_text(
                                            subchild, source_code
                                        )
                                        return result
                        return result

                elif language == "rust":
                    if node.type in [
                        "struct_item",
                        "impl_item",
                        "fn_item",
                        "trait_item",
                    ]:
                        for child in node.children:
                            if child.type == "identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result
                    elif node.type in ["static_item", "const_item", "let_declaration"]:
                        # Handle Rust static items, constants, and let declarations
                        for child in node.children:
                            if child.type == "identifier":
                                result["type"] = "variable_declaration"
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                            elif child.type == "pattern" and child.children:
                                result["name"] = self._extract_node_text(
                                    child.children[0], source_code
                                )
                        return result

                elif language == "php":
                    if node.type in [
                        "class_declaration",
                        "method_declaration",
                        "function_definition",
                        "interface_declaration",
                        "trait_declaration",
                    ]:
                        for child in node.children:
                            if child.type == "name":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result
                    elif (
                        node.type == "property_declaration"
                        or node.type == "const_declaration"
                    ):
                        # Handle PHP class properties and constants
                        for child in node.children:
                            if (
                                child.type == "property_element"
                                or child.type == "const_element"
                            ):
                                for subchild in child.children:
                                    if (
                                        subchild.type == "variable_name"
                                        or subchild.type == "name"
                                    ):
                                        result["type"] = "variable_declaration"
                                        result["name"] = self._extract_node_text(
                                            subchild, source_code
                                        )
                        return result

                elif language == "c-sharp":
                    if node.type == "class_declaration":
                        # Create a more comprehensive class result
                        class_name = None
                        base_class_name = None

                        # Extract class name and base class name
                        for child in node.children:
                            if child.type == "identifier":
                                class_name = self._extract_node_text(child, source_code)
                                result["name"] = class_name
                            elif child.type == "base_list":
                                # Extract base class if present
                                if (
                                    len(child.children) > 1
                                ):  # Check if there's a base class
                                    base_class_name = self._extract_node_text(
                                        child.children[1], source_code
                                    )
                                    result["base_class"] = base_class_name

                        # DO NOT return early here to ensure methods are processed

                    elif node.type == "method_declaration":
                        method_name = None
                        parameters = []
                        access_modifiers = []

                        for child in node.children:
                            if child.type == "identifier":
                                method_name = self._extract_node_text(
                                    child, source_code
                                )
                                result["name"] = method_name
                            elif child.type == "parameter_list":
                                # Extract parameter information
                                for param in child.children:
                                    if param.type == "parameter":
                                        param_type = ""
                                        param_name = None

                                        # Get type and name fields from parameter
                                        type_node = param.child_by_field_name("type")
                                        name_node = param.child_by_field_name("name")

                                        if type_node:
                                            param_type = self._extract_node_text(
                                                type_node, source_code
                                            )
                                        if name_node:
                                            param_name = self._extract_node_text(
                                                name_node, source_code
                                            )

                                        if param_name:
                                            parameters.append(
                                                param_type + " " + param_name
                                            )

                                # Add parameters to result
                                if parameters:
                                    result["parameters"] = parameters
                            elif child.type == "modifier":
                                # Capture access modifiers
                                modifier = self._extract_node_text(child, source_code)
                                access_modifiers.append(modifier)

                        # Add access modifiers to result
                        if access_modifiers:
                            result["modifiers"] = access_modifiers

                        # DO NOT return early here

                    elif node.type in ["property_declaration", "field_declaration"]:
                        # Improved handling for properties and fields
                        property_name = None
                        property_type = None

                        for child in node.children:
                            if child.type == "variable_declaration":
                                for subchild in child.children:
                                    if subchild.type == "identifier":
                                        result["type"] = "variable_declaration"
                                        result["name"] = self._extract_node_text(
                                            subchild, source_code
                                        )
                                    # Look for the type of the variable
                                    elif subchild.type == "predefined_type" or (
                                        subchild.type == "identifier"
                                        and subchild != child
                                    ):
                                        result["variable_type"] = (
                                            self._extract_node_text(
                                                subchild, source_code
                                            )
                                        )
                            # Check for property name directly in property_declaration
                            elif child.type == "identifier":
                                property_name = self._extract_node_text(
                                    child, source_code
                                )
                                result["name"] = property_name
                                result["type"] = "property_declaration"
                            # Check for property type
                            elif child.type == "predefined_type" or (
                                child.type == "identifier" and child != property_name
                            ):
                                if (
                                    not property_name
                                    or self._extract_node_text(child, source_code)
                                    != property_name
                                ):
                                    property_type = self._extract_node_text(
                                        child, source_code
                                    )
                                    result["property_type"] = property_type

                elif language == "kotlin":
                    if node.type in ["class_declaration", "function_declaration"]:
                        for child in node.children:
                            if child.type == "simple_identifier":
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                        return result
                    elif node.type in ["property_declaration", "variable_declaration"]:
                        # Handle Kotlin properties and variables
                        for child in node.children:
                            if child.type == "simple_identifier":
                                result["type"] = "variable_declaration"
                                result["name"] = self._extract_node_text(
                                    child, source_code
                                )
                                return result
                            break  # Only capture the first identifier
                        return result

                # Recursively process children
                children = []
                # if file_path.endswith("models/wishlist.js"):
                #     print(f"{file_path} {language}")
                #     print(
                #         f"{node.type} ({self._extract_node_text(node, source_code) if node.type == 'identifier' else ''})"
                #     )
                #     print(self._extract_node_text(node, source_code))
                #     print("=============")
                for child in node.children:
                    child_result = process_node(child)
                    if child_result and (
                        child_result.get("type")
                        in [
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
                            "class",
                            "method",
                            "singleton_method",
                            "module",
                            "type_declaration",
                            "method_declaration",
                            "interface_declaration",
                            "struct_item",
                            "impl_item",
                            "fn_item",
                            "trait_item",
                            "trait_declaration",
                            "property_declaration",
                            "object_definition",
                            "trait_definition",
                            "def_definition",
                            "function_definition",
                            "class_definition",
                            "variable_declaration",
                            "arrow_function",
                        ]
                        or "children" in child_result
                    ):
                        children.append(child_result)

                if children:
                    result["children"] = children
                return result

            return process_node(root_node)

        except Exception as e:
            return {"error": f"Error analyzing file: {str(e)}"}

    def _count_nodes(self, structure: Dict[str, Any], node_types: set[str]) -> int:
        """Recursively count nodes of specific types in the tree structure."""
        count = 0

        # Count current node if it matches
        if structure.get("type") in node_types:
            count += 1

        # Recursively count in children
        for child in structure.get("children", []):
            count += self._count_nodes(child, node_types)

        return count

    def analyze_code_structure(
        self, path: str, exclude_patterns: List[str] = []
    ) -> Dict[str, Any] | str:
        """
        Build a tree-sitter based structural map of source code files in a git repository.

        Args:
            path: Root directory to analyze (must be a git repository)

        Returns:
            Dictionary containing analysis results for each file or formatted string
        """
        try:
            # Verify the path exists
            if not os.path.exists(path):
                return {"error": f"Path does not exist: {path}"}

            # Run git ls-files to get all tracked files
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

            # Filter for supported file types
            supported_files = []
            for file_path in files:
                excluded = False
                if file_path.strip():  # Skip empty lines
                    # Check against glob exclude patterns
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(file_path, pattern):
                            excluded = True
                            break
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in self.LANGUAGE_MAP and not excluded:
                        supported_files.append(os.path.join(path, file_path))

            # Analyze each file
            analysis_results = []
            errors = []
            for file_path in supported_files:
                rel_path = os.path.relpath(file_path, path)
                try:
                    language = self._detect_language(file_path)

                    if language == "config":
                        # Skip problematic file
                        if os.path.basename(file_path) == "package-lock.json":
                            continue
                        result = {"type": "config", "name": os.path.basename(file_path)}
                    else:
                        result = self._analyze_file(file_path)

                    if result and isinstance(result, dict) and "error" not in result:
                        # Successfully analyzed file
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
                analysis_results, supported_files, errors
            )

        except Exception as e:
            return {"error": f"Error analyzing directory: {str(e)}"}

    def _generate_text_map(self, analysis_results: List[Dict[str, Any]]) -> str:
        """Generate a compact text representation of the code structure analysis."""

        def format_node(
            node: Dict[str, Any], prefix: str = "", is_last: bool = True
        ) -> List[str]:
            lines = []

            node_type = node.get("type", "")
            node_name = node.get("name", "")
            node_lines = f"(L:{node.get('start_line', '')}-{node.get('end_line', '')})"

            # Handle decorated functions - extract the actual function definition
            if node_type == "decorated_definition" and "children" in node:
                for child in node.get("children", []):
                    if child.get("type") in {
                        "function_definition",
                        "method_definition",
                        "member_function_definition",
                    }:
                        return format_node(child, prefix, is_last)

            # Handle class body, block nodes, and wrapper functions
            if not node_name and node_type in {
                "class_body",
                "block",
                "declaration_list",
                "body",
                "namespace_declaration",
                "lexical_declaration",
                "variable_declarator",
            }:
                return process_children(node.get("children", []), prefix, is_last)
            elif not node_name:
                return lines

            branch = "└── " if is_last else "├── "
            # Format node information based on type
            if node_type in {
                "class_definition",
                "class_declaration",
                "class_specifier",
                "class",
                "interface_declaration",
                "struct_specifier",
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
                # Handle parameters
                if "first_line" in node:
                    node_info = node["first_line"] + node_lines
                else:
                    params = []
                    modfilers = ""
                    if "parameters" in node and node["parameters"]:
                        params = node["parameters"]
                    elif "children" in node:
                        # Try to extract parameters from children for languages that structure them differently
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

            if len(node_info) > 500:
                node_info = node_info[:497] + "(REDACTED due to long content)..."

            lines.append(f"{prefix}{branch}{node_info}")

            # Process children
            if "children" in node:
                new_prefix = prefix + ("    " if is_last else "│   ")
                child_lines = process_children(node["children"], new_prefix, is_last)
                if child_lines:  # Only add child lines if there are any
                    lines.extend(child_lines)

            return lines

        def process_children(
            children: List[Dict], prefix: str, is_last: bool
        ) -> List[str]:
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
                    # Class-related nodes
                    "class_definition",
                    "class_declaration",
                    "class_specifier",
                    "class",
                    "interface_declaration",
                    "struct_specifier",
                    "struct_item",
                    "trait_item",
                    "trait_declaration",
                    "module",
                    "type_declaration",
                    "impl_item",  # Rust implementations
                    # Method-related nodes
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
                    # Container nodes that might have methods
                    "class_body",
                    "block",
                    "declaration_list",
                    "body",
                    "impl_block",  # Rust implementation blocks
                    # Property and field nodes
                    "property_declaration",
                    "field_declaration",
                    "variable_declaration",
                    "const_declaration",
                }
            ]

            for i, child in enumerate(significant_children):
                is_last_child = i == len(significant_children) - 1
                child_lines = format_node(child, prefix, is_last_child)
                if child_lines:  # Only add child lines if there are any
                    lines.extend(child_lines)

            return lines

        # Process each file
        output_lines = []

        # Sort analysis results by path
        sorted_results = sorted(analysis_results, key=lambda x: x["path"])

        for result in sorted_results:
            # Skip files with no significant structure
            if not result.get("structure") or not result.get("structure", {}).get(
                "children"
            ):
                if not result.get("structure"):
                    output_lines.append(
                        f"\n{result['path']}: {result['structure']['type']}"
                    )
                    continue

            # Add file header
            output_lines.append(f"\n{result['path']}")
            # Format the structure
            structure = result["structure"]
            if "children" in structure:
                significant_nodes = [
                    child
                    for child in structure["children"]
                    if child.get("type")
                    in {
                        "arrow_function",
                        "lexical_declaration",
                        "call_expression",
                        "decorated_definition",
                        # Class-related nodes
                        "class_definition",
                        "class_declaration",
                        "class_specifier",
                        "class",
                        "interface_declaration",
                        "struct_specifier",
                        "struct_item",
                        "trait_item",
                        "trait_declaration",
                        "module",
                        "type_declaration",
                        "impl_item",  # Rust implementations
                        # Method-related nodes
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
                        # Property and field nodes
                        "property_declaration",
                        "field_declaration",
                        "variable_declaration",
                        "const_declaration",
                        "namespace_declaration",
                    }
                ]

                for i, node in enumerate(significant_nodes):
                    is_last = i == len(significant_nodes) - 1
                    node_lines = format_node(node, "", is_last)
                    if node_lines:  # Only add node lines if there are any
                        output_lines.extend(node_lines)
                    # else:
                    #     output_lines.append(
                    #         self.get_file_content(result["path"]).get("file")
                    #     )
                    #
        # Return the formatted text
        return (
            "\n".join(output_lines)
            if output_lines
            else "No significant code structure found."
        )

    def get_file_content(
        self,
        file_path,
        start_line=None,
        end_line=None,
    ) -> Dict[str, str]:
        """
        Return the content of a file, optionally reading only a specific line range.

        Args:
            file_path: Path to the file to read
            start_line: Optional starting line number (1-indexed)
            end_line: Optional ending line number (1-indexed, inclusive)

        Returns:
            Dictionary with file content (key: "file", value: file content string)
        """
        # Read the whole file
        with open(file_path, "rb") as file:
            content = file.read()

        decoded_content = content.decode("utf-8")

        # If line range is specified, extract those lines
        if start_line is not None and end_line is not None:
            # Validate line range
            if start_line < 1:
                raise ValueError("start_line must be >= 1")
            if end_line < start_line:
                raise ValueError("end_line must be >= start_line")

            lines = decoded_content.split("\n")
            total_lines = len(lines)

            # Validate bounds
            if start_line > total_lines:
                raise ValueError(
                    f"start_line {start_line} exceeds file length ({total_lines} lines)"
                )
            if end_line > total_lines:
                raise ValueError(
                    f"end_line {end_line} exceeds file length ({total_lines} lines)"
                )

            # Extract the line range (convert to 0-indexed)
            selected_lines = lines[start_line - 1 : end_line]
            return {"file": "\n".join(selected_lines)}

        # Return the whole file
        return {"file": decoded_content}

    def _format_analysis_results(
        self,
        analysis_results: List[Dict[str, Any]],
        analyzed_files: List[str],
        errors: List[Dict[str, str]],
    ) -> str:
        """Format the analysis results into a clear text format."""

        # Count statistics
        total_files = len(analyzed_files)
        classes = sum(
            self._count_nodes(f["structure"], self.class_types)
            for f in analysis_results
        )
        functions = sum(
            self._count_nodes(f["structure"], self.function_types)
            for f in analysis_results
        )
        decorated_functions = sum(
            self._count_nodes(f["structure"], {"decorated_definition"})
            for f in analysis_results
        )
        error_count = len(errors)

        # Build output sections
        sections = []

        # Add statistics section
        sections.append("\n===ANALYSIS STATISTICS===\n")
        sections.append(f"Total files analyzed: {total_files}")
        sections.append(f"Total errors: {error_count}")
        sections.append(f"Total classes found: {classes}")
        sections.append(f"Total functions found: {functions}")
        sections.append(f"Total decorated functions: {decorated_functions}")

        # Add errors section if any
        if errors:
            sections.append("\n===ERRORS===")
            for error in errors:
                error_first_line = error["error"].split("\n")[0]
                sections.append(f"{error['path']}: {error_first_line}")

        # Add repository map
        sections.append("\n===REPOSITORY STRUCTURE===")
        sections.append(self._generate_text_map(analysis_results))

        # Join all sections with newlines
        return "\n".join(sections)
