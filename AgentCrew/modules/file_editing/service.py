"""
File editing service orchestrating search/replace, syntax checking, and safety validation.

Main service for file editing operations in AgentCrew.
"""

from typing import Dict, Any, Optional
import os
import shutil
import uuid
from datetime import datetime

from .search_replace_engine import SearchReplaceEngine
from .tree_sitter_checker import TreeSitterChecker
from .safety_validator import SafetyValidator, SafetyConfig


class FileEditingService:
    """
    Main file editing service with exact matching and tree-sitter validation.

    Features:
    - Search/replace blocks with exact matching
    - Universal syntax checking via tree-sitter
    - Safety validation and path restrictions
    - Automatic backup and rollback
    - Atomic file writes
    """

    def __init__(self, safety_config: Optional[SafetyConfig] = None):
        """
        Initialize file editing service.

        Args:
            safety_config: Optional safety configuration. Defaults to permissive config.
        """
        self.search_replace_engine = SearchReplaceEngine()
        self.syntax_checker = TreeSitterChecker()
        self.safety_validator = SafetyValidator(safety_config or SafetyConfig())

    def write_or_edit_file(
        self,
        file_path: str,
        percentage_to_change: float,
        text_or_search_replace_blocks: str,
        agent_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for file editing.

        Decision logic:
        - percentage > 50: Full file write
        - percentage <= 50: Search/replace blocks

        Args:
            file_path: Path to file (absolute or relative, ~ supported)
            percentage_to_change: Percentage of lines changing (0-100)
            text_or_search_replace_blocks: Full content or search/replace blocks
            agent_name: Optional agent name for permission checks

        Returns:
            Dict with status, errors, and results
        """
        file_path = self._resolve_path(file_path)

        validation = self.safety_validator.validate_write_permission(
            file_path, agent_name
        )

        if not validation.allowed:
            return {
                "status": "denied",
                "error": validation.error_message,
                "suggestion": validation.suggestion,
            }

        safety_check = self.safety_validator.validate_file_safety(file_path)
        if not safety_check.allowed:
            return {
                "status": "denied",
                "error": safety_check.error_message,
                "suggestion": safety_check.suggestion,
            }

        backup_path = None
        if os.path.exists(file_path) and self.safety_validator.config.create_backups:
            try:
                backup_path = self._create_backup(file_path)
            except Exception as e:
                return {
                    "status": "error",
                    "error": f"Failed to create backup: {e}",
                    "suggestion": "Check backup directory permissions",
                }

        try:
            if percentage_to_change > 50:
                result = self._write_full_file(file_path, text_or_search_replace_blocks)
            else:
                result = self._apply_search_replace(
                    file_path, text_or_search_replace_blocks
                )

            if result["status"] != "success":
                return result

            syntax_result = self.syntax_checker.check_syntax(
                file_path, result["new_content"]
            )

            if not syntax_result.is_valid:
                if backup_path and os.path.exists(backup_path):
                    shutil.copy2(backup_path, file_path)

                return {
                    "status": "syntax_error",
                    "errors": [
                        {
                            "line": err.line,
                            "column": err.column,
                            "message": err.message,
                            "severity": err.severity,
                        }
                        for err in syntax_result.errors
                    ],
                    "language": syntax_result.language,
                    "suggestion": "Fix syntax errors and retry. File has been restored from backup.",
                    "backup_restored": backup_path is not None,
                }

            self._atomic_write(file_path, result["new_content"])

            return {
                "status": "success",
                "file_path": file_path,
                "changes_applied": result.get("blocks_applied", 1),
                "syntax_check": {"is_valid": True, "language": syntax_result.language},
                "backup_created": backup_path is not None,
            }

        except Exception as e:
            if backup_path and os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, file_path)
                except Exception:
                    pass

            return {
                "status": "error",
                "error": str(e),
                "backup_restored": backup_path is not None,
            }

    def _apply_search_replace(self, file_path: str, blocks_text: str) -> Dict[str, Any]:
        """
        Apply search/replace blocks to file.

        Args:
            file_path: Path to file
            blocks_text: Search/replace blocks text

        Returns:
            Dict with status and new_content
        """
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "error": f"File not found: {file_path}",
                "suggestion": "Use percentage_to_change > 50 to create new files",
            }

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        except UnicodeDecodeError:
            return {
                "status": "error",
                "error": f"Cannot read file (not UTF-8 encoded): {file_path}",
                "suggestion": "Ensure file is text-based and UTF-8 encoded",
            }

        # Parse blocks
        try:
            blocks = self.search_replace_engine.parse_blocks(blocks_text)
        except ValueError as e:
            return {
                "status": "parse_error",
                "error": str(e),
                "suggestion": "Check search/replace block format",
            }

        new_content, results = self.search_replace_engine.apply_blocks(
            original_content, blocks
        )

        failed_results = [r for r in results if r.status != "success"]
        if failed_results:
            failure = failed_results[0]
            return {
                "status": failure.status,
                "error": failure.error_message,
                "block_index": failure.block.block_index,
                "suggestion": "Fix the search block and retry",
            }

        return {
            "status": "success",
            "new_content": new_content,
            "blocks_applied": len(results),
        }

    def _write_full_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """
        Write complete file content.

        Args:
            file_path: Path to file
            content: Complete file content

        Returns:
            Dict with status and new_content
        """
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                return {
                    "status": "error",
                    "error": f"Cannot create parent directory: {e}",
                    "suggestion": "Check directory permissions",
                }

        return {"status": "success", "new_content": content, "blocks_applied": 1}

    def _atomic_write(self, file_path: str, content: str):
        """
        Atomic file write to prevent corruption.

        Args:
            file_path: Destination file path
            content: Content to write
        """
        temp_path = f"{file_path}.tmp.{uuid.uuid4()}"

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)

            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def _create_backup(self, file_path: str) -> str:
        """
        Create timestamped backup of file.

        Args:
            file_path: File to backup

        Returns:
            Path to backup file
        """
        backup_dir = self.safety_validator.config.backup_directory
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(file_path)
        backup_path = os.path.join(backup_dir, f"{filename}.{timestamp}.backup")

        shutil.copy2(file_path, backup_path)
        return backup_path

    def _resolve_path(self, file_path: str) -> str:
        """
        Resolve and normalize file path.

        Args:
            file_path: Input path (may be relative or use ~)

        Returns:
            Absolute normalized path
        """
        file_path = os.path.expanduser(file_path)

        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        return file_path
