import os
import sys
from typing import Any, Dict, List, Optional, Literal
from loguru import logger

from AgentCrew.modules.command_execution.service import CommandExecutionService


class FileSearchError(Exception):
    """
    Custom exception for file search errors.
    """

    pass


class FileSearchService:
    """
    File search service with platform detection and searcher fallback mechanism.

    This service provides efficient file searching across different operating systems
    by automatically detecting and using the best available searcher tool.

    Supported searchers by platform:
    - Linux/macOS: fd, rg (ripgrep), find
    - Windows: fd, rg (ripgrep), dir, Get-ChildItem (PowerShell)
    """

    _instance = None

    SEARCHER_PRIORITY_UNIX = ["fd", "rg", "find"]
    SEARCHER_PRIORITY_WINDOWS = ["fd", "rg", "dir", "Get-ChildItem"]

    @classmethod
    def get_instance(cls):
        """
        Get singleton instance of FileSearchService.
        """
        if cls._instance is None:
            cls._instance = FileSearchService()

        return cls._instance

    def __init__(self) -> None:
        """
        Initialize the FileSearchService with platform detection.
        """
        self.platform = sys.platform
        self._is_windows = self.platform == "win32"
        self._searcher_availability_cache: Dict[str, bool] = {}
        self._searcher_priority = self._get_searcher_priority()

        logger.info(f"FileSearchService initialized for platform: {self.platform} ")
        logger.debug(f"Searcher priority: {self._searcher_priority}")

    def _get_searcher_priority(self) -> List[str]:
        """
        Get the searcher priority list based on the current platform.

        Returns:
            List[str]: Ordered list of searchers to try, from best to worst
        """
        if self._is_windows:
            return self.SEARCHER_PRIORITY_WINDOWS.copy()
        else:
            return self.SEARCHER_PRIORITY_UNIX.copy()

    def _is_searcher_available(self, searcher_name: str) -> bool:
        """
        Check if a specific searcher is available on the system.

        Results are cached to avoid repeated system calls.

        Args:
            searcher_name: Name of the searcher to check (e.g., "fd", "rg", "find")

        Returns:
            bool: True if the searcher is available, False otherwise
        """
        if searcher_name in self._searcher_availability_cache:
            return self._searcher_availability_cache[searcher_name]

        cmd_service = CommandExecutionService.get_instance()
        is_available = False

        try:
            if self._is_windows:
                if searcher_name in ["dir"]:
                    is_available = True
                elif searcher_name == "Get-ChildItem":
                    result = cmd_service.execute_command(
                        f'powershell -Command "Get-Command {searcher_name}"',
                        timeout=5,
                    )
                    is_available = (
                        result.get("exit_code") == 0
                        and result.get("status") == "completed"
                    )
                else:
                    result = cmd_service.execute_command(
                        f"where {searcher_name}",
                        timeout=5,
                    )
                    is_available = (
                        result.get("exit_code") == 0
                        and result.get("status") == "completed"
                    )
            else:
                result = cmd_service.execute_command(
                    f"command -v {searcher_name}",
                    timeout=5,
                )
                is_available = (
                    result.get("exit_code") == 0 and result.get("status") == "completed"
                )

        except Exception as e:
            logger.warning(
                f"Error checking availability of searcher '{searcher_name}': {e}"
            )
            is_available = False

        self._searcher_availability_cache[searcher_name] = is_available

        logger.debug(f"Searcher '{searcher_name}' availability: {is_available}")
        return is_available

    def _build_fd_command(
        self, pattern: str, directory: str, max_results: Optional[int] = None
    ) -> str:
        """
        Build fd command for file searching.

        Args:
            pattern: File pattern to search for (glob or regex)
            directory: Directory to search in
            max_results: Maximum number of results to return

        Returns:
            str: fd command string
        """
        escaped_pattern = pattern.replace("'", "'\\''")

        cmd_parts = [
            "fd",
            "--type=file",
            "--absolute-path",
            "--hidden",
            f"--glob '{escaped_pattern}'",
            f"'{directory}'",
        ]

        if max_results is not None and max_results > 0:
            cmd_parts.append(f"--max-results {max_results}")

        return " ".join(cmd_parts)

    def _build_rg_command(self, pattern: str, directory: str) -> str:
        """
        Build ripgrep command for file searching.

        Args:
            pattern: File pattern to search for (glob)
            directory: Directory to search in

        Returns:
            str: rg command string
        """
        escaped_pattern = pattern.replace("'", "'\\''")

        cmd_parts = [
            "rg",
            "--files",
            f"'{directory}'",
            f"--glob='{escaped_pattern}'",
            "--hidden",
        ]

        # rg doesn't have native max-results for --files
        # will need post-processing in _parse_search_results

        return " ".join(cmd_parts)

    def _build_find_command(self, pattern: str, directory: str) -> str:
        """
        Build find command for file searching.

        Args:
            pattern: File pattern to search for (glob)
            directory: Directory to search in

        Returns:
            str: find command string
        """
        escaped_pattern = pattern.replace("'", "'\\''")

        cmd_parts = [
            "find",
            f"'{directory}'",
            "-type f",
            "-name",
            f"'{escaped_pattern}'",
        ]

        # find doesn't have native max-results
        # will need post-processing in _parse_search_results

        return " ".join(cmd_parts)

    def _build_dir_command(self, pattern: str, directory: str) -> str:
        """
        Build Windows dir command for file searching.

        Args:
            pattern: File pattern to search for (wildcard)
            directory: Directory to search in

        Returns:
            str: dir command string wrapped in cmd.exe
        """
        # /a-d = all items including hidden files and excluding directories
        # /s = recursive search
        # /b = bare format (paths only, no headers)
        # /c = execute command and then terminate
        dir_command = f'dir "{directory}\\{pattern}" /s /b /a-d'

        # Wrap dir command in cmd.exe since dir is a built-in command
        cmd_parts = ["cmd.exe", "/c", f"""'{dir_command}'"""]

        # dir doesn't have native max-results
        # will need post-processing in _parse_search_results

        return " ".join(cmd_parts)

    def _build_powershell_command(
        self, pattern: str, directory: str, max_results: Optional[int] = None
    ) -> str:
        """
        Build PowerShell Get-ChildItem command for file searching.

        Args:
            pattern: File pattern to search for (wildcard)
            directory: Directory to search in
            max_results: Maximum number of results to return

        Returns:
            str: PowerShell command string
        """
        # Escape single quotes for PowerShell string literals
        escaped_directory = directory.replace("'", "''")
        escaped_pattern = pattern.replace("'", "''")

        # Build the PowerShell command parts within the script block
        ps_command_parts = [
            f'Get-ChildItem -Path "{escaped_directory}"',
            f'-Filter "{escaped_pattern}"',
            "-Recurse",
            "-File",
            "-Force",  # -Force includes hidden files
        ]

        if max_results is not None and max_results > 0:
            ps_command_parts.append(f"| Select-Object -First {max_results}")

        # Get full path
        ps_command_parts.append("| ForEach-Object { $_.FullName }")

        # Combine all PowerShell command parts
        ps_command = " ".join(ps_command_parts)

        # Wrap in powershell execution with proper quoting
        return f"powershell -Command '{ps_command}'"

    def _execute_search(self, searcher: str, command: str) -> Dict[str, Any]:
        """
        Execute search command using CommandExecutionService.

        Args:
            searcher: Name of the searcher being used
            command: Command string to execute

        Returns:
            Dict: Command execution result with status, output, error, exit_code
        """
        cmd_service = CommandExecutionService.get_instance()

        logger.debug(f"Executing {searcher} command: {command}")

        try:
            result = cmd_service.execute_command(command, timeout=30)

            logger.debug(
                f"{searcher} execution result: status={result.get('status')}, "
                f"exit_code={result.get('exit_code')}"
            )

            return result

        except Exception as e:
            logger.error(f"Error executing {searcher} command: {e}")
            return {
                "status": "error",
                "error": str(e),
                "exit_code": 1,
            }

    def _convert_to_relative_paths(self, paths: List[str], base_dir: str) -> List[str]:
        """
        Convert list of absolute paths to paths relative to base_dir.

        Args:
            paths: List of absolute file paths
            base_dir: Base directory to calculate relative paths from

        Returns:
            List[str]: List of relative file paths
        """
        import os

        relative_paths = []
        for path in paths:
            try:
                rel_path = os.path.relpath(path, base_dir)
                relative_paths.append(rel_path)
            except (ValueError, TypeError) as e:
                # On Windows, relpath can fail if paths are on different drives
                # In such cases, keep the absolute path
                logger.warning(
                    f"Could not convert '{path}' to relative path from '{base_dir}': {e}. "
                    f"Keeping absolute path."
                )
                relative_paths.append(path)

        logger.debug(
            f"Converted {len(paths)} absolute paths to relative paths "
            f"(base: {base_dir})"
        )

        return relative_paths

    def _format_results_as_markdown(self, files: List[str]) -> str:
        """
        Convert list of file paths to markdown format with summary.

        Args:
            files: List of file paths

        Returns:
            str: Markdown formatted string with count summary and file paths
        """
        count = len(files)

        if count == 0:
            return "**Found 0 files**"

        # Build markdown with summary header and file list
        markdown_lines = [f"**Found {count} file{'s' if count != 1 else ''}:**", ""]
        markdown_lines.extend(files)

        return "\n".join(markdown_lines)

    def _parse_search_results(
        self, output: str, searcher: str, max_results: Optional[int] = None
    ) -> List[str]:
        """
        Parse searcher output into list of file paths.

        Different searchers have different output formats. This method
        normalizes the output into a consistent list of absolute file paths.

        Args:
            output: Raw output from searcher command
            searcher: Name of the searcher used
            max_results: Maximum number of results to return (post-processing)

        Returns:
            List[str]: List of absolute file paths
        """
        if not output or not output.strip():
            return []

        # Split output into lines and filter empty lines
        lines = [line.strip() for line in output.strip().split("\n") if line.strip()]

        results = []

        for line in lines:
            # Skip error messages or non-path lines
            if line.startswith("Error:") or line.startswith("Warning:"):
                continue

            if self._is_windows:
                # Ensure Windows paths use backslashes
                line = line.replace("/", "\\")
            else:
                # Ensure Unix paths use forward slashes
                line = line.replace("\\", "/")

            results.append(line)

        # Apply max_results if specified (post-processing for searchers that don't support it)
        if max_results is not None and max_results > 0:
            results = results[:max_results]

        logger.debug(f"Parsed {len(results)} results from {searcher} output")

        return results

    def _validate_directory(self, directory: str) -> str:
        """
        Validate that the directory path is valid and accessible.

        Checks:
        - Path exists
        - Path is a directory
        - Directory is readable (permission check)

        Args:
            directory: Directory path to validate

        Returns:
            str: Absolute path of the validated directory

        Raises:
            FileSearchError: If directory is invalid or inaccessible
        """
        if not os.path.exists(directory):
            error_msg = f"Directory does not exist: {directory}"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        if not os.path.isdir(directory):
            error_msg = f"Path is not a directory: {directory}"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        if not os.access(directory, os.R_OK):
            error_msg = f"Permission denied: Cannot read directory '{directory}'"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        # Convert to absolute path for consistency
        abs_directory = os.path.abspath(directory)
        logger.debug(f"Directory validated: {abs_directory}")

        return abs_directory

    def search_files(
        self,
        pattern: str,
        directory: str = ".",
        max_results: Optional[int] = None,
        path_type: Literal["absolute", "relative"] = "absolute",
    ) -> str:
        """
        Search for files matching the given pattern in the specified directory.

        Args:
            pattern: File pattern to search for (glob pattern like "*.py", "test_*.txt")
            directory: Directory to search in (default: current directory ".")
            max_results: Maximum number of results to return (None = unlimited)
            path_type: Type of paths to return - "absolute" (default) or "relative"
                      Relative paths are calculated relative to the search directory

        Returns:
            str: Markdown formatted string with count summary and file paths

        Raises:
            FileSearchError: If directory is invalid, inaccessible, or search parameters are invalid
            PermissionError: If directory cannot be accessed due to permissions
            OSError: If file system operations fail
        """
        if not pattern.strip():
            error_msg = "Search pattern cannot be empty"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        if max_results is not None and max_results < 0:
            error_msg = f"max_results must be non-negative, got: {max_results}"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        if path_type not in ["absolute", "relative"]:
            error_msg = f"path_type must be 'absolute' or 'relative', got: {path_type}"
            logger.error(error_msg)
            raise FileSearchError(error_msg)

        try:
            directory = self._validate_directory(directory)
        except FileSearchError:
            raise
        except PermissionError as e:
            error_msg = f"Permission denied accessing directory '{directory}': {e}"
            logger.error(error_msg)
            raise FileSearchError(error_msg) from e
        except OSError as e:
            error_msg = f"OS error while validating directory '{directory}': {e}"
            logger.error(error_msg)
            raise FileSearchError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error validating directory '{directory}': {e}"
            logger.error(error_msg)
            raise FileSearchError(error_msg) from e

        logger.info(
            f"Searching for pattern '{pattern}' in directory '{directory}' "
            f"(max_results={max_results}, path_type={path_type})"
        )

        files = []
        last_error = None

        for searcher in self._searcher_priority:
            if not self._is_searcher_available(searcher):
                continue

            try:
                logger.debug(f"Trying searcher: {searcher}")

                if searcher == "fd":
                    command = self._build_fd_command(pattern, directory, max_results)
                elif searcher == "rg":
                    command = self._build_rg_command(pattern, directory)
                elif searcher == "find":
                    command = self._build_find_command(pattern, directory)
                elif searcher == "dir":
                    command = self._build_dir_command(pattern, directory)
                elif searcher == "Get-ChildItem":
                    command = self._build_powershell_command(
                        pattern, directory, max_results
                    )
                else:
                    logger.warning(f"Unknown searcher: {searcher}")
                    continue

                result = self._execute_search(searcher, command)

                if result.get("status") == "completed" and result.get("exit_code") == 0:
                    output = result.get("output", "")
                    files = self._parse_search_results(output, searcher, max_results)

                    if path_type == "relative":
                        files = self._convert_to_relative_paths(files, directory)

                    logger.info(
                        f"Search completed successfully with {searcher}: "
                        f"found {len(files)} files (path_type={path_type})"
                    )

                    return self._format_results_as_markdown(files)

                else:
                    error = result.get("error", "Unknown error")
                    last_error = error
                    logger.warning(
                        f"Searcher {searcher} failed: {error}. Trying next searcher..."
                    )
                    continue

            except PermissionError as e:
                error_msg = f"Permission error with searcher {searcher}: {e}"
                last_error = str(e)
                logger.warning(f"{error_msg}. Trying next searcher...")
                continue

            except OSError as e:
                error_msg = f"OS error with searcher {searcher}: {e}"
                last_error = str(e)
                logger.warning(f"{error_msg}. Trying next searcher...")
                continue

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Exception with searcher {searcher}: {e}. Trying next searcher..."
                )
                continue

        error_msg = (
            f"All available file searchers failed to complete the search. "
            f"Last error: {last_error or 'Unknown error'}. "
            f"Attempted searchers: {', '.join([s for s in self._searcher_priority if self._is_searcher_available(s)])}"
        )
        logger.error(error_msg)
        raise FileSearchError(error_msg)
