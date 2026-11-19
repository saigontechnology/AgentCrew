import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv

KEENABLE_BASE_URL = "https://api.keenable.ai"
KEENABLE_APP_TITLE = "AgentCrew"


class KeenableSearchService:
    """Web search and page fetch through the Keenable API.

    Works without a key on the public endpoints; KEENABLE_API_KEY switches to
    the keyed endpoints for higher limits. Crawling is not supported.
    """

    def __init__(self, timeout: float = 30):
        load_dotenv()
        self.api_key = os.getenv("KEENABLE_API_KEY") or None
        self.timeout = timeout

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": KEENABLE_APP_TITLE}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        else:
            path += "/public"
            headers["X-Keenable-Title"] = KEENABLE_APP_TITLE

        url = KEENABLE_BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def search(
        self,
        query: str,
        search_depth: str = "basic",
        topic: str = "general",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        """Search the web. Returns results in the same shape as Tavily."""
        body: dict[str, Any] = {"query": query, "max_results": max_results}
        if include_domains:
            # Keenable filters on a single site; use the first one given
            body["site"] = include_domains[0]
        if exclude_domains:
            body["exclude_domains"] = exclude_domains
        try:
            data = self._request("/v1/search", body=body)
        except Exception as e:
            print(f"❌ Search error: {e!s}")
            return {"error": str(e)}

        return {
            "results": [
                {
                    "title": item.get("title") or "No title",
                    "url": item.get("url", ""),
                    "content": item.get("snippet") or item.get("description") or "",
                }
                for item in data.get("results") or []
            ]
        }

    def extract(self, url: str, include_images: bool = False) -> dict[str, Any]:
        """Fetch a page as text. Returns results in the same shape as Tavily."""
        try:
            data = self._request("/v1/fetch", params={"url": url})
        except Exception as e:
            print(f"❌ Extract error: {e!s}")
            return {"failed_results": [{"url": url, "error": str(e)}]}

        return {
            "results": [
                {
                    "url": data.get("url") or url,
                    "raw_content": data.get("content") or "",
                    "images": [],
                }
            ]
        }

    def format_search_results(self, results: dict[str, Any]) -> str:
        if "error" in results:
            return f"Search error: {results['error']}"

        search_results = results.get("results") or []
        if not search_results:
            return "0 results"

        lines = [f"{len(search_results)} results"]
        for i, result in enumerate(search_results, 1):
            lines.append(f"{i}. {result['title']}")
            lines.append(result["url"])
            if result["content"]:
                lines.append(result["content"])
        return "\n".join(lines)
