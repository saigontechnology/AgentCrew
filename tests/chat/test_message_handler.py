import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from AgentCrew.modules.chat.message_handler import MessageHandler
from AgentCrew.modules.llm.base import BaseLLMService


class TestMessageHandler(unittest.TestCase):
    def setUp(self):
        # Create a mock LLM service
        self.mock_llm = MagicMock(spec=BaseLLMService)
        self.mock_console = MagicMock(spec=Console)
        self.handler = MessageHandler(self.mock_llm, self.mock_console)

    def test_process_file_command(self):
        # Setup
        self.mock_llm.handle_file_command.return_value = {
            "type": "file",
            "content": "file content",
        }

        # Execute
        result = self.handler.process_file_command("test_file.txt")

        # Assert
        self.mock_llm.handle_file_command.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "user")

    def test_process_file_command_failure(self):
        # Setup
        self.mock_llm.handle_file_command.return_value = None

        # Execute
        result = self.handler.process_file_command("nonexistent_file.txt")

        # Assert
        self.assertIsNone(result)

    def test_process_file_for_message(self):
        # Setup
        expected_content = {"type": "file", "content": "file content"}
        self.mock_llm.process_file_for_message.return_value = expected_content

        # Execute
        result = self.handler.process_file_for_message("test_file.txt")

        # Assert
        self.assertEqual(result, expected_content)

    def test_format_assistant_message(self):
        # Setup
        expected_message = {"role": "assistant", "content": "test response"}
        self.mock_llm.format_assistant_message.return_value = expected_message

        # Execute
        result = self.handler.format_assistant_message("test response")

        # Assert
        self.assertEqual(result, expected_message)

    def test_calculate_cost(self):
        # Setup
        self.mock_llm.calculate_cost.return_value = 0.05

        # Execute
        cost = self.handler.calculate_cost(1000, 500)

        # Assert
        self.assertEqual(cost, 0.05)
        self.assertEqual(self.handler.session_cost, 0.05)

        # Test cumulative cost
        cost = self.handler.calculate_cost(1000, 500)
        self.assertEqual(self.handler.session_cost, 0.1)

    @patch("modules.chat.message_handler.MessageHandler._clear_to_start")
    def test_stream_assistant_response_error(self, mock_clear):
        # Setup
        self.mock_llm.stream_assistant_response.side_effect = Exception("Test error")

        # Execute
        response, input_tokens, output_tokens = self.handler.stream_assistant_response(
            []
        )

        # Assert
        self.assertIsNone(response)
        self.assertEqual(input_tokens, 0)
        self.assertEqual(output_tokens, 0)


if __name__ == "__main__":
    unittest.main()
