from AgentCrew.modules.web_search.service import TavilySearchService


def get_web_search_tool_definition():
    """Return the tool definition for web search based on provider."""
    tool_description = "Searches the internet for up-to-date information on a specific topic or query. Use for research, current facts, documentation lookup, comparisons, and discovering relevant URLs."
    tool_arguments = {
        "query": {
            "type": "string",
            "description": "The search query to use for web search. Use precise and specific keywords to get the most relevant results. Include any relevant context to improve search accuracy.",
        },
        "search_depth": {
            "type": "string",
            "enum": ["basic", "advanced"],
            "description": "The depth of the search to perform. 'basic' is faster and suitable for general information. 'advanced' is more thorough and suitable for complex or nuanced queries.",
        },
        "topic": {
            "type": "string",
            "enum": ["general", "news", "finance"],
            "description": "The category of the search. `news` is useful for retrieving real-time updates, particularly about politics, sports, and major current events covered by mainstream media sources. `general` is for broader, more general-purpose searches that may include a wide range of sources.",
        },
        "included_domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Restrict results to specific authoritative domains only (e.g., ['python.org', 'docs.python.org']). Use when the user asks for official documentation or trusted sources.",
        },
        "max_results": {
            "type": "integer",
            "description": "The maximum number of search results to return. (Range: 1-20, default: 10)",
            "default": 10,
            "minimum": 1,
            "maximum": 20,
        },
    }
    tool_required = ["query", "search_depth", "topic"]
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_web_extract_tool_definition():
    """Return the tool definition for web content extraction based on provider."""
    tool_description = "Retrieves the full content (text + up to 3 images) from a web page. Only use HTTP/HTTPS URLs \u2014 do not use for local project files."
    tool_arguments = {
        "url": {
            "type": "string",
            "description": "The complete HTTP or HTTPS web address to retrieve content from (e.g., 'https://example.com/page'). Ensure the URL is valid and accessible. Verify the URL's relevance to the user's request before fetching.",
        },
        "include_images": {
            "type": "boolean",
            "description": "Whether to include extracted images from the page in the results. Set to True when the page may contain graphs, product images, diagrams, or other visual content that would be useful.",
            "default": False,
        },
    }
    tool_required = ["url"]
    return {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_web_search_tool_handler(tavily_service: TavilySearchService):
    """
    Return a handler function for the web search tool.

    Args:
        tavily_service: An instance of TavilySearchService

    Returns:
        Function that handles web search tool calls
    """

    async def web_search_handler(**params):
        query = params.get("query")
        search_depth = params.get("search_depth", "basic")
        max_results = params.get("max_results", 10)
        included_domains = params.get("included_domains", [])
        topic = params.get("topic", "general")

        if not query:
            return "Error: No search query provided."

        results = tavily_service.search(
            query=query,
            topic=topic,
            search_depth=search_depth,
            max_results=max_results,
            include_domains=included_domains,
        )

        return tavily_service.format_search_results(results)

    return web_search_handler


def get_web_extract_tool_handler(tavily_service: TavilySearchService):
    """
    Return a handler function for the web extract tool.

    Args:
        tavily_service: An instance of TavilySearchService

    Returns:
        Function that handles web extract tool calls
    """

    _MAX_IMAGES = 3

    async def _download_image_to_data_uri(img_url: str) -> str | None:
        """Download an image URL and convert to base64 data URI."""
        try:
            import base64
            import mimetypes

            import httpx2

            async with httpx2.AsyncClient(timeout=15) as client:
                response = await client.get(img_url)
                response.raise_for_status()
                content = response.content
                mime_type = response.headers.get("content-type", "")
                if not mime_type or mime_type == "application/octet-stream":
                    mime_type, _ = mimetypes.guess_type(img_url.split("?")[0])
                    mime_type = mime_type or "image/jpeg"
                base64_data = base64.b64encode(content).decode("utf-8")
                return f"data:{mime_type};base64,{base64_data}"
        except Exception as e:
            print(f"⚠️ Failed to download image {img_url}: {e}")
            return None

    async def web_extract_handler(**params):
        url = params.get("url")

        if not url:
            return "Error: No URL provided."

        include_images = params.get("include_images", False)
        results = tavily_service.extract(url=url, include_images=include_images)

        if results.get("failed_results"):
            err = results["failed_results"][0]
            return f"Extract failed: {err.get('error', 'Unknown error')}"

        if "results" not in results or not results["results"]:
            return "No content could be extracted."

        result = results["results"][0]
        page_url = result.get("url", "Unknown URL")
        content = result.get("raw_content", "No content available")

        # Build content blocks: text first, then up to _MAX_IMAGES images
        content_blocks = []
        content_blocks.append({"type": "text", "text": f"{page_url}\n{content}"})

        images = result.get("images") or []
        included = 0
        for img_url in images:
            if included >= _MAX_IMAGES:
                break
            if not isinstance(img_url, str) or not img_url.startswith("http"):
                continue
            data_uri = await _download_image_to_data_uri(img_url)
            if data_uri:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    }
                )
                included += 1

        return content_blocks

    return web_extract_handler


