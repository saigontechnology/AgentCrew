"""
Unit tests for FileSearchService.

This test suite provides comprehensive coverage of the FileSearchService class,
testing core functionality including:
- Platform detection
- Searcher availability checking
- Command building for various searchers
- File search functionality with fallback mechanisms
- Max results filtering
- Path type conversion (absolute/relative)

Uses mocking to isolate tests from system dependencies and external searchers.
"""

import unittest
from unittest.mock import Mock, patch
import os
import tempfile
import shutil

from AgentCrew.modules.code_analysis.file_search_service import (
    FileSearchService,
    FileSearchError,
)


class TestFileSearchServicePlatformDetection(unittest.TestCase):
    """Test platform detection functionality."""

    def setUp(self):
        """Reset singleton instance before each test."""
        FileSearchService._instance = None

    @patch("sys.platform", "linux")
    def test_platform_detection_linux(self):
        """Test platform detection for Linux."""
        service = FileSearchService.get_instance()

        self.assertEqual(service.platform, "linux")
        self.assertFalse(service._is_windows)
        self.assertEqual(
            service._searcher_priority, FileSearchService.SEARCHER_PRIORITY_UNIX
        )

    @patch("sys.platform", "darwin")
    def test_platform_detection_macos(self):
        """Test platform detection for macOS."""
        service = FileSearchService.get_instance()

        self.assertEqual(service.platform, "darwin")
        self.assertFalse(service._is_windows)
        self.assertEqual(
            service._searcher_priority, FileSearchService.SEARCHER_PRIORITY_UNIX
        )

    @patch("sys.platform", "win32")
    def test_platform_detection_windows(self):
        """Test platform detection for Windows."""
        service = FileSearchService.get_instance()

        self.assertEqual(service.platform, "win32")
        self.assertTrue(service._is_windows)
        self.assertEqual(
            service._searcher_priority, FileSearchService.SEARCHER_PRIORITY_WINDOWS
        )


class TestFileSearchServiceSearcherAvailability(unittest.TestCase):
    """Test searcher availability detection."""

    def setUp(self):
        """Reset singleton and set up mocks."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()
        self.service._searcher_availability_cache.clear()

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_fd_availability_unix(self, mock_get_instance):
        """Test fd availability detection on Unix."""
        # Mock CommandExecutionService instance
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock successful command execution
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "/usr/bin/fd",
        }

        self.service._is_windows = False
        result = self.service._is_searcher_available("fd")

        self.assertTrue(result)
        mock_cmd_instance.execute_command.assert_called_once_with(
            "command -v fd", timeout=5
        )

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_rg_availability_unix(self, mock_get_instance):
        """Test ripgrep availability detection on Unix."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "/usr/bin/rg",
        }

        self.service._is_windows = False
        result = self.service._is_searcher_available("rg")

        self.assertTrue(result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_searcher_not_available(self, mock_get_instance):
        """Test searcher not available scenario."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock failed command execution (searcher not found)
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 1,
            "output": "",
        }

        self.service._is_windows = False
        result = self.service._is_searcher_available("nonexistent_searcher")

        self.assertFalse(result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_availability_caching(self, mock_get_instance):
        """Test that searcher availability results are cached."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "/usr/bin/fd",
        }

        self.service._is_windows = False

        # First call should execute command
        result1 = self.service._is_searcher_available("fd")
        self.assertTrue(result1)

        # Second call should use cache
        result2 = self.service._is_searcher_available("fd")
        self.assertTrue(result2)

        # Command should only be executed once
        mock_cmd_instance.execute_command.assert_called_once()

    def test_dir_always_available_on_windows(self):
        """Test that 'dir' is always considered available on Windows."""
        self.service._is_windows = True
        result = self.service._is_searcher_available("dir")

        self.assertTrue(result)


