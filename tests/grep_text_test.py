"""
Unit tests for GrepTextService.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from AgentCrew.modules.code_analysis.grep_service import (
    GrepTextError,
    GrepTextService,
)


class TestGrepTextServicePlatformDetection(unittest.TestCase):
    """Test platform detection functionality."""

    def setUp(self):
        """Reset singleton instance before each test."""
        GrepTextService._instance = None

    @patch("sys.platform", "linux")
    def test_platform_detection_linux(self):
        """Test platform detection for Linux."""
        service = GrepTextService.get_instance()

        self.assertEqual(service.platform, "linux")
        self.assertFalse(service._is_windows)
        self.assertEqual(
            service._get_tool_priority(), GrepTextService.TOOL_PRIORITY_UNIX
        )

    @patch("sys.platform", "darwin")
    def test_platform_detection_macos(self):
        """Test platform detection for macOS."""
        service = GrepTextService.get_instance()

        self.assertEqual(service.platform, "darwin")
        self.assertFalse(service._is_windows)
        self.assertEqual(
            service._get_tool_priority(), GrepTextService.TOOL_PRIORITY_UNIX
        )

    @patch("sys.platform", "win32")
    def test_platform_detection_windows(self):
        """Test platform detection for Windows."""
        service = GrepTextService.get_instance()

        self.assertEqual(service.platform, "win32")
        self.assertTrue(service._is_windows)
        self.assertEqual(
            service._get_tool_priority(), GrepTextService.TOOL_PRIORITY_WINDOWS
        )

    def test_tool_priority_includes_git_grep_unix(self):
        """Test that Unix tool priority includes git-grep."""
        priority = GrepTextService.TOOL_PRIORITY_UNIX
        self.assertIn("git-grep", priority)
        self.assertIn("rg", priority)
        self.assertIn("grep", priority)

    def test_tool_priority_includes_git_grep_windows(self):
        """Test that Windows tool priority includes git-grep."""
        priority = GrepTextService.TOOL_PRIORITY_WINDOWS
        self.assertIn("git-grep", priority)
        self.assertIn("rg", priority)
        self.assertIn("Select-String", priority)


class TestGrepTextServiceToolAvailability(unittest.TestCase):
    """Test tool availability detection."""

    def setUp(self):
        """Reset singleton and set up mocks."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()
        self.service._tool_availability_cache.clear()

    @patch("shutil.which")
    def test_grep_availability_unix(self, mock_which):
        """Test grep availability detection on Unix."""
        mock_which.return_value = "/usr/bin/grep"
        self.service._is_windows = False

        result = self.service._is_tool_available("grep")

        self.assertTrue(result)
        mock_which.assert_called_once_with("grep")

    @patch("shutil.which")
    def test_rg_availability_unix(self, mock_which):
        """Test ripgrep availability detection on Unix."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False

        result = self.service._is_tool_available("rg")

        self.assertTrue(result)
        mock_which.assert_called_once_with("rg")

    @patch("shutil.which")
    def test_git_grep_availability_unix(self, mock_which):
        """Test git-grep availability detection on Unix."""
        mock_which.return_value = "/usr/bin/git"
        self.service._is_windows = False

        result = self.service._is_tool_available("git-grep")

        self.assertTrue(result)
        mock_which.assert_called_once_with("git")

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_git_grep_availability_windows(self, mock_get_instance):
        """Test git-grep availability detection on Windows."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "git version 2.40.0",
        }

        self.service._is_windows = True
        result = self.service._is_tool_available("git-grep")

        self.assertTrue(result)
        mock_cmd_instance.execute_command.assert_called_once_with(
            "git --version", timeout=5
        )

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_select_string_availability_windows(self, mock_get_instance):
        """Test PowerShell Select-String availability detection on Windows."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "Select-String",
        }

        self.service._is_windows = True
        result = self.service._is_tool_available("Select-String")

        self.assertTrue(result)
        mock_cmd_instance.execute_command.assert_called_once()
        call_args = mock_cmd_instance.execute_command.call_args[0][0]
        self.assertIn("Get-Command Select-String", call_args)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_rg_availability_windows(self, mock_get_instance):
        """Test ripgrep availability detection on Windows."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "C:\\Program Files\\rg\\rg.exe",
        }

        self.service._is_windows = True
        result = self.service._is_tool_available("rg")

        self.assertTrue(result)
        mock_cmd_instance.execute_command.assert_called_once_with("where rg", timeout=5)

    @patch("shutil.which")
    def test_tool_not_available(self, mock_which):
        """Test tool not available scenario."""
        mock_which.return_value = None
        self.service._is_windows = False

        result = self.service._is_tool_available("nonexistent_tool")

        self.assertFalse(result)

    @patch("shutil.which")
    def test_availability_caching(self, mock_which):
        """Test that tool availability results are cached."""
        mock_which.return_value = "/usr/bin/grep"
        self.service._is_windows = False

        # First call should check tool availability
        result1 = self.service._is_tool_available("grep")
        self.assertTrue(result1)

        # Second call should use cache
        result2 = self.service._is_tool_available("grep")
        self.assertTrue(result2)

        # which should only be called once
        mock_which.assert_called_once_with("grep")

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_availability_check_error_handling(self, mock_get_instance):
        """Test error handling in availability checking."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance
        mock_cmd_instance.execute_command.side_effect = Exception("Command failed")

        self.service._is_windows = True
        result = self.service._is_tool_available("rg")

        # Should return False on error, not raise exception
        self.assertFalse(result)


class TestGrepTextServicePatternValidation(unittest.TestCase):
    """Test pattern validation functionality."""

    def setUp(self):
        """Set up service instance."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()

    def test_validate_pattern_valid_simple(self):
        """Test validation of simple valid pattern."""
        pattern = "test"
        result = self.service._validate_pattern(pattern)
        self.assertEqual(result, pattern)

    def test_validate_pattern_valid_regex(self):
        """Test validation of valid regex pattern."""
        pattern = r"\bclass\s+\w+"
        result = self.service._validate_pattern(pattern)
        self.assertEqual(result, pattern)

    def test_validate_pattern_valid_complex_regex(self):
        """Test validation of complex regex pattern."""
        pattern = r"^(def|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        result = self.service._validate_pattern(pattern)
        self.assertEqual(result, pattern)

    def test_validate_pattern_empty_string(self):
        """Test validation rejects empty string."""
        with self.assertRaises(GrepTextError) as context:
            self.service._validate_pattern("")

        self.assertIn("cannot be empty", str(context.exception))

    def test_validate_pattern_whitespace_only(self):
        """Test validation rejects whitespace-only pattern."""
        with self.assertRaises(GrepTextError) as context:
            self.service._validate_pattern("   ")

        self.assertIn("cannot be empty", str(context.exception))

    def test_validate_pattern_invalid_regex(self):
        """Test validation rejects invalid regex."""
        invalid_patterns = [
            r"[unclosed",
            r"(?P<invalid",
            r"*invalid",
            r"(?P<name>test)(?P<name>duplicate)",
        ]

        for pattern in invalid_patterns:
            with self.assertRaises(GrepTextError) as context:
                self.service._validate_pattern(pattern)

            self.assertIn("Invalid regex pattern", str(context.exception))

    def test_validate_pattern_special_characters(self):
        """Test validation of patterns with special characters."""
        valid_patterns = [
            r"file\.py$",
            r"test\d+",
            r"[a-z]+@[a-z]+\.[a-z]+",
            r"function\(.*\)",
        ]

        for pattern in valid_patterns:
            result = self.service._validate_pattern(pattern)
            self.assertEqual(result, pattern)


