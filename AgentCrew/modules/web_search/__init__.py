import os

from AgentCrew.modules.web_search.service import TavilySearchService

__all__ = [
    "TavilySearchService",
]


def create_search_service():
    """Create the web search service selected by ``WEB_SEARCH_PROVIDER``.

    Defaults to the Tavily backend; set ``WEB_SEARCH_PROVIDER=youcom`` to use
    the You.com MCP backend (keyless by default, ``YDC_API_KEY`` optional).
    """
    provider = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
    if provider == "youcom":
        from AgentCrew.modules.web_search.providers.youcom import (
            YoucomSearchService,
        )

        return YoucomSearchService()
    return TavilySearchService()

