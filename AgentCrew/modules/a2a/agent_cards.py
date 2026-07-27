"""
Functions for generating A2A v1 agent cards from AgentCrew agents.
"""

from __future__ import annotations

from typing import Any

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

from AgentCrew import __version__


def map_tool_to_skill(tool_name: str, tool_def: Any) -> AgentSkill:
    """Map an AgentCrew tool to an A2A skill."""
    description = "A tool capability"
    if isinstance(tool_def, dict):
        if "description" in tool_def:
            description = tool_def["description"]
        elif "function" in tool_def and "description" in tool_def["function"]:
            description = tool_def["function"]["description"]

    return AgentSkill(
        id=tool_name,
        name=tool_name.replace("_", " ").title(),
        description=description[:200] if description else "",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=[tool_name, "tool"],
    )


def create_agent_card(agent: Any, base_url: str) -> AgentCard:
    """Create an A2A v1 AgentCard from an AgentCrew agent.

    Args:
        agent: The AgentCrew agent (LocalAgent).
        base_url: Base URL for the agent's JSON-RPC endpoint.

    Returns:
        An A2A v1 AgentCard.
    """
    skills: list[AgentSkill] = []
    try:
        for tool_name, (tool_def, _, _) in agent.tool_definitions.items():
            if callable(tool_def):
                try:
                    definition = tool_def()
                except Exception:
                    definition = None
            else:
                definition = tool_def

            if definition:
                skill = map_tool_to_skill(tool_name, definition)
                skills.append(skill)
    except Exception:
        skills = [
            AgentSkill(
                id="general",
                name="General Assistant",
                description="General purpose AI assistant",
                tags=["general", "assistant"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ]

    capabilities = AgentCapabilities(
        streaming=True,
        push_notifications=False,
    )

    provider = AgentProvider(
        organization="AgentCrew",
        url="https://github.com/saigontechnology/AgentCrew",
    )

    agent_name = agent.name if hasattr(agent, "name") else "AgentCrew Assistant"
    agent_desc = agent.description if hasattr(agent, "description") else ""

    return AgentCard(
        name=agent_name,
        description=agent_desc,
        version=__version__,
        default_input_modes=["text/plain", "application/octet-stream"],
        default_output_modes=["text/plain", "application/octet-stream"],
        capabilities=capabilities,
        skills=skills,
        provider=provider,
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=base_url,
            ),
        ],
    )
