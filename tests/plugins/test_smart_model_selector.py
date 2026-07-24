"""Behavioral tests for smart_model_selector v5 (per-provider policy selection).

Run: uv run pytest tests/plugins/test_smart_model_selector.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", ".agentcrew", "plugins")
)

import pytest
from smart_model_selector import (
    _COMPLEXITY_PROMPT,
    _PROVIDER_COMPLEXITY_MAP,
    smart_model_selector,
)

from AgentCrew.modules.llm.types import Model

# ══════════════════════════════════════════════════════════════
#  Fixtures & mocks
# ══════════════════════════════════════════════════════════════


@pytest.fixture
def plugin():
    return smart_model_selector()


@pytest.fixture(autouse=True)
def reset_singletons():
    from AgentCrew.modules.events.event_bus import EventBus
    from AgentCrew.modules.events.hooks import HookRegistry

    HookRegistry.reset_instance()
    EventBus.reset_instance()
    yield


class FakeClassifier:
    def __init__(self, resp: str | None = None, raises: bool = False):
        self.resp = resp
        self.raises = raises
        self.closed = False
        self.model = "fake"

    async def process_message(self, **kw) -> str | None:
        if self.raises:
            raise RuntimeError("fail")
        if self.resp is None:
            return None
        if self.resp == "":
            return ""
        return self.resp

    async def close(self):
        self.closed = True


def _patch_registry(plugin, model_list, classifier_fake=None):
    from AgentCrew.modules.llm.model_registry import ModelRegistry

    mock_registry = MagicMock(spec=ModelRegistry)
    mock_registry.get_models_by_provider.side_effect = lambda prov: [
        m for m in model_list if m.provider == prov
    ]
    mock_registry.get_model.side_effect = lambda full_id: next(
        (m for m in model_list if f"{m.provider}/{m.id}" == full_id), None
    )
    orig = ModelRegistry._instance
    ModelRegistry._instance = mock_registry
    if classifier_fake is not None:
        plugin._create_classifier_service = AsyncMock(return_value=classifier_fake)
    return mock_registry, orig


# ══════════════════════════════════════════════════════════════
#  Test models
# ══════════════════════════════════════════════════════════════

CLAUDE_HAIKU = Model(
    id="claude-haiku-4-5",
    provider="claude",
    name="Haiku",
    description="",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    input_token_price_1m=0.8,
    output_token_price_1m=4.0,
)
CLAUDE_SONNET = Model(
    id="claude-sonnet-4-6",
    provider="claude",
    name="Sonnet",
    description="",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    input_token_price_1m=3.0,
    output_token_price_1m=15.0,
)
CLAUDE_OPUS = Model(
    id="claude-opus-4-7",
    provider="claude",
    name="Opus",
    description="",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    max_context_token=1_000_000,
    input_token_price_1m=5.0,
    output_token_price_1m=25.0,
)
OPENAI_CHEAP = Model(
    id="gpt-5",
    provider="openai",
    name="GPT-5",
    description="",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    input_token_price_1m=1.25,
    output_token_price_1m=10.0,
)
CLAUDE_RESP = Model(
    id="claude-response-model",
    provider="claude",
    name="Claude Resp",
    description="",
    service_name="claude_response",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    input_token_price_1m=1.0,
    output_token_price_1m=5.0,
)
NO_VISION = Model(
    id="no-vision",
    provider="test",
    name="No-Vision",
    description="",
    capabilities=["tool_use", "stream", "thinking"],
    input_token_price_1m=0.5,
    output_token_price_1m=2.0,
)
VISION_OK = Model(
    id="has-vision",
    provider="test",
    name="Has-Vision",
    description="",
    capabilities=["tool_use", "stream", "vision", "thinking"],
    input_token_price_1m=2.0,
    output_token_price_1m=8.0,
)


# ══════════════════════════════════════════════════════════════
#  1. Per-provider policy selection
# ══════════════════════════════════════════════════════════════


class TestPolicySelection:
    def test_claude_level1_prefers_haiku(self, plugin):
        """Claude level 1 should prefer haiku."""
        fam = {"claude-haiku-4-5": CLAUDE_HAIKU, "claude-sonnet-4-6": CLAUDE_SONNET}
        result = plugin._select_by_policy("claude", 1, fam, "hi")
        assert result is not None
        assert result.id == "claude-haiku-4-5"

    def test_claude_level2_prefers_sonnet(self, plugin):
        fam = {"claude-haiku-4-5": CLAUDE_HAIKU, "claude-sonnet-4-6": CLAUDE_SONNET}
        result = plugin._select_by_policy("claude", 2, fam, "hi")
        assert result is not None
        assert result.id == "claude-sonnet-4-6"

    def test_claude_level3_prefers_sonnet5(self, plugin):
        fam = {"claude-sonnet-4-6": CLAUDE_SONNET, "claude-opus-4-7": CLAUDE_OPUS}
        result = plugin._select_by_policy("claude", 3, fam, "hi")
        # sonnet-5 not in fam, so falls to sonnet-4-6
        assert result is not None
        assert result.id == "claude-sonnet-4-6"

    def test_claude_level4_prefers_opus(self, plugin):
        fam = {
            "claude-haiku-4-5": CLAUDE_HAIKU,
            "claude-sonnet-4-6": CLAUDE_SONNET,
            "claude-opus-4-7": CLAUDE_OPUS,
        }
        result = plugin._select_by_policy("claude", 4, fam, "hi")
        assert result is not None
        assert result.id == "claude-opus-4-7"

    def test_first_available_chosen(self, plugin):
        """When first preferred is absent, second is chosen."""
        fam = {"claude-sonnet-4-6": CLAUDE_SONNET}
        result = plugin._select_by_policy("claude", 2, fam, "hi")
        assert result is not None
        assert result.id == "claude-sonnet-4-6"

    def test_none_when_no_preferred_available(self, plugin):
        """When no preferred model is in same_family, preserve current."""
        fam = {"claude-opus-4-1": CLAUDE_OPUS}  # not in any policy list
        result = plugin._select_by_policy("claude", 1, fam, "hi")
        assert result is None

    def test_unknown_provider_returns_none(self, plugin):
        """Missing provider mapping → preserve current."""
        fam = {}
        result = plugin._select_by_policy("nonexistent", 1, fam, "hi")
        assert result is None

    def test_missing_level_returns_none(self, plugin):
        """Provider has mapping but missing level → preserve current."""
        fam = {"claude-haiku-4-5": CLAUDE_HAIKU}
        result = plugin._select_by_policy("claude", 99, fam, "hi")
        assert result is None

    def test_vision_required_rejects_non_vision(self, plugin):
        """Non-vision haiku in level-1 policy skipped when [IMAGE] present."""
        no_vision = Model(
            id="claude-haiku-4-5",
            provider="claude",
            name="Haiku (no vision)",
            description="",
            capabilities=["tool_use", "stream", "thinking"],
            input_token_price_1m=0.8,
            output_token_price_1m=4.0,
        )
        fam = {"claude-haiku-4-5": no_vision}
        # Level-1 policy: only ["claude-haiku-4-5"] — no fallback with vision
        result = plugin._select_by_policy("claude", 1, fam, "describe [IMAGE]")
        assert result is None

    def test_no_tool_use_skipped(self, plugin):
        """Model without tool_use in policy list is skipped."""
        no_tools = Model(
            id="claude-haiku-4-5",
            provider="claude",
            name="Haiku (no tools)",
            description="",
            capabilities=["stream", "vision", "thinking"],
            input_token_price_1m=0.8,
            output_token_price_1m=4.0,
        )
        fam = {"claude-haiku-4-5": no_tools}
        # Level-1 policy: only ["claude-haiku-4-5"] — no tool_use → skip → None
        result = plugin._select_by_policy("claude", 1, fam, "hi")
        assert result is None

    def test_preferred_model_selected(self, plugin):
        """Model at top of openai level-2 list is selected."""
        gpt51 = Model(
            id="gpt-5.1",
            provider="openai",
            name="GPT-5.1",
            description="",
            capabilities=["tool_use", "stream", "vision", "thinking"],
            input_token_price_1m=1.25,
            output_token_price_1m=10.0,
        )
        fam = {"gpt-5.1": gpt51}
        result = plugin._select_by_policy("openai", 2, fam, "hi")
        assert result is not None
        assert result.id == "gpt-5.1"

    def test_wrong_service_family_not_in_fam(self, plugin):
        """Different service-family model not in same_family → skipped."""
        fam = {"claude-response-model": CLAUDE_RESP}
        # CLAUDE_RESP has service_name="claude_response", so it's NOT in fam
        # when current is regular claude family
        result = plugin._select_by_policy("claude", 1, fam, "hi")
        assert result is None  # haiku not in fam


# ══════════════════════════════════════════════════════════════
#  2. Mapping completeness
# ══════════════════════════════════════════════════════════════


class TestMappingCompleteness:
    def test_all_providers_have_all_levels(self):
        """Every registered provider must have entries for levels 1-4."""
        for provider, levels in _PROVIDER_COMPLEXITY_MAP.items():
            for level in range(1, 5):
                assert level in levels, f"{provider} missing level {level}"
                assert len(levels[level]) > 0, (
                    f"{provider} level {level} has empty list"
                )

    def test_known_providers_covered(self):
        """Expected provider keys exist."""
        expected = {
            "claude",
            "openai",
            "openai_codex",
            "google",
            "deepinfra",
            "crofai",
            "fireworks",
            "github_copilot",
            "commandcode",
            "opencode_go",
            "together",
        }
        actual = set(_PROVIDER_COMPLEXITY_MAP.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing providers: {missing}"
        assert not extra, f"Unexpected providers: {extra}"


# ══════════════════════════════════════════════════════════════
#  3. End-to-end via _select_best_model (mocked registry)
# ══════════════════════════════════════════════════════════════


class TestSelectBestModelPolicy:
    @pytest.mark.asyncio
    async def test_claude_level1_selects_haiku(self, plugin):
        """End-to-end: claude level 1 → haiku selected (not cheapest global)."""
        fake = FakeClassifier(resp='{"level": 1, "reason": "simple"}')
        reg, orig = _patch_registry(
            plugin, [CLAUDE_HAIKU, CLAUDE_SONNET, CLAUDE_OPUS], fake
        )
        try:
            ctx = {
                "provider": "claude",
                "model_id": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello"}],
            }
            result = await plugin._select_best_model(ctx)
            chosen_id = result.get("model_id") if result else None
            assert chosen_id == "claude-haiku-4-5", f"Expected haiku, got {chosen_id}"
        finally:
            from AgentCrew.modules.llm.model_registry import ModelRegistry

            ModelRegistry._instance = orig

    @pytest.mark.asyncio
    async def test_preserve_current_when_none_qualify(self, plugin):
        """When policy returns None, current model preserved."""
        fake = FakeClassifier(resp='{"level": 1, "reason": "simple"}')
        # Only a model NOT in any policy list
        weird = Model(
            id="claude-opus-4-1",
            provider="claude",
            name="4.1 Opus",
            description="",
            capabilities=["tool_use", "stream"],
            input_token_price_1m=15.0,
            output_token_price_1m=75.0,
        )
        reg, orig = _patch_registry(plugin, [weird], fake)
        try:
            ctx = {
                "provider": "claude",
                "model_id": "claude-opus-4-1",
                "messages": [{"role": "user", "content": "hello"}],
            }
            result = await plugin._select_best_model(ctx)
            assert result is ctx  # unchanged
        finally:
            from AgentCrew.modules.llm.model_registry import ModelRegistry

            ModelRegistry._instance = orig


# ══════════════════════════════════════════════════════════════
#  4. New-message gate
# ══════════════════════════════════════════════════════════════


class TestNewMessageGate:
    @pytest.mark.asyncio
    async def test_normal_user_triggers(self, plugin):
        fake = FakeClassifier(resp='{"level": 1, "reason": "ok"}')
        reg, orig = _patch_registry(plugin, [CLAUDE_HAIKU, CLAUDE_SONNET], fake)
        try:
            ctx = {
                "provider": "claude",
                "model_id": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
            }
            result = await plugin._select_best_model(ctx)
            assert result is not None
        finally:
            from AgentCrew.modules.llm.model_registry import ModelRegistry

            ModelRegistry._instance = orig

    @pytest.mark.asyncio
    async def test_non_user_last_skips(self, plugin):
        ctx = {
            "provider": "claude",
            "model_id": "claude-haiku-4-5",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "tc", "tool_call_id": "abc"},
                {"role": "tool", "content": "result"},
            ],
        }
        result = await plugin._select_best_model(ctx)
        assert result is ctx

    @pytest.mark.asyncio
    async def test_tool_call_id_skips(self, plugin):
        ctx = {
            "provider": "claude",
            "model_id": "claude-haiku-4-5",
            "messages": [
                {"role": "user", "content": "real"},
                {"role": "assistant", "content": "resp"},
                {"role": "user", "content": "tool", "tool_call_id": "abc"},
            ],
        }
        result = await plugin._select_best_model(ctx)
        assert result is ctx

    @pytest.mark.asyncio
    async def test_internal_prefix_skips(self, plugin):
        ctx = {
            "provider": "claude",
            "model_id": "claude-haiku-4-5",
            "messages": [
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "<Post_Transfer_Action_Reminder>task"},
            ],
        }
        result = await plugin._select_best_model(ctx)
        assert result is ctx

    @pytest.mark.asyncio
    async def test_empty_messages_skips(self, plugin):
        ctx = {"provider": "claude", "model_id": "haiku", "messages": []}
        result = await plugin._select_best_model(ctx)
        assert result is ctx


# ══════════════════════════════════════════════════════════════
#  5. Classifier cleanup
# ══════════════════════════════════════════════════════════════


class TestClassifierCleanup:
    @pytest.mark.asyncio
    async def test_close_after_success(self, plugin):
        fake = FakeClassifier(resp='{"level": 1, "reason": "ok"}')
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert fake.closed

    @pytest.mark.asyncio
    async def test_close_after_exception(self, plugin):
        fake = FakeClassifier(raises=True)
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert fake.closed

    @pytest.mark.asyncio
    async def test_close_after_malformed(self, plugin):
        fake = FakeClassifier(resp="garbage")
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert fake.closed


# ══════════════════════════════════════════════════════════════
#  6. Malformed → heuristic
# ══════════════════════════════════════════════════════════════


class TestMalformedToHeuristic:
    @pytest.mark.asyncio
    async def test_none(self, plugin):
        fake = FakeClassifier(resp=None)
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        r = await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert r["level"] == 1

    @pytest.mark.asyncio
    async def test_empty(self, plugin):
        fake = FakeClassifier(resp="")
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        r = await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert r["level"] == 1

    @pytest.mark.asyncio
    async def test_garbage(self, plugin):
        fake = FakeClassifier(resp="not json")
        plugin._create_classifier_service = AsyncMock(return_value=fake)
        r = await plugin._classify_complexity("hi", "", "", [CLAUDE_HAIKU])
        assert r["level"] == 1


# ══════════════════════════════════════════════════════════════
#  7. Vision enforcement
# ══════════════════════════════════════════════════════════════


class TestVisionEnforcement:
    def test_vision_rejects_non_vision(self, plugin):
        """Non-vision model in policy list skipped when [IMAGE] present."""
        non_vision = Model(
            id="claude-haiku-4-5",
            provider="claude",
            name="Haiku (no vision)",
            description="",
            capabilities=["tool_use", "stream", "thinking"],
            input_token_price_1m=0.8,
            output_token_price_1m=4.0,
        )
        fam = {"claude-haiku-4-5": non_vision}
        # Level 1 policy: ["claude-haiku-4-5"] — only entry has no vision → skip → None
        result = plugin._select_by_policy("claude", 1, fam, "describe [IMAGE]")
        assert result is None, (
            "Non-vision model should be rejected when [IMAGE] present"
        )


# ══════════════════════════════════════════════════════════════
#  8. PluginManager lifecycle
# ══════════════════════════════════════════════════════════════


class TestPluginManagerLifecycle:
    @pytest.mark.asyncio
    async def test_discover_load_unload(self):
        from AgentCrew.modules.events.hooks import HookPoints, HookRegistry
        from AgentCrew.modules.events.plugin_system import PluginManager

        mgr = PluginManager(
            project_plugins_dir=".agentcrew/plugins", trusted_project_plugins=True
        )
        metas = mgr.discover()
        assert "smart_model_selector" in [m.name for m in metas]

        plugin = await mgr.load("smart_model_selector")
        assert plugin is not None and plugin.name == "smart_model_selector"

        registry = HookRegistry.get_instance()
        hooks = registry.get_hooks(HookPoints.AGENT_PROCESS)
        owned = [h for h in hooks if h.owner == "smart_model_selector"]
        assert len(owned) == 1

        await mgr.unload("smart_model_selector")
        assert mgr.get("smart_model_selector") is None
        hooks_after = registry.get_hooks(HookPoints.AGENT_PROCESS)
        owned_after = [h for h in hooks_after if h.owner == "smart_model_selector"]
        assert len(owned_after) == 0

    @pytest.mark.asyncio
    async def test_trust_gate_rejects_untrusted(self):
        from AgentCrew.modules.events.plugin_system import PluginManager

        mgr = PluginManager(
            project_plugins_dir=".agentcrew/plugins", trusted_project_plugins=False
        )
        mgr.discover()
        plugin = await mgr.load("smart_model_selector")
        assert plugin is None


# ══════════════════════════════════════════════════════════════
#  9. Heuristic fallback
# ══════════════════════════════════════════════════════════════


class TestHeuristic:
    def test_short(self, plugin):
        assert plugin._heuristic_complexity("hi")["level"] == 1

    def test_medium(self, plugin):
        assert plugin._heuristic_complexity("x " * 1500)["level"] == 2

    def test_long(self, plugin):
        assert plugin._heuristic_complexity("x " * 2500)["level"] == 3

    def test_code(self, plugin):
        assert plugin._heuristic_complexity("```python\ndef f(): pass```")["level"] == 3

    def test_vision_marker(self, plugin):
        assert plugin._heuristic_complexity("Describe [IMAGE]")["level"] == 3


# ══════════════════════════════════════════════════════════════
#  10. Context extraction
# ══════════════════════════════════════════════════════════════


class TestContextExtraction:
    def test_user_text_plain(self, plugin):
        assert (
            plugin._extract_user_text([{"role": "user", "content": "hello"}]) == "hello"
        )

    def test_user_text_image_marker(self, plugin):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        text = plugin._extract_user_text(msgs)
        assert "[IMAGE]" in text and "data:" not in text

    def test_prev_user_basic(self, plugin):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "resp"},
            {"role": "user", "content": "latest"},
        ]
        assert "first" in plugin._extract_prev_user_text(msgs)

    def test_prev_user_skips_tool_call_id(self, plugin):
        msgs = [
            {"role": "user", "content": "real"},
            {"role": "assistant", "content": "resp"},
            {"role": "user", "content": "tool", "tool_call_id": "abc"},
            {"role": "assistant", "content": "proc"},
            {"role": "user", "content": "latest"},
        ]
        prev = plugin._extract_prev_user_text(msgs)
        assert "real" in prev and "tool" not in prev

    def test_prompt_three_parts(self, plugin):
        assert "{user_text}" in _COMPLEXITY_PROMPT
        assert "{prev_user_text}" in _COMPLEXITY_PROMPT
        assert "{assistant_context}" in _COMPLEXITY_PROMPT


# ══════════════════════════════════════════════════════════════
#  11. No memory remnants
# ══════════════════════════════════════════════════════════════


class TestNoMemory:
    def test_no_memory_attrs(self, plugin):
        assert not hasattr(plugin, "_memory_themes")
        assert not hasattr(plugin, "_last_user_message_text")
