"""Test that an exception from ``analyze_repo`` cache listing does not prevent
message enhancement in ``AgentContextManager.enhance_messages``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestContextCacheDegradation:
    """Verify graceful degradation when cache entry listing fails."""

    @pytest.fixture
    def agent(self) -> MagicMock:
        """Return a LocalAgent-like mock with a code_analysis service that raises."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.services = {
            "code_analysis": MagicMock(),
            "agent_manager": MagicMock(),
            "memory": MagicMock(),
            "context_persistent": MagicMock(),
        }
        agent.services["agent_manager"].one_turn_process = False
        agent.services["agent_manager"].context_shrink_enabled = False
        agent.services["context_persistent"].get_adaptive_behaviors.return_value = {}
        agent.services["memory"].list_memory_headers.return_value = []
        # Cache listing raises - must be caught gracefully
        agent.services[
            "code_analysis"
        ].get_cache_entries_for_context.side_effect = RuntimeError(
            "cache listing failed"
        )
        agent._colaboration_mode = MagicMock()
        agent._colaboration_mode.value = "normal"
        return agent

    def test_cache_listing_exception_does_not_block_enhance_messages(
        self, agent: MagicMock
    ) -> None:
        """An exception from get_cache_entries_for_context is caught harmlessly."""
        from AgentCrew.modules.agents.context_manager import AgentContextManager

        cm = AgentContextManager(agent)

        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

        try:
            cm.enhance_messages(messages)
        except RuntimeError as exc:
            pytest.fail(f"enhance_messages raised unexpectedly: {exc}")

        assert any(msg.get("role") == "user" for msg in messages), (
            "User message must remain after enhancement"
        )

    def test_cache_listing_returns_empty_no_section(self, agent: MagicMock) -> None:
        """When cache listing returns [], no 'Cached analyze_repo Results' section."""
        agent.services["code_analysis"].get_cache_entries_for_context.side_effect = None
        agent.services["code_analysis"].get_cache_entries_for_context.return_value = []

        from AgentCrew.modules.agents.context_manager import AgentContextManager

        cm = AgentContextManager(agent)

        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        cm.enhance_messages(messages)

        all_text = " ".join(
            part.get("text", "")
            for msg in messages
            if isinstance(msg.get("content"), list)
            for part in msg["content"]
            if isinstance(part, dict)
        )
        assert "Cached analyze_repo" not in all_text, (
            "No cache section should appear when no entries exist"
        )
