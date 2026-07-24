import unittest
from unittest.mock import MagicMock, patch

from AgentCrew.modules.chat.command_handler import CommandHandler
from AgentCrew.modules.chat.types import CommandType

from AgentCrew.modules.agents import AgentManager
from AgentCrew.modules.llm.base import BaseLLMService


class TestCommandHandler(unittest.TestCase):
    def setUp(self):
        # Create mock dependencies
        self.mock_llm = MagicMock(spec=BaseLLMService)
        self.mock_agent_manager = MagicMock(spec=AgentManager)
        self.handler = CommandHandler(self.mock_llm, self.mock_agent_manager)

    def test_parse_command(self):
        # Test various command types
        self.assertEqual(self.handler.parse_command("/clear"), (CommandType.CLEAR, ""))
        self.assertEqual(self.handler.parse_command("exit"), (CommandType.EXIT, ""))
        self.assertEqual(self.handler.parse_command("quit"), (CommandType.EXIT, ""))
        self.assertEqual(self.handler.parse_command("/copy"), (CommandType.COPY, ""))
        self.assertEqual(self.handler.parse_command("/debug"), (CommandType.DEBUG, ""))
        self.assertEqual(
            self.handler.parse_command("/think 1024"), (CommandType.THINK, "1024")
        )
        self.assertEqual(self.handler.parse_command("/jump 3"), (CommandType.JUMP, "3"))
        self.assertEqual(
            self.handler.parse_command("/agent code"), (CommandType.AGENT, "code")
        )
        self.assertEqual(
            self.handler.parse_command("/model gpt-4"), (CommandType.MODEL, "gpt-4")
        )
        self.assertEqual(
            self.handler.parse_command("/file test.py"), (CommandType.FILE, "test.py")
        )
        self.assertEqual(
            self.handler.parse_command("regular message"),
            (CommandType.UNKNOWN, "regular message"),
        )

    def test_handle_clear_command(self):
        result = self.handler._handle_clear_command()
        self.assertTrue(result.success)
        self.assertTrue(result.clear_screen)
        self.assertEqual(result.messages, [])

    def test_handle_exit_command(self):
        result = self.handler._handle_exit_command()
        self.assertTrue(result.success)
        self.assertTrue(result.exit_chat)

    @patch("modules.chat.command_handler.pyperclip")
    def test_handle_copy_command_no_response(self, mock_pyperclip):
        # Setup - no latest response
        self.mock_llm.latest_assistant_response = ""

        # Execute
        result = self.handler._handle_copy_command()

        # Assert
        self.assertFalse(result.success)
        mock_pyperclip.copy.assert_not_called()

    def test_handle_think_command_valid(self):
        # Setup
        self.mock_llm.set_think.return_value = True

        # Execute
        result = self.handler._handle_think_command("1024")

        # Assert
        self.assertTrue(result.success)
        self.mock_llm.set_think.assert_called_with(1024)

    def test_handle_think_command_invalid(self):
        # Execute
        result = self.handler._handle_think_command("invalid")

        # Assert
        self.assertFalse(result.success)
        self.mock_llm.set_think.assert_not_called()

    def test_handle_agent_command_list(self):
        # Setup
        self.mock_agent_manager.agents = {
            "code": MagicMock(description="Code assistant"),
            "architect": MagicMock(description="Architecture assistant"),
        }
        current_agent = MagicMock()
        current_agent.name = "code"
        self.mock_agent_manager.get_current_agent.return_value = current_agent

        # Execute
        result = self.handler._handle_agent_command("")

        # Assert
        self.assertTrue(result.success)
        self.assertTrue(result.skip_iteration)

    def test_handle_agent_command_switch(self):
        # Setup
        self.mock_agent_manager.select_agent.return_value = True
        new_agent = MagicMock()
        new_agent.llm = MagicMock()
        self.mock_agent_manager.get_current_agent.return_value = new_agent

        # Execute
        result = self.handler._handle_agent_command("architect")

        # Assert
        self.assertTrue(result.success)
        self.mock_agent_manager.select_agent.assert_called_with("architect")
        self.assertEqual(self.handler.llm, new_agent.llm)

    def test_handle_agent_command_invalid(self):
        # Setup
        self.mock_agent_manager.select_agent.return_value = False
        self.mock_agent_manager.agents = {"code": MagicMock(), "architect": MagicMock()}

        # Execute
        result = self.handler._handle_agent_command("invalid_agent")

        # Assert
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
