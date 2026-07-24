import unittest

from AgentCrew.modules.clipboard.service import ClipboardService
from AgentCrew.modules.clipboard.tool import (
    get_clipboard_read_tool_handler,
    get_clipboard_write_tool_handler,
)


class ClipboardServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clipboard_service = ClipboardService()
        self.read_handler = get_clipboard_read_tool_handler(self.clipboard_service)
        self.write_handler = get_clipboard_write_tool_handler(self.clipboard_service)

    def test_write_and_read_text(self):
        """Test writing text to clipboard and reading it back."""
        test_text = "This is a test clipboard content"

        write_result = self.clipboard_service.write(test_text)
        self.assertTrue(write_result["success"])

        read_result = self.clipboard_service.read()
        self.assertTrue(read_result["success"])
        self.assertEqual(read_result["type"], "text")
        self.assertEqual(read_result["content"], test_text)

    async def test_clipboard_write_handler(self):
        """Test the clipboard write tool handler."""
        test_text = "Testing clipboard write handler"

        result = await self.write_handler(content=test_text)
        self.assertIsInstance(result, str)

        read_result = self.clipboard_service.read()
        self.assertTrue(read_result["success"])
        self.assertEqual(read_result["content"], test_text)

    async def test_clipboard_read_handler(self):
        """Test the clipboard read tool handler."""
        test_text = "Testing clipboard read handler"

        self.clipboard_service.write(test_text)

        result = await self.read_handler()
        self.assertIsInstance(result, str)
        self.assertEqual(result, test_text)

    async def test_missing_content_parameter(self):
        """Test write handler with missing content parameter."""
        with self.assertRaises(Exception):
            await self.write_handler()


if __name__ == "__main__":
    unittest.main()