class TestFileSearchServiceCommandBuilders(unittest.TestCase):
    """Test command building methods for various searchers."""

    def setUp(self):
        """Set up service instance."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()

    def test_build_fd_command_basic(self):
        """Test basic fd command building."""
        command = self.service._build_fd_command("*.py", "/home/user/project")

        self.assertIn("fd", command)
        self.assertIn("*.py", command)
        self.assertIn("/home/user/project", command)
        self.assertIn("--type=file", command)
        self.assertIn("--absolute-path", command)
        self.assertIn("--hidden", command)

    def test_build_fd_command_with_max_results(self):
        """Test fd command building with max results."""
        command = self.service._build_fd_command("*.py", "/home/user/project", 10)

        self.assertIn("--max-results 10", command)

    def test_build_rg_command_basic(self):
        """Test basic ripgrep command building."""
        command = self.service._build_rg_command("*.py", "/home/user/project")

        self.assertIn("rg", command)
        self.assertIn("--files", command)
        self.assertIn("--glob", command)
        self.assertIn("*.py", command)
        self.assertIn("/home/user/project", command)
        self.assertIn("--hidden", command)

    def test_build_find_command_basic(self):
        """Test basic find command building."""
        command = self.service._build_find_command("*.py", "/home/user/project")

        self.assertIn("find", command)
        self.assertIn("/home/user/project", command)
        self.assertIn("-type f", command)
        self.assertIn("-name", command)
        self.assertIn("*.py", command)

    def test_build_dir_command_basic(self):
        """Test basic Windows dir command building."""
        command = self.service._build_dir_command("*.py", "C:\\Users\\user\\project")

        self.assertIn("dir", command)
        self.assertIn("*.py", command)
        self.assertIn("/s", command)  # recursive
        self.assertIn("/b", command)  # bare format
        self.assertIn("/a-d", command)  # files only
        self.assertIn("/a", command)  # all attributes (includes hidden files)

    def test_build_powershell_command_basic(self):
        """Test basic PowerShell Get-ChildItem command building."""
        command = self.service._build_powershell_command(
            "*.py", "C:\\Users\\user\\project"
        )

        self.assertIn("powershell", command)
        self.assertIn("Get-ChildItem", command)
        self.assertIn("*.py", command)
        self.assertIn("-Recurse", command)
        self.assertIn("-File", command)
        self.assertIn("-Force", command)  # Verify hidden files are included

    def test_build_powershell_command_with_max_results(self):
        """Test PowerShell command with max results."""
        command = self.service._build_powershell_command(
            "*.py", "C:\\Users\\user\\project", 10
        )

        self.assertIn("Select-Object -First 10", command)

    def test_command_pattern_escaping(self):
        """Test that special characters in patterns are escaped."""
        command = self.service._build_fd_command("file's.py", "/home/user")

        self.assertIn("file", command)


class TestFileSearchServiceHelperMethods(unittest.TestCase):
    """Test helper methods for result processing."""

    def setUp(self):
        """Set up service instance."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()

    def test_convert_to_relative_paths(self):
        """Test absolute to relative path conversion."""
        base_dir = "/home/user/project"
        absolute_paths = [
            "/home/user/project/file1.py",
            "/home/user/project/src/file2.py",
            "/home/user/project/tests/test_file.py",
        ]

        relative_paths = self.service._convert_to_relative_paths(
            absolute_paths, base_dir
        )

        self.assertEqual(relative_paths[0], "file1.py")
        self.assertEqual(relative_paths[1], os.path.join("src", "file2.py"))
        self.assertEqual(relative_paths[2], os.path.join("tests", "test_file.py"))

    def test_parse_search_results_empty_output(self):
        """Test parsing empty search results."""
        results = self.service._parse_search_results("", "fd")

        self.assertEqual(results, [])

    def test_parse_search_results_multiple_lines(self):
        """Test parsing multiple search results."""
        output = "/home/user/file1.py\n/home/user/file2.py\n/home/user/file3.py"
        results = self.service._parse_search_results(output, "fd")

        self.assertEqual(len(results), 3)
        self.assertIn("/home/user/file1.py", results)

    def test_parse_search_results_with_max_results(self):
        """Test parsing search results with max results limit."""
        output = "\n".join([f"/home/user/file{i}.py" for i in range(10)])
        results = self.service._parse_search_results(output, "fd", max_results=5)

        self.assertEqual(len(results), 5)