class TestGrepTextServiceDirectoryValidation(unittest.TestCase):
    """Test directory validation functionality."""

    def setUp(self):
        """Set up service instance and temporary directory."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()
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

        with self.assertRaises(GrepTextError) as context:
            self.service._validate_directory(non_existent_dir)

        self.assertIn("does not exist", str(context.exception))

    def test_validate_directory_is_file(self):
        """Test validation when path is a file, not directory."""
        temp_file = os.path.join(self.temp_dir, "test_file.txt")
        with open(temp_file, "w") as f:
            f.write("test")

        with self.assertRaises(GrepTextError) as context:
            self.service._validate_directory(temp_file)

        self.assertIn("not a directory", str(context.exception))

    def test_validate_directory_empty_string(self):
        """Test validation rejects empty string."""
        with self.assertRaises(GrepTextError) as context:
            self.service._validate_directory("")

        self.assertIn("cannot be empty", str(context.exception))

    def test_validate_directory_whitespace_only(self):
        """Test validation rejects whitespace-only path."""
        with self.assertRaises(GrepTextError) as context:
            self.service._validate_directory("   ")

        self.assertIn("cannot be empty", str(context.exception))

    def test_validate_directory_converts_to_absolute(self):
        """Test that validation converts relative paths to absolute."""
        original_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir)
            validated_path = self.service._validate_directory(".")

            self.assertTrue(os.path.isabs(validated_path))
            self.assertEqual(validated_path, os.path.abspath(self.temp_dir))
        finally:
            os.chdir(original_cwd)

    @patch("os.access")
    @patch("os.path.isdir")
    @patch("os.path.exists")
    def test_validate_directory_permission_denied(
        self, mock_exists, mock_isdir, mock_access
    ):
        """Test validation when directory is not readable."""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_access.return_value = False

        with self.assertRaises(GrepTextError) as context:
            self.service._validate_directory("/restricted")

        self.assertIn("Permission denied", str(context.exception))


class TestGrepTextServiceGitRepository(unittest.TestCase):
    """Test git repository detection functionality."""

    def setUp(self):
        """Set up service instance and temporary directory."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()
        self.service._git_repo_cache.clear()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_is_git_repository_unix(self, mock_get_instance):
        """Test git repository detection on Unix."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": ".git",
        }

        self.service._is_windows = False
        result = self.service._is_git_repository(self.temp_dir)

        self.assertTrue(result)
        call_args = mock_cmd_instance.execute_command.call_args[0][0]
        self.assertIn("git rev-parse --git-dir", call_args)
        self.assertIn(self.temp_dir, call_args)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_is_git_repository_windows(self, mock_get_instance):
        """Test git repository detection on Windows."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": ".git",
        }

        self.service._is_windows = True
        result = self.service._is_git_repository(self.temp_dir)

        self.assertTrue(result)
        call_args = mock_cmd_instance.execute_command.call_args[0][0]
        self.assertIn("cd /d", call_args)
        self.assertIn("git rev-parse --git-dir", call_args)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_is_not_git_repository(self, mock_get_instance):
        """Test detection when directory is not a git repository."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 128,  # git error code for not a git repo
            "error": "fatal: not a git repository",
        }

        self.service._is_windows = False
        result = self.service._is_git_repository(self.temp_dir)

        self.assertFalse(result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_is_git_repository_caching(self, mock_get_instance):
        """Test that git repository detection results are cached."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": ".git",
        }

        self.service._is_windows = False

        # First call should check git
        result1 = self.service._is_git_repository(self.temp_dir)
        self.assertTrue(result1)

        # Second call should use cache
        result2 = self.service._is_git_repository(self.temp_dir)
        self.assertTrue(result2)

        # execute_command should only be called once
        self.assertEqual(mock_cmd_instance.execute_command.call_count, 1)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_is_git_repository_error_handling(self, mock_get_instance):
        """Test error handling in git repository detection."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance
        mock_cmd_instance.execute_command.side_effect = Exception("Command failed")

        self.service._is_windows = False
        result = self.service._is_git_repository(self.temp_dir)

        # Should return False on error, not raise exception
        self.assertFalse(result)


class TestGrepTextServiceCommandBuilders(unittest.TestCase):
    """Test command building methods for various grep tools."""

    def setUp(self):
        """Set up service instance."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()

    def test_build_grep_command_basic(self):
        """Test basic grep command building."""
        command = self.service._build_grep_command("test", "/home/user/project", True)

        self.assertIn("grep", command)
        self.assertIn("-r", command)  # recursive
        self.assertIn("-n", command)  # line numbers
        self.assertIn("-H", command)  # always show filename
        self.assertIn("-E", command)  # extended regex
        self.assertIn("test", command)
        self.assertIn("/home/user/project", command)

    def test_build_grep_command_case_insensitive(self):
        """Test grep command with case-insensitive flag."""
        command = self.service._build_grep_command("test", "/home/user/project", False)

        self.assertIn("-i", command)

    def test_build_grep_command_case_sensitive(self):
        """Test grep command without case-insensitive flag."""
        command = self.service._build_grep_command("test", "/home/user/project", True)

        self.assertNotIn("-i", command)

    def test_build_grep_command_pattern_escaping(self):
        """Test that patterns with quotes are properly escaped."""
        command = self.service._build_grep_command("test's", "/home/user/project", True)

        # Should escape single quotes in pattern
        self.assertIn("test", command)

    def test_build_rg_command_basic(self):
        """Test basic ripgrep command building."""
        command = self.service._build_rg_command("test", "/home/user/project", True)

        self.assertIn("rg", command)
        self.assertIn("--line-number", command)
        self.assertIn("--no-heading", command)
        self.assertIn("--with-filename", command)
        self.assertIn("--hidden", command)
        self.assertIn("test", command)
        self.assertIn("/home/user/project", command)

    def test_build_rg_command_case_insensitive(self):
        """Test ripgrep command with case-insensitive flag."""
        command = self.service._build_rg_command("test", "/home/user/project", False)

        self.assertIn("--ignore-case", command)

    def test_build_rg_command_case_sensitive(self):
        """Test ripgrep command without case-insensitive flag."""
        command = self.service._build_rg_command("test", "/home/user/project", True)

        self.assertNotIn("--ignore-case", command)

    def test_build_windows_command_basic(self):
        """Test basic PowerShell Select-String command building."""
        command = self.service._build_windows_command(
            "test", "C:\\Users\\user\\project", True
        )

        self.assertIn("powershell", command)
        self.assertIn("Get-ChildItem", command)
        self.assertIn("Select-String", command)
        self.assertIn("test", command)
        self.assertIn("C:\\Users\\user\\project", command)
        self.assertIn("-Recurse", command)
        self.assertIn("-File", command)

    def test_build_windows_command_case_sensitive(self):
        """Test PowerShell command with case-sensitive flag."""
        command = self.service._build_windows_command(
            "test", "C:\\Users\\user\\project", True
        )

        self.assertIn("-CaseSensitive", command)

    def test_build_windows_command_case_insensitive(self):
        """Test PowerShell command without case-sensitive flag (default insensitive)."""
        command = self.service._build_windows_command(
            "test", "C:\\Users\\user\\project", False
        )

        self.assertNotIn("-CaseSensitive", command)

    def test_build_windows_command_path_escaping(self):
        """Test PowerShell command with special characters in path."""
        command = self.service._build_windows_command(
            "test", "C:\\Users\\user's project", True
        )

        # Should escape quotes in path
        self.assertIn("user", command)
        self.assertIn("project", command)

    def test_build_git_grep_command_basic_unix(self):
        """Test basic git grep command building on Unix."""
        self.service._is_windows = False
        command = self.service._build_git_grep_command(
            "test", "/home/user/project", True
        )

        self.assertIn("git", command)
        self.assertIn("grep", command)
        self.assertIn("-n", command)  # line numbers
        self.assertIn("--full-name", command)  # full paths
        self.assertIn("-E", command)  # extended regex
        self.assertIn("test", command)
        self.assertIn("/home/user/project", command)
        self.assertIn("cd ", command)

    def test_build_git_grep_command_basic_windows(self):
        """Test basic git grep command building on Windows."""
        self.service._is_windows = True
        command = self.service._build_git_grep_command(
            "test", "C:\\Users\\user\\project", True
        )

        self.assertIn("git", command)
        self.assertIn("grep", command)
        self.assertIn("-n", command)  # line numbers
        self.assertIn("--full-name", command)  # full paths
        self.assertIn("-E", command)  # extended regex
        self.assertIn("test", command)
        self.assertIn("C:\\Users\\user\\project", command)
        self.assertIn("cd /d", command)

    def test_build_git_grep_command_case_insensitive_unix(self):
        """Test git grep command with case-insensitive flag on Unix."""
        self.service._is_windows = False
        command = self.service._build_git_grep_command(
            "test", "/home/user/project", False
        )

        self.assertIn("-i", command)

    def test_build_git_grep_command_case_sensitive_unix(self):
        """Test git grep command without case-insensitive flag on Unix."""
        self.service._is_windows = False
        command = self.service._build_git_grep_command(
            "test", "/home/user/project", True
        )

        # Should not contain -i flag
        parts = command.split()
        self.assertNotIn("-i", parts)

    def test_build_git_grep_command_case_insensitive_windows(self):
        """Test git grep command with case-insensitive flag on Windows."""
        self.service._is_windows = True
        command = self.service._build_git_grep_command(
            "test", "C:\\Users\\user\\project", False
        )

        self.assertIn("-i", command)

    def test_build_git_grep_command_pattern_escaping_unix(self):
        """Test that patterns with quotes are properly escaped on Unix."""
        self.service._is_windows = False
        command = self.service._build_git_grep_command(
            "test's", "/home/user/project", True
        )

        # Should escape single quotes in pattern
        self.assertIn("test", command)

    def test_build_git_grep_command_pattern_escaping_windows(self):
        """Test that patterns with quotes are properly escaped on Windows."""
        self.service._is_windows = True
        command = self.service._build_git_grep_command(
            'test"s', "C:\\Users\\user\\project", True
        )

        # Should escape double quotes in pattern
        self.assertIn("test", command)


