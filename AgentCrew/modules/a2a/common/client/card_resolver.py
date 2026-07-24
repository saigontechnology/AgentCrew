import json

import httpx
from a2a.types import AgentCard

DEFAULT_AGENT_CARD_PATHS = [
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
]


class A2ACardResolver:
    def __init__(self, base_url, agent_card_path: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.agent_card_path = agent_card_path.lstrip("/") if agent_card_path else None

    def get_agent_card(self) -> AgentCard:
        with httpx.Client() as client:
            if self.agent_card_path:
                return self._fetch_agent_card(client, self.agent_card_path)

            for path in DEFAULT_AGENT_CARD_PATHS:
                try:
                    return self._fetch_agent_card(client, path.lstrip("/"))
                except httpx.HTTPStatusError:
                    continue

            raise httpx.RequestError(
                f"Agent card not found at any of the default paths: {DEFAULT_AGENT_CARD_PATHS}"
            )

    def _fetch_agent_card(self, client: httpx.Client, path: str) -> AgentCard:
        response = client.get(self.base_url + "/" + path)
        response.raise_for_status()
        try:
            return AgentCard(**response.json())
        except json.JSONDecodeError as e:
            raise httpx.RequestError(str(e)) from e
