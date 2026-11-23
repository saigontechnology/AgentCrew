"""Skill tools and history-derived FIFO activation (no session-global state)."""

import os
import re
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from functools import partial
from html import escape
from typing import TYPE_CHECKING
from xml.sax.saxutils import quoteattr

from .service import MAX_BODY_CHARS

if TYPE_CHECKING:
    from .service import SkillsService

_skill_activation: ContextVar[tuple[object, str | None, dict] | None] = ContextVar(
    "agentcrew_skill_activation", default=None
)


def bind_skill_history(owner: object, messages: list) -> None:
    """Create request-local FIFO state before stream iteration may spawn tasks."""
    from AgentCrew.modules.agents.tool_result_summary import current_inference_scope

    active, _ = active_skill_results(messages)
    _skill_activation.set((owner, current_inference_scope(), dict.fromkeys(active)))


def _activation_name(message: dict) -> str | None:
    if message.get("role") != "tool" or message.get("tool_name") != "activate_skill":
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    opening = re.match(r"<skill_content\b[^>]*>", content)
    if not opening:
        return None
    try:
        return ET.fromstring(opening[0][:-1] + "/>").get("name")
    except ET.ParseError:
        return None


def active_skill_results(messages: list) -> tuple[dict[str, int], dict[int, str]]:
    """Replay successful activations in message order; duplicates never reorder."""
    active: dict[str, int] = {}
    evicted: dict[int, str] = {}
    for index, message in enumerate(messages):
        name = _activation_name(message)
        if not name:
            continue
        if name in active:
            evicted[index] = name
            continue
        active[name] = index
        if len(active) > 3:
            oldest = next(iter(active))
            evicted[active.pop(oldest)] = oldest
    return active, evicted


def compact_skill_results(messages: list) -> None:
    """Replace only bodies in an inference copy, retaining protocol envelopes."""
    _, evicted = active_skill_results(messages)
    for index, name in evicted.items():
        messages[index]["content"] = (
            f"<skill_content_evicted name={quoteattr(name)} "
            'reason="active-skill-limit"/>'
        )


def get_activate_skill_tool_definition(skills_service: "SkillsService"):
    return {
        "type": "function",
        "function": {
            "name": "activate_skill",
            "description": (
                "Load full skill instructions and lazy resource paths. At most 3 distinct "
                "skills are active per history (FIFO). Duplicate activation does not "
                "reorder; reactivating an evicted skill loads it again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": skills_service.get_skill_names()}
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    }


def get_activate_skill_tool_handler(skills_service: "SkillsService", *, agent=None):
    async def handler(**params):
        name = params.get("name", "")
        if not isinstance(name, str) or not name:
            raise ValueError("No skill name provided")
        skills_service.refresh()
        skill = skills_service.get_skill(name)
        if not skill:
            return f"Skill '{name}' not found or not trusted."
        from AgentCrew.modules.agents.tool_result_summary import current_inference_scope

        bound = _skill_activation.get()
        active = (
            bound[2]
            if bound and bound[0] is agent and bound[1] == current_inference_scope()
            else {}
        )
        if name in active:
            return f"Skill '{name}' is already loaded in this history (FIFO order unchanged)."
        body = skill["body"]
        if len(body) > MAX_BODY_CHARS:
            raise ValueError(
                f"Skill '{name}' body exceeds {MAX_BODY_CHARS} characters; move details to references. Instructions were not truncated."
            )
        skill_dir = os.path.dirname(skill["location"])
        resources = skills_service.list_resources(skill_dir)
        resources_xml = ""
        if resources:
            files_xml = "\n".join(f"  <file>{escape(r)}</file>" for r in resources)
            resources_xml = f"\n<skill_resources>\n{files_xml}\n</skill_resources>"
        provenance = " ".join(
            f"{key}={quoteattr(str(value))}"
            for key, value in {
                "name": name,
                "scope": skill["scope"],
                "trusted": str(skill["trusted"]).lower(),
                "content_hash": skill["content_hash"],
                "version": skill["version"],
            }.items()
        )
        active[name] = None
        if len(active) > 3:
            active.pop(next(iter(active)))
        return (
            f"<skill_content {provenance}>\n{body}\n\n"
            f"Skill directory: {escape(skill_dir)}\n"
            "Relative paths are relative to the skill directory. "
            "Resource scripts are not executed by activation; use the normal approved command path."
            f"{resources_xml}\n</skill_content>"
        )

    return handler


def get_craft_skill_tool_definition():
    return {
        "type": "function",
        "function": {
            "name": "craft_skill",
            "description": (
                "WRITE-CAPABLE: atomically create/update a trusted project skill in "
                ".agents/skills/<name>. Requires instructions and at least one reusable, "
                "project-self-contained Python script. Static syntax validation only; "
                "never executes scripts. Use environment variable names, never credential "
                "values. Existing updates require update_existing; use the returned whole-tree "
                "content_hash as expected_existing_hash to protect user edits. Linux renameat2 required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
                    },
                    "description": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    },
                    "instructions": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_BODY_CHARS,
                    },
                    "scripts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Relative scripts/.../*.py path",
                                },
                                "language": {"type": "string", "enum": ["python"]},
                                "content": {"type": "string", "minLength": 1},
                            },
                            "required": ["path", "language", "content"],
                            "additionalProperties": False,
                        },
                    },
                    "compatibility": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "metadata": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "update_existing": {"type": "boolean", "default": False},
                    "expected_existing_hash": {
                        "type": "string",
                        "pattern": "^[a-f0-9]{64}$",
                    },
                },
                "required": ["name", "description", "instructions", "scripts"],
                "additionalProperties": False,
            },
        },
    }


def get_craft_skill_tool_handler(skills_service: "SkillsService"):
    async def handler(**params):
        return skills_service.craft_skill(**params)

    return handler


def register(service_instance: "SkillsService", agent=None):
    from AgentCrew.modules.tools.registration import register_tool

    if service_instance.has_skills():
        register_tool(
            lambda: get_activate_skill_tool_definition(service_instance),
            partial(get_activate_skill_tool_handler, agent=agent),
            service_instance,
            agent,
        )
    if service_instance.trusted_project:
        register_tool(
            get_craft_skill_tool_definition,
            get_craft_skill_tool_handler,
            service_instance,
            agent,
        )
