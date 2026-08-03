Based on my review of the current scraping module and research on Crawl4AI, I'll now draft a plan for revising the scraping functionality with Crawl4AI. Here's my architectural recommendation:

# Crawl4AI Integration Plan for SwissKnife

## Current State Analysis

The current scraping module in SwissKnife:

- Uses `FirecrawlApp` for web scraping
- Requires an API key (FC_API_KEY)
- Has basic differentiation between file URLs and web pages
- Returns content as markdown or raw text
- Provides a simple tool interface for agents

## Crawl4AI Advantages

Crawl4AI offers significant improvements:

1. **Open-source**: No API key requirements, reducing external dependencies
2. **Rich content extraction**: Better markdown generation with heuristic filtering
3. **Structured data extraction**: Supports CSS, XPath, and LLM-based extraction
4. **Advanced browser control**: Session management, stealth modes, proxies
5. **Performance improvements**: Async architecture, caching, parallel crawling
6. **Media handling**: Better image, audio, video extraction
7. **Dynamic content support**: Handles JavaScript-driven websites better

## Implementation Plan

### 1. Module Structure Update

```
AgentCrew.
├── modules/
│   ├── scraping/
│   │   ├── __init__.py
│   │   ├── service.py         # Main service implementation
│   │   ├── tool.py            # Tool definitions and handlers
│   │   ├── config.py          # Configuration options
│   │   └── strategies/        # Optional folder for custom extraction strategies
│   │       ├── __init__.py
│   │       └── custom.py
```

### 2. Service Implementation

```python
# service.py
import asyncio
import os
from typing import Dict, Any, Optional, List, Union

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


class ScrapingService:
    """Enhanced web scraping service using Crawl4AI."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the scraping service with configuration options.

        Args:
            config: Optional configuration dictionary with browser and crawler settings
        """
        self.browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            **(config.get("browser", {}) if config else {}),
        )

        # Default content filter with reasonable thresholds
        self.content_filter = PruningContentFilter(
            threshold=0.45, threshold_type="fixed", min_word_threshold=0
        )

        # Default markdown generator
        self.markdown_generator = DefaultMarkdownGenerator(
            content_filter=self.content_filter
        )

        # Initialize the crawler instance
        self._crawler = None
        self._crawler_lock = asyncio.Lock()

    async def _get_crawler(self):
        """Get or create an AsyncWebCrawler instance."""
        async with self._crawler_lock:
            if self._crawler is None:
                self._crawler = AsyncWebCrawler(config=self.browser_config)
                await self._crawler.__aenter__()
            return self._crawler

    async def close(self):
        """Close the crawler instance if open."""
        async with self._crawler_lock:
            if self._crawler is not None:
                await self._crawler.__aexit__(None, None, None)
                self._crawler = None

    def _is_file_url(self, url: str) -> bool:
        """Check if the URL points to a raw file that should be fetched directly."""
        file_extensions = [".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml"]
        return any(url.lower().endswith(ext) for ext in file_extensions)

    async def scrape_url(
        self,
        url: str,
        output_format: str = "markdown",
        extract_media: bool = False,
        extract_metadata: bool = True,
        use_cache: bool = True,
    ) -> Union[str, Dict[str, Any]]:
        """
        Scrape content from a URL with enhanced options.

        Args:
            url: The URL to scrape
            output_format: "markdown", "raw", "html", or "json"
            extract_media: Whether to extract media content
            extract_metadata: Whether to extract metadata
            use_cache: Whether to use caching

        Returns:
            Content in the requested format or result object

        Raises:
            Exception: If scraping fails
        """
        try:
            crawler = await self._get_crawler()

            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.ENABLED if use_cache else CacheMode.BYPASS,
                markdown_generator=self.markdown_generator,
                extract_media=extract_media,
                extract_metadata=extract_metadata,
            )

            # For file URLs, use a simpler approach
            if self._is_file_url(url):
                run_config.content_type = "raw"

            result = await crawler.arun(url=url, config=run_config)

            # Return the requested format
            if output_format == "markdown":
                return result.markdown
            elif output_format == "raw":
                return result.raw_text
            elif output_format == "html":
                return result.html
            elif output_format == "json":
                return {
                    "markdown": result.markdown,
                    "fit_markdown": result.fit_markdown,  # Filtered version
                    "raw_text": result.raw_text,
                    "html": result.html,
                    "url": result.url,
                    "title": result.title,
                    "metadata": result.metadata if extract_metadata else {},
                    "media": result.media if extract_media else [],
                }
            else:
                return result.markdown

        except Exception as e:
            raise Exception(f"Failed to scrape URL: {str(e)}")
```

### 3. Tool Definition Update

