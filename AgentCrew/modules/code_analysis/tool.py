import os
import sys
from typing import Any, Callable
from loguru import logger

from .service import CodeAnalysisService
from .file_search_service import FileSearchService
from .grep_service import GrepTextService
from .symbol_lookup_service import SymbolLookupService


# ============================================================================
# Code Analysis Tool
# ============================================================================


def get_code_analysis_tool_definition() -> dict[str, Any]:
    """
    Return the tool definition for code analysis based on provider.

    Args:
        provider: The LLM provider ("claude", "openai", or another OpenAI-compatible provider)

    Returns:
        dict containing the tool definition
    """
    description = "Analyzes the structure of source code files within a repository, creating a structural map. This identifies key code elements, enabling code understanding and project organization insights. When deep_analysis is true, project notes (rules, conventions, patterns, flows) are extracted and included in the result. Use the project notes to adapt your behaviors accordingly - call learn_behavior with scope 'project' for key patterns you identify."

    tool_arguments = {
        "path": {
            "type": "string",
            "description": "The root directory to analyze_repo. Use '.' to analyze all source files in the current directory, or specify a subdirectory (e.g., 'src') to analyze files within that directory. Choose the path that will provide the most relevant information for the task at hand.",
            "default": ".",
        },
        "exclude_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": 'list of glob patterns to exclude certain files or directories from analysis. Always use double quotes " for array string. Example: ["tests/*", "*.md"]',
        },
        "feature_scope": {
            "type": "string",
            "description": "Optional focused feature scope used to prioritize the most relevant files during repository analysis. Example: 'authentication flow', 'delegate parallel execution', or 'browser automation console logs'.",
        },
        "deep_analysis": {
            "type": "boolean",
            "description": "When true, include project notes (rules, conventions, patterns, flows) extracted in the tool result. When false, skip project notes for faster analysis.",
            "default": True,
        },
    }
    tool_required = []

    return {
        "type": "function",
        "function": {
            "name": "analyze_repo",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_code_analysis_tool_handler(
    code_analysis_service: CodeAnalysisService,
) -> Callable:
    """Return the handler function for the code analysis tool."""

    async def handler(**params):
        path = params.get("path", ".")
        path = os.path.expanduser(path)

        if not os.path.isabs(path):
            path = os.path.abspath(path)

        exclude_patterns = params.get("exclude_patterns", [])
        feature_scope = params.get("feature_scope")
        deep_analysis = params.get("deep_analysis", True)

        result = await code_analysis_service.analyze_code_structure_cached(
            path=path,
            exclude_patterns=exclude_patterns,
            feature_scope=feature_scope,
            deep_analysis=deep_analysis,
        )

        error = result.get("error")
        if error:
            raise Exception(f"Failed to analyze code: {error}")

        output = [
            {
                "type": "text",
                "text": result["analysis_text"],
            },
        ]

        project_notes = result.get("project_notes")
        if project_notes:
            output.append(
                {
                    "type": "text",
                    "text": f"project notes:\n{project_notes}",
                }
            )

        return output

    return handler


# ============================================================================
# File Content Tool
# ============================================================================


def get_file_content_tool_definition():
    """
    Return the tool definition for retrieving file content based on provider.

    Args:
        provider: The LLM provider ("claude", "openai", or another OpenAI-compatible provider)

    Returns:
        dict containing the tool definition
    """
    tool_description = "Gets the content of a file, or a specific lines within that file (function or class body). Use this to examine the logic of specific functions, the structure of classes, or the overall content of a file. Also supports reading document files (PDF, DOCX, XLSX, PPTX, images) which will be converted to text/markdown - for document files, start_line and end_line parameters are ignored."

    tool_arguments = {
        "file_path": {
            "type": "string",
            "description": "The relative path from the current directory of the agent to the local repository file. Example: 'src/my_module.py'",
        },
        "start_line": {
            "type": "integer",
            "description": "Optional. The starting line number (1-indexed) to begin reading from. If provided with end_line, only reads the specified line range.",
        },
        "end_line": {
            "type": "integer",
            "description": "Optional. The ending line number (1-indexed) to stop reading at. If provided with start_line, only reads the specified line range.",
        },
    }
    tool_required = ["file_path"]

    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_file_content_tool_handler(
    code_analysis_service: CodeAnalysisService,
):
    """Returns a function that handles the get_file_content tool."""

    async def handler(**params):
        file_path = params.get("file_path", None)
        start_line = params.get("start_line")
        end_line = params.get("end_line")

        if not file_path:
            raise Exception("File path is required")

        file_path = os.path.expanduser(file_path)

        if not os.path.exists(file_path):
            raise ValueError(f"File {file_path} not found.")

        if not os.path.isabs(file_path):
            abs_file_path = os.path.abspath(file_path)
        else:
            abs_file_path = file_path

        _, file_content = await code_analysis_service.get_file_content(
            abs_file_path, start_line=start_line, end_line=end_line
        )

        if isinstance(file_content, dict) and file_content.get("type") == "image_url":
            return [
                {"type": "text", "text": f"Image file: {file_path}"},
                file_content,
            ]

        return f"{file_content}"

    return handler


# ============================================================================
# File Search Tool
# ============================================================================


def get_find_files_tool_definition() -> dict[str, Any]:
    """
    Return the tool definition for file search based on provider.

    This function generates the appropriate tool schema for different LLM providers,
    enabling them to call the find_files functionality. The tool supports searching
    for files by pattern in a specified directory with optional result limiting and
    path type selection.

    Args:
        provider: The LLM provider ("claude" for Claude/Anthropic,
                 "openai" for OpenAI-compatible providers)

    Returns:
        dict containing the tool definition in provider-specific format

    Example Claude Format:
        {
            "name": "find_files",
            "description": "...",
            "input_schema": {...}
        }

    Example OpenAI Format:
        {
            "type": "function",
            "function": {
                "name": "find_files",
                "description": "...",
                "parameters": {...}
            }
        }
    """
    description = (
        "Find files in a project folder that match the given pattern. "
        "Use this when you need to locate files by name, extension, or pattern within a directory structure."
    )

    tool_arguments = {
        "pattern": {
            "type": "string",
            "description": (
                "File pattern to search for using glob syntax. "
                "Examples: '*.py' (all Python files), 'test_*.txt' (test text files), "
                "'*.js' (JavaScript files), '**/*.md' (Markdown files in any subdirectory). "
                "Supports wildcards: * (any characters), ? (single character), ** (any subdirectories). "
                "Note: On Windows, only * and ? wildcards are supported and patterns cannot contain \\ or / path separators."
            ),
        },
        "directory": {
            "type": "string",
            "description": (
                "Directory to search in (required). Use '.' or './' for current directory, "
                "or specify a subdirectory path like 'src', 'tests', or 'lib/utils'. "
            ),
            "default": "./",
        },
        "max_results": {
            "type": "integer",
            "description": (
                "Maximum number of results to return. Use this to limit output when "
                "searching large directories. Examples: 10, 50, 100. "
                "Set to null or omit for unlimited results. Must be a positive integer."
            ),
        },
    }
    tool_required = ["pattern"]

    return {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_find_files_tool_handler(service_instance: FileSearchService) -> Callable:
    """
    Return the handler function for the find_files tool.

    This function creates a handler that wraps the FileSearchService and provides
    the interface for LLM tool calling. It extracts parameters, validates input,
    executes the file search, and formats results in a standardized way.

    Args:
        service_instance: The FileSearchService instance to use for searches

    Returns:
        Callable handler function that accepts **params and returns formatted results as list of dicts

    Result Format:
        [
            {
                "type": "text",
                "text": "Summary of search results"
            }
        ]
    """

    async def handler(**params):
        """
        Handle the find_files tool call from LLM.

        Args:
            **params: Tool parameters from LLM
                - pattern (str, required): File pattern to search for
                - directory (str, optional): Directory to search in
                - max_results (int, optional): Maximum results to return (default: None)

        Returns:
            list of dictionaries with "type" and "text" keys containing search results summary and details

        Raises:
            Exception: For validation errors or file search failures
        """
        pattern = params.get("pattern")
        directory = params.get("directory", "./")
        max_results = params.get("max_results")

        if not pattern:
            error_msg = "Parameter 'pattern' is required but was not provided"
            logger.error(error_msg)
            raise Exception(error_msg)

        if not pattern.strip():
            error_msg = "Parameter 'pattern' cannot be empty or whitespace"
            logger.error(error_msg)
            raise Exception(error_msg)

        is_windows = sys.platform == "win32"
        if is_windows:
            if "\\" in pattern or "/" in pattern:
                error_msg = "Parameter 'pattern' contains path separators ('\\' or '/') which are not supported on Windows."
                logger.error(error_msg)
                raise Exception(error_msg)

        if max_results is not None:
            if not isinstance(max_results, int):
                error_msg = f"Parameter 'max_results' must be an integer, got: {type(max_results).__name__}"
                logger.error(error_msg)
                raise Exception(error_msg)

            if max_results < 0:
                error_msg = (
                    f"Parameter 'max_results' must be non-negative, got: {max_results}"
                )
                logger.error(error_msg)
                raise Exception(error_msg)

        # Expand user directory if present (e.g., ~/project -> /home/user/project)
        expanded_directory = os.path.expanduser(directory)

        logger.info(
            f"find_files tool called with: pattern='{pattern}', directory='{directory}', "
            f"expanded_directory='{expanded_directory}', isabs={os.path.isabs(expanded_directory)}, "
            f"max_results={max_results}, path_type='relative'"
        )

        file_search_result = service_instance.search_files(
            pattern=pattern,
            directory=expanded_directory,
            max_results=max_results,
            path_type="relative",
        )

        return [
            {"type": "text", "text": file_search_result},
        ]

    return handler


# ============================================================================
# Grep Text Tool
# ============================================================================


def get_grep_text_tool_definition() -> dict[str, Any]:
    """
    Return the tool definition for grep text search based on provider.

    Args:
        provider: The LLM provider ("claude" for Claude/Anthropic,
                 "openai" for OpenAI-compatible providers)

    Returns:
        dict containing the tool definition in provider-specific format
    """
    description = "Searches for text patterns within files in specified file or directory paths using grep-like functionality. "

    tool_arguments = {
        "pattern": {
            "type": "string",
            "description": (
                "The regular expression pattern to search for. "
                "Supports standard regular expressions. "
                "Examples: 'TODO', 'def .*\\(' (regex for Python functions), "
                "'import .*' (regex for import statements), '^class ' (regex for class definitions at line start). "
            ),
        },
        "path": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Array of file or directory paths to search within. Use ['.'] for the current directory. "
                "Pass one or more paths as strings inside the array. "
            ),
            "default": ["."],
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "Boolean flag to control case sensitivity of the search.",
            "default": True,
        },
        "max_results": {
            "type": "integer",
            "description": (
                "Maximum number of matching lines to return. Use this to limit output when "
                "searching large codebases or patterns with many matches. "
                "Examples: 50, 100, 500. Set to null or omit for unlimited results. "
            ),
            "default": 50,
        },
    }
    tool_required = ["pattern"]

    return {
        "type": "function",
        "function": {
            "name": "grep_text",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_grep_text_tool_handler(service_instance: GrepTextService) -> Callable:
    """
    Return the handler function for the grep_text tool.

    Args:
        service_instance: The GrepTextService instance to use for searches

    Returns:
        Callable handler function that accepts **params and returns formatted results as list of dicts

    Raises:
        Exception: For validation errors or search execution failures
    """

    async def handler(**params):
        """
        Handle the grep_text tool call from LLM.

        Args:
            **params: Tool parameters from LLM
                - pattern (str, required): Regex pattern to search for
                - path (list[str], optional): File or directory paths to search in
                - case_sensitive (bool, optional): Enable case sensitivity (default: True)
                - max_results (int, optional): Maximum results to return (default: None)

        Returns:
            list of dictionaries with "type" and "text" keys containing search results

        Raises:
            Exception: For parameter validation errors or search failures
        """
        pattern = params.get("pattern")
        raw_path = params.get("path")
        fallback_path = params.get("directory", ".")
        case_sensitive = params.get("case_sensitive", True)
        max_results = params.get("max_results", 50)

        if raw_path is None:
            raw_path = [fallback_path]

        if not pattern:
            error_msg = "Parameter 'pattern' is required but was not provided"
            logger.error(error_msg)
            raise Exception(error_msg)

        if not pattern.strip():
            error_msg = "Parameter 'pattern' cannot be empty or whitespace"
            logger.error(error_msg)
            raise Exception(error_msg)

        if not isinstance(case_sensitive, bool):
            error_msg = f"Parameter 'case_sensitive' must be a boolean, got: {type(case_sensitive).__name__}"
            logger.error(error_msg)
            raise Exception(error_msg)

        if max_results is not None:
            if not isinstance(max_results, int):
                error_msg = f"Parameter 'max_results' must be an integer, got: {type(max_results).__name__}"
                logger.error(error_msg)
                raise Exception(error_msg)

            if max_results < 0:
                error_msg = (
                    f"Parameter 'max_results' must be non-negative, got: {max_results}"
                )
                logger.error(error_msg)
                raise Exception(error_msg)

        if isinstance(raw_path, str):
            expanded_path = [os.path.expanduser(raw_path)]
        elif isinstance(raw_path, list):
            expanded_path = []
            for index, item in enumerate(raw_path):
                if not isinstance(item, str):
                    error_msg = f"Parameter 'path' list items must be strings, got: {type(item).__name__} at index {index}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                expanded_path.append(os.path.expanduser(item))
        else:
            error_msg = f"Parameter 'path' must be an array of strings, got: {type(raw_path).__name__}"
            logger.error(error_msg)
            raise Exception(error_msg)

        logger.info(
            f"grep_text tool called with: pattern='{pattern}', path='{expanded_path}', "
            f"case_sensitive={case_sensitive}, max_results={max_results}"
        )

        try:
            result_text = service_instance.search_text(
                pattern=pattern,
                path=expanded_path,
                case_sensitive=case_sensitive,
                max_results=max_results,
                path_type="relative",
            )
        except Exception as e:
            error_msg = f"Text search failed: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

        return [
            {
                "type": "text",
                "text": result_text,
            }
        ]

    return handler


# ============================================================================
# Symbol Lookup Tools
# ============================================================================


def _get_symbol_lookup_parameters(include_definitions: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "symbol": {
            "type": "string",
            "description": "Exact symbol name to find.",
        },
        "path": {
            "type": "string",
            "description": "Source file or directory scope to search.",
            "default": ".",
        },
        "exclude_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional glob patterns to exclude when searching a directory.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": SymbolLookupService.MAX_RESULTS,
            "default": SymbolLookupService.DEFAULT_MAX_RESULTS,
            "description": "Maximum number of deterministic matches to return.",
        },
    }
    if include_definitions:
        properties["include_definitions"] = {
            "type": "boolean",
            "default": False,
            "description": "Include recognized declaration names in reference results.",
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["symbol"],
    }


def get_find_definition_tool_definition() -> dict[str, Any]:
    """Return the tool definition for syntax-based symbol definition lookup."""
    return {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": (
                "Find candidate definitions of an exact symbol within a source file or "
                "directory using Tree-sitter syntax. Results include paths, ranges, "
                "symbol kinds, and snippets. This is not type-aware or import-aware, "
                "so duplicate names can produce multiple candidates."
            ),
            "parameters": _get_symbol_lookup_parameters(),
        },
    }


