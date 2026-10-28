import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv

YOU_FREE_SEARCH_URL = "https://api.you.com/v1/agents/search"
YOU_KEYED_SEARCH_URL = "https://ydc-index.io/v1/search"
YOU_USER_AGENT = "youdotcom-integration/saigontechnology-agentcrew"


class YouSearchService:
    """Web search through the You.com Search API.

    Works without an API key on the free tier (100 searches a day per IP).
    Set YDC_API_KEY to use the keyed endpoint with higher limits.
    Only search is supported; page extraction and crawling still need Tavily.
    """

    def __init__(self, timeout: float = 20.0):
        load_dotenv()
        self.api_key = (os.getenv("YDC_API_KEY") or "").strip() or None
        self.timeout = timeout

    def _request(self, query: str, count: int) -> dict[str, Any]:
        headers = {"User-Agent": YOU_USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Content-Type"] = "application/json"
            data = json.dumps({"query": query, "count": count}).encode("utf-8")
            req = urllib.request.Request(
                YOU_KEYED_SEARCH_URL, data=data, headers=headers, method="POST"
            )
        else:
            params = urllib.parse.urlencode({"query": query, "count": count})
            req = urllib.request.Request(
                f"{YOU_FREE_SEARCH_URL}?{params}", headers=headers, method="GET"
            )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Search the web and return results in the same shape as Tavily."""
        count = max(1, min(int(max_results or 5), 20))
        if include_domains:
            sites = " OR ".join(f"site:{d}" for d in include_domains)
            query = f"{query} ({sites})"
        for attempt in range(2):
            try:
                payload = self._request(query, count)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    retry_after = e.headers.get("Retry-After", "")
                    if retry_after.isdigit() and int(retry_after) <= 10:
                        time.sleep(int(retry_after))
                        continue
                return {"error": self._describe_http_error(e.code)}
            except Exception as e:
                return {"error": f"You.com search failed: {e!s}"}

        web = (payload.get("results") or {}).get("web") or []
        results = []
        for item in web[:count]:
            snippets = item.get("snippets") or []
            content = " ".join(s for s in snippets if s) or item.get("description", "")
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": content,
                    "page_age": item.get("page_age"),
                }
            )
        return {"query": query, "results": results}

    def _describe_http_error(self, code: int) -> str:
        if code == 401:
            return "You.com search: invalid YDC_API_KEY (401)"
        if code == 402:
            return "You.com search: API credits exhausted (402)"
        if code == 403:
            return "You.com search: key not allowed for this endpoint (403)"
        if code == 429:
            if self.api_key:
                return "You.com search: rate limited (429), try again shortly"
            return (
                "You.com search: free daily limit reached (429). "
                "Set YDC_API_KEY for higher limits."
            )
        return f"You.com search: HTTP {code}"

    def format_search_results(self, results: dict[str, Any]) -> str:
        if "error" in results:
            return f"Search error: {results['error']}"
        items = results.get("results") or []
        if not items:
            return "0 results"
        lines = [f"{len(items)} results"]
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. {r.get('title') or 'No title'}")
            lines.append(r.get("url") or "No URL")
            if r.get("content"):
                lines.append(r["content"])
        return "\n".join(lines)