class TestFileSearchServiceValidation(unittest.TestCase):
    """Test directory validation functionality."""

    def setUp(self):
        """Set up service instance and temporary directory."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_validate_directory_exists(self):
        """Test validation of existing directory."""
        validated_path = self.service._validate_directory(self.temp_dir)

        self.assertTrue(os.path.isabs(validated_path))
        self.assertTrue(os.path.exists(validated_path))

    def test_validate_directory_not_exists(self):
        """Test validation of non-existent directory."""
        non_existent_dir = "/this/path/does/not/exist"

        with self.assertRaises(FileSearchError) as context:
            self.service._validate_directory(non_existent_dir)

        self.assertIn("does not exist", str(context.exception))

    def test_validate_directory_is_file(self):
        """Test validation when path is a file, not directory."""
        # Create a temporary file
        temp_file = os.path.join(self.temp_dir, "test_file.txt")
        with open(temp_file, "w") as f:
            f.write("test")

        with self.assertRaises(FileSearchError) as context:
            self.service._validate_directory(temp_file)

        self.assertIn("not a directory", str(context.exception))

    def test_validate_directory_converts_to_absolute(self):
        """Test that validation converts relative paths to absolute."""
        # Use current directory
        validated_path = self.service._validate_directory(".")

        self.assertTrue(os.path.isabs(validated_path))


class TestFileSearchServiceMainSearch(unittest.TestCase):
    """Test the main search_files method with real file system."""

    def setUp(self):
        """Set up service instance and temporary directory with test files."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()
        self.temp_dir = tempfile.mkdtemp()

        self.test_files = [
            "file1.py",
            "file2.py",
            "test_file.py",
            "readme.md",
            "data.json",
        ]

        for filename in self.test_files:
            filepath = os.path.join(self.temp_dir, filename)
            with open(filepath, "w") as f:
                f.write(f"# Content of {filename}")

        subdir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(subdir)
        for filename in ["sub1.py", "sub2.py"]:
            filepath = os.path.join(subdir, filename)
            with open(filepath, "w") as f:
                f.write(f"# Content of {filename}")

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_search_files_absolute_paths(self):
        """Test that search returns absolute paths when directory is absolute."""

        # Mock rg as available
        def mock_availability(searcher):
            return searcher == "rg"

        mock_cmd_service = Mock()
        mock_cmd_instance = Mock()
        mock_cmd_service.get_instance.return_value = mock_cmd_instance

        absolute_dir = os.path.abspath(self.temp_dir)

        mock_results = [
            os.path.join(absolute_dir, "file1.py"),
            os.path.join(absolute_dir, "file2.py"),
        ]
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "\n".join(mock_results),
        }

        with (
            patch.object(
                self.service, "_is_searcher_available", side_effect=mock_availability
            ),
            patch(
                "AgentCrew.modules.code_analysis.file_search_service.CommandExecutionService",
                mock_cmd_service,
            ),
        ):
            # Pass absolute directory path - should return absolute paths
            result = self.service.search_files("*.py", absolute_dir)

            # Result should be markdown formatted string
            self.assertIsInstance(result, str)
            self.assertIn("Found 2 files:", result)

            # Extract file paths from markdown
            lines = result.split("\n")
            file_lines = [
                line for line in lines[2:] if line.strip()
            ]  # Skip header and empty line

            # All file paths should be absolute
            for file_path in file_lines:
                self.assertTrue(os.path.isabs(file_path))

    def test_search_files_relative_paths(self):
        """Test that search returns relative paths when directory is relative."""

        # Mock rg as available
        def mock_availability(searcher):
            return searcher == "rg"

        mock_cmd_service = Mock()
        mock_cmd_instance = Mock()
        mock_cmd_service.get_instance.return_value = mock_cmd_instance

        # Use relative path for directory
        # Change to temp_dir first to make "." a valid relative path to it
        original_dir = os.getcwd()
        os.chdir(self.temp_dir)

        try:
            relative_dir = "."
            absolute_dir = os.path.abspath(relative_dir)

            mock_absolute = [
                os.path.join(absolute_dir, "file1.py"),
                os.path.join(absolute_dir, "subdir", "sub1.py"),
            ]
            mock_cmd_instance.execute_command.return_value = {
                "status": "completed",
                "exit_code": 0,
                "output": "\n".join(mock_absolute),
            }

            with (
                patch.object(
                    self.service,
                    "_is_searcher_available",
                    side_effect=mock_availability,
                ),
                patch(
                    "AgentCrew.modules.code_analysis.file_search_service.CommandExecutionService",
                    mock_cmd_service,
                ),
            ):
                # Pass relative directory path with path_type="relative" - should return relative paths
                result = self.service.search_files(
                    "*.py", relative_dir, path_type="relative"
                )

                # Result should be markdown formatted string
                self.assertIsInstance(result, str)
                self.assertIn("Found 2 files:", result)

                # Extract file paths from markdown
                lines = result.split("\n")
                file_lines = [
                    line for line in lines[2:] if line.strip()
                ]  # Skip header and empty line

                # All file paths should be relative
                for file_path in file_lines:
                    # Relative paths should not start with / or drive letter
                    self.assertFalse(os.path.isabs(file_path))
        finally:
            # Restore original directory
            os.chdir(original_dir)

    def test_search_files_max_results(self):
        """Test max results limiting."""

        # Mock fd as available (it supports max_results natively)
        def mock_availability(searcher):
            return searcher == "fd"

        mock_cmd_service = Mock()
        mock_cmd_instance = Mock()
        mock_cmd_service.get_instance.return_value = mock_cmd_instance

        mock_results = [
            os.path.join(self.temp_dir, "file1.py"),
            os.path.join(self.temp_dir, "file2.py"),
        ]
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "\n".join(mock_results),
        }

        with (
            patch.object(
                self.service, "_is_searcher_available", side_effect=mock_availability
            ),
            patch(
                "AgentCrew.modules.code_analysis.file_search_service.CommandExecutionService",
                mock_cmd_service,
            ),
        ):
            result = self.service.search_files("*.py", self.temp_dir, max_results=2)

            # Result should be markdown formatted string
            self.assertIsInstance(result, str)
            self.assertIn("Found 2 files:", result)

            # Extract file paths from markdown
            lines = result.split("\n")
            file_lines = [
                line for line in lines[2:] if line.strip()
            ]  # Skip header and empty line

            self.assertEqual(len(file_lines), 2)

    def test_search_files_empty_pattern_raises_error(self):
        """Test that empty pattern raises FileSearchError."""
        with self.assertRaises(FileSearchError) as context:
            self.service.search_files("", self.temp_dir)

        self.assertIn("cannot be empty", str(context.exception))

    def test_search_files_invalid_directory_raises_error(self):
        """Test that invalid directory raises FileSearchError."""
        with self.assertRaises(FileSearchError) as context:
            self.service.search_files("*.py", "/nonexistent/directory")

        self.assertIn("does not exist", str(context.exception))

    def test_search_files_negative_max_results_raises_error(self):
        """Test that negative max_results raises FileSearchError."""
        with self.assertRaises(FileSearchError) as context:
            self.service.search_files("*.py", self.temp_dir, max_results=-1)

        self.assertIn("must be non-negative", str(context.exception))