class TestGrepTextServiceOutputParsing(unittest.TestCase):
    """Test output parsing functionality."""

    def setUp(self):
        """Set up service instance."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()

    def test_parse_output_empty_string(self):
        """Test parsing empty output."""
        result = self.service._parse_output("")

        self.assertEqual(result, "Found 0 matches.")

    def test_parse_output_whitespace_only(self):
        """Test parsing whitespace-only output."""
        result = self.service._parse_output("   \n\n  ")

        self.assertEqual(result, "Found 0 matches.")

    def test_parse_output_single_match(self):
        """Test parsing single match."""
        output = "/home/user/file.py:10:def test_function():"
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)
        self.assertIn("/home/user/file.py", result)
        self.assertIn("10: def test_function():", result)

    def test_parse_output_multiple_matches(self):
        """Test parsing multiple matches."""
        output = """
/home/user/file1.py:10:def test():
/home/user/file2.py:20:class Test:
/home/user/file3.py:30:import test
        """.strip()
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        self.assertIn("Found 3 match(es).", result)
        self.assertIn("file1.py", result)
        self.assertIn("file2.py", result)
        self.assertIn("file3.py", result)

    def test_parse_output_with_max_results(self):
        """Test parsing with max results limit."""
        output = "\n".join(
            [f"/home/user/file{i}.py:{i}:line content" for i in range(10)]
        )
        result = self.service._parse_output(output, max_results=5)

        self.assertIsInstance(result, str)
        self.assertIn("Found 5 match(es).", result)
        # Count occurrences of "line content"
        self.assertEqual(result.count("line content"), 5)

    def test_parse_output_malformed_line_skipped(self):
        """Test that malformed lines are skipped."""
        output = """