```python
# tool.py
from typing import Dict, Any
from .service import ScrapingService


def get_scraping_tool_definition(provider="claude"):
    """Return the enhanced tool definition for web scraping based on provider."""
    if provider == "claude":
        return {
            "name": "fetch_webpage",
            "description": "Extract and retrieve the content from external web pages (HTTP/HTTPS URLs only). Use this tool exclusively for accessing online resources, not for reading local project files.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The complete HTTP/HTTPS web address to extract content from (e.g., 'https://example.com/page')",
                    },
                    "output_file": {
                        "type": "string",
                        "description": "If defined, the content will be saved to this file. Only save to file if explicitly requested",
                        "default": "",
                    },
                    "format": {
                        "type": "string",
                        "description": "The format of the output content",
                        "enum": ["markdown", "raw", "html", "json"],
                        "default": "markdown",
                    },
                    "extract_media": {
                        "type": "boolean",
                        "description": "Whether to extract media content (images, videos, audio)",
                        "default": False,
                    },
                },
                "required": ["url"],
            },
        }
    else:  # For other providers (OpenAI, Groq, etc.)
        return {
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": "Extract and retrieve the content from external web pages (HTTP/HTTPS URLs only). Use this tool exclusively for accessing online resources, not for reading local project files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The complete HTTP/HTTPS web address to extract content from (e.g., 'https://example.com/page')",
                        },
                        "output_file": {
                            "type": "string",
                            "description": "If defined, the content will be saved to this file. Only save to file if explicitly requested",
                            "default": "",
                        },
                        "format": {
                            "type": "string",
                            "description": "The format of the output content",
                            "enum": ["markdown", "raw", "html", "json"],
                            "default": "markdown",
                        },
                        "extract_media": {
                            "type": "boolean",
                            "description": "Whether to extract media content (images, videos, audio)",
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
            },
        }


def get_scraping_tool_handler(scraping_service: ScrapingService):
    """
    Returns a handler function for the enhanced scraping tool.

    Args:
        scraping_service: An instance of ScrapingService

    Returns:
        function: A handler function that can be registered with the LLM service
    """

    async def scraping_handler(
        url: str,
        output_file: str = "",
        format: str = "markdown",
        extract_media: bool = False,
    ):
        print(f"\n🌐 Scraping content from: {url}")

        # Use the asyncio event loop to call the async method
        import asyncio

        content = await scraping_service.scrape_url(
            url=url, output_format=format, extract_media=extract_media
        )

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                if isinstance(content, dict):
                    import json

                    f.write(json.dumps(content, indent=2))
                else:
                    f.write(content)
            print(f"✅ Content saved to {output_file}")

        print("✅ Content successfully scraped")
        return content

    # Return a synchronous wrapper for the async function
    def handler(*args, **kwargs):
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(scraping_handler(*args, **kwargs))

    return handler


def register(service_instance=None, agent=None):
    """
    Register this tool with the central registry or directly with an agent

    Args:
        service_instance: The scraping service instance
        agent: Agent instance to register with directly (optional)
    """
    from AgentCrew.modules.tools.registration import register_tool

    register_tool(
        get_scraping_tool_definition, get_scraping_tool_handler, service_instance, agent
    )
```

### 4. Configuration Options

```python
# config.py
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ScrapingConfig:
    """Configuration for the scraping service."""

    # Browser configuration
    headless: bool = True
    verbose: bool = False
    user_agent: Optional[str] = None
    viewport_width: int = 1280
    viewport_height: int = 800

    # Content filtering options
    content_filter_threshold: float = 0.45
    min_word_threshold: int = 0

    # Extraction options
    default_format: str = "markdown"
    extract_media_by_default: bool = False
    extract_metadata_by_default: bool = True
    use_cache_by_default: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert the configuration to a dictionary."""
        return {
            "browser": {
                "headless": self.headless,
                "verbose": self.verbose,
                "user_agent": self.user_agent,
                "viewport": {
                    "width": self.viewport_width,
                    "height": self.viewport_height,
                },
            },
            "content_filter": {
                "threshold": self.content_filter_threshold,
                "min_word_threshold": self.min_word_threshold,
            },
            "extraction": {
                "default_format": self.default_format,
                "extract_media_by_default": self.extract_media_by_default,
                "extract_metadata_by_default": self.extract_metadata_by_default,
                "use_cache_by_default": self.use_cache_by_default,
            },
        }
```

### 5. Integration in Main Module

