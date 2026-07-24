import subprocess
from unittest.mock import MagicMock, patch

import pytest
from AgentCrew.modules.coding.service import AiderConfig, CodeAssistant


class TestCodeAssistant:
    def test_sanitize_repo_path_valid(self, tmp_path):
        """Test that a valid path passes sanitization."""
        ca = CodeAssistant()
        result = ca._sanitize_repo_path(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_sanitize_repo_path_not_dir(self, tmp_path):
        """Test that a file path raises NotADirectoryError."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        ca = CodeAssistant()
        with pytest.raises(NotADirectoryError):
            ca._sanitize_repo_path(str(test_file))

    def test_sanitize_repo_path_not_exist(self):
        """Test that a non-existent path raises NotADirectoryError."""
        ca = CodeAssistant()
        with pytest.raises(NotADirectoryError):
            ca._sanitize_repo_path("/path/does/not/exist")

    def test_aider_env_var_override(self, monkeypatch, tmp_path):
        """Test that AIDER_PATH environment variable is respected."""
        monkeypatch.setenv("AIDER_PATH", "/usr/local/bin/aider_custom")
        ca = CodeAssistant()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Test output")
            ca.generate_implementation(
                "test spec",
                str(tmp_path),
                aider_config=AiderConfig(
                    "claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest"
                ),
            )

            # Verify the custom path was used
            args, kwargs = mock_run.call_args
            assert args[0][0] == "/usr/local/bin/aider_custom"
            assert kwargs["timeout"] == 120
            assert kwargs["cwd"] == str(tmp_path.resolve())

    def test_generate_implementation_success(self, tmp_path):
        """Test successful code generation."""
        ca = CodeAssistant()

        with (
            patch("subprocess.run") as mock_run,
            patch("tempfile.NamedTemporaryFile") as mock_temp,
        ):
            # Mock the temporary file
            mock_temp_instance = MagicMock()
            mock_temp_instance.__enter__.return_value = mock_temp_instance
            mock_temp_instance.name = "/tmp/test_spec.spec"
            mock_temp.return_value = mock_temp_instance

            # Mock subprocess.run
            mock_run.return_value = MagicMock(stdout="Code generated successfully")

            result = ca.generate_implementation("test spec prompt", str(tmp_path))

            # Verify the result
            assert result == "Code generated successfully"

            # Verify subprocess.run was called with correct arguments
            args, kwargs = mock_run.call_args
            assert "--model" in args[0]
            assert "claude-3-7-sonnet-latest" in args[0]
            assert kwargs["timeout"] == 120
            assert kwargs["cwd"] == str(tmp_path.resolve())

    def test_generate_implementation_subprocess_error(self, tmp_path):
        """Test handling of subprocess error."""
        ca = CodeAssistant()

        with (
            patch("subprocess.run") as mock_run,
            patch("tempfile.NamedTemporaryFile") as mock_temp,
            patch("pathlib.Path.unlink") as mock_unlink,
        ):
            # Mock the temporary file
            mock_temp_instance = MagicMock()
            mock_temp_instance.__enter__.return_value = mock_temp_instance
            mock_temp_instance.name = "/tmp/test_spec.spec"
            mock_temp.return_value = mock_temp_instance

            # Mock subprocess.run to raise an error
            mock_run.side_effect = subprocess.SubprocessError("Command failed")

            result = ca.generate_implementation("test spec prompt", str(tmp_path))

            # Verify error handling
            assert "Error executing aider" in result

            # Verify temp file cleanup was attempted
            mock_unlink.assert_called_once()