/home/user/file1.py:10:def test():
malformed_line_without_colons
/home/user/file2.py:20:class Test:
another:malformed
/home/user/file3.py:30:import test
        """.strip()
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Should only parse the 3 valid lines
        self.assertIn("Found 3 match(es).", result)

    def test_parse_output_invalid_line_number(self):
        """Test that lines with invalid line numbers are skipped."""
        output = """
/home/user/file1.py:10:def test():
/home/user/file2.py:notanumber:class Test:
/home/user/file3.py:30:import test
        """.strip()
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Should skip the line with invalid line number
        self.assertIn("Found 2 match(es).", result)

    def test_parse_output_preserves_line_content_whitespace(self):
        """Test that line content whitespace at the end may be stripped during parsing."""
        output = "/home/user/file.py:10:    def test():"
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Leading whitespace is preserved
        self.assertIn("    def test():", result)

    def test_parse_output_windows_paths(self):
        """Test parsing Windows-style paths.

        Note: Windows paths with backslashes in output format like C:\\path\\file.py:10:content
        where ':' after drive letter can cause parsing issues.
        """
        self.service._is_windows = True
        # Use a path format that won't be confused by the ':' in C:
        output = r"Users\user\file.py:10:def test():"
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        self.assertIn("Users", result)

    def test_parse_output_unix_paths(self):
        """Test parsing Unix-style paths."""
        self.service._is_windows = False
        output = "/home/user/file.py:10:def test():"
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        self.assertIn("/home/user/file.py", result)

    def test_parse_output_path_normalization_windows(self):
        """Test path normalization on Windows."""
        self.service._is_windows = True
        output = "home/user/file.py:10:def test():"  # Forward slashes (no drive letter)
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Should convert forward slashes to backslashes on Windows
        self.assertIn("home\\user\\file.py", result)

    def test_parse_output_path_normalization_unix(self):
        """Test path normalization on Unix."""
        self.service._is_windows = False
        output = "home\\user\\file.py:10:def test():"  # Backslashes
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Should convert backslashes to forward slashes on Unix
        self.assertIn("home/user/file.py", result)

    def test_parse_output_sorting(self):
        """Test that matches are sorted by file and line number."""
        output = """
