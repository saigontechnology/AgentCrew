import os
from typing import Dict, Any, Callable
from .service import CodeAnalysisService


# Tool definition function
def get_code_analysis_tool_definition(provider="claude") -> Dict[str, Any]:
    """
    Return the tool definition for code analysis based on provider.

    Args:
        provider: The LLM provider ("claude", "groq", or "openai")

    Returns:
        Dict containing the tool definition
    """
    description = "Analyzes the structure of source code files within a repository, creating a structural map. This identifies key code elements, enabling code understanding and project organization insights. Explain what insights you are hoping to gain from analyzing the repository before using this tool."

    tool_arguments = {
        "path": {
            "type": "string",
            "description": "The root directory to analyze. Use './' to analyze all source files in the current directory, or specify a subdirectory (e.g., 'src') to analyze files within that directory. Choose the path that will provide the most relevant information for the task at hand.",
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
            "name": "analyze_repo",
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
                "name": "analyze_repo",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": tool_arguments,
                    "required": tool_required,
                },
            },
        }


# Tool handler function
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

        return result

    return handler


def get_file_content_tool_definition(provider="claude"):
    """
    Return the tool definition for retrieving file content based on provider.

    Args:
        provider: The LLM provider ("claude", "groq", or "openai")

    Returns:
        Dict containing the tool definition
    """
    tool_description = "Reads the content of a file, or a specific lines within that file (function or class body). Use this to examine the logic of specific functions, the structure of classes, or the overall content of a file."

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
            "name": "read_file",
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


def register(service_instance=None, agent=None):
    """
    Register this tool with the central registry or directly with an agent

    Args:
        service_instance: The code analysis service instance
        agent: Agent instance to register with directly (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

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