```python
# AgentCrew.main.py (partial update)


def setup_services(provider):
    """Set up and return service instances"""
    llm_service = setup_llm_service(provider)
    clipboard_service = ClipboardService()
    code_analysis_service = CodeAnalysisService()
    memory_service = MemoryService()

    # Initialize scraping service with default configuration
    from AgentCrew.modules.scraping.config import ScrapingConfig
    from AgentCrew.modules.scraping.service import ScrapingService

    scraping_config = ScrapingConfig()
    scraping_service = ScrapingService(config=scraping_config.to_dict())

    # Return all services
    return {
        "llm": llm_service,
        "clipboard": clipboard_service,
        "code_analysis": code_analysis_service,
        "memory": memory_service,
        "scraping": scraping_service,
    }
```

### 6. Installation and Dependencies

Update `pyproject.toml` to include the Crawl4AI dependency:

```toml
[tool.poetry.dependencies]
python = ">=3.9,<3.12"
crawl4ai = "^0.5.0"
```

### 7. Migration Steps

1. Install the new dependency: `pip install crawl4ai`
2. Run the setup script: `crawl4ai-setup`
3. Replace the existing scraping module with the new implementation
4. Update any direct references to the scraping service in other modules
5. Test the new implementation with various types of websites
6. Create documentation for the enhanced scraping capabilities

## Additional Features to Consider

1. **Advanced Extraction Strategy Interface**:
   - Add support for structured data extraction with JSON schema
   - Create specialized extraction strategies for common websites

2. **Screenshot and PDF Generation**:
   - Add tool options to capture full-page screenshots
   - Support PDF generation for documentation purposes

3. **Authentication Support**:
   - Add capabilities for authenticated scraping (login credentials)
   - Session state preservation for multi-page scraping

4. **Rate Limiting and Ethical Scraping**:
   - Add built-in rate limiting to avoid overloading websites
   - Respect robots.txt directives

5. **Error Handling and Fallbacks**:
   - Enhanced error reporting
   - Fallback strategies when JavaScript execution fails

## Timeline

1. **Phase 1 (1-2 days)**:
   - Basic integration of Crawl4AI
   - Core functionality working with existing tool interface

2. **Phase 2 (2-3 days)**:
   - Enhance tool definitions with new capabilities
   - Add configuration options
   - Implement error handling

3. **Phase 3 (3-5 days)**:
   - Add advanced features (structured extraction, auth support)
   - Comprehensive testing
   - Documentation updates

## Conclusion

Migrating to Crawl4AI offers significant advantages over the current FirecrawlApp implementation:

1. **Reduced Dependencies**: No API key requirement means less external dependencies
2. **Enhanced Capabilities**: Better content extraction, especially for AI consumption
3. **Performance Improvements**: Async architecture and caching for speed
4. **Flexibility**: More configuration options for different scraping needs
5. **Future-Proofing**: Active development and community support

This migration aligns with SwissKnife's architecture and will enhance the capabilities of all agents that use web scraping functionality.

# Implement Crawl4AI Integration for SwissKnife Scraping Module

> Ingest the information from this file, implement the Low-level Tasks, and generate the code that will satisfy Objectives

## Objectives

- Replace the current FirecrawlApp-based scraping module with Crawl4AI
- Implement an enhanced scraping service with improved content extraction capabilities
- Maintain compatibility with the existing tool interface and agent system
- Add configuration options for customizing scraping behavior
- Support multiple output formats (markdown, raw, HTML, JSON)
- Add support for media extraction and metadata
- Implement proper async/sync handling for the tool interface

## Contexts

- ./AgentCrew.modules/scraping/service.py: Current scraping service implementation using FirecrawlApp
- ./AgentCrew.modules/scraping/tool.py: Current tool definition and handler implementation
- ./AgentCrew.modules/tools/registration.py: Tool registration utility
- ./AgentCrew.main.py: Main application file for service initialization

## Low-level Tasks

1. CREATE ./AgentCrew.modules/scraping/config.py:
   - Create a ScrapingConfig dataclass with browser and content filtering configuration options
   - Implement to_dict method to convert configuration to dictionary format

2. UPDATE ./AgentCrew.modules/scraping/service.py:
   - Replace FirecrawlApp with AsyncWebCrawler from Crawl4AI
   - Implement initialization with configurable options
   - Create async crawler management methods
   - Update scrape_url method to support multiple output formats
   - Add support for media and metadata extraction
   - Maintain the existing file URL detection functionality
   - Add proper error handling and cleanup methods

3. UPDATE ./AgentCrew.modules/scraping/tool.py:
   - Enhance the tool definition with support for output format and media extraction options
   - Update the scraping handler to work with the new async service
   - Implement a synchronous wrapper for the async scrape_url method
   - Update the register function to maintain compatibility

4. UPDATE ./requirements.txt or ./pyproject.toml:
   - Add Crawl4AI dependency (version 0.5.0 or later)
