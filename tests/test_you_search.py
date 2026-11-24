import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from AgentCrew.modules.web_search.tool import get_web_search_tool_handler
from AgentCrew.modules.web_search.you_service import (
    YOU_FREE_SEARCH_URL,
    YOU_KEYED_SEARCH_URL,
    YOU_USER_AGENT,
    YouSearchService,
)

SAMPLE = {
    "results": {
        "web": [
            {
                "url": "https://docs.python.org/3/library/asyncio.html",
                "title": "asyncio",
                "description": "Asynchronous I/O",
                "snippets": ["asyncio is a library", "to write concurrent code"],
                "page_age": "2026-01-01T00:00:00",
            },
            {"url": "https://example.com", "title": "Example", "description": "desc"},
        ]
    }
}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(payload=SAMPLE):
    return _Resp(json.dumps(payload).encode())


class YouSearchServiceTest(unittest.IsolatedAsyncioTestCase):
    def _service(self, key=None):
        with (
            patch.dict("os.environ", {"YDC_API_KEY": key or ""}),
            patch("AgentCrew.modules.web_search.you_service.load_dotenv"),
        ):
            return YouSearchService()

    def test_keyless_uses_free_get_endpoint(self):
        svc = self._service()
        with patch("urllib.request.urlopen", return_value=_ok()) as m:
            result = svc.search("python asyncio", max_results=2)
        req = m.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.startswith(YOU_FREE_SEARCH_URL + "?"))
        self.assertIn("count=2", req.full_url)
        self.assertEqual(req.get_header("User-agent"), YOU_USER_AGENT)
        self.assertIsNone(req.get_header("X-api-key"))
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(
            result["results"][0]["content"],
            "asyncio is a library to write concurrent code",
        )
        self.assertEqual(result["results"][1]["content"], "desc")

    def test_key_uses_keyed_post_endpoint(self):
        svc = self._service("test-key")
        with patch("urllib.request.urlopen", return_value=_ok()) as m:
            svc.search("python", max_results=3)
        req = m.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, YOU_KEYED_SEARCH_URL)
        self.assertEqual(req.get_header("X-api-key"), "test-key")
        self.assertEqual(json.loads(req.data), {"query": "python", "count": 3})

    def test_http_errors_are_reported(self):
        svc = self._service()
        err = urllib.error.HTTPError(YOU_FREE_SEARCH_URL, 429, "Too Many", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = svc.search("x")
        self.assertIn("YDC_API_KEY", result["error"])

    async def test_handler_formats_results(self):
        svc = self._service()
        handler = get_web_search_tool_handler(svc)
        with patch("urllib.request.urlopen", return_value=_ok()):
            text = await handler(
                query="python asyncio", search_depth="basic", topic="general"
            )
        self.assertIn("2 results", text)
        self.assertIn("https://docs.python.org/3/library/asyncio.html", text)


if __name__ == "__main__":
    unittest.main()