class TestFileSearchServiceSearcherFallback(unittest.TestCase):
    """Test searcher fallback mechanism."""

    def setUp(self):
        """Set up service instance and mocks."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()
        self.service._is_windows = False
        self.temp_dir = tempfile.mkdtemp()

        self.test_file = os.path.join(self.temp_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("# test")

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_fallback_to_next_searcher_on_failure(self, mock_get_instance):
        """Test that search falls back to next searcher when first fails."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock fd as available but failing
        availability_results = {"fd": True, "rg": True, "find": True}

        def mock_availability(searcher):
            return availability_results.get(searcher, False)

        # Mock execution: fd fails, rg succeeds
        execution_results = [
            {"status": "completed", "exit_code": 1, "error": "fd failed"},  # fd fails
            {
                "status": "completed",
                "exit_code": 0,
                "output": self.test_file,
            },  # rg succeeds
        ]

        mock_cmd_instance.execute_command.side_effect = execution_results

        with patch.object(
            self.service, "_is_searcher_available", side_effect=mock_availability
        ):
            result = self.service.search_files("*.py", self.temp_dir)

            # Result should be markdown formatted string
            self.assertIsInstance(result, str)
            self.assertIn("Found 1 file:", result)

            # Should have attempted fd first, then rg
            self.assertEqual(mock_cmd_instance.execute_command.call_count, 2)

    def test_all_searchers_unavailable_raises_error(self):
        """Test that FileSearchError is raised when no searchers are available."""
        # Mock all external searchers as unavailable
        with patch.object(self.service, "_is_searcher_available", return_value=False):
            with self.assertRaises(FileSearchError) as context:
                self.service.search_files("*.py", self.temp_dir)

            self.assertIn(
                "All available file searchers failed to complete the search",
                str(context.exception),
            )


