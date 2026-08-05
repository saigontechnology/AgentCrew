"""
Tests for per-agent conversation usage tracking and the /stats command.

Focus areas:
1. ConversationUsage cumulative value object
2. LocalAgent.record_conversation_usage / reset_conversation_usage
3. Per-turn, per-agent request ledger: raw requests summed per category/cost
4. Transfer attribution: requests recorded under the executing agent
5. Idempotent finalization committing only uncommitted ledger deltas
6. Delegate usage: optional run_agent_loop callback under the target agent
7. /stats formatting: markers, zeros, unknown limits, context bar, aggregate
8. /stats routing via CommandProcessor (and /usage left unchanged)
9. Console completion + help exposure
10. Reset on new/loaded conversation
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from AgentCrew.modules.agents import LocalAgent, run_agent_loop
from AgentCrew.modules.chat.message.command_processor import CommandProcessor
from AgentCrew.modules.chat.message.commands.base import CommandResult
from AgentCrew.modules.chat.message.commands.utility_commands import UtilityCommands
from AgentCrew.modules.chat.message.conversation import ConversationManager
from AgentCrew.modules.chat.message.handler import MessageHandler
from AgentCrew.modules.console.completers import ChatCompleter
from AgentCrew.modules.console.constants import COMMAND_HELP_MESSAGES
from AgentCrew.modules.events.constants import AppEvents
from AgentCrew.modules.llm.token_usage import ConversationUsage, TokenUsage


class _FakeLLM:
    """Deterministic cost calculator for tests."""

    def __init__(self, provider="claude", model="claude-test"):
        self.provider_name = provider
        self.model = model

    def calculate_cost(self, input_tokens, output_tokens, cached_tokens=0):
        return (
            (input_tokens / 1_000_000) * 10.0
            + (output_tokens / 1_000_000) * 20.0
            + (cached_tokens / 1_000_000) * 5.0
        )


class _StubModel:
    def __init__(self, max_context_token):
        self.max_context_token = max_context_token


class _StubRegistry:
    def __init__(self, model=None):
        self._model = model

    def get_model(self, model_id):
        return self._model


def make_agent(name="coder", llm=None):
    """Create a real LocalAgent without an LLM (or with a fake one)."""
    return LocalAgent(
        name=name,
        description="",
        llm_service=llm,
        services={},
        tools=[],
    )


def make_handler(agent):
    """Build a MagicMock MessageHandler-shaped object for finalize tests."""
    handler = MagicMock()
    handler._get_messages_for_current_turn = MagicMock(
        return_value=[{"role": "user", "content": "hi"}]
    )
    handler.current_conversation_id = "conv-1"
    handler.persistent_service = MagicMock()
    handler.bus = MagicMock()
    handler.current_user_input = {
        "role": "user",
        "content": [{"type": "text", "text": "hi"}],
    }
    handler.current_user_input_idx = 3
    handler.conversation_manager = MagicMock()
    handler._extract_user_text = MagicMock(return_value="hi")
    handler.agent = agent
    handler.streamline_messages = []
    handler.last_assisstant_response_idx = 0
    handler._turn_usage_ledger = {}
    handler._turn_usage_committed = {}
    return handler


# ─────────────────────────────────────────────
#  ConversationUsage value object
# ─────────────────────────────────────────────


class TestConversationUsage:
    def test_defaults_are_zero(self):
        usage = ConversationUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cached_tokens == 0
        assert usage.cache_creation_tokens == 0
        assert usage.total_input_tokens == 0
        assert usage.cost == 0.0
        assert usage.total_tokens == 0

    def test_total_tokens_is_total_input_plus_output(self):
        usage = ConversationUsage(total_input_tokens=1200, output_tokens=500)
        assert usage.total_tokens == 1700

    def test_add_accumulates_all_fields_and_cost(self):
        usage = ConversationUsage()
        usage.add(
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            cache_creation_tokens=5,
            total_input_tokens=120,
            cost=0.021,
        )
        usage.add(
            input_tokens=200,
            output_tokens=80,
            cached_tokens=30,
            cache_creation_tokens=3,
            total_input_tokens=230,
            cost=0.042,
        )
        assert usage.input_tokens == 300
        assert usage.output_tokens == 130
        assert usage.cached_tokens == 50
        assert usage.cache_creation_tokens == 8
        assert usage.total_input_tokens == 350
        assert usage.cost == pytest.approx(0.063)
        assert usage.total_tokens == 480


# ─────────────────────────────────────────────
#  LocalAgent conversation usage
# ─────────────────────────────────────────────


class TestLocalAgentConversationUsage:
    def test_record_accumulates_tokens_and_cost_once(self):
        agent = make_agent(llm=_FakeLLM())
        usage = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=200,
            cache_creation_tokens=50,
            total_input_tokens=1200,
        )
        agent.record_conversation_usage(usage)
        assert agent.conversation_usage.input_tokens == 1000
        assert agent.conversation_usage.output_tokens == 500
        assert agent.conversation_usage.cached_tokens == 200
        assert agent.conversation_usage.cache_creation_tokens == 50
        assert agent.conversation_usage.total_input_tokens == 1200
        assert agent.conversation_usage.total_tokens == 1700
        expected_cost = 1000 / 1e6 * 10 + 500 / 1e6 * 20 + 200 / 1e6 * 5
        assert agent.conversation_usage.cost == pytest.approx(expected_cost)

    def test_two_agents_track_independently(self):
        coder = make_agent("coder", llm=_FakeLLM())
        writer = make_agent("writer", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        assert coder.conversation_usage.total_tokens == 150
        assert writer.conversation_usage.total_tokens == 0
        assert writer.conversation_usage.cost == 0.0

    def test_cost_accumulates_across_turns(self):
        agent = make_agent(llm=_FakeLLM())
        agent.record_conversation_usage(
            TokenUsage(
                input_tokens=1000,
                output_tokens=500,
                cached_tokens=200,
                total_input_tokens=1200,
            )
        )
        agent.record_conversation_usage(
            TokenUsage(
                input_tokens=2000,
                output_tokens=1000,
                cached_tokens=400,
                total_input_tokens=2400,
            )
        )
        first = 1000 / 1e6 * 10 + 500 / 1e6 * 20 + 200 / 1e6 * 5
        second = 2000 / 1e6 * 10 + 1000 / 1e6 * 20 + 400 / 1e6 * 5
        assert agent.conversation_usage.cost == pytest.approx(first + second)
        assert agent.conversation_usage.total_input_tokens == 3600

    def test_zero_usage_turn_adds_nothing(self):
        agent = make_agent(llm=_FakeLLM())
        agent.record_conversation_usage(TokenUsage())
        assert agent.conversation_usage.total_tokens == 0
        assert agent.conversation_usage.cost == 0.0

    def test_no_llm_records_zero_cost(self):
        agent = make_agent(llm=None)
        agent.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        assert agent.conversation_usage.total_tokens == 150
        assert agent.conversation_usage.cost == 0.0

    def test_reset_conversation_usage_zeroes_tracker(self):
        agent = make_agent(llm=_FakeLLM())
        agent.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        agent.reset_conversation_usage()
        assert agent.conversation_usage == ConversationUsage()

    def test_two_requests_sum_all_categories_and_cost(self):
        agent = make_agent(llm=_FakeLLM())
        req1 = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            cache_creation_tokens=5,
            total_input_tokens=120,
        )
        req2 = TokenUsage(
            input_tokens=200,
            output_tokens=80,
            cached_tokens=30,
            cache_creation_tokens=3,
            total_input_tokens=230,
        )
        # Raw requests are recorded individually, so no recursive request's
        # output/cost is lost to the merged control-flow representation.
        agent.record_conversation_usage(req1)
        agent.record_conversation_usage(req2)
        assert agent.conversation_usage.input_tokens == 300
        assert agent.conversation_usage.output_tokens == 130
        assert agent.conversation_usage.cached_tokens == 50
        assert agent.conversation_usage.cache_creation_tokens == 8
        assert agent.conversation_usage.total_input_tokens == 350
        assert agent.conversation_usage.total_tokens == 480
        expected_cost = (
            100 / 1e6 * 10
            + 50 / 1e6 * 20
            + 20 / 1e6 * 5
            + 200 / 1e6 * 10
            + 80 / 1e6 * 20
            + 30 / 1e6 * 5
        )
        assert agent.conversation_usage.cost == pytest.approx(expected_cost)


# ─────────────────────────────────────────────
#  Exactly-once recording at turn finalization
# ─────────────────────────────────────────────


class TestMessageHandlerRecording:
    def test_ledger_commit_attributes_usage_to_executing_agents(self):
        source = make_agent("source", llm=_FakeLLM())
        target = make_agent("target", llm=_FakeLLM())
        handler = make_handler(source)
        MessageHandler._record_turn_request_usage(
            handler,
            source,
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100),
        )
        MessageHandler._record_turn_request_usage(
            handler,
            target,
            TokenUsage(
                input_tokens=200,
                output_tokens=100,
                cached_tokens=50,
                total_input_tokens=250,
            ),
        )
        MessageHandler._finalize_current_turn(handler, TokenUsage(), True)
        assert source.conversation_usage.total_tokens == 150
        assert target.conversation_usage.total_tokens == 350

        # Both executing agents are involved, so both appear in /stats with
        # the aggregate current-conversation total.
        message = UtilityCommands._format_stats_message(
            {"source": source, "target": target}, "source"
        )
        assert "source" in message and "target" in message
        assert "300 in | 150 out | 50 cached | 0 cache-write | 500 total" in message

    def test_finalize_commits_only_new_usage_across_finalizations(self):
        source = make_agent("source", llm=_FakeLLM())
        target = make_agent("target", llm=_FakeLLM())
        handler = make_handler(source)
        MessageHandler._record_turn_request_usage(
            handler,
            source,
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100),
        )
        MessageHandler._finalize_current_turn(handler, TokenUsage(), True)
        assert source.conversation_usage.total_tokens == 150
        assert source.conversation_usage.cost == pytest.approx(
            100 / 1e6 * 10 + 50 / 1e6 * 20
        )

        # A deferred continuation adds a new request under the target agent
        # and finalizes the turn a second time: only the new usage is
        # committed, with neither omission nor double counting.
        MessageHandler._record_turn_request_usage(
            handler,
            target,
            TokenUsage(input_tokens=200, output_tokens=100, total_input_tokens=200),
        )
        MessageHandler._finalize_current_turn(handler, TokenUsage(), True)
        assert source.conversation_usage.total_tokens == 150
        assert target.conversation_usage.total_tokens == 300

        # A repeated finalization without new requests is a no-op.
        MessageHandler._finalize_current_turn(handler, TokenUsage(), True)
        assert source.conversation_usage.total_tokens == 150
        assert target.conversation_usage.total_tokens == 300

    def test_record_turn_request_usage_skips_empty_and_non_local(self):
        remote = MagicMock()
        remote.name = "remote"
        handler = make_handler(remote)
        MessageHandler._record_turn_request_usage(
            handler, remote, TokenUsage(input_tokens=100, total_input_tokens=100)
        )
        MessageHandler._record_turn_request_usage(handler, remote, TokenUsage())
        assert handler._turn_usage_ledger == {}

    @pytest.mark.asyncio
    async def test_fresh_turn_resets_usage_ledger(self):
        agent = make_agent("coder")
        handler = MagicMock()
        handler.agent = agent
        handler._turn_usage_ledger = {"stale": ["old"]}
        handler._turn_usage_committed = {"stale": 1}
        handler._create_stream_session = MagicMock(return_value=MagicMock())
        handler._clear_stream_session = MagicMock()
        handler.voice_service = None
        run_response = AsyncMock(return_value=(None, TokenUsage()))
        handler._run_stream_response = run_response
        await MessageHandler.get_assistant_response(handler)
        assert handler._turn_usage_ledger == {}
        assert handler._turn_usage_committed == {}

    @pytest.mark.asyncio
    async def test_recursive_response_keeps_usage_ledger_open(self):
        agent = make_agent("coder")
        handler = MagicMock()
        handler.agent = agent
        handler._turn_usage_ledger = {"stale": ["old"]}
        handler._turn_usage_committed = {"stale": 1}
        handler._create_stream_session = MagicMock(return_value=MagicMock())
        handler._clear_stream_session = MagicMock()
        handler.voice_service = None
        run_response = AsyncMock(return_value=(None, TokenUsage()))
        handler._run_stream_response = run_response
        await MessageHandler.get_assistant_response(
            handler, TokenUsage(input_tokens=10, total_input_tokens=10)
        )
        assert handler._turn_usage_ledger == {"stale": ["old"]}
        assert handler._turn_usage_committed == {"stale": 1}


# ─────────────────────────────────────────────
#  Context window helpers
# ─────────────────────────────────────────────


class TestContextUsage:
    def _patch_registry(self, monkeypatch, model=None):
        from AgentCrew.modules.llm import model_registry

        stub = _StubRegistry(model)
        monkeypatch.setattr(
            model_registry.ModelRegistry, "get_instance", staticmethod(lambda: stub)
        )

    def test_unknown_model_limit(self, monkeypatch):
        self._patch_registry(monkeypatch, model=None)
        agent = make_agent()
        limit, occupied, remaining, percent = UtilityCommands._context_usage(agent)
        assert limit is None
        assert occupied == 0
        assert remaining is None
        assert percent is None

    def test_known_limit_remaining_and_percent(self, monkeypatch):
        self._patch_registry(monkeypatch, model=_StubModel(max_context_token=200_000))
        agent = make_agent()
        agent.token_usage = TokenUsage(total_input_tokens=1000)
        limit, occupied, remaining, percent = UtilityCommands._context_usage(agent)
        assert limit == 200_000
        assert occupied == 1000
        assert remaining == 199_000
        assert percent == pytest.approx(99.5)

    def test_remaining_clamped_to_zero(self, monkeypatch):
        self._patch_registry(monkeypatch, model=_StubModel(max_context_token=1000))
        agent = make_agent()
        agent.token_usage = TokenUsage(total_input_tokens=5000)
        limit, occupied, remaining, percent = UtilityCommands._context_usage(agent)
        assert limit == 1000
        assert occupied == 5000
        assert remaining == 0
        assert percent == 0.0


class _FakeProcessAgent:
    """Minimal agent for run_agent_loop without an LLM."""

    def __init__(self):
        self.history = []
        self.tool_uses = []

    def _extract_last_user_message_for_memory(self, history):
        return ""

    def store_memory_if_available(self, *args, **kwargs):
        pass

    def format_message(self, message_type, message_data):
        return {"role": "assistant", "content": message_data.get("message", "")}

    def validate_tool_use(self, tool_use):
        return None

    async def process_messages(self, messages=None, callback=None):
        callback(
            [],
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100),
        )
        callback(
            [],
            TokenUsage(input_tokens=200, output_tokens=80, total_input_tokens=200),
        )
        yield ("delegated response", "chunk", None)


class _FakeToolAgent(_FakeProcessAgent):
    """Agent that first issues a tool call, then produces the final response."""

    def __init__(self):
        super().__init__()
        self.executed = False

    async def execute_tool_call(self, tool_use):
        self.executed = True
        return "tool ok"

    async def process_messages(self, messages=None, callback=None):
        if not self.executed:
            callback(
                [{"id": "t1", "name": "run_command", "input": {"cmd": "echo hi"}}],
                TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100),
            )
            yield ("", "chunk", None)
        else:
            callback(
                [],
                TokenUsage(input_tokens=200, output_tokens=80, total_input_tokens=200),
            )
            yield ("final answer", "chunk", None)


class TestRunAgentLoopCallback:
    @pytest.mark.asyncio
    async def test_callback_receives_each_raw_request(self):
        target = make_agent("target", llm=_FakeLLM())
        collected = []

        def on_usage(usage):
            collected.append(usage)
            target.record_conversation_usage(usage)

        response, _ = await run_agent_loop(
            _FakeProcessAgent(), [], request_usage_callback=on_usage
        )
        assert response == "delegated response"
        assert len(collected) == 2
        assert target.conversation_usage.input_tokens == 300
        assert target.conversation_usage.output_tokens == 130
        assert target.conversation_usage.total_tokens == 430

    @pytest.mark.asyncio
    async def test_bound_record_method_works_as_callback(self):
        target = make_agent("target", llm=_FakeLLM())
        response, _ = await run_agent_loop(
            _FakeProcessAgent(),
            [],
            request_usage_callback=target.record_conversation_usage,
        )
        assert response == "delegated response"
        assert target.conversation_usage.total_tokens == 430

    @pytest.mark.asyncio
    async def test_callback_covers_recursive_loop_requests(self):
        target = make_agent("target", llm=_FakeLLM())
        response, usage = await run_agent_loop(
            _FakeToolAgent(),
            [],
            request_usage_callback=target.record_conversation_usage,
        )
        assert response == "final answer"
        assert usage.total_input_tokens == 300
        assert target.conversation_usage.input_tokens == 300
        assert target.conversation_usage.output_tokens == 130
        assert target.conversation_usage.total_tokens == 430

    @pytest.mark.asyncio
    async def test_default_behavior_without_callback(self):
        response, usage = await run_agent_loop(_FakeProcessAgent(), [])
        assert response == "delegated response"
        assert usage.total_input_tokens == 300


# ─────────────────────────────────────────────
#  Context bar rendering
# ─────────────────────────────────────────────


class TestFormatContextBar:
    def test_matches_user_example_at_38_percent(self):
        assert (
            UtilityCommands._format_context_bar(38.0)
            == "[████████░░░░░░░░░░░░] 38% left"
        )

    def test_half_full_bar(self):
        assert (
            UtilityCommands._format_context_bar(50.0)
            == "[██████████░░░░░░░░░░] 50% left"
        )

    def test_full_and_empty_bars(self):
        assert (
            UtilityCommands._format_context_bar(100.0)
            == "[████████████████████] 100% left"
        )
        assert (
            UtilityCommands._format_context_bar(0.0) == "[░░░░░░░░░░░░░░░░░░░░] 0% left"
        )

    def test_bar_length_is_always_20_cells(self):
        for percent in (0.0, 12.5, 38.0, 50.0, 87.3, 100.0):
            bar = UtilityCommands._format_context_bar(percent)
            assert len(bar.split()[0].strip("[]")) == 20


# ─────────────────────────────────────────────
#  /stats formatting
# ─────────────────────────────────────────────


class TestFormatStatsMessage:
    def test_two_agents_with_current_marker_and_aggregate(self, monkeypatch):
        from AgentCrew.modules.llm import model_registry

        monkeypatch.setattr(
            model_registry.ModelRegistry,
            "get_instance",
            staticmethod(lambda: _StubRegistry(_StubModel(max_context_token=200_000))),
        )
        coder = make_agent("coder", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        writer = make_agent("writer", llm=_FakeLLM())
        writer.record_conversation_usage(
            TokenUsage(
                input_tokens=200,
                output_tokens=100,
                cached_tokens=50,
                total_input_tokens=250,
            )
        )
        # Context occupancy reflects each agent's current prompt input usage
        # (agent.token_usage), not the cumulative conversation tracker.
        coder.token_usage = TokenUsage(total_input_tokens=100000)
        writer.token_usage = TokenUsage(total_input_tokens=124000)
        message = UtilityCommands._format_stats_message(
            {"coder": coder, "writer": writer}, "coder"
        )
        assert "* coder (current)" in message
        assert "  writer" in message
        assert "100 in | 50 out | 0 cached" in message
        assert "200 in | 100 out | 50 cached" in message
        assert "150 total" in message
        assert "350 total" in message
        assert (
            "[██████████░░░░░░░░░░] 50% left (200,000 limit | 100,000 occupied)"
            in message
        )
        assert (
            "[████████░░░░░░░░░░░░] 38% left (200,000 limit | 124,000 occupied)"
            in message
        )
        assert "Current conversation total:" in message
        assert "300 in | 150 out | 50 cached | 0 cache-write | 500 total" in message

    def test_zero_usage_agents_are_omitted(self, monkeypatch):
        from AgentCrew.modules.llm import model_registry

        monkeypatch.setattr(
            model_registry.ModelRegistry,
            "get_instance",
            staticmethod(lambda: _StubRegistry(None)),
        )
        agent = make_agent("coder", llm=_FakeLLM())
        message = UtilityCommands._format_stats_message({"coder": agent}, "coder")
        assert "coder" not in message
        assert "0 in | 0 out | 0 cached | 0 cache-write | 0 total" in message
        assert "Cost: $0.0000" in message

    def test_only_involved_agents_are_listed(self):
        coder = make_agent("coder", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        writer = make_agent("writer", llm=_FakeLLM())
        message = UtilityCommands._format_stats_message(
            {"coder": coder, "writer": writer}, "coder"
        )
        assert "coder" in message
        assert "writer" not in message
        assert "Current conversation total:" in message

    def test_remote_agent_is_omitted(self):
        remote = MagicMock()
        remote.name = "remote1"
        coder = make_agent("coder")
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        message = UtilityCommands._format_stats_message(
            {"coder": coder, "remote1": remote}, "coder"
        )
        assert "coder" in message
        assert "remote1" not in message
        assert "Current conversation total:" in message

    def test_unknown_limit_renders_unknown_text(self, monkeypatch):
        from AgentCrew.modules.llm import model_registry

        monkeypatch.setattr(
            model_registry.ModelRegistry,
            "get_instance",
            staticmethod(lambda: _StubRegistry(None)),
        )
        agent = make_agent("coder", llm=_FakeLLM())
        agent.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        block = UtilityCommands._format_agent_stats(agent, is_current=True)
        assert "Context: unknown limit | occupied: 0" in block

    def test_empty_agents_renders_zero_totals(self):
        message = UtilityCommands._format_stats_message({}, "coder")
        assert "Current conversation total:" in message
        assert "0 in | 0 out | 0 cached | 0 cache-write | 0 total" in message

    def test_handle_stats_emits_system_message(self):
        coder = make_agent("coder", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        handler = MagicMock()
        handler.bus = MagicMock()
        handler.agent_manager = MagicMock()
        handler.agent_manager.agents = {"coder": coder}
        handler.agent = MagicMock()
        handler.agent.name = "coder"
        commands = UtilityCommands(handler)
        result = commands.handle_stats("/stats")
        assert result.handled
        handler.bus.emit_sync.assert_called_once()
        event, kwargs = handler.bus.emit_sync.call_args
        assert event[0] == AppEvents.SYSTEM_MESSAGE
        assert "Current conversation total" in kwargs["message"]
        assert "coder" in kwargs["message"]


# ─────────────────────────────────────────────
#  Command routing
# ─────────────────────────────────────────────


class TestCommandProcessorRouting:
    def _processor(self):
        handler = MagicMock()
        handler.bus = AsyncMock()
        return CommandProcessor(handler)

    @pytest.mark.asyncio
    async def test_routes_stats(self):
        processor = self._processor()
        processor.utility_commands.handle_stats = MagicMock(
            return_value=CommandResult(handled=True, clear_flag=True)
        )
        result = await processor.process_command("/stats")
        assert result.handled
        processor.utility_commands.handle_stats.assert_called_once_with("/stats")

    @pytest.mark.asyncio
    async def test_stats_is_case_insensitive(self):
        processor = self._processor()
        processor.utility_commands.handle_stats = MagicMock(
            return_value=CommandResult(handled=True, clear_flag=True)
        )
        result = await processor.process_command("/STATS")
        assert result.handled

    @pytest.mark.asyncio
    async def test_usage_still_routes_to_usage_handler(self):
        processor = self._processor()
        processor.utility_commands.handle_usage = AsyncMock(
            return_value=CommandResult(handled=True, clear_flag=True)
        )
        processor.utility_commands.handle_stats = MagicMock()
        result = await processor.process_command("/usage")
        assert result.handled
        processor.utility_commands.handle_usage.assert_awaited_once_with("/usage")
        processor.utility_commands.handle_stats.assert_not_called()


# ─────────────────────────────────────────────
#  Completion and help exposure
# ─────────────────────────────────────────────


class TestCompletionAndHelp:
    def test_command_completions_include_stats(self):
        completer = ChatCompleter()
        document = SimpleNamespace(text="/st")
        completions = list(completer.get_command_completions(document))
        assert any(c.text == "/stats" for c in completions)

    def test_help_messages_include_stats(self):
        assert any("/stats" in entry for entry in COMMAND_HELP_MESSAGES)


# ─────────────────────────────────────────────
#  Reset on new / loaded conversation
# ─────────────────────────────────────────────


class TestConversationReset:
    def test_start_new_conversation_resets_all_local_agents(self):
        coder = make_agent("coder", llm=_FakeLLM())
        writer = make_agent("writer", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        writer.record_conversation_usage(
            TokenUsage(input_tokens=200, output_tokens=100, total_input_tokens=200)
        )
        handler = MagicMock()
        handler.persistent_service = MagicMock()
        handler.memory_service = None
        handler._queued_attached_files = []
        handler.current_conversation_id = None
        handler.agent_manager = MagicMock()
        handler.agent_manager.agents = {"coder": coder, "writer": writer}
        handler.streamline_messages = []
        handler.conversation_turns = []
        handler.last_assisstant_response_idx = 0
        handler.current_user_input = None
        handler.current_user_input_idx = -1
        handler.agent = coder
        handler.bus = MagicMock()
        ConversationManager(handler).start_new_conversation()
        assert coder.conversation_usage == ConversationUsage()
        assert writer.conversation_usage == ConversationUsage()

    def test_load_conversation_restarts_tracking(self):
        coder = make_agent("coder", llm=_FakeLLM())
        coder.record_conversation_usage(
            TokenUsage(input_tokens=100, output_tokens=50, total_input_tokens=100)
        )
        handler = MagicMock()
        handler.memory_service = None
        handler.current_conversation_id = None
        handler.agent_manager = MagicMock()
        handler.agent_manager.agents = {"coder": coder}
        handler.agent_manager.rebuild_agents_messages = MagicMock()
        handler.streamline_messages = []
        handler.conversation_turns = []
        handler.last_assisstant_response_idx = 0
        handler.agent = coder
        handler.bus = MagicMock()
        ConversationManager(handler)._load_conversation_after_agent(
            history=[], metadata={}, conversation_id="conv-2", last_agent_name="coder"
        )
        assert coder.conversation_usage == ConversationUsage()