def get_find_references_tool_definition() -> dict[str, Any]:
    """Return the tool definition for syntax-based symbol reference lookup."""
    return {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Find exact identifier usages within a source file or directory using "
                "Tree-sitter syntax. Recognized declarations are excluded by default. "
                "This is not type-aware or import-aware, so results are candidate "
                "references when names are duplicated or languages are dynamic."
            ),
            "parameters": _get_symbol_lookup_parameters(include_definitions=True),
        },
    }


def _format_symbol_lookup_markdown(result: dict[str, Any]) -> str:
    """Format a structured symbol lookup result for LLM consumption."""

    def code_span(value: Any) -> str:
        text = str(value).replace("\n", " ")
        longest_run = 0
        current_run = 0
        for character in text:
            if character == "`":
                current_run += 1
                longest_run = max(longest_run, current_run)
            else:
                current_run = 0
        fence = "`" * (longest_run + 1)
        padding = " " if text.startswith("`") or text.endswith("`") else ""
        return f"{fence}{padding}{text}{padding}{fence}"

    lookup = result["lookup"]
    title = "Definitions" if lookup == "definition" else "References"
    count = result["count"]
    lines = [
        f"## {title}: {code_span(result['symbol'])}",
        "",
        f"**Scope:** {code_span(result['scope'])} · **Matches:** {count}",
        "",
    ]

    if not result["matches"]:
        lines.append("No syntax-based candidates found.")
    else:
        for match in result["matches"]:
            start = match["range"]["start"]
            location = f"{match['path']}:{start['line']}:{start['column']}"
            lines.append(
                f"- {code_span(location)} — **{match['kind']}** ({match['language']})"
            )
            snippet_lines = str(match.get("snippet", "")).splitlines() or [""]
            lines.extend(["", *(f"      {line}" for line in snippet_lines), ""])

    if result["truncated"]:
        lines.extend(
            [
                "",
                f"**Truncated:** Showing the first {count} matches.",
            ]
        )

    lines.extend(
        [
            "",
            "> **Note:** Syntax-only candidates; results are not type-aware or import-aware.",
        ]
    )
    return "\n".join(lines).rstrip()


