"""
Safety validator for file editing operations.

Provides path restrictions, permission checks, and safety validations.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
import fnmatch
import tempfile


@dataclass
class SafetyConfig:
    """Configuration for file editing safety."""

    allowed_paths: List[str] = field(default_factory=lambda: ["**/*"])
    denied_paths: List[str] = field(default_factory=list)
    max_file_size_mb: int = 10
    create_backups: bool = True
    backup_directory: str = tempfile.mkdtemp("agentcrew_backups")


@dataclass
class ValidationResult:
    """Result of safety validation."""

    allowed: bool
    error_message: Optional[str] = None
    suggestion: Optional[str] = None


class SafetyValidator:
    """
    Path restrictions and safety checks for file editing.

    Features:
    - Glob pattern-based path restrictions
    - File size limits
    - Backup configuration
    - Agent-specific permissions
    """

    def __init__(self, config: SafetyConfig):
        """
        Initialize safety validator.

        Args:
            config: SafetyConfig with allowed/denied paths and limits
        """
        self.config = config

    def validate_write_permission(
        self, file_path: str, agent_name: Optional[str] = None
    ) -> ValidationResult:
        """
        Check if agent can write to this path.

        Args:
            file_path: Absolute path to file
            agent_name: Name of agent requesting access (optional)

        Returns:
            ValidationResult indicating if write is allowed
        """
        file_path = str(Path(file_path).resolve())

        # Check denied paths first (higher priority)
        if self._matches_any_pattern(file_path, self.config.denied_paths):
            return ValidationResult(
                allowed=False,
                error_message=f"Path {file_path} is in denied paths",
                suggestion="Choose a different file path or update agent configuration to remove from denied paths",
            )

        # Check allowed paths
        if not self._matches_any_pattern(file_path, self.config.allowed_paths):
            return ValidationResult(
                allowed=False,
                error_message=f"Path {file_path} is not in allowed paths",
                suggestion="Update agent configuration to allow this path pattern",
            )

        # Check file size if file exists
        if Path(file_path).exists():
            size_mb = Path(file_path).stat().st_size / (1024 * 1024)
            if size_mb > self.config.max_file_size_mb:
                return ValidationResult(
                    allowed=False,
                    error_message=f"File size {size_mb:.1f}MB exceeds limit {self.config.max_file_size_mb}MB",
                    suggestion="Increase max_file_size_mb in configuration or choose a smaller file",
                )

        return ValidationResult(allowed=True)

    def _matches_any_pattern(self, file_path: str, patterns: List[str]) -> bool:
        """
        Check if file path matches any glob pattern.

        Args:
            file_path: Absolute file path to check
            patterns: List of glob patterns

        Returns:
            True if file matches any pattern
        """
        if not patterns:
            return False

        file_path = str(Path(file_path).resolve())

        for pattern in patterns:
            # Handle absolute patterns
            if Path(pattern).is_absolute() or pattern.startswith("~"):
                pattern_path = str(Path(pattern).expanduser().resolve())
                if fnmatch.fnmatch(file_path, pattern_path):
                    return True
            else:
                # Handle relative patterns - check if file is under pattern directory
                # Convert pattern to absolute based on current working directory
                pattern_abs = str(Path(pattern).resolve())
                if fnmatch.fnmatch(file_path, pattern_abs):
                    return True

                # Also check just the pattern itself for wildcards
                if "*" in pattern or "?" in pattern:
                    if fnmatch.fnmatch(file_path, f"*/{pattern}"):
                        return True
                    if fnmatch.fnmatch(file_path, pattern):
                        return True

        return False

    def validate_file_safety(self, file_path: str) -> ValidationResult:
        """
        Additional safety checks for file operations.

        Args:
            file_path: Path to validate

        Returns:
            ValidationResult
        """
        path = Path(file_path)

        # Check if path traversal attempt
        try:
            path.resolve()
        except Exception as e:
            return ValidationResult(
                allowed=False,
                error_message=f"Invalid file path: {e}",
                suggestion="Provide a valid file path",
            )

        # Check if trying to write to system directories
        dangerous_paths = ["/etc", "/sys", "/proc", "/dev", "/boot"]
        resolved_path = str(path.resolve())

        for dangerous in dangerous_paths:
            if resolved_path.startswith(dangerous):
                return ValidationResult(
                    allowed=False,
                    error_message=f"Cannot write to system directory: {dangerous}",
                    suggestion="Choose a different location in user space",
                )

        return ValidationResult(allowed=True)
