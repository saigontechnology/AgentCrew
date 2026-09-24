import os
import unittest
from unittest.mock import patch

from dotenv import load_dotenv


class YoucomSearchServiceShapeTest(unittest.TestCase):
    """Offline shape tests for the optional You.com search provider."""

    def setUp(self):
        load_dotenv()
        os.environ.pop("YDC_API_KEY", None)
        from AgentCrew.modules.web_search.providers.youcom import (
            YoucomSearchService,
        )

        self.service = YoucomSearchService()

    def test_keyless_uses_free_profile(self):
        self.assertIn("profile=free", self.service.base_url)
        self.assertIsNone(self.service.api_key)

    def test_authenticated_uses_standard_endpoint(self):
        from AgentCrew.modules.web_search.providers.youcom import (
            YOU_MCP_BASE_URL,
            YoucomSearchService,
        )

        with patch.dict(os.environ, {"YDC_API_KEY": "test-key"}):
            service = YoucomSearchService()
        self.assertEqual(service.base_url, YOU_MCP_BASE_URL)
        self.assertEqual(
            service._headers()["Authorization"],
            "Bearer test-key",
        )

    def test_keyless_headers_have_no_auth(self):
        self.assertNotIn("Authorization", self.service._headers())

    def test_search_formats_you_results(self):
        you_payload = {
            "results": {
                "web": [
                    {
                        "title": "Example",
                        "url": "https://example.com/",
                        "snippets": ["snippet text"],
                        "contents": {"highlights": ["highlight text"]},
                    }
                ]
            }
        }
        with patch.object(self.service, "_call_tool", return_value=you_payload):
            results = self.service.search("example query", max_results=5)
        self.assertIn("results", results)
        self.assertEqual(len(results["results"]), 1)
        self.assertEqual(results["results"][0]["title"], "Example")
        self.assertEqual(results["results"][0]["url"], "https://example.com/")
        self.assertEqual(results["results"][0]["content"], "highlight text")
        formatted = self.service.format_search_results(results)
        self.assertIn("Example", formatted)
        self.assertIn("https://example.com/", formatted)

    def test_search_error_is_formatted(self):
        with patch.object(
            self.service, "_call_tool", return_value={"error": "boom"}
        ):
            results = self.service.search("anything")
        self.assertIn("error", results)
        self.assertIn("boom", self.service.format_search_results(results))

    def test_crawl_returns_explanatory_error(self):
        results = self.service.crawl("https://example.com/")
        self.assertIn("error", results)
        self.assertIn("fetch_webpage", results["error"])

    def test_factory_selects_provider(self):
        from AgentCrew.modules.web_search import (
            TavilySearchService,
            create_search_service,
        )
        from AgentCrew.modules.web_search.providers.youcom import (
            YoucomSearchService,
        )

        with patch.dict(os.environ, {"WEB_SEARCH_PROVIDER": "youcom"}):
            service = create_search_service()
        self.assertIsInstance(service, YoucomSearchService)

        with patch.dict(
            os.environ,
            {"WEB_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "test-key"},
        ):
            service = create_search_service()
        self.assertIsInstance(service, TavilySearchService)

        env = {k: v for k, v in os.environ.items() if k != "WEB_SEARCH_PROVIDER"}
        env["TAVILY_API_KEY"] = "test-key"
        with patch.dict(os.environ, env, clear=True):
            service = create_search_service()
        self.assertIsInstance(service, TavilySearchService)


class YoucomSearchLiveTest(unittest.IsolatedAsyncioTestCase):
    """Live tests against the keyless You.com MCP endpoint."""

    @classmethod
    def setUpClass(cls):
        load_dotenv()
        if os.getenv("YDC_API_KEY"):
            raise unittest.SkipTest(
                "YDC_API_KEY set; live keyless test skipped"
            )

        from AgentCrew.modules.web_search.providers.youcom import (
            YoucomSearchService,
        )
        from AgentCrew.modules.web_search.tool import (
            get_web_search_tool_handler,
        )

        cls.service = YoucomSearchService()
        cls.search_handler = staticmethod(
            get_web_search_tool_handler(cls.service)
        )

    def test_live_web_search(self):
        result = self.service.search("Python programming language", max_results=2)
        self.assertNotIn("error", result)
        self.assertIsInstance(result["results"], list)
        self.assertGreaterEqual(len(result["results"]), 1)
        first = result["results"][0]
        self.assertIn("title", first)
        self.assertIn("url", first)

    async def test_live_search_handler(self):
        params = {
            "query": "Artificial intelligence news",
            "search_depth": "basic",
            "max_results": 3,
        }
        result = await self.search_handler(**params)
        self.assertIsInstance(result, str)
        self.assertIn("http", result)


if __name__ == "__main__":
    unittest.main()
