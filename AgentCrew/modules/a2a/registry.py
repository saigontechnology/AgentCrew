"""
Agent registry for A2A v1 server.
"""

from __future__ import annotations

from typing import Any

from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel

from AgentCrew.modules.agents import LocalAgent

from .agent_cards import create_agent_card


class AgentInfo(BaseModel):
    """Basic information about an agent for the /agents endpoint."""

    name: str
    description: str
    endpoint: str
    capabilities: dict[str, Any]


class AgentRegistry:
    """Registry of all available agents for A2A server."""

    def __init__(self, agent_manager: Any, base_url: str = "http://localhost:41241"):
        self.agent_manager = agent_manager
        self.base_url = base_url.rstrip("/")
        self._agent_cards: dict[str, Any] = {}  # protobuf AgentCard
        self._initialize_agent_cards()

    def _initialize_agent_cards(self):
        """Initialize agent cards for all registered agents."""
        for agent_name, agent in self.agent_manager.agents.items():
            agent_url = f"{self.base_url}/{agent_name}/"
            if isinstance(agent, LocalAgent):
                self._agent_cards[agent_name] = create_agent_card(agent, agent_url)

    def get_agent_card(self, agent_name: str) -> Any | None:
        """Get the AgentCard (protobuf) for a specific agent."""
        return self._agent_cards.get(agent_name)

    def list_agents(self) -> list[AgentInfo]:
        """List all available agents with basic info."""
        agents = []
        for agent_name, card in self._agent_cards.items():
            # Get the first JSON-RPC interface URL
            endpoint = ""
            for iface in card.supported_interfaces:
                if iface.protocol_binding == "JSONRPC":
                    endpoint = iface.url
                    break
            if not endpoint:
                endpoint = f"{self.base_url}/{agent_name}/"

            agents.append(
                AgentInfo(
                    name=agent_name,
                    description=card.description or "",
                    endpoint=endpoint,
                    capabilities=MessageToDict(card.capabilities)
                    if card.HasField("capabilities")
                    else {},
                )
            )
        return agents

    def refresh_agent(self, agent_name: str):
        """Refresh the agent card for a specific agent."""
        agent = self.agent_manager.get_agent(agent_name)
        if agent and isinstance(agent, LocalAgent):
            agent_url = f"{self.base_url}/{agent_name}"
            self._agent_cards[agent_name] = create_agent_card(agent, agent_url)

    def refresh_all_agents(self):
        """Refresh all agent cards."""
        self._initialize_agent_cards()
