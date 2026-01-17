import os
import sys
from typing import Dict, Any, Callable
from loguru import logger

from .service import CodeAnalysisService
from .file_search_service import FileSearchService
from .grep_service import GrepTextService


# ============================================================================
# Code Analysis Tool
# ============================================================================


def get_code_analysis_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Return the tool definition for code analysis based on provider.

    Args:
        provider: The LLM provider ("claude", "groq", or "openai")

    Returns:
        Dict containing the tool definition
    """
    description = "Reads the structure of source code files within a repository, creating a structural map. This identifies key code elements, enabling code understanding and project organization insights."

    tool_arguments = {
        "path": {
            "type": "string",
            "description": "The root directory to read_repo. Use './' to read all source files in the current directory, or specify a subdirectory (e.g., 'src') to read files within that directory. Choose the path that will provide the most relevant information for the task at hand.",
        },
        "exclude_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": 'List of glob patterns to exclude certain files or directories from analysis. Always use double quotes " for array string. Example: ["tests/*", "*.md"]',
        },
    }
    tool_required = ["path"]

    if provider == "claude":
        return {
            "name": "read_repo",
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "openai"
        return {
            "type": "function",
            "function": {
                "name": "read_repo",
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

    def handler(**params):
        path = params.get("path", ".")
        path = os.path.expanduser(path)

        if not os.path.isabs(path):
            path = os.path.abspath(path)

        exclude_patterns = params.get("exclude_patterns", [])
        result = code_analysis_service.analyze_code_structure(path, exclude_patterns)
        if isinstance(result, dict) and "error" in result:
            raise Exception(f"Failed to analyze code: {result['error']}")

        return [
            {
                "type": "text",
                "text": result,
            },
            {
                "type": "text",
                "text": "Base on the code analysis, learn about the patterns and development flows, adapt project behaviors if possible for better response.",
            },
        ]

    return handler


# ============================================================================
# File Content Tool
# ============================================================================


def get_file_content_tool_definition(provider="claude"):
    """
    Return the tool definition for retrieving file content based on provider.

    Args:
        provider: The LLM provider ("claude", "groq", or "openai")

    Returns:
        Dict containing the tool definition
    """
    tool_description = "Gets the content of a file, or a specific lines within that file (function or class body). Use this to examine the logic of specific functions, the structure of classes, or the overall content of a file."

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
            "description": "Optional. The ending line number (1-indexed) to stop reading at (inclusive). If provided with start_line, only reads the specified line range.",
        },
    }
    tool_required = ["file_path"]

    if provider == "claude":
        return {
            "name": "get_file",
            "description": tool_description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "openai"
        return {
            "type": "function",
            "function": {
                "name": "get_file",
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

    def handler(**params) -> str:
        file_path = params.get("file_path", "./")
        start_line = params.get("start_line")
        end_line = params.get("end_line")

        if not file_path:
            raise Exception("File path is required")

        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        results = code_analysis_service.get_file_content(
            file_path, start_line=start_line, end_line=end_line
        )

        content = ""

        for path, code in results.items():
            content += f"{path}: {code}\n"

        return content

    return handler


# ============================================================================
# File Search Tool
# ============================================================================


def get_find_files_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Return the tool definition for file search based on provider.

    This function generates the appropriate tool schema for different LLM providers,
    enabling them to call the find_files functionality. The tool supports searching
    for files by pattern in a specified directory with optional result limiting and
    path type selection.

    Args:
        provider: The LLM provider ("claude" for Claude/Anthropic,
                 "openai" for OpenAI/Groq)

    Returns:
        Dict containing the tool definition in provider-specific format

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

    if provider == "claude":
        return {
            "name": "find_files",
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "openai" or "groq"
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

    def handler(**params):
        """
        Handle the find_files tool call from LLM.

        Args:
            **params: Tool parameters from LLM
                - pattern (str, required): File pattern to search for
                - directory (str, optional): Directory to search in
                - max_results (int, optional): Maximum results to return (default: None)

        Returns:
            List of dictionaries with "type" and "text" keys containing search results summary and details

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

        path_type = "absolute" if os.path.isabs(expanded_directory) else "relative"

        logger.info(
            f"find_files tool called with: pattern='{pattern}', directory='{directory}', "
            f"expanded_directory='{expanded_directory}', isabs={os.path.isabs(expanded_directory)}, "
            f"max_results={max_results}, path_type='{path_type}'"
        )

        file_search_result = service_instance.search_files(
            pattern=pattern,
            directory=expanded_directory,
            max_results=max_results,
            path_type=path_type,
        )

        return [
            {"type": "text", "text": file_search_result},
        ]

    return handler


# ============================================================================
# Grep Text Tool
# ============================================================================


def get_grep_text_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Return the tool definition for grep text search based on provider.

    Args:
        provider: The LLM provider ("claude" for Claude/Anthropic,
                 "openai" for OpenAI/Groq)

    Returns:
        Dict containing the tool definition in provider-specific format
    """
    description = "Searches for text patterns within files in a specified directory using grep-like functionality. "

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
        "directory": {
            "type": "string",
            "description": (
                "The directory path to search within. Use '.' for current directory, "
                "or specify a subdirectory path like 'src', 'tests', or 'lib/utils'."
            ),
            "default": ".",
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

    if provider == "claude":
        return {
            "name": "grep_text",
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        }
    else:  # provider == "openai" or "groq"
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

    def handler(**params):
        """
        Handle the grep_text tool call from LLM.

        Args:
            **params: Tool parameters from LLM
                - pattern (str, required): Regex pattern to search for
                - directory (str, required): Directory to search in (default: ".")
                - case_sensitive (bool, optional): Enable case sensitivity (default: True)
                - max_results (int, optional): Maximum results to return (default: None)

        Returns:
            List of dictionaries with "type" and "text" keys containing search results

        Raises:
            Exception: For parameter validation errors or search failures
        """
        pattern = params.get("pattern")
        directory = params.get("directory", ".")
        case_sensitive = params.get("case_sensitive", True)
        max_results = params.get("max_results", 50)

        # Validate required parameters
        if not pattern:
            error_msg = "Parameter 'pattern' is required but was not provided"
            logger.error(error_msg)
            raise Exception(error_msg)

        if not pattern.strip():
            error_msg = "Parameter 'pattern' cannot be empty or whitespace"
            logger.error(error_msg)
            raise Exception(error_msg)

        # Validate optional parameters
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

        # Expand user directory if present (e.g., ~/project -> /home/user/project)
        expanded_directory = os.path.expanduser(directory)

        logger.info(
            f"grep_text tool called with: pattern='{pattern}', directory='{expanded_directory}', "
            f"case_sensitive={case_sensitive}, max_results={max_results}"
        )

        try:
            # search_text now returns a formatted string
            result_text = service_instance.search_text(
                pattern=pattern,
                directory=expanded_directory,
                case_sensitive=case_sensitive,
                max_results=max_results,
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
# Registration
# ============================================================================


def register(service_instance=None, agent=None):
    """
    Register code analysis, file search, and grep text tools with the central registry or directly with an agent.

    This function registers all available tools from the code_analysis module:
    - read_repo: Analyze code structure and create structural maps
    - get_file: Retrieve file content or specific line ranges
    - find_files: Search for files by pattern
    - grep_text: Search for text patterns within file contents

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
