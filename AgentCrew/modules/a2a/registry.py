from __future__ import annotations

from typing import TYPE_CHECKING
from pydantic import BaseModel
from .agent_cards import create_agent_card
from AgentCrew.modules.agents import LocalAgent


if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional
    from AgentCrew.modules.agents import AgentManager
    from a2a.types import AgentCard


class AgentInfo(BaseModel):
    """Basic information about an agent for the registry"""

    name: str
    description: str
    endpoint: str
    capabilities: Dict[str, Any]


class AgentRegistry:
    """Registry of all available agents for A2A server"""

    def __init__(
        self, agent_manager: AgentManager, base_url: str = "http://localhost:41241"
    ):
        self.agent_manager = agent_manager
        self.base_url = base_url.rstrip("/")
        self._agent_cards: Dict[str, AgentCard] = {}
        self._initialize_agent_cards()

    def _initialize_agent_cards(self):
        """Initialize agent cards for all registered agents"""
        for agent_name, agent in self.agent_manager.agents.items():
            agent_url = f"{self.base_url}/{agent_name}/"
            if isinstance(agent, LocalAgent):
                self._agent_cards[agent_name] = create_agent_card(agent, agent_url)

    def get_agent_card(self, agent_name: str) -> Optional[AgentCard]:
        """
        Get the agent card for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            The agent card if found, None otherwise
        """
        return self._agent_cards.get(agent_name)

    def list_agents(self) -> List[AgentInfo]:
        """
        List all available agents.

        Returns:
            List of agent information
        """
        agents = []
        for agent_name, card in self._agent_cards.items():
            agents.append(
                AgentInfo(
                    name=agent_name,
                    description=card.description or "",
                    endpoint=card.url,
                    capabilities=card.capabilities.model_dump(),
                )
            )
        return agents

    def refresh_agent(self, agent_name: str):
        """
        Refresh the agent card for a specific agent.

        Args:
            agent_name: Name of the agent to refresh
        """
        agent = self.agent_manager.get_agent(agent_name)
        if agent and isinstance(agent, LocalAgent):
            agent_url = f"{self.base_url}/{agent_name}"
            self._agent_cards[agent_name] = create_agent_card(agent, agent_url)

    def refresh_all_agents(self):
        """Refresh all agent cards"""
        self._initialize_agent_cards()
