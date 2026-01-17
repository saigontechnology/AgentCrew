import os
import re
import sys
import shutil
from typing import Any, Dict, List, Optional
from loguru import logger

from AgentCrew.modules.command_execution.service import CommandExecutionService


class GrepTextError(Exception):
    """
    Custom exception for grep text search errors.
    """

    pass


class GrepTextService:
    """
    Text pattern search service with cross-platform grep tool support.

    Supported tools by platform:
    - Linux/macOS: ripgrep (preferred), grep
    - Windows: ripgrep (preferred), Select-String (PowerShell)
    """

    _instance = None

    # Tool priority by platform (best to fallback)
    # git-grep is inserted dynamically if directory is a git repo
    TOOL_PRIORITY_UNIX = ["rg", "git-grep", "grep"]
    TOOL_PRIORITY_WINDOWS = ["rg", "git-grep", "Select-String"]

    # Default search parameters
    DEFAULT_MAX_RESULTS = 100
    DEFAULT_TIMEOUT = 30

    @classmethod
    def get_instance(cls):
        """
        Get singleton instance of GrepTextService.

        Returns:
            GrepTextService: Singleton instance
        """
        if cls._instance is None:
            cls._instance = GrepTextService()

        return cls._instance

    def __init__(self) -> None:
        """
        Initialize the GrepTextService with platform detection.
        """
        self.platform = sys.platform
        self._is_windows = self.platform == "win32"
        self._tool_availability_cache: Dict[str, bool] = {}
        self._git_repo_cache: Dict[str, bool] = {}

    def _get_tool_priority(self) -> List[str]:
        """
        Get the tool priority list based on the current platform.

        Returns:
            List[str]: Ordered list of tools to try, from best to worst
        """
        if self._is_windows:
            return self.TOOL_PRIORITY_WINDOWS.copy()
        else:
            return self.TOOL_PRIORITY_UNIX.copy()

    def _is_tool_available(self, tool_name: str) -> bool:
        """
        Check if a specific grep tool is available on the system.

        Args:
            tool_name: Name of the tool to check (e.g., "rg", "grep", "git-grep", "Select-String")

        Returns:
            bool: True if the tool is available, False otherwise
        """
        # Return cached result if available
        if tool_name in self._tool_availability_cache:
            return self._tool_availability_cache[tool_name]

        is_available = False

        try:
            cmd_service = CommandExecutionService.get_instance()
            if self._is_windows:
                if tool_name == "Select-String":
                    # Check if PowerShell is available
                    result = cmd_service.execute_command(
                        'powershell -Command "Get-Command Select-String"',
                        timeout=5,
                    )
                    is_available = (
                        result.get("exit_code") == 0
                        and result.get("status") == "completed"
                    )
                elif tool_name == "git-grep":
                    # Check if git is available (git grep is part of git)
                    result = cmd_service.execute_command(
                        "git --version",
                        timeout=5,
                    )
                    is_available = (
                        result.get("exit_code") == 0
                        and result.get("status") == "completed"
                    )
                else:
                    # Check using 'where' command
                    result = cmd_service.execute_command(
                        f"where {tool_name}",
                        timeout=5,
                    )
                    is_available = (
                        result.get("exit_code") == 0
                        and result.get("status") == "completed"
                    )
            else:
                if tool_name == "git-grep":
                    # Check if git is available (git grep is part of git)
                    is_available = shutil.which("git") is not None
                else:
                    is_available = shutil.which(tool_name) is not None

        except Exception as e:
            logger.warning(f"Error checking availability of tool '{tool_name}': {e}")
            is_available = False

        # Cache the result
        self._tool_availability_cache[tool_name] = is_available

        logger.debug(f"Tool '{tool_name}' availability: {is_available}")

        return is_available

    def _validate_pattern(self, pattern: str) -> str:
        """
        Validate the search pattern.

        Args:
            pattern: The regex search pattern to validate

        Returns:
            str: Validated pattern ready for use

        Raises:
            GrepTextError: If the pattern is invalid (empty or malformed regex)
        """
        if not pattern or not pattern.strip():
            error_msg = "Search pattern cannot be empty"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        try:
            re.compile(pattern)
            logger.debug(f"Regex pattern validated: {pattern}")
            return pattern
        except re.error as e:
            error_msg = f"Invalid regex pattern '{pattern}': {e}"
            logger.error(error_msg)
            raise GrepTextError(error_msg) from e

    def _validate_directory(self, directory: str) -> str:
        """
        Validate that the directory path is valid and accessible.

        Args:
            directory: Directory path to validate

        Returns:
            str: Absolute path of the validated directory

        Raises:
            GrepTextError: If directory is invalid or inaccessible
        """
        if not directory or not directory.strip():
            error_msg = "Directory path cannot be empty"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.path.exists(directory):
            error_msg = f"Directory does not exist: {directory}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.path.isdir(directory):
            error_msg = f"Path is not a directory: {directory}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.access(directory, os.R_OK):
            error_msg = f"Permission denied: Cannot read directory '{directory}'"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        # Convert to absolute path for consistency
        abs_directory = os.path.abspath(directory)
        logger.debug(f"Directory validated: {abs_directory}")

        return abs_directory

    def _is_git_repository(self, directory: str) -> bool:
        """
        Check if the given directory belongs to a git repository.

        Args:
            directory: Directory path to check

        Returns:
            bool: True if directory is within a git repository, False otherwise
        """
        # Return cached result if available
        if directory in self._git_repo_cache:
            return self._git_repo_cache[directory]

        is_git_repo = False

        try:
            # Check if .git directory exists in current or parent directories
            # Use git rev-parse --git-dir command which is cross-platform
            cmd_service = CommandExecutionService.get_instance()

            if self._is_windows:
                command = f'cd /d "{directory}" && git rev-parse --git-dir'
            else:
                command = f"cd '{directory}' && git rev-parse --git-dir"

            result = cmd_service.execute_command(command, timeout=5)

            is_git_repo = (
                result.get("exit_code") == 0 and result.get("status") == "completed"
            )

            logger.debug(f"Git repository check for '{directory}': {is_git_repo}")

        except Exception as e:
            logger.debug(f"Error checking git repository for '{directory}': {e}")
            is_git_repo = False

        # Cache the result
        self._git_repo_cache[directory] = is_git_repo

        return is_git_repo

    def _build_grep_command(
        self,
        pattern: str,
        directory: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build grep command for Unix systems.

        Args:
            pattern: Search pattern (already validated regex)
            directory: Directory to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        # Escape pattern for shell safety
        escaped_pattern = pattern.replace("'", "'\\''")

        # grep command building (always use extended regex)
        cmd_parts = [
            "grep",
            "-r",  # Recursive
            "-n",  # Line numbers
            "-H",  # Always show filename
            "-E",  # Extended regex
        ]

        if not case_sensitive:
            cmd_parts.append("-i")

        cmd_parts.append(f"'{escaped_pattern}'")
        cmd_parts.append(f"'{directory}'")

        command = " ".join(cmd_parts)
        logger.debug(f"Built grep command: {command}")
        return command

    def _build_git_grep_command(
        self,
        pattern: str,
        directory: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build git grep command for searching within git repositories.

        Args:
            pattern: Search pattern (already validated regex)
            directory: Directory to search in (must be within a git repo)
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        # Escape pattern for shell safety
        if self._is_windows:
            # Windows/CMD escaping
            escaped_pattern = pattern.replace('"', '""')
            escaped_directory = directory.replace('"', '""')

            # git grep command building
            cmd_parts = [
                f'cd /d "{escaped_directory}" &&',
                "git",
                "grep",
                "-n",  # Show line numbers
                "--full-name",  # Show full paths relative to repo root
                "-E",  # Extended regex
            ]

            if not case_sensitive:
                cmd_parts.append("-i")

            cmd_parts.append(f'"{escaped_pattern}"')

            command = " ".join(cmd_parts)
        else:
            # Unix escaping
            escaped_pattern = pattern.replace("'", "'\\''")
            escaped_directory = directory.replace("'", "'\\''")

            # git grep command building
            cmd_parts = [
                f"cd '{escaped_directory}' &&",
                "git",
                "grep",
                "-n",  # Show line numbers
                "--full-name",  # Show full paths relative to repo root
                "-E",  # Extended regex
            ]

            if not case_sensitive:
                cmd_parts.append("-i")

            cmd_parts.append(f"'{escaped_pattern}'")

            command = " ".join(cmd_parts)

        logger.debug(f"Built git grep command: {command}")
        return command

    def _build_rg_command(
        self,
        pattern: str,
        directory: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build ripgrep (rg) command for Unix systems.

        Args:
            pattern: Search pattern (already validated regex)
            directory: Directory to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        # Escape pattern for shell safety
        escaped_pattern = pattern.replace("'", "'\\''")

        # ripgrep command building
        cmd_parts = [
            "rg",
            "--line-number",  # Show line numbers
            "--no-heading",  # Don't group by file
            "--with-filename",  # Always show filename
            "--hidden",  # Search hidden files
        ]

        if not case_sensitive:
            cmd_parts.append("--ignore-case")

        cmd_parts.append(f"'{escaped_pattern}'")
        cmd_parts.append(f"'{directory}'")

        command = " ".join(cmd_parts)
        logger.debug(f"Built rg command: {command}")
        return command

    def _build_windows_command(
        self,
        pattern: str,
        directory: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build PowerShell Select-String command for Windows.

        Args:
            pattern: Search pattern
            directory: Directory to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        # PowerShell Select-String command (regex mode always enabled)
        # Escape for PowerShell string literals
        escaped_directory = directory.replace("'", "''")
        escaped_pattern = pattern.replace("'", "''").replace('"', '`"')

        ps_parts = [
            f'Get-ChildItem -Path "{escaped_directory}" -Recurse -File',
            "|",
            f'Select-String -Pattern "{escaped_pattern}"',
        ]

        # Select-String is case-insensitive by default, use -CaseSensitive for sensitive
        if case_sensitive:
            ps_parts.append("-CaseSensitive")

        # Format output as "filename:line_number:line_content"
        ps_parts.append('| ForEach-Object { "$($_.Path):$($_.LineNumber):$($_.Line)" }')

        ps_command = " ".join(ps_parts)
        command = f"powershell -Command '{ps_command}'"

        logger.debug(f"Built window command: {command}")
        return command

    def _execute_command(
        self, command: str, timeout: int = DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        """
        Execute the grep command using CommandExecutionService.

        Args:
            command: Command string to execute
            timeout: Timeout in seconds (default: 30)

        Returns:
            Dict[str, Any]: Command execution result with status, output, error, exit_code

        Raises:
            GrepTextError: If command execution fails
        """
        cmd_service = CommandExecutionService.get_instance()

        logger.debug(f"Executing command with timeout={timeout}s: {command}")

        try:
            result = cmd_service.execute_command(command, timeout=timeout)

            status = result.get("status")
            exit_code = result.get("exit_code")

            logger.debug(
                f"Command execution result: status={status}, exit_code={exit_code}"
            )

            # Note: grep tools return exit code 1 when no matches found (not an error)
            # Only consider it a real error if there's stderr output or other issues
            if status == "completed":
                return result
            else:
                error = result.get("error", "Unknown error")
                error_msg = f"Command execution failed: {error}"
                logger.error(error_msg)
                raise GrepTextError(error_msg)

        except Exception as e:
            error_msg = f"Error executing command: {e}"
            logger.error(error_msg)
            raise GrepTextError(error_msg) from e

    def _parse_output(
        self,
        output: str,
        max_results: Optional[int] = None,
    ) -> str:
        """
        Parse grep tool output into formatted string result.

        Args:
            output: Raw command output
            max_results: Maximum number of results to return

        Returns:
            str: Formatted search results with structure:
                Found <number of matches> match(es).
                **<path/to/file1>:**
                - <line number>: <line content>
                - <line number>: <line content>
                **<path/to/file2>:**
                ...
        """
        if not output or not output.strip():
            # No matches found (not an error)
            logger.debug("No matches found in output")
            return "Found 0 matches."

        # Parse, sort, and format in a single pass
        matches = []
        lines = output.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse the line: "filename:line_number:line_content"
            parts = line.split(":", 2)  # Split into max 3 parts

            if len(parts) < 3:
                logger.debug(f"Skipping malformed line: {line}")
                continue

            file_path = parts[0].strip()
            line_number_str = parts[1].strip()
            line_content = parts[2]  # Don't strip content, preserve whitespace

            # Validate line number
            try:
                line_number = int(line_number_str)
            except ValueError:
                logger.debug(f"Skipping line with invalid line number: {line}")
                continue

            # Normalize file path
            if self._is_windows:
                file_path = file_path.replace("/", "\\")
            else:
                file_path = file_path.replace("\\", "/")

            matches.append((file_path, line_number, line_content))

            # Check if we've reached max_results
            if max_results and len(matches) >= max_results:
                logger.debug(f"Reached max_results limit: {max_results}")
                break

        # Sort matches by file path and line number
        matches.sort(key=lambda x: (x[0], x[1]))

        total_matches = len(matches)
        logger.info(f"Parsed {total_matches} matches")

        result_lines = [f"Found {total_matches} match(es)."]
        current_file = None

        for file_path, line_number, line_content in matches:
            # Add file header when file changes
            if file_path != current_file:
                result_lines.append(f"**{file_path}:**")
                current_file = file_path

            # Add match line
            result_lines.append(f"- {line_number}: {line_content}")

        return "\n".join(result_lines)

    def search_text(
        self,
        pattern: str,
        directory: str,
        case_sensitive: bool = True,
        max_results: Optional[int] = None,
    ) -> str:
        """
        Search for text patterns within files in the specified directory.

        Args:
            pattern: The regular expression pattern to search for.
            directory: The directory path to search within. Use './' for current directory.
                      Must be a valid, readable directory path.
            case_sensitive: Boolean flag to control case sensitivity of the search.
                           Default: True (case-sensitive)
            max_results: Maximum number of results to return. None for unlimited.
                        Default: None (return all matches)

        Returns:
            str: Formatted search results with structure:
                Found <number of matches> match(es).
                **<path/to/file1>:**
                - <line number>: <line content>
                - <line number>: <line content>
                **<path/to/file2>:**
                ...

        Raises:
            GrepTextError: If parameters are invalid, directory is inaccessible,
                          pattern is malformed, or search execution fails
        """
        # Validate inputs
        directory = self._validate_directory(directory)

        if max_results is not None and max_results < 0:
            error_msg = f"max_results must be non-negative, got: {max_results}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        # Validate pattern (always treated as regex)
        validated_pattern = self._validate_pattern(pattern)

        logger.info(
            f"Searching for pattern '{pattern}' in '{directory}' "
            f"(regex=True, case_sensitive={case_sensitive}, "
            f"max_results={max_results})"
        )

        # Check if directory is a git repository
        is_git_repo = self._is_git_repository(directory)

        # Determine available tools, filtering out git-grep if not a git repo
        tool_priority = self._get_tool_priority()
        if not is_git_repo:
            # Remove git-grep from priority list if not in a git repo
            tool_priority = [tool for tool in tool_priority if tool != "git-grep"]
            logger.debug(
                "Directory is not a git repository, excluding git-grep from tools"
            )
        else:
            logger.debug("Directory is a git repository, git-grep is available")

        used_tool = ""
        for tool in tool_priority:
            if self._is_tool_available(tool):
                logger.info(f"Using tool '{tool}' for search")
                used_tool = tool
                break

        # Build appropriate command based on platform and tool
        try:
            if used_tool == "grep":
                command = self._build_grep_command(
                    validated_pattern, directory, case_sensitive
                )
            elif used_tool == "rg":
                command = self._build_rg_command(
                    validated_pattern, directory, case_sensitive
                )
            elif used_tool == "git-grep":
                command = self._build_git_grep_command(
                    validated_pattern, directory, case_sensitive
                )
            elif used_tool == "Select-String":
                command = self._build_windows_command(
                    validated_pattern, directory, case_sensitive
                )
            else:
                error_msg = f"Unsupported selected tool: {used_tool}"
                logger.error(error_msg)
                raise GrepTextError(error_msg)

        except Exception as e:
            error_msg = f"Error building command: {e}"
            logger.error(error_msg)
            raise GrepTextError(error_msg) from e

        # Execute command
        try:
            result = self._execute_command(command, timeout=self.DEFAULT_TIMEOUT)
        except GrepTextError:
            raise
        except Exception as e:
            error_msg = f"Unexpected error during command execution: {e}"
            logger.error(error_msg)
            raise GrepTextError(error_msg) from e

        # Parse output
        output = result.get("output", "")
        exit_code = result.get("exit_code", 0)

        # exit_code 0 = matches found
        # exit_code 1 = no matches (not an error)
        # exit_code >1 = actual error
        if exit_code > 1:
            error = result.get("error", "Unknown error")
            error_msg = f"Search command failed with exit code {exit_code}: {error}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        try:
            formatted_result = self._parse_output(output, max_results)
            logger.info("Search completed successfully")
            return formatted_result

        except Exception as e:
            error_msg = f"Error parsing search results: {e}"
            logger.error(error_msg)
            raise GrepTextError(error_msg) from e