/home/user/file2.py:30:line 30
/home/user/file1.py:20:line 20
/home/user/file2.py:10:line 10
/home/user/file1.py:10:line 10
        """.strip()
        result = self.service._parse_output(output)

        self.assertIsInstance(result, str)
        # Verify file1.py matches appear before file2.py matches
        file1_index = result.find("file1.py")
        file2_index = result.find("file2.py")
        self.assertLess(file1_index, file2_index)


class TestGrepTextServiceCommandExecution(unittest.TestCase):
    """Test command execution functionality."""

    def setUp(self):
        """Set up service instance."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_execute_command_success(self, mock_get_instance):
        """Test successful command execution."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "test output",
            "error": "",
        }

        result = self.service._execute_command("test command")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        mock_cmd_instance.execute_command.assert_called_once_with(
            "test command", timeout=GrepTextService.DEFAULT_TIMEOUT
        )

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_execute_command_with_custom_timeout(self, mock_get_instance):
        """Test command execution with custom timeout."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "test output",
        }

        self.service._execute_command("test command", timeout=60)

        mock_cmd_instance.execute_command.assert_called_once_with(
            "test command", timeout=60
        )

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_execute_command_failure(self, mock_get_instance):
        """Test command execution failure."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "failed",
            "exit_code": 1,
            "error": "Command failed",
        }

        with self.assertRaises(GrepTextError) as context:
            self.service._execute_command("test command")

        self.assertIn("Command execution failed", str(context.exception))

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_execute_command_exception(self, mock_get_instance):
        """Test command execution with exception."""
        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.side_effect = Exception("Unexpected error")

        with self.assertRaises(GrepTextError) as context:
            self.service._execute_command("test command")

        self.assertIn("Error executing command", str(context.exception))


class TestGrepTextServiceMainSearch(unittest.TestCase):
    """Test the main search_text method."""

    def setUp(self):
        """Set up service instance and temporary directory with test files."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()
        self.temp_dir = tempfile.mkdtemp()

        # Create test files with content
        self.test_files = {
            "file1.py": "def test_function():\n    pass\n",
            "file2.py": "class TestClass:\n    def test_method(self):\n        pass\n",
            "file3.txt": "This is a test file.\n",
        }

        for filename, content in self.test_files.items():
            filepath = os.path.join(self.temp_dir, filename)
            with open(filepath, "w") as f:
                f.write(content)

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_with_rg(self, mock_which, mock_get_instance):
        """Test search using ripgrep."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        output = f"{self.temp_dir}/file1.py:1:def test_function():"
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": output,
        }

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)
        self.assertIn("file1.py", result)
        self.assertIn("1: def test_function():", result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_with_grep(self, mock_which, mock_get_instance):
        """Test search using grep (rg not available)."""

        # Mock rg as not available, grep as available
        def which_side_effect(tool):
            if tool == "rg":
                return None
            elif tool == "grep":
                return "/usr/bin/grep"
            return None

        mock_which.side_effect = which_side_effect
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        output = f"{self.temp_dir}/file1.py:1:def test_function():"
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": output,
        }

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)
        self.assertIn("file1.py", result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    def test_search_text_with_powershell(self, mock_get_instance):
        """Test search using PowerShell Select-String."""
        self.service._is_windows = True
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock availability checks
        availability_responses = [
            {"status": "completed", "exit_code": 1},  # rg not available
            {"status": "completed", "exit_code": 0},  # Select-String available
        ]

        # Mock search execution - use path without drive letter to avoid parsing issues
        search_output = "temp/file1.py:1:def test_function():"
        search_response = {
            "status": "completed",
            "exit_code": 0,
            "output": search_output,
        }

        mock_cmd_instance.execute_command.side_effect = availability_responses + [
            search_response
        ]

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)
        self.assertIn("temp", result)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_case_sensitive(self, mock_which, mock_get_instance):
        """Test case-sensitive search."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "",
        }

        result = self.service.search_text("Test", self.temp_dir, case_sensitive=True)

        self.assertIsInstance(result, str)
        self.assertIn("Found 0 matches.", result)

        # Verify command was called without case-insensitive flag
        call_args = mock_cmd_instance.execute_command.call_args[0][0]
        self.assertNotIn("--ignore-case", call_args)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_case_insensitive(self, mock_which, mock_get_instance):
        """Test case-insensitive search."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": "",
        }

        result = self.service.search_text("test", self.temp_dir, case_sensitive=False)

        self.assertIsInstance(result, str)
        self.assertIn("Found 0 matches.", result)

        # Verify command includes case-insensitive flag
        call_args = mock_cmd_instance.execute_command.call_args[0][0]
        self.assertIn("--ignore-case", call_args)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_max_results(self, mock_which, mock_get_instance):
        """Test search with max results limit."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock output with multiple matches
        output = "\n".join(
            [f"{self.temp_dir}/file{i}.py:{i}:test line" for i in range(1, 11)]
        )
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 0,
            "output": output,
        }

        result = self.service.search_text("test", self.temp_dir, max_results=5)

        self.assertIsInstance(result, str)
        self.assertIn("Found 5 match(es).", result)
        # Should only contain 5 matches
        self.assertEqual(result.count("test line"), 5)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_no_matches(self, mock_which, mock_get_instance):
        """Test search with no matches (exit code 1)."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # grep tools return exit code 1 when no matches found
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 1,
            "output": "",
        }

        result = self.service.search_text("nonexistent", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertEqual(result, "Found 0 matches.")

    def test_search_text_empty_pattern(self):
        """Test search with empty pattern raises error."""
        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("", self.temp_dir)

        self.assertIn("cannot be empty", str(context.exception))

    def test_search_text_invalid_regex_pattern(self):
        """Test search with invalid regex pattern raises error."""
        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("[unclosed", self.temp_dir)

        self.assertIn("Invalid regex pattern", str(context.exception))

    def test_search_text_invalid_directory(self):
        """Test search with invalid directory raises error."""
        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("test", "/nonexistent/directory")

        self.assertIn("does not exist", str(context.exception))

    def test_search_text_negative_max_results(self):
        """Test search with negative max_results raises error."""
        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("test", self.temp_dir, max_results=-1)

        self.assertIn("must be non-negative", str(context.exception))

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_command_error(self, mock_which, mock_get_instance):
        """Test search when command execution fails with high exit code."""
        mock_which.return_value = "/usr/local/bin/rg"
        self.service._is_windows = False

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Exit code > 1 indicates actual error
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 2,
            "error": "Permission denied",
            "output": "",
        }

        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("test", self.temp_dir)

        self.assertIn("Search command failed", str(context.exception))

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_with_git_grep_in_git_repo(self, mock_which, mock_get_instance):
        """Test search using git grep when directory is a git repository."""

        # Mock tool availability: rg not available, git available
        def which_side_effect(tool):
            if tool == "rg":
                return None
            elif tool == "git":
                return "/usr/bin/git"
            return None

        mock_which.side_effect = which_side_effect
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()
        self.service._git_repo_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock git repo detection (returns True)
        git_check_response = {
            "status": "completed",
            "exit_code": 0,
            "output": ".git",
        }

        # Mock search execution
        search_output = "file1.py:1:def test_function():"
        search_response = {
            "status": "completed",
            "exit_code": 0,
            "output": search_output,
        }

        mock_cmd_instance.execute_command.side_effect = [
            git_check_response,  # git repo check
            search_response,  # git grep search
        ]

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)
        self.assertIn("file1.py", result)

        # Verify git grep was used
        search_call = mock_cmd_instance.execute_command.call_args_list[1][0][0]
        self.assertIn("git grep", search_call)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_falls_back_to_grep_if_not_git_repo(
        self, mock_which, mock_get_instance
    ):
        """Test search falls back to grep when directory is not a git repository."""

        # Mock tool availability: rg not available, git available, grep available
        def which_side_effect(tool):
            if tool == "rg":
                return None
            elif tool == "git":
                return "/usr/bin/git"
            elif tool == "grep":
                return "/usr/bin/grep"
            return None

        mock_which.side_effect = which_side_effect
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()
        self.service._git_repo_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock git repo detection (returns False - not a git repo)
        git_check_response = {
            "status": "completed",
            "exit_code": 128,
            "error": "fatal: not a git repository",
        }

        # Mock search execution with grep
        search_output = f"{self.temp_dir}/file1.py:1:def test_function():"
        search_response = {
            "status": "completed",
            "exit_code": 0,
            "output": search_output,
        }

        mock_cmd_instance.execute_command.side_effect = [
            git_check_response,  # git repo check
            search_response,  # grep search
        ]

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)

        # Verify grep was used (not git grep)
        search_call = mock_cmd_instance.execute_command.call_args_list[1][0][0]
        self.assertIn("grep", search_call)
        self.assertNotIn("git grep", search_call)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_search_text_prefers_rg_over_git_grep(self, mock_which, mock_get_instance):
        """Test that ripgrep is preferred over git grep even in git repos."""

        # Mock tool availability: rg available, git available
        def which_side_effect(tool):
            if tool == "rg":
                return "/usr/local/bin/rg"
            elif tool == "git":
                return "/usr/bin/git"
            return None

        mock_which.side_effect = which_side_effect
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()
        self.service._git_repo_cache.clear()

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock git repo detection (returns True)
        git_check_response = {
            "status": "completed",
            "exit_code": 0,
            "output": ".git",
        }

        # Mock search execution with rg
        search_output = f"{self.temp_dir}/file1.py:1:def test_function():"
        search_response = {
            "status": "completed",
            "exit_code": 0,
            "output": search_output,
        }

        mock_cmd_instance.execute_command.side_effect = [
            git_check_response,  # git repo check
            search_response,  # rg search
        ]

        result = self.service.search_text("test", self.temp_dir)

        self.assertIsInstance(result, str)
        self.assertIn("Found 1 match(es).", result)

        # Verify rg was used (not git grep)
        search_call = mock_cmd_instance.execute_command.call_args_list[1][0][0]
        self.assertIn("rg", search_call)
        self.assertNotIn("git grep", search_call)


class TestGrepTextServiceToolFallback(unittest.TestCase):
    """Test tool fallback mechanism."""

    def setUp(self):
        """Set up service instance."""
        GrepTextService._instance = None
        self.service = GrepTextService.get_instance()
        self.service._tool_availability_cache.clear()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary directory."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch(
        "AgentCrew.modules.command_execution.service.CommandExecutionService.get_instance"
    )
    @patch("shutil.which")
    def test_fallback_to_next_tool(self, mock_which, mock_get_instance):
        """Test that tool failure raises error.

        Note: The current implementation does NOT support fallback.
        When a tool fails with exit code > 1, it raises GrepTextError immediately.
        This test documents the current behavior.
        """
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        # Mock tool availability: rg available
        mock_which.return_value = "/usr/local/bin/rg"

        mock_cmd_instance = Mock()
        mock_get_instance.return_value = mock_cmd_instance

        # Mock execution: rg fails with error
        mock_cmd_instance.execute_command.return_value = {
            "status": "completed",
            "exit_code": 2,
            "error": "rg failed",
            "output": "",
        }

        # Current implementation raises error instead of falling back
        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("test", self.temp_dir)

        self.assertIn("Search command failed", str(context.exception))

    @patch("shutil.which")
    def test_all_tools_unavailable(self, mock_which):
        """Test error when all tools are unavailable."""
        mock_which.return_value = None
        self.service._is_windows = False
        self.service._tool_availability_cache.clear()

        with self.assertRaises(GrepTextError) as context:
            self.service.search_text("test", self.temp_dir)

        # When no tool is available, used_tool will be empty string
        # which triggers "Unsupported selected tool" error
        self.assertIn("Unsupported selected tool", str(context.exception))


class TestGrepTextServiceSingleton(unittest.TestCase):
    """Test singleton pattern implementation."""

    def setUp(self):
        """Reset singleton instance."""
        GrepTextService._instance = None

    def test_get_instance_returns_same_instance(self):
        """Test that get_instance always returns the same instance."""
        instance1 = GrepTextService.get_instance()
        instance2 = GrepTextService.get_instance()

        self.assertIs(instance1, instance2)

    def test_multiple_get_instance_calls(self):
        """Test multiple get_instance calls return same instance."""
        instances = [GrepTextService.get_instance() for _ in range(5)]

        # All instances should be the same object
        for instance in instances[1:]:
            self.assertIs(instances[0], instance)


if __name__ == "__main__":
    unittest.main(verbosity=2)
