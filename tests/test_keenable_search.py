import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from AgentCrew.modules.web_search.keenable_service import KeenableSearchService
from AgentCrew.modules.web_search.tool import (
    get_web_extract_tool_handler,
    get_web_search_tool_handler,
    register,
)

SEARCH_RESPONSE = {
    "query": "python asyncio",
    "mode": "pro",
    "results": [
        {
            "title": "asyncio docs",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "description": "",
            "snippet": "asyncio is a library to write concurrent code",
        }
    ],
}

FETCH_RESPONSE = {
    "url": "https://www.python.org/",
    "title": "Welcome to Python.org",
    "content": "# Welcome to Python.org",
    "truncated": False,
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def fake_urlopen(payload):
    def _urlopen(request, timeout=None):
        _urlopen.request = request
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    return _urlopen


class KeenableSearchServiceTest(unittest.IsolatedAsyncioTestCase):
    def make_service(self, api_key=None):
        env = {"KEENABLE_API_KEY": api_key} if api_key else {}
        with (
            patch("AgentCrew.modules.web_search.keenable_service.load_dotenv"),
            patch.dict("os.environ", env, clear=True),
        ):
            return KeenableSearchService()

    def test_search_without_key_uses_public_endpoint(self):
        service = self.make_service()
        opener = fake_urlopen(SEARCH_RESPONSE)
        with patch("urllib.request.urlopen", opener):
            results = service.search(
                "python asyncio", max_results=3, include_domains=["docs.python.org"]
            )

        request = opener.request
        self.assertEqual(request.full_url, "https://api.keenable.ai/v1/search/public")
        self.assertEqual(request.get_header("X-keenable-title"), "AgentCrew")
        self.assertIsNone(request.get_header("X-api-key"))
        self.assertEqual(
            json.loads(request.data),
            {"query": "python asyncio", "max_results": 3, "site": "docs.python.org"},
        )
        self.assertEqual(
            results["results"][0],
            {
                "title": "asyncio docs",
                "url": "https://docs.python.org/3/library/asyncio.html",
                "content": "asyncio is a library to write concurrent code",
            },
        )

    def test_search_with_key_uses_keyed_endpoint(self):
        service = self.make_service(api_key="test-key")
        opener = fake_urlopen(SEARCH_RESPONSE)
        with patch("urllib.request.urlopen", opener):
            service.search("python asyncio")

        self.assertEqual(opener.request.full_url, "https://api.keenable.ai/v1/search")
        self.assertEqual(opener.request.get_header("X-api-key"), "test-key")

    def test_search_error_is_returned(self):
        service = self.make_service()
        error = urllib.error.URLError("boom")
        with patch("urllib.request.urlopen", side_effect=error):
            results = service.search("python")

        self.assertIn("error", results)
        self.assertTrue(
            service.format_search_results(results).startswith("Search error")
        )

    async def test_search_handler_formats_results(self):
        service = self.make_service()
        handler = get_web_search_tool_handler(service)
        with patch("urllib.request.urlopen", fake_urlopen(SEARCH_RESPONSE)):
            output = await handler(
                query="python asyncio", search_depth="basic", topic="general"
            )

        self.assertIn("1 results", output)
        self.assertIn("https://docs.python.org/3/library/asyncio.html", output)

    async def test_extract_handler_uses_fetch_endpoint(self):
        service = self.make_service()
        handler = get_web_extract_tool_handler(service)
        opener = fake_urlopen(FETCH_RESPONSE)
        with patch("urllib.request.urlopen", opener):
            blocks = await handler(url="https://www.python.org/")

        self.assertEqual(
            opener.request.full_url,
            "https://api.keenable.ai/v1/fetch/public?url=https%3A%2F%2Fwww.python.org%2F",
        )
        self.assertEqual(
            blocks,
            [
                {
                    "type": "text",
                    "text": "https://www.python.org/\n# Welcome to Python.org",
                }
            ],
        )

    async def test_extract_error_is_reported(self):
        service = self.make_service()
        handler = get_web_extract_tool_handler(service)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            output = await handler(url="https://www.python.org/")

        self.assertTrue(output.startswith("Extract failed"))

    def test_register_skips_crawl(self):
        service = self.make_service()
        with patch(
            "AgentCrew.modules.tools.registration.register_tool"
        ) as register_tool:
            register(service)

        names = [c.args[0]()["function"]["name"] for c in register_tool.call_args_list]
        self.assertEqual(names, ["search_web", "fetch_webpage"])


if __name__ == "__main__":
    unittest.main()