def get_web_crawl_tool_definition():
    """Return the tool definition for website crawling."""
    tool_description = (
        "Crawls a website starting from a root URL, discovering and extracting content "
        "from multiple pages. Use when you need comprehensive content from an entire "
        "site or section (e.g., documentation, blogs, knowledge bases, API references)."
    )
    tool_arguments = {
        "url": {
            "type": "string",
            "description": "The root URL to start crawling from. Only use HTTP/HTTPS URLs. Example: 'https://docs.example.com'",
        },
        "max_depth": {
            "type": "integer",
            "description": "How deep to crawl from the starting URL. Depth 1 = current page only, Depth 2 = current page + linked pages, etc. Higher depths increase latency significantly. (Range: 1-5)",
            "default": 2,
            "minimum": 1,
            "maximum": 5,
        },
        "max_pages": {
            "type": "integer",
            "description": "Maximum number of pages to crawl before stopping. (Range: 1-100)",
            "default": 50,
            "minimum": 1,
            "maximum": 100,
        },
        "include_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "list of regex patterns to only crawl URLs with specific path patterns (e.g., ['/docs/.*', '/api/v1.*']). Leave empty to crawl all paths.",
        },
        "exclude_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "list of regex patterns to exclude URLs with specific path patterns (e.g., ['/admin/.*', '/private/.*']).",
        },
        "extract_depth": {
            "type": "string",
            "enum": ["basic", "advanced"],
            "description": "How detailed to extract content. 'basic' is faster. 'advanced' captures tables and embedded content but much slower and more expensive.",
            "default": "basic",
        },
        "instructions": {
            "type": "string",
            "description": "Natural language instructions for the crawler to focus on relevant content (e.g., 'Find all documentation pages about authentication').",
        },
    }
    tool_required = ["url"]
    return {
        "type": "function",
        "function": {
            "name": "crawl_website",
            "description": tool_description,
            "parameters": {
                "type": "object",
                "properties": tool_arguments,
                "required": tool_required,
            },
        },
    }


def get_web_crawl_tool_handler(tavily_service: TavilySearchService):
    """
    Return a handler function for the web crawl tool.

    Args:
        tavily_service: An instance of TavilySearchService

    Returns:
        Function that handles web crawl tool calls
    """

    async def web_crawl_handler(**params):
        url = params.get("url")

        if not url:
            return "Error: No URL provided."

        max_depth = params.get("max_depth", 2)
        max_pages = params.get("max_pages", 50)
        include_paths = params.get("include_paths", [])
        exclude_paths = params.get("exclude_paths", [])
        extract_depth = params.get("extract_depth", "basic")
        instructions = params.get("instructions", "")

        results = tavily_service.crawl(
            url=url,
            max_depth=max_depth,
            limit=max_pages,
            select_paths=include_paths if include_paths else None,
            exclude_paths=exclude_paths if exclude_paths else None,
            extract_depth=extract_depth,
            instructions=instructions if instructions else None,
        )

        return tavily_service.format_crawl_results(results)

    return web_crawl_handler


def register(service_instance=None, agent=None):
    """
    Register this tool with the central registry or directly with an agent

    Args:
        service_instance: The web search service instance
        agent: Agent instance to register with directly (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_web_search_tool_definition,
        get_web_search_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_web_extract_tool_definition,
        get_web_extract_tool_handler,
        service_instance,
        agent,
    )
    register_tool(
        get_web_crawl_tool_definition,
        get_web_crawl_tool_handler,
        service_instance,
        agent,
    )
