"""Unit tests for the onboarding agent-definition generation flow."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from AgentCrew.modules.onboarding.service import (
    OnboardingGenerationResult,
    OnboardingService,
)

VALID_TOML = """Here is the agent:

```toml
[[agents]]
name = "TestAgent"
description = "Test agent"
system_prompt = "You are a test agent."
tools = []
temperature = 0.4
```
"""

CLARIFYING_TEXT = "A couple of questions: what stack, what output format?"


def _make_service() -> OnboardingService:
    """Build an OnboardingService with mocked dependencies."""
    service = OnboardingService(
        llm_service=MagicMock(), agents_config=MagicMock(), services={}
    )
    service.console = MagicMock()
    return service


class TestRunOnboardingChat(unittest.IsolatedAsyncioTestCase):
    """Tests for _run_onboarding_chat exit paths."""

    async def _run_chat(self, service, generate_mock, ask_mock=None):
        service._build_onboarding_agent = MagicMock(return_value=MagicMock())
        service._generate_onboarding_response = generate_mock
        service._print_assistant_message = MagicMock()
        service._ask_onboarding_input = ask_mock or AsyncMock(return_value="answer")
        return await service._run_onboarding_chat("TestAgent", "Test description")

    async def test_success_returns_toml_definition(self):
        service = _make_service()
        result = await self._run_chat(service, AsyncMock(return_value=VALID_TOML))
        self.assertEqual(result.toml_definition, VALID_TOML)
        self.assertIsNone(result.error)
        self.assertIsNone(result.last_response)

    async def test_exception_returns_error_detail(self):
        service = _make_service()
        generate = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch("AgentCrew.modules.onboarding.service.logger") as mock_logger:
            result = await self._run_chat(service, generate)
        self.assertIsNone(result.toml_definition)
        self.assertIsNone(result.last_response)
        self.assertEqual(result.error, "RuntimeError: provider down")
        mock_logger.warning.assert_called_once()

    async def test_non_string_response_returns_error(self):
        service = _make_service()
        result = await self._run_chat(service, AsyncMock(return_value=None))
        self.assertEqual(result.error, "LLM returned an empty or invalid response.")

    async def test_max_turns_returns_error_with_last_response(self):
        service = _make_service()
        generate = AsyncMock(return_value=CLARIFYING_TEXT)
        ask = AsyncMock(return_value="answer")
        result = await self._run_chat(service, generate, ask_mock=ask)
        self.assertIsNone(result.toml_definition)
        self.assertEqual(
            result.error,
            "Reached maximum conversation turns without getting a valid "
            "agent definition.",
        )
        self.assertEqual(result.last_response, CLARIFYING_TEXT)
        self.assertEqual(ask.await_count, 5)

    async def test_cancel_returns_no_error(self):
        service = _make_service()
        generate = AsyncMock(return_value=CLARIFYING_TEXT)
        ask = AsyncMock(return_value=None)
        result = await self._run_chat(service, generate, ask_mock=ask)
        self.assertIsNone(result.toml_definition)
        self.assertIsNone(result.error)
        self.assertIsNone(result.last_response)
        ask.assert_awaited_once()


class TestCreateAgent(unittest.TestCase):
    """Tests for create_agent result handling."""

    def setUp(self):
        self.service = _make_service()

    @patch("AgentCrew.modules.onboarding.service.sys.stdin")
    def test_success_saves_agent(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        result_obj = OnboardingGenerationResult(toml_definition=VALID_TOML)
        self.service._run_onboarding_chat = AsyncMock(return_value=result_obj)
        self.service._save_agent = MagicMock(return_value=True)
        self.service._print_success = MagicMock()
        ok = self.service.create_agent(name="TestAgent", description="desc")
        self.assertTrue(ok)
        self.service._save_agent.assert_called_once_with(VALID_TOML)

    @patch("AgentCrew.modules.onboarding.service.sys.stdin")
    def test_failure_shows_error_and_last_response(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        result_obj = OnboardingGenerationResult(
            error="RuntimeError: provider down",
            last_response="I tried to generate the agent but failed.",
        )
        self.service._run_onboarding_chat = AsyncMock(return_value=result_obj)
        with patch.object(self.service, "_print_error") as mock_print_error:
            ok = self.service.create_agent(name="TestAgent", description="desc")
        self.assertFalse(ok)
        mock_print_error.assert_called_once()
        message = mock_print_error.call_args.args[0]
        self.assertIn("Failed to generate agent definition.", message)
        self.assertIn("Reason: RuntimeError: provider down", message)
        self.assertIn("Last model response:", message)
        self.assertIn("I tried to generate the agent but failed.", message)
        self.assertIn("You can create one manually in agents.toml.", message)

    @patch("AgentCrew.modules.onboarding.service.sys.stdin")
    def test_cancel_prints_no_error_panel(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        self.service._run_onboarding_chat = AsyncMock(
            return_value=OnboardingGenerationResult()
        )
        with patch.object(self.service, "_print_error") as mock_print_error:
            ok = self.service.create_agent(name="TestAgent", description="desc")
        self.assertFalse(ok)
        mock_print_error.assert_not_called()

    @patch("AgentCrew.modules.onboarding.service.sys.stdin")
    def test_outer_exception_becomes_error_result(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        self.service._run_onboarding_chat = AsyncMock(
            side_effect=RuntimeError("async boom")
        )
        with (
            patch.object(self.service, "_print_error") as mock_print_error,
            patch("AgentCrew.modules.onboarding.service.logger") as mock_logger,
        ):
            ok = self.service.create_agent(name="TestAgent", description="desc")
        self.assertFalse(ok)
        mock_logger.warning.assert_called_once()
        message = mock_print_error.call_args.args[0]
        self.assertIn("Reason: RuntimeError: async boom", message)