class TestFileSearchServiceHiddenFiles(unittest.TestCase):
    """Test hidden file search functionality."""

    def setUp(self):
        """Set up service instance and temporary directory with hidden files."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()
        self.temp_dir = tempfile.mkdtemp()

        # Create regular files
        self.regular_files = ["file1.py", "file2.py"]
        for filename in self.regular_files:
            filepath = os.path.join(self.temp_dir, filename)
            with open(filepath, "w") as f:
                f.write(f"# Content of {filename}")

        # Create hidden files (Unix-style with dot prefix)
        self.hidden_files = [".hidden1.py", ".hidden2.py"]
        for filename in self.hidden_files:
            filepath = os.path.join(self.temp_dir, filename)
            with open(filepath, "w") as f:
                f.write(f"# Content of {filename}")

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_fd_command_includes_hidden_flag(self):
        """Test that fd command includes --hidden flag by default."""
        command = self.service._build_fd_command("*.py", self.temp_dir)

        self.assertIn("--hidden", command)

    def test_rg_command_includes_hidden_flag(self):
        """Test that rg command includes --hidden flag by default."""
        command = self.service._build_rg_command("*.py", self.temp_dir)

        self.assertIn("--hidden", command)

    def test_find_command_searches_hidden_files_by_default(self):
        """Test that find command documentation is updated for hidden files."""
        # find searches hidden files by default, no need to test

    def test_dir_command_includes_all_attributes_flag(self):
        """Test that dir command includes /a flag to show hidden files."""
        command = self.service._build_dir_command("*.py", "C:\\test")

        self.assertIn("/a-d", command)

    def test_powershell_command_includes_force_parameter(self):
        """Test that PowerShell command includes -Force parameter."""
        command = self.service._build_powershell_command("*.py", "C:\\test")

        self.assertIn("-Force", command)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_search_includes_hidden_files_with_fd(self, mock_get_instance):
        """Test that search results include hidden files when using fd."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock fd as available
        def mock_availability(searcher):
            return searcher == "fd"

        # Mock results that include both regular and hidden files
        all_files = [
            os.path.join(self.temp_dir, f)
            for f in self.regular_files + self.hidden_files
        ]
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "\n".join(all_files),
        }

        with patch.object(
            self.service, "_is_searcher_available", side_effect=mock_availability
        ):
            result = self.service.search_files("*.py", self.temp_dir)

            # Verify that the command was called with --hidden flag
            call_args = mock_cmd_instance.execute_command.call_args[0][0]
            self.assertIn("--hidden", call_args)

            # Result should be markdown formatted string
            self.assertIsInstance(result, str)
            self.assertIn("Found 4 files:", result)

            # Extract file paths from markdown
            lines = result.split("\n")
            file_lines = [
                line for line in lines[2:] if line.strip()
            ]  # Skip header and empty line

            # Verify results include both regular and hidden files
            self.assertEqual(len(file_lines), 4)  # 2 regular + 2 hidden

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_search_includes_hidden_files_with_rg(self, mock_get_instance):
        """Test that search results include hidden files when using rg."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock rg as available (fd not available)
        def mock_availability(searcher):
            return searcher == "rg"

        # Mock results that include both regular and hidden files
        all_files = [
            os.path.join(self.temp_dir, f)
            for f in self.regular_files + self.hidden_files
        ]
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "\n".join(all_files),
        }

        with patch.object(
            self.service, "_is_searcher_available", side_effect=mock_availability
        ):
            result = self.service.search_files("*.py", self.temp_dir)

            # Verify that the command was called with --hidden flag
            call_args = mock_cmd_instance.execute_command.call_args[0][0]
            self.assertIn("--hidden", call_args)

            # Result should be markdown formatted string
            self.assertIsInstance(result, str)
            self.assertIn("Found 4 files:", result)

            # Extract file paths from markdown
            lines = result.split("\n")
            file_lines = [
                line for line in lines[2:] if line.strip()
            ]  # Skip header and empty line

            # Verify results include both regular and hidden files
            self.assertEqual(len(file_lines), 4)  # 2 regular + 2 hidden


class TestFileSearchServiceMarkdownFormatting(unittest.TestCase):
    """Test the _format_results_as_markdown method."""

    def setUp(self):
        """Set up service instance."""
        FileSearchService._instance = None
        self.service = FileSearchService.get_instance()

    def test_format_results_empty_list(self):
        """Test markdown formatting with empty file list."""
        result = self.service._format_results_as_markdown([])

        self.assertEqual(result, "**Found 0 files**")

    def test_format_results_single_file(self):
        """Test markdown formatting with single file."""
        files = ["/home/user/project/file.py"]
        result = self.service._format_results_as_markdown(files)

        expected = "**Found 1 file:**\n\n/home/user/project/file.py"
        self.assertEqual(result, expected)

    def test_format_results_multiple_files(self):
        """Test markdown formatting with multiple files."""
        files = [
            "/home/user/project/file1.py",
            "/home/user/project/file2.py",
            "/home/user/project/file3.py",
        ]
        result = self.service._format_results_as_markdown(files)

        # Check header
        self.assertIn("**Found 3 files:**", result)

        # Check all files are included
        for file in files:
            self.assertIn(file, result)

        # Check structure (header + empty line + files)
        lines = result.split("\n")
        self.assertEqual(lines[0], "**Found 3 files:**")
        self.assertEqual(lines[1], "")
        self.assertEqual(len(lines), 5)  # header + empty + 3 files

    def test_format_results_relative_paths(self):
        """Test markdown formatting with relative paths."""
        files = [
            "src/main.py",
            "tests/test_main.py",
            "README.md",
        ]
        result = self.service._format_results_as_markdown(files)

        self.assertIn("**Found 3 files:**", result)

        for file in files:
            self.assertIn(file, result)

    def test_format_results_windows_paths(self):
        """Test markdown formatting with Windows-style paths."""
        files = [
            "C:\\Users\\user\\project\\file1.py",
            "C:\\Users\\user\\project\\src\\file2.py",
        ]
        result = self.service._format_results_as_markdown(files)

        self.assertIn("**Found 2 files:**", result)

        for file in files:
            self.assertIn(file, result)

    def test_format_results_preserves_order(self):
        """Test that markdown formatting preserves file order."""
        files = [
            "/home/user/z.py",
            "/home/user/a.py",
            "/home/user/m.py",
        ]
        result = self.service._format_results_as_markdown(files)

        # Extract file lines (skip header and empty line)
        lines = result.split("\n")[2:]

        # Verify order is preserved
        self.assertEqual(lines[0], "/home/user/z.py")
        self.assertEqual(lines[1], "/home/user/a.py")
        self.assertEqual(lines[2], "/home/user/m.py")

    def test_format_results_with_special_characters(self):
        """Test markdown formatting with special characters in paths."""
        files = [
            "/home/user/file's.py",
            "/home/user/project (copy)/file.py",
            "/home/user/file&data.py",
        ]
        result = self.service._format_results_as_markdown(files)

        self.assertIn("**Found 3 files:**", result)

        # All special characters should be preserved
        for file in files:
            self.assertIn(file, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
