from __future__ import annotations

import json
import re
from typing import Any

import xmltodict
from loguru import logger

from AgentCrew.modules.agents.local_agent import LocalAgent
from AgentCrew.modules.config.agents_config import AgentsConfig
from AgentCrew.modules.agents import AgentManager


class PromptEvolutionService:
    DEFAULT_MEMORY_ITEMS = 15
    MIN_MEMORY_RECORDS = 2

    ANALYSIS_PROMPT = """You are analyzing pre-extracted memory records for an AI agent to identify durable behavioral patterns that should be incorporated into the agent's system prompt.

## Agent Context
- Agent name: {agent_name}
- Agent description: {agent_description}

## Current System Prompt
<current_system_prompt>
{current_system_prompt}
</current_system_prompt>

## Memory Records
Each memory record contains:
- **Topic/Flow**: what was discussed and how the conversation progressed (INTENT → EXPLORATION → OUTCOME)
- **Insights**: durable lessons the agent learned
- **Notes**: user corrections, preferences, caveats, workflow constraints, non-obvious decisions

<memory_records>
{memory_corpus}
</memory_records>
{user_answers_section}

## Your Task
Analyze ALL memory records to identify **repeated patterns** that appear across multiple conversations. A pattern must appear in at least 2 separate memory records to qualify as durable.

Focus on:
1. **Notes** — these contain direct user corrections, stated preferences, and workflow constraints. This is your primary evidence source.
2. **Insights** — these contain durable lessons. Cross-reference with Notes for confirmation.
3. **Flow/Topic patterns** — recurring themes and conversation arcs reveal the agent's actual usage patterns.
{user_task_priority}

## Extraction Rules
- ONLY extract patterns with evidence from multiple conversations (not one-off instructions)
- EXCLUDE anything tied to a specific project, repository, codebase, file path, framework, service, database, deployment, or incident
- EXCLUDE patterns already present in the current system prompt (no duplication)
- DO NOT weaken or contradict existing safety, tool approval, transfer, or role rules
- Keep each item to one clear, actionable sentence
- Rate evidence strength honestly — "high" requires 3+ conversations, "medium" requires 2

## Output Format
Return ONLY valid JSON:
{{
  "durable_traits": [
    {{"item": "behavioral pattern description", "evidence": "seen in memories #X, #Y, #Z — brief explanation", "strength": "high|medium"}}
  ],
  "output_preferences": [
    {{"item": "response format/style preference", "evidence": "...", "strength": "high|medium"}}
  ],
  "recurring_user_corrections": [
    {{"item": "what user repeatedly corrects or requests differently", "evidence": "...", "strength": "high|medium"}}
  ],
  "workflow_patterns": [
    {{"item": "recurring step sequence or process the user expects", "evidence": "...", "strength": "high|medium"}}
  ],
  "tool_usage_preferences": [
    {{"item": "preferred tool usage pattern or tool selection preference", "evidence": "...", "strength": "high|medium"}}
  ],
  "excluded_as_project_specific": [
    {{"item": "pattern that was considered but excluded", "reason": "tied to specific project/repo/framework"}}
  ],
  "confidence_notes": ["any caveats about the analysis"]
}}"""

    REVISION_PROMPT = """Revise an AI agent's system prompt to incorporate approved behavioral improvements.

## Rules
1. PRESERVE the complete existing prompt structure, tone, and organization
2. PRESERVE all existing role definitions, mission statements, and identity descriptions
3. PRESERVE all tool/safety/transfer/approval rules unchanged
4. PRESERVE all placeholders exactly: {{current_date}}, {{cwd}}, {{current_agent_name}}, {{current_agent_description}}
5. PRESERVE any XML-like structural tags (e.g., <Agent_Instructions>, <Transferable_Agents>)
6. INTEGRATE the approved improvements naturally into existing sections where they fit best
7. If no existing section fits, add a concise new section near the end (before any closing tags)
8. CONSOLIDATE overlapping instructions instead of appending near-duplicates
9. REMOVE duplicated rules, repeated sections, and redundant wording in the final revised prompt
10. If the approved improvements overlap with existing instructions, merge them into the strongest single instruction rather than restating them
11. DO NOT add project-specific context, temporary instructions, or example-heavy elaborations
12. DO NOT rewrite sections that don't need changes — surgical integration only
13. Keep additions concise — each improvement should be 1-2 sentences in the prompt
14. The final output must read as one clean, coherent system prompt with no repeated guidance, no duplicate headings, and no conflicting or redundant instructions

## Current System Prompt
<current_system_prompt>
{current_system_prompt}
</current_system_prompt>

## Approved Improvements to Integrate
<approved_summary>
{approved_summary}
</approved_summary>

Before finalizing, deduplicate and consolidate the full prompt so the output is clean and dense rather than repetitive.

Output ONLY the complete revised system prompt. No commentary, no explanation, no markdown wrapping, no current_system_prompt tags warning."""

    PROJECT_SPECIFIC_PATTERNS = [
        re.compile(r"(tests?/|src/|lib/|modules/|components/)[^\s]+", re.I),
        re.compile(r"\.\w{1,5}\b"),
        re.compile(r"\b(JIRA|TICK|ISSUE|PR|MR)-?\d+\b", re.I),
        re.compile(r"\b(branch|commit|merge|rebase)\s+\w+/\w+", re.I),
    ]

    def __init__(
        self,
        memory_service=None,
        persistence_service=None,
        agents_config: AgentsConfig | None = None,
    ):
        self.memory_service = memory_service
        self.persistence_service = persistence_service
        self.agents_config = agents_config or AgentsConfig()

    async def create_evolution_proposal(
        self, agent: Any, user_answers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        if not isinstance(agent, LocalAgent):
            raise ValueError("/evolve is only supported for local agents.")

        memory_corpus = self._get_memory_corpus(agent)
        if not memory_corpus:
            raise ValueError("No memory available to evolve this agent prompt.")
        if len(memory_corpus) < self.MIN_MEMORY_RECORDS:
            raise ValueError(
                "At least 2 memory records are required to infer durable prompt improvements."
            )

        logger.debug(
            "Creating prompt evolution proposal for agent={} with {} memory records",
            agent.name,
            len(memory_corpus),
        )
        analysis = await self._run_analysis(
            agent, memory_corpus, user_answers=user_answers
        )
        sanitized = self._sanitize_analysis(analysis)
        summary = self._format_user_summary(sanitized)

        if not summary.strip():
            raise ValueError(
                "No durable non-project-specific prompt improvements were found."
            )

        return {
            "agent_name": agent.name,
            "source_memory_count": len(memory_corpus),
            "memory_ids": [item.get("id") for item in memory_corpus if item.get("id")],
            "current_system_prompt": agent.get_system_prompt(),
            "analysis_summary": sanitized,
            "generated_summary": summary,
            "approved_summary": summary,
            "user_editable_summary": summary,
            "status": "draft",
        }

    async def build_revised_prompt(
        self, agent: LocalAgent, approved_summary: str
    ) -> str:
        prompt = self.REVISION_PROMPT.format(
            current_system_prompt=agent.get_system_prompt(),
            approved_summary=approved_summary,
        )
        if not agent.llm:
            raise ValueError("LLM Service is not ready.")

        revised_prompt = await agent.llm.process_message(prompt)
        revised_prompt = revised_prompt.strip()
        if not revised_prompt:
            raise ValueError("Prompt revision returned an empty prompt.")

        self._validate_revised_prompt(agent.get_system_prompt(), revised_prompt)
        return revised_prompt

    def apply_prompt_revision(
        self,
        agent: LocalAgent,
        revised_prompt: str,
        approved_summary: str,
        generated_summary: str | None = None,
        memory_ids: list[str] | None = None,
        edited_by_user: bool = False,
    ) -> dict[str, Any]:
        previous_prompt = agent.get_system_prompt()
        normalized_generated_summary = generated_summary or approved_summary
        normalized_memory_ids = memory_ids or []

        if not self.agents_config.update_agent_system_prompt(
            agent.name, revised_prompt
        ):
            raise ValueError(f"Failed to persist system prompt for agent: {agent.name}")

        refreshed_agent = AgentManager.get_instance().get_local_agent(agent.name)
        if isinstance(refreshed_agent, LocalAgent):
            refreshed_agent.set_system_prompt(revised_prompt)
        else:
            logger.warning(
                f"Persisted prompt revision for agent '{agent.name}' but could not refresh the in-memory local agent instance."
            )

        if self.persistence_service:
            try:
                self.persistence_service.store_prompt_evolution(
                    agent.name,
                    {
                        "previous_system_prompt": previous_prompt,
                        "generated_summary": normalized_generated_summary,
                        "approved_summary": approved_summary,
                        "revised_system_prompt": revised_prompt,
                        "memory_ids": normalized_memory_ids,
                        "edited_by_user": edited_by_user,
                    },
                )
            except Exception as e:
                logger.error(
                    f"Prompt revision persisted for agent '{agent.name}' but failed to store evolution audit record: {e}"
                )
                raise ValueError(
                    f"Prompt was persisted for agent '{agent.name}', but storing the prompt evolution audit record failed: {e}"
                ) from e

        if self.memory_service and normalized_memory_ids:
            try:
                self.memory_service.mark_memories_evolved(
                    normalized_memory_ids, agent.name
                )
            except Exception as e:
                logger.warning(f"Failed to mark memories as evolved: {e}")

        return {
            "agent_name": agent.name,
            "previous_system_prompt": previous_prompt,
            "revised_system_prompt": revised_prompt,
            "generated_summary": normalized_generated_summary,
            "accepted_summary": approved_summary,
            "edited_by_user": edited_by_user,
        }

    def _get_memory_corpus(self, agent: LocalAgent) -> list[dict[str, Any]]:
        if not self.memory_service:
            return []
        return self.memory_service.get_agent_memory_corpus(
            agent.name,
            max_items=self.DEFAULT_MEMORY_ITEMS,
        )

    def _prepare_corpus_for_analysis(self, memory_corpus: list[dict[str, Any]]) -> str:
        entries = []
        for i, item in enumerate(memory_corpus, 1):
            doc = item.get("document", "")
            entry = self._extract_evolution_fields(doc, i)
            if entry:
                entries.append(entry)
        return "\n\n".join(entries)

    def _extract_evolution_fields(self, xml_doc: str, index: int) -> str:
        try:
            parsed = xmltodict.parse(xml_doc)
            mem = parsed.get("MEMORY", {})
        except Exception as exc:
            logger.debug(
                "Skipping malformed memory record during evolve analysis at index {}: {}",
                index,
                exc,
            )
            return ""

        head = mem.get("HEAD", "")
        date = mem.get("DATE", "")

        flow = mem.get("FLOW", {})
        flow_parts = []
        if isinstance(flow, dict):
            for flow_field in ("INTENT", "EXPLORATION", "OUTCOME"):
                if flow.get(flow_field):
                    flow_parts.append(f"{flow_field}: {flow[flow_field]}")
        elif isinstance(flow, str) and flow:
            flow_parts.append(flow)
        context = mem.get("CONTEXT", "")

        insights_raw = mem.get("INSIGHTS", {})
        insights = (
            insights_raw.get("INSIGHT", []) if isinstance(insights_raw, dict) else []
        )
        if isinstance(insights, str):
            insights = [insights]

        notes_raw = mem.get("CONVERSATION_NOTES", {})
        notes = notes_raw.get("NOTE", []) if isinstance(notes_raw, dict) else []
        if isinstance(notes, str):
            notes = [notes]

        domains_raw = mem.get("DOMAINS", {})
        domains = domains_raw.get("DOMAIN", []) if isinstance(domains_raw, dict) else []
        if isinstance(domains, str):
            domains = [domains]

        if not notes and not insights and not flow_parts and not context:
            return ""

        parts = [f"--- Memory #{index} ({date}) ---"]
        if head:
            parts.append(f"Topic: {head}")
        if flow_parts:
            parts.append("Flow:")
            parts.extend(f"  {fp}" for fp in flow_parts)
        elif context:
            parts.append(f"Context: {context}")
        if domains:
            parts.append(f"Domains: {', '.join(d for d in domains if d)}")
        if insights:
            parts.append("Insights:")
            parts.extend(f"  - {i}" for i in insights if i)
        if notes:
            parts.append("Notes:")
            parts.extend(f"  - {n}" for n in notes if n)

        return "\n".join(parts)

    async def _run_analysis(
        self,
        agent: LocalAgent,
        memory_corpus: list[dict[str, Any]],
        user_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        corpus_text = self._prepare_corpus_for_analysis(memory_corpus)

        # Format user answers section for the prompt
        user_answers_section = ""
        user_task_priority = ""
        if user_answers:
            answer_lines = []
            for key, label in (
                ("wanted_change", "What the user wants to change"),
                ("does_well", "What the user says the agent does well"),
                ("does_badly", "What the user says the agent does poorly"),
            ):
                if user_answers.get(key):
                    answer_lines.append(f"- {label}: {user_answers[key]}")
            if answer_lines:
                user_answers_section = (
                    "\n## User Feedback\n"
                    "The user provided the following feedback to guide this evolution:\n"
                    + "\n".join(answer_lines)
                )
                user_task_priority = (
                    "\n4. **User Feedback** — The user's direct feedback above is your highest priority evidence. "
                    "Cross-reference user feedback with Memory Records to find matching patterns, "
                    "and give extra weight to patterns that align with what the user explicitly mentioned."
                )

        prompt = self.ANALYSIS_PROMPT.format(
            agent_name=agent.name,
            agent_description=agent.description,
            current_system_prompt=agent.get_system_prompt(),
            memory_corpus=corpus_text,
            user_answers_section=user_answers_section,
            user_task_priority=user_task_priority,
        )
        logger.debug(f"Evolution analysis prompt length: {len(prompt)} chars")
        if not agent.llm:
            raise ValueError("LLM Service is not ready.")
        raw_response = await agent.llm.process_message(prompt)
        return self._parse_json_response(raw_response)

    def _parse_json_response(self, raw_response: str) -> dict[str, Any]:
        raw_response = raw_response.strip()
        if raw_response.startswith("```"):
            lines = raw_response.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_response = "\n".join(lines).strip()
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_response, re.S)
            if not match:
                raise ValueError(
                    "Evolution analysis returned invalid structured output."
                )
            return json.loads(match.group(0))

    def _sanitize_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {
            "durable_traits": self._sanitize_items(analysis.get("durable_traits", [])),
            "output_preferences": self._sanitize_items(
                analysis.get("output_preferences", [])
            ),
            "recurring_user_corrections": self._sanitize_items(
                analysis.get("recurring_user_corrections", [])
            ),
            "workflow_patterns": self._sanitize_items(
                analysis.get("workflow_patterns", [])
            ),
            "tool_usage_preferences": self._sanitize_items(
                analysis.get("tool_usage_preferences", [])
            ),
            "excluded_as_project_specific": analysis.get(
                "excluded_as_project_specific", []
            ),
            "confidence_notes": analysis.get("confidence_notes", []),
        }
        return sanitized

    def _sanitize_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized = []
        for item in items:
            text = str(item.get("item", "")).strip()
            strength = str(item.get("strength", "")).lower().strip()
            if not text or strength == "low" or self._is_project_specific(text):
                continue
            sanitized.append(
                {
                    "item": text,
                    "evidence": str(item.get("evidence", "")).strip(),
                    "strength": strength or "medium",
                }
            )
        return sanitized

    def _is_project_specific(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.PROJECT_SPECIFIC_PATTERNS)

    def _format_user_summary(self, analysis: dict[str, Any]) -> str:
        sections = []
        for title, key in (
            ("Durable traits", "durable_traits"),
            ("Output preferences", "output_preferences"),
            ("Recurring user corrections", "recurring_user_corrections"),
            ("Workflow patterns", "workflow_patterns"),
            ("Tool usage preferences", "tool_usage_preferences"),
        ):
            items = analysis.get(key, [])
            if items:
                sections.append(title + ":")
                sections.extend(
                    f"- {item['item']} [{item.get('strength', 'medium')}]"
                    for item in items
                )
                sections.append("")

        return "\n".join(sections).strip()

    def _validate_revised_prompt(
        self, previous_prompt: str, revised_prompt: str
    ) -> None:
        if len(revised_prompt.strip()) < 40:
            raise ValueError("Revised system prompt is too short to be valid.")

        for placeholder in (
            "{current_date}",
            "{cwd}",
            "{current_agent_name}",
            "{current_agent_description}",
        ):
            if placeholder in previous_prompt and placeholder not in revised_prompt:
                raise ValueError(
                    f"Revised prompt removed required placeholder: {placeholder}"
                )
