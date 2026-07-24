from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from AgentCrew.modules.llm.base import BaseLLMService

KNOWN_RULE_FILES = [
    ".cursorrules",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    "CONVENTIONS.md",
    ".windsurfrules",
    "AGENTS.md",
    ".editorconfig",
    "CONTRIBUTING.md",
    ".ai/rules.md",
]


class ProjectNotesExtractor:
    """Extracts project notes, rules, and conventions from analysis results."""

    def __init__(self, llm_service: BaseLLMService | None = None):
        self._llm_service = llm_service

    async def extract_project_notes(
        self,
        analysis_result: str,
        repo_path: str,
        feature_scope: str | None = None,
    ) -> str:
        """Extract project notes, rules, and conventions from the analysis result using LLM.

        Sends the analyzed code structure to the LLM with a prompt to extract
        project-specific patterns, conventions, and rules. Also checks for
        known rule/instruction files in the repository.

        Args:
            analysis_result: The formatted analysis result string from analyze_code_structure
            repo_path: The root path of the repository being analyzed
            feature_scope: Optional feature scope to focus extraction on relevant areas

        Returns:
            Structured project notes string for the agent to use as context
        """
        if not self._llm_service:
            return self._fallback_project_notes(repo_path, feature_scope)

        found_rule_files = []
        for rule_file in KNOWN_RULE_FILES:
            full_path = os.path.join(repo_path, rule_file)
            if os.path.isfile(full_path):
                found_rule_files.append(rule_file)

        rule_files_section = ""
        if found_rule_files:
            rule_files_section = f"""\n\nIMPORTANT: The following project rule/instruction files were detected in the repository:
{chr(10).join(f"- {f}" for f in found_rule_files)}\n\nYou MUST read these files using the read_file tool to understand project-specific rules and conventions before making any changes."""

        feature_scope_instruction = ""
        if feature_scope:
            feature_scope_instruction = f"""

FEATURE SCOPE FOCUS: "{feature_scope}"
This analysis is scoped to a specific feature. You MUST prioritize extraction toward this feature:
- Emphasize modules, classes, functions, and patterns directly relevant to: {feature_scope}
- Trace the data flow and control flow through the feature: entry point → service → data layer → output
- Highlight dependencies and integration points this feature has with other parts of the codebase
- Note conventions and rules that specifically apply when working in this feature area
- Only mention general project conventions if they directly affect how this feature should be implemented
- Reduce coverage of unrelated subsystems, unrelated architecture layers, and irrelevant terminology
"""

        prompt = f"""You are analyzing a codebase structure to extract project notes and rules for a development assistant.
{feature_scope_instruction}
The developer already has access to the full structural analysis below. Do NOT repeat information that can be directly read from it (e.g., exact file paths, class names, function signatures). Instead, extract what the structural analysis alone cannot reveal.

Based on the code structure analysis, extract:

1. **Implicit Conventions**: Patterns that are consistently followed but not formally documented (e.g., “services always receive dependencies via constructor, never import them directly”, “error handling uses Result types, not exceptions”)
2. **Cross-Cutting Patterns**: How concerns like logging, error handling, authentication, and configuration are threaded through the codebase
3. **Module Wiring & Registration**: How modules discover and connect to each other (e.g., plugin registries, dependency injection containers, auto-import patterns, factory functions)
4. **Data Flow & Control Flow**: How data moves through the system — from entry points through services to storage/output. Trace the typical request lifecycle.
5. **Domain Terminology**: Formalize domain-specific terms used in names, comments, and structure into a concise glossary. Group related terms together.
6. **Constraint & Rules**: Hard constraints a developer MUST follow (e.g., “new API endpoints must be registered in router.py”, “all database access goes through repository layer”, “state mutations must go through action dispatchers”)
7. **Extension Points**: Where and how new functionality should be added (e.g., “add new tools in tools/ and register in register()”, “new services implement BaseService and are auto-discovered”)

Code Structure Analysis:
{analysis_result}

Return a concise, structured summary in plain text (NOT JSON). Use clear headings and bullet points.
Focus only on actionable insights that help a developer understand how to work within this codebase.
Keep it under 600 words."""

        try:
            response = await self._llm_service.process_message(prompt, temperature=0.5)

            notes = response.strip()
            if rule_files_section:
                notes += rule_files_section

            logger.info("Successfully extracted project notes from analysis result")
            return notes
        except Exception as e:
            logger.warning(f"Failed to extract project notes via LLM: {e}")
            return self._fallback_project_notes(repo_path, feature_scope)

    @staticmethod
    def _fallback_project_notes(
        repo_path: str, feature_scope: str | None = None
    ) -> str:
        """Generate minimal project notes when LLM is unavailable."""
        found_rule_files = []
        for rule_file in KNOWN_RULE_FILES:
            full_path = os.path.join(repo_path, rule_file)
            if os.path.isfile(full_path):
                found_rule_files.append(rule_file)

        scope_hint = (
            f" Focus especially on areas related to: {feature_scope}."
            if feature_scope
            else ""
        )
        notes = f"Based on the code analysis, learn about the patterns and development flows, adapt project behaviors if possible for better response.{scope_hint}"
        if found_rule_files:
            notes += "\n\nIMPORTANT: The following project rule/instruction files were detected:\n"
            notes += chr(10).join(f"- {f}" for f in found_rule_files)
            notes += "\n\nYou MUST read these files using the read_file tool before making any changes."
        return notes
