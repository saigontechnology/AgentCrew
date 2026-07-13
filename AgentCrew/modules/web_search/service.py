import os
from typing import Any
from dotenv import load_dotenv
from tavily import TavilyClient


class TavilySearchService:
    """Service for interacting with the Tavily Search API using the official SDK."""

    def __init__(self):
        """Initialize the Tavily search service with API key from environment."""
        load_dotenv()
        self.api_key = os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY not found in environment variables")

        self.client = TavilyClient(api_key=self.api_key)

    def search(
        self,
        query: str,
        search_depth: str = "basic",
        topic: str = "general",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> dict[str, Any]:
        """
        Perform a web search using Tavily API.

        Args:
            query: The search query
            search_depth: 'basic' or 'advanced' search depth
            include_domains: list of domains to include in search
            exclude_domains: list of domains to exclude from search
            max_results: Maximum number of results to return

        Returns:
            dict containing search results
        """
        try:
            params = {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
                "include_answer": search_depth,
                "topic": topic,
            }

            if include_domains:
                params["include_domains"] = include_domains

            if exclude_domains:
                params["exclude_domains"] = exclude_domains

            return self.client.search(**params)
        except Exception as e:
            print(f"❌ Search error: {str(e)}")
            return {"error": str(e)}

    def extract(self, url: str, include_images: bool = False) -> dict[str, Any]:
        """
        Extract content from a specific URL using Tavily API.

        Args:
            url: The URL to extract content from
            include_images: Whether to include extracted images in results

        Returns:
            dict containing the extracted content
        """
        try:
            return self.client.extract(
                url, include_images=include_images, extract_depth="advanced"
            )
        except Exception as e:
            print(f"❌ Extract error: {str(e)}")
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
        """
        Crawl a website starting from a root URL using Tavily Crawl API.

        Args:
            url: The root URL to start crawling from
            max_depth: How many levels deep to crawl (1-5)
            limit: Maximum number of pages to crawl (max 500)
            select_paths: list of regex patterns to only crawl matching paths
            exclude_paths: list of regex patterns to exclude matching paths
            extract_depth: "basic" or "advanced" extraction
            instructions: Natural language guidance for the crawler

        Returns:
            dict containing crawl results with multiple pages
        """
        try:
            kwargs: dict[str, Any] = {
                "url": url,
                "max_depth": max_depth,
                "limit": limit,
                "extract_depth": extract_depth,
            }
            if select_paths:
                kwargs["select_paths"] = select_paths
            if exclude_paths:
                kwargs["exclude_paths"] = exclude_paths
            if instructions:
                kwargs["instructions"] = instructions

            return self.client.crawl(**kwargs)
        except Exception as e:
            print(f"❌ Crawl error: {str(e)}")
            return {"error": str(e)}

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

        if "failed_results" in results and results["failed_results"]:
            result = results["failed_results"][0]
            return f"Extract failed: {result.get('error', 'Unknown error')}"

        if "results" in results and results["results"]:
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

        pages = results.get("results") or []
        if not pages:
            return "No pages were crawled."

        lines = []
        base_url = results.get("base_url", "Unknown")
        lines.append(f"Crawl results from: {base_url}")
        lines.append(f"Pages crawled: {len(pages)}")
        if results.get("usage"):
            lines.append(f"Credits used: {results['usage'].get('credits', 'N/A')}")
        lines.append("")

        for i, page in enumerate(pages, 1):
            lines.append(f"--- Page {i} ---")
            lines.append(f"URL: {page.get('url', 'Unknown')}")
            content = page.get("raw_content", "No content")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)
