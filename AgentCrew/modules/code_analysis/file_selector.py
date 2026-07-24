from __future__ import annotations

import fnmatch
import json
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

MAX_FILES_TO_ANALYZE = 150


class FileSelector:
    """Selects relevant files for analysis using LLM-based exclusion patterns."""

    def __init__(self, llm_service: BaseLLMService | None = None):
        self._llm_service = llm_service

    async def select_files_with_llm(
        self,
        files: list[str],
        max_files: int = MAX_FILES_TO_ANALYZE,
        feature_scope: str | None = None,
    ) -> list[str]:
        """Use LLM to intelligently select which files to analyze from a large repository.

        Args:
            files: list of relative file paths to select from
            max_files: Maximum number of files to select
            feature_scope: Optional feature scope to prioritize relevant files

        Returns:
            list of selected file paths that should be analyzed
        """
        if not self._llm_service:
            return files[:max_files]

        feature_scope_instruction = ""
        if feature_scope:
            feature_scope_instruction = f"""
FEATURE SCOPE: "{feature_scope}"
This analysis is focused on a specific feature area. You MUST:
- Keep ALL files directly related to: {feature_scope}
- Keep test files that test the feature scope area (they reveal expected behavior and contracts)
- Keep configuration and wiring files that register or connect feature-scoped modules
- Keep base/shared modules that the feature-scoped code depends on
- Exclude files from unrelated feature areas even if they would normally be kept
- When in doubt about a file's relevance, KEEP it rather than exclude it
"""

        prompt = f"""You are analyzing a code repository with {len(files)} files.
The analysis system can only process {max_files} files at a time.

Generate glob patterns to EXCLUDE less important files. The goal is to keep around {max_files} most important files after exclusion.
{feature_scope_instruction}
Files to EXCLUDE (generate patterns for these):
1. Test files — UNLESS they test the feature scope area
2. Generated/build output files
3. Vendor/dependency files (node_modules, vendor, third-party)
4. Documentation files (e.g., **/docs/**, **/*.md) — UNLESS they describe the feature scope
5. Configuration duplicates and environment files (keep primary config)
6. Database migration files
7. Static assets (images, fonts, icons, etc.)
8. Example/sample/demo files
9. Files from unrelated feature areas when a feature scope is specified

Files to KEEP (NEVER exclude) - ordered by priority:
1. Files directly relevant to the feature scope (if specified)
2. Entry points, routing, and module registration/wiring that connect feature-scoped code
3. Base classes, abstract classes, and interfaces that feature-scoped code inherits or implements
4. Core application logic (main entry points, core modules)
5. Shared functions, utilities, and helper modules that feature-scoped code uses
6. Service classes, middleware, and dependency injection setup
7. Key configuration files that define app structure
8. Test files for the feature scope area (they reveal expected behavior and contracts)

Here is the complete list of files:
{chr(10).join(files)}

Current file count: {len(files)}
Target file count: ~{max_files}
Files to exclude: ~{max(0, len(files) - max_files)}

Return ONLY a JSON array of glob patterns to exclude. Be strategic - use broad patterns when possible.

Example response format:
["**/tests/**", "**/test_*", "**/*.test.*", "**/docs/**", "**/migrations/**", "**/__pycache__/**"]"""

        try:
            response = await self._llm_service.process_message(prompt, temperature=0.5)

            response = response.strip()
            response = response.removeprefix("```json")
            response = response.removeprefix("```")
            response = response.removesuffix("```")
            response = response.strip()

            exclude_patterns = json.loads(response)

            if isinstance(exclude_patterns, list):
                filtered_files = []
                for file_path in files:
                    excluded = False
                    for pattern in exclude_patterns:
                        if fnmatch.fnmatch(file_path, pattern):
                            excluded = True
                            break
                    if not excluded:
                        filtered_files.append(file_path)

                logger.info(
                    f"LLM exclusion patterns reduced files from {len(files)} to {len(filtered_files)}"
                )

                return filtered_files[:max_files]
        except Exception as e:
            logger.warning(f"Cannot extract exclusion patterns from LLM response: {e}")

        return files[:max_files]