def get_find_definition_tool_handler(
    service_instance: SymbolLookupService,
) -> Callable:
    """Return the handler for syntax-based definition lookup."""

    async def handler(**params):
        result = service_instance.find_definitions(
            symbol=params.get("symbol", ""),
            path=params.get("path", "."),
            exclude_patterns=params.get("exclude_patterns"),
            max_results=params.get(
                "max_results", SymbolLookupService.DEFAULT_MAX_RESULTS
            ),
        )
        return [{"type": "text", "text": _format_symbol_lookup_markdown(result)}]

    return handler


def get_find_references_tool_handler(
    service_instance: SymbolLookupService,
) -> Callable:
    """Return the handler for syntax-based reference lookup."""

    async def handler(**params):
        result = service_instance.find_references(
            symbol=params.get("symbol", ""),
            path=params.get("path", "."),
            exclude_patterns=params.get("exclude_patterns"),
            max_results=params.get(
                "max_results", SymbolLookupService.DEFAULT_MAX_RESULTS
            ),
            include_definitions=params.get("include_definitions", False),
        )
        return [{"type": "text", "text": _format_symbol_lookup_markdown(result)}]

    return handler


# ============================================================================
# Registration
# ============================================================================


def register(service_instance=None, agent=None):
    """
    Register code analysis, file search, and grep text tools with the central registry or directly with an agent.

    This function registers all available tools from the code_analysis module:
    - analyze_repo: Analyze code structure and create structural maps
    - read_file: Retrieve file content or specific line ranges
    - find_files: Search for files by pattern
    - grep_text: Search for text patterns within file contents
    - find_definition: Find syntax-based symbol definition candidates
    - find_references: Find syntax-based symbol reference candidates

    Args:
        service_instance: The code analysis service instance (optional, for backward compatibility)
        agent: Agent instance to register with directly (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    # Register code analysis tools
    register_tool(
        get_code_analysis_tool_definition,
        get_code_analysis_tool_handler,
        service_instance,
        agent,
    )

    register_tool(
        get_file_content_tool_definition,
        get_file_content_tool_handler,
        service_instance,
        agent,
    )

    # Register file search tool
    file_search_service = FileSearchService.get_instance()
    register_tool(
        get_find_files_tool_definition,
        get_find_files_tool_handler,
        file_search_service,
        agent,
    )

    # Register grep text search tool
    grep_text_service = GrepTextService.get_instance()
    register_tool(
        get_grep_text_tool_definition,
        get_grep_text_tool_handler,
        grep_text_service,
        agent,
    )

    symbol_lookup_service = SymbolLookupService.get_instance()
    register_tool(
        get_find_definition_tool_definition,
        get_find_definition_tool_handler,
        symbol_lookup_service,
        agent,
    )
    register_tool(
        get_find_references_tool_definition,
        get_find_references_tool_handler,
        symbol_lookup_service,
        agent,
    )
