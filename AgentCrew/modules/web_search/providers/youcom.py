import os
from typing import Any

import requests
from dotenv import load_dotenv

YOU_MCP_BASE_URL = "https://api.you.com/mcp"
YOU_MCP_FREE_URL = "https://api.you.com/mcp?profile=free"
YOU_REQUEST_TIMEOUT = 30


class YoucomSearchService:
    """Optional web search provider backed by the You.com MCP endpoint.

    Implements the same surface as ``TavilySearchService`` so the
    ``web_search`` tool registrations work unchanged. Selected by setting
    ``WEB_SEARCH_PROVIDER=youcom``; the default remains Tavily.

    The free profile (``https://api.you.com/mcp?profile=free``) works without
    an API key and exposes the ``you-search`` tool. Setting ``YDC_API_KEY``
    switches to the authenticated endpoint, which additionally exposes
    ``you-contents`` for URL content extraction.
    """

    def __init__(self):
        """Initialize the You.com search service."""
        load_dotenv()
        self.api_key = os.getenv("YDC_API_KEY")
        self.base_url = YOU_MCP_BASE_URL if self.api_key else YOU_MCP_FREE_URL

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the You.com MCP endpoint over streamable HTTP."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = requests.post(
            self.base_url,
            json=payload,
            headers=self._headers(),
            timeout=YOU_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        data = None
        for line in response.text.splitlines():
            if line.startswith("data: "):
                candidate = line[len("data: ") :]
                # Skip server notifications (progress/log messages); the
                # tool result is the message carrying a "result" member.
                if '"result"' in candidate:
                    data = candidate
                    break
                if data is None:
                    data = candidate
        if data is None and response.text.strip().startswith("{"):
            data = response.text.strip()

        if data is None:
            return {"error": "You.com MCP returned an empty response"}

        import json

        message = json.loads(data)
        if "error" in message:
            return {"error": str(message["error"].get("message", message["error"]))}

        content = (message.get("result") or {}).get("content") or []
        for block in content:
            if block.get("type") == "text" and block.get("text"):
                try:
                    return json.loads(block["text"])
                except (TypeError, ValueError):
                    return {"error": block["text"]}
        return {"error": "You.com MCP returned no text content"}

    def search(
        self,
        query: str,
        search_depth: str = "basic",
        topic: str = "general",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        """Perform a web search using the You.com MCP ``you-search`` tool.

        Args:
            query: The search query
            search_depth: 'basic' or 'advanced' (advanced requests full-page
                extraction, mirroring the Tavily semantics)
            topic: 'general', 'news', or 'finance' (mapped to a freshness
                window for news)
            include_domains: list of domains to include in search
            exclude_domains: list of domains to exclude from search
            max_results: Maximum number of results to return

        Returns:
            dict in the Tavily search response shape (``results`` list with
            ``title``/``url``/``content`` entries), or ``{"error": ...}``
        """
        try:
            arguments: dict[str, Any] = {
                "query": query,
                "count": max_results,
            }
            if search_depth == "advanced":
                arguments["extraction"] = "full_page"
            else:
                arguments["extraction"] = "highlights"
            if topic == "news":
                arguments["freshness"] = "week"
            if include_domains:
                arguments["query"] = (
                    f"{query} site:{' OR site:'.join(include_domains)}"
                )
            if exclude_domains:
                arguments["exclude_domains"] = exclude_domains

            data = self._call_tool("you-search", arguments)
            if "error" in data:
                return data

            results = (data.get("results") or {}).get("web") or []
            formatted = []
            for item in results[:max_results]:
                content = ""
                contents = item.get("contents") or {}
                highlights = contents.get("highlights")
                if highlights:
                    content = highlights[0]
                elif contents.get("markdown"):
                    content = contents["markdown"]
                elif item.get("description"):
                    content = item["description"]
                elif item.get("snippets"):
                    content = item["snippets"][0]
                formatted.append(
                    {
                        "title": item.get("title", "No title"),
                        "url": item.get("url", ""),
                        "content": content,
                    }
                )
            return {"results": formatted}
        except Exception as e:
            print(f"❌ Search error: {e!s}")
            return {"error": str(e)}

    def extract(self, url: str, include_images: bool = False) -> dict[str, Any]:
        """Extract content from a URL.

        Uses the ``you-contents`` tool when authenticated (``YDC_API_KEY``);
        otherwise falls back to fetching the page directly and converting it
        to markdown.

        Args:
            url: The URL to extract content from
            include_images: Whether to include extracted images in results

        Returns:
            dict in the Tavily extract response shape (``results`` list with
            ``url``/``raw_content``), or ``{"error": ...}``
        """
        try:
            if self.api_key:
                data = self._call_tool(
                    "you-contents",
                    {"urls": [url], "formats": ["markdown"]},
                )
                if "error" not in data:
                    extracted = data.get("results") or []
                    if extracted:
                        first = extracted[0]
                        if first.get("error"):
                            return {
                                "failed_results": [
                                    {"url": url, "error": first["error"]}
                                ]
                            }
                        content = (
                            (first.get("contents") or {}).get("markdown") or ""
                        )
                        return {
                            "results": [
                                {
                                    "url": first.get("url", url),
                                    "raw_content": content,
                                    "images": [],
                                }
                            ]
                        }

            # Keyless fallback: fetch the page directly and convert to
            # markdown with the same converter the browser module uses.
            response = requests.get(
                url,
                timeout=YOU_REQUEST_TIMEOUT,
                headers={"User-Agent": "AgentCrew/YoucomSearchService"},
            )
            response.raise_for_status()
            from html_to_markdown import (
                ConversionOptions,
                PreprocessingOptions,
                convert,
            )

            raw_content = convert(
                response.text,
                ConversionOptions(
                    strip_newlines=True,
                    extract_metadata=False,
                ),
                PreprocessingOptions(
                    remove_navigation=False,
                    remove_forms=False,
                    preset="minimal",
                ),
            )
            if isinstance(raw_content, str):
                return {"results": [{"url": url, "raw_content": raw_content}]}
            return {
                "results": [
                    {"url": url, "raw_content": str(raw_content)}
                ]
            }
        except Exception as e:
            print(f"❌ Extract error: {e!s}")
            return {"error": str(e)}

    def crawl(
        self,
        url: str,
        max_depth: int = 2,
        limit: int = 50,
        select_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        extract_depth: str = "basic",
        instructions: str | None = None,
    ) -> dict[str, Any]:
        """Crawling is not supported by the You.com MCP free profile.

        Returns an explanatory error so the agent can fall back to
        ``fetch_webpage`` / ``search_web`` instead of failing silently.
        """
        return {
            "error": (
                "crawl_website is not available with the You.com search "
                "provider; use fetch_webpage for single pages or search_web "
                "to discover more URLs."
            )
        }

    def format_search_results(self, results: dict[str, Any]) -> str:
        """Format search results into a readable string."""
        if "error" in results:
            return f"Search error: {results['error']}"

        lines = []

        if results.get("answer"):
            lines.append(f"summary: {results['answer']}")

        search_results = results.get("results") or []
        if not search_results:
            lines.append("0 results")
            return "\n".join(lines)

        lines.append(f"{len(search_results)} results")
        for i, result in enumerate(search_results, 1):
            lines.append(f"{i}. {result.get('title', 'No title')}")
            lines.append(result.get("url", "No URL"))
            content = result.get("content")
            if content:
                lines.append(content)

        return "\n".join(lines)

    def format_extract_results(self, results: dict[str, Any]) -> str:
        """Format extract results into a readable string."""

        if results.get("failed_results"):
            result = results["failed_results"][0]
            return f"Extract failed: {result.get('error', 'Unknown error')}"

        if results.get("results"):
            result = results["results"][0]
            url = result.get("url", "Unknown URL")
            content = result.get("raw_content", "No content available")
            return f"{url}\n{content}"
        else:
            return "No content could be extracted."

    def format_crawl_results(self, results: dict[str, Any]) -> str:
        """Format crawl results into a readable string."""
        if "error" in results:
            return f"Crawl error: {results['error']}"
        return "No pages were crawled."
