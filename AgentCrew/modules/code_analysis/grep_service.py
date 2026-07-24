import os
import re
import shutil
import sys
from typing import Any, Literal

from loguru import logger

from AgentCrew.modules.command_execution.service import CommandExecutionService


class GrepTextError(Exception):
    """
    Custom exception for grep text search errors.
    """


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
    DEFAULT_EXCLUDE_DIRS = [".agentcrew"]
    MATCH_CONTEXT_CHARS = 120

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
        self._tool_availability_cache: dict[str, bool] = {}
        self._git_repo_cache: dict[str, bool] = {}

    def _get_tool_priority(self) -> list[str]:
        """
        Get the tool priority list based on the current platform.

        Returns:
            list[str]: Ordered list of tools to try, from best to worst
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

    def _validate_path(self, path: str) -> str:
        """
        Validate that the path (file or directory) is valid and accessible.

        Args:
            path: File or directory path to validate

        Returns:
            str: Absolute path of the validated path

        Raises:
            GrepTextError: If path is invalid or inaccessible
        """
        if not path or not path.strip():
            error_msg = "Path cannot be empty"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.path.exists(path):
            error_msg = f"Path does not exist: {path}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.path.isdir(path) and not os.path.isfile(path):
            error_msg = f"Path is not a file or directory: {path}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        if not os.access(path, os.R_OK):
            error_msg = f"Permission denied: Cannot read '{path}'"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        abs_path = os.path.abspath(path)
        logger.debug(f"Path validated: {abs_path}")

        return abs_path

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
        path: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build grep command for Unix systems.

        Args:
            pattern: Search pattern (already validated regex)
            path: File or directory path to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        escaped_pattern = pattern.replace("'", "'\\''")

        cmd_parts = [
            "grep",
        ]

        is_dir = os.path.isdir(path)
        if is_dir:
            cmd_parts.append("-r")

        cmd_parts.extend(
            [
                "-n",
                "-H",
                "-E",
            ]
        )

        if not case_sensitive:
            cmd_parts.append("-i")

        if is_dir:
            for exclude_dir in self.DEFAULT_EXCLUDE_DIRS:
                cmd_parts.append(f"--exclude-dir='{exclude_dir}'")

        cmd_parts.append("--")
        cmd_parts.append(f"'{escaped_pattern}'")
        cmd_parts.append(f"'{path}'")

        command = " ".join(cmd_parts)
        logger.debug(f"Built grep command: {command}")
        return command

    def _build_git_grep_command(
        self,
        pattern: str,
        path: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build git grep command for searching within git repositories.

        Args:
            pattern: Search pattern (already validated regex)
            path: File or directory path to search in (must be within a git repo)
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        is_file = os.path.isfile(path)
        escaped_file = None

        if self._is_windows:
            escaped_pattern = pattern.replace('"', '""')

            if is_file:
                search_dir = os.path.dirname(path) or "."
                escaped_directory = search_dir.replace('"', '""')
                escaped_file = os.path.basename(path).replace('"', '""')
            else:
                escaped_directory = path.replace('"', '""')

            cmd_parts = [
                f'cd /d "{escaped_directory}" &&',
                "git",
                "grep",
                "-n",
                "--full-name",
                "-E",
            ]

            if not case_sensitive:
                cmd_parts.append("-i")

            cmd_parts.append("--")
            cmd_parts.append(f'"{escaped_pattern}"')

            if is_file:
                cmd_parts.append(f'"{escaped_file}"')
            else:
                for exclude_dir in self.DEFAULT_EXCLUDE_DIRS:
                    cmd_parts.append(f'":(exclude){exclude_dir}"')

            command = " ".join(cmd_parts)
        else:
            escaped_pattern = pattern.replace("'", "'\\''")

            if is_file:
                search_dir = os.path.dirname(path) or "."
                escaped_directory = search_dir.replace("'", "'\\''")
                escaped_file = os.path.basename(path).replace("'", "'\\''")
            else:
                escaped_directory = path.replace("'", "'\\''")

            cmd_parts = [
                f"cd '{escaped_directory}' &&",
                "git",
                "grep",
                "-n",
                "--full-name",
                "-E",
            ]

            if not case_sensitive:
                cmd_parts.append("-i")

            cmd_parts.append("--")
            cmd_parts.append(f"'{escaped_pattern}'")

            if is_file:
                cmd_parts.append(f"'{escaped_file}'")
            else:
                for exclude_dir in self.DEFAULT_EXCLUDE_DIRS:
                    cmd_parts.append(f"':(exclude){exclude_dir}'")

            command = " ".join(cmd_parts)

        logger.debug(f"Built git grep command: {command}")
        return command

    def _build_rg_command(
        self,
        pattern: str,
        path: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build ripgrep (rg) command.

        Args:
            pattern: Search pattern (already validated regex)
            path: File or directory path to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        escaped_pattern = pattern.replace("'", "'\\''")

        cmd_parts = [
            "rg",
            "--line-number",
            "--no-heading",
            "--with-filename",
            "--hidden",
        ]

        if not case_sensitive:
            cmd_parts.append("--ignore-case")

        if os.path.isdir(path):
            for exclude_dir in self.DEFAULT_EXCLUDE_DIRS:
                cmd_parts.append(f"--glob '!{exclude_dir}'")

        cmd_parts.append("--")
        cmd_parts.append(f"'{escaped_pattern}'")
        cmd_parts.append(f"'{path}'")

        command = " ".join(cmd_parts)
        logger.debug(f"Built rg command: {command}")
        return command

    def _build_windows_command(
        self,
        pattern: str,
        path: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build PowerShell Select-String command for Windows.

        Args:
            pattern: Search pattern
            path: File or directory path to search in
            case_sensitive: Whether search is case-sensitive

        Returns:
            str: Command string ready for execution
        """
        escaped_path = path.replace("'", "''")
        escaped_pattern = pattern.replace("'", "''").replace('"', '`"')

        is_file = os.path.isfile(path)

        if is_file:
            ps_parts = [
                f'Get-Content -Path "{escaped_path}"',
                "|",
                f'Select-String -Pattern "{escaped_pattern}"',
            ]
        else:
            exclude_filter = " -and ".join(
                [
                    f'$_.DirectoryName -notlike "*\\{d}*"'
                    for d in self.DEFAULT_EXCLUDE_DIRS
                ]
            )
            ps_parts = [
                f'Get-ChildItem -Path "{escaped_path}" -Recurse -File',
                f"| Where-Object {{ {exclude_filter} }}",
                "|",
                f'Select-String -Pattern "{escaped_pattern}"',
            ]

        if case_sensitive:
            ps_parts.append("-CaseSensitive")

        ps_parts.append('| ForEach-Object { "$($_.Path):$($_.LineNumber):$($_.Line)" }')

        ps_command = " ".join(ps_parts)
        command = f"powershell -Command '{ps_command}'"

        logger.debug(f"Built window command: {command}")
        return command

    def _execute_command(
        self, command: str, timeout: int = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """
        Execute the grep command using CommandExecutionService.

        Args:
            command: Command string to execute
            timeout: Timeout in seconds (default: 30)

        Returns:
            dict[str, Any]: Command execution result with status, output, error, exit_code

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

    def _normalize_paths(self, path: list[str] | str) -> list[str]:
        """
        Normalize path input into a validated absolute path list.

        Args:
            path: An array of file/directory paths. A single string is still normalized defensively.

        Returns:
            list[str]: Validated absolute paths with exact duplicates removed

        Raises:
            GrepTextError: If no valid paths are provided or any path item is invalid
        """
        if isinstance(path, str):
            raw_paths = [path]
        else:
            raw_paths = path

        if not raw_paths:
            error_msg = "At least one path must be provided"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        normalized_paths: list[str] = []
        seen_paths = set()

        for index, item in enumerate(raw_paths):
            if not isinstance(item, str):
                error_msg = f"path list items must be strings, got: {type(item).__name__} at index {index}"
                logger.error(error_msg)
                raise GrepTextError(error_msg)

            validated_path = self._validate_path(item)
            if validated_path not in seen_paths:
                normalized_paths.append(validated_path)
                seen_paths.add(validated_path)

        if not normalized_paths:
            error_msg = "At least one path must be provided"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        return normalized_paths

    def _build_search_command(
        self,
        tool_name: str,
        pattern: str,
        path: str,
        case_sensitive: bool,
    ) -> str:
        """
        Build the appropriate command for a validated path using the selected tool.
        """
        if tool_name == "grep":
            return self._build_grep_command(pattern, path, case_sensitive)
        if tool_name == "rg":
            return self._build_rg_command(pattern, path, case_sensitive)
        if tool_name == "git-grep":
            return self._build_git_grep_command(pattern, path, case_sensitive)
        if tool_name == "Select-String":
            return self._build_windows_command(pattern, path, case_sensitive)

        error_msg = f"Unsupported selected tool: {tool_name}"
        logger.error(error_msg)
        raise GrepTextError(error_msg)

    def _select_search_tool(self, path: str) -> str:
        """
        Select the best available search tool for a validated path.
        """
        git_check_dir = path if os.path.isdir(path) else os.path.dirname(path)
        is_git_repo = self._is_git_repository(git_check_dir)

        tool_priority = self._get_tool_priority()
        if not is_git_repo:
            tool_priority = [tool for tool in tool_priority if tool != "git-grep"]
            logger.debug(
                "Path is not in a git repository, excluding git-grep from tools"
            )
        else:
            logger.debug("Path is in a git repository, git-grep is available")

        for tool in tool_priority:
            if self._is_tool_available(tool):
                logger.info(f"Using tool '{tool}' for search in '{path}'")
                return tool

        error_msg = "Unsupported selected tool"
        logger.error(error_msg)
        raise GrepTextError(error_msg)

    def _format_line_context(
        self,
        line_content: str,
        pattern: str | None = None,
        case_sensitive: bool = True,
    ) -> str:
        max_length = self.MATCH_CONTEXT_CHARS * 2
        if len(line_content) <= max_length:
            return line_content

        match = None
        if pattern:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                match = re.search(pattern, line_content, flags)
            except re.error:
                match = None

        if match:
            start = max(0, match.start() - self.MATCH_CONTEXT_CHARS)
            end = min(len(line_content), match.end() + self.MATCH_CONTEXT_CHARS)
        else:
            start = 0
            end = max_length

        snippet = line_content[start:end]
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(line_content) else ""
        return f"{prefix}{snippet}{suffix}"

    def _parse_output(
        self,
        output: str,
        max_results: int | None = None,
        pattern: str | None = None,
        case_sensitive: bool = True,
    ) -> str:
        """
        Parse grep tool output into formatted string result.

        Args:
            output: Raw command output
            max_results: Maximum number of results to return

        Returns:
            str: Formatted search results with structure:
                <number> matches
                <path/to/file1>
                <line number> <line content>
                <line number> <line content>
                <path/to/file2>
                ...
        """
        if not output or not output.strip():
            # No matches found (not an error)
            logger.debug("No matches found in output")
            return "0 matches"
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

        result_lines = [f"{total_matches} match{'es' if total_matches != 1 else ''}"]
        current_file = None

        for file_path, line_number, line_content in matches:
            if file_path != current_file:
                result_lines.append(file_path)
                current_file = file_path

            formatted_content = self._format_line_context(
                line_content,
                pattern=pattern,
                case_sensitive=case_sensitive,
            )
            result_lines.append(f"{line_number} {formatted_content}")

        return "\n".join(result_lines)

    def search_text(
        self,
        pattern: str,
        path: list[str] | str,
        case_sensitive: bool = True,
        max_results: int | None = None,
        path_type: Literal["absolute", "relative"] = "absolute",
    ) -> str:
        """
        Search for text patterns within one or more files and directories.

        Args:
            pattern: The regular expression pattern to search for.
            path: An array of file and directory paths to search within. A single string is still normalized defensively.
            case_sensitive: Boolean flag to control case sensitivity of the search.
                           Default: True (case-sensitive)
            max_results: Maximum number of results to return. None for unlimited.
                        Default: None (return all matches)
            path_type: Type of paths to return - "absolute" (default) or "relative".
                      Relative paths are calculated relative to the current working directory.

        Returns:
            str: Formatted search results with structure:
                <number> matches
                <path/to/file1>
                <line number> <line content>
                <line number> <line content>
                <path/to/file2>
                ...

        Raises:
            GrepTextError: If parameters are invalid, path is inaccessible,
                          pattern is malformed, or search execution fails
        """
        normalized_paths = self._normalize_paths(path)

        if max_results is not None and max_results < 0:
            error_msg = f"max_results must be non-negative, got: {max_results}"
            logger.error(error_msg)
            raise GrepTextError(error_msg)

        validated_pattern = self._validate_pattern(pattern)

        logger.info(
            f"Searching for pattern '{pattern}' in '{normalized_paths}' "
            f"(regex=True, case_sensitive={case_sensitive}, "
            f"max_results={max_results})"
        )

        combined_matches = []
        for current_path in normalized_paths:
            try:
                used_tool = self._select_search_tool(current_path)
                command = self._build_search_command(
                    used_tool,
                    validated_pattern,
                    current_path,
                    case_sensitive,
                )
            except Exception as e:
                error_msg = f"Error building command: {e}"
                logger.error(error_msg)
                raise GrepTextError(error_msg) from e

            try:
                result = self._execute_command(command, timeout=self.DEFAULT_TIMEOUT)
            except GrepTextError:
                raise
            except Exception as e:
                error_msg = f"Unexpected error during command execution: {e}"
                logger.error(error_msg)
                raise GrepTextError(error_msg) from e

            output = result.get("output", "")
            exit_code = result.get("exit_code", 0)

            if exit_code > 1:
                error = result.get("error", "Unknown error")
                error_msg = f"Search command failed with exit code {exit_code}: {error}"
                logger.error(error_msg)
                raise GrepTextError(error_msg)

            if output and output.strip():
                lines = output.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    parts = line.split(":", 2)
                    if len(parts) < 3:
                        logger.debug(f"Skipping malformed line: {line}")
                        continue

                    file_path = parts[0].strip()
                    line_number_str = parts[1].strip()
                    line_content = parts[2]

                    try:
                        line_number = int(line_number_str)
                    except ValueError:
                        logger.debug(f"Skipping line with invalid line number: {line}")
                        continue

                    if self._is_windows:
                        file_path = file_path.replace("/", "\\")
                    else:
                        file_path = file_path.replace("\\", "/")

                    combined_matches.append((file_path, line_number, line_content))

        combined_matches.sort(key=lambda x: (x[0], x[1]))

        if max_results is not None:
            combined_matches = combined_matches[:max_results]

        if path_type == "relative":
            cwd = os.getcwd()
            converted_matches = []
            for file_path, line_number, line_content in combined_matches:
                try:
                    rel_path = os.path.relpath(file_path, cwd)
                    converted_matches.append((rel_path, line_number, line_content))
                except (ValueError, TypeError):
                    converted_matches.append((file_path, line_number, line_content))
            combined_matches = converted_matches

        total_matches = len(combined_matches)
        if total_matches == 0:
            logger.info("Search completed successfully with 0 matches")
            return "0 matches"
        result_lines = [f"{total_matches} match{'es' if total_matches != 1 else ''}"]
        current_file = None

        for file_path, line_number, line_content in combined_matches:
            if file_path != current_file:
                result_lines.append(file_path)
                current_file = file_path
            formatted_content = self._format_line_context(
                line_content,
                pattern=validated_pattern,
                case_sensitive=case_sensitive,
            )
            result_lines.append(f"{line_number} {formatted_content}")

        logger.info("Search completed successfully")
        return "\n".join(result_lines)
