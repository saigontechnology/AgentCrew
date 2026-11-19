"""Offline invariants for the built-in provider catalog (2026-11-19 refresh)."""

import ast
import importlib
from pathlib import Path

import pytest

from AgentCrew.modules.llm.model_registry import PROVIDER_MODEL_MODULES, ModelRegistry
from AgentCrew.modules.llm.types import Model

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = {
    provider: getattr(importlib.import_module(module), attribute)
    for provider, (module, attribute) in PROVIDER_MODEL_MODULES.items()
}
DEFAULTS = {
    "claude": "claude-sonnet-5-5",
    "openai": "gpt-6.2-sol",
    "openai_codex": "gpt-6.1-sol",
    "xai": "grok-4.7",
    "google": "gemini-3.6-flash",
    "together": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "fireworks": "accounts/fireworks/models/deepseek-v4p1-flash-1017",
    "opencode_go": "kimi-k2.5",
    "refuseless": "glm-5.3-abliterated",
    "github_copilot": "gpt-6.1-sol",
}
RETIRED_DEFAULTS = {
    "together": "deepseek-ai/DeepSeek-V4-Flash-0731",
    "fireworks": "accounts/fireworks/models/deepseek-v4-pro",
    "openai_codex": "gpt-5.4",
    "github_copilot": "raptor-mini-tertiary",
}
SERVICE_FILES = {
    "claude": "AgentCrew/modules/anthropic/service.py",
    "github_copilot": "AgentCrew/modules/custom_llm/copilot_response_service.py",
    "together": "AgentCrew/modules/together/service.py",
    "fireworks": "AgentCrew/modules/custom_llm/fireworks_service.py",
    "openai_codex": "AgentCrew/modules/openai_codex/service.py",
}


def catalog_model(provider, model_id):
    return next(m for m in CATALOGS[provider] if m.id == model_id)


def test_no_duplicate_qualified_ids_and_correct_provider_identity():
    models = [(provider, m) for provider, models in CATALOGS.items() for m in models]
    qualified_ids = [f"{m.provider}/{m.id}" for _, m in models]
    assert len(qualified_ids) == len(set(qualified_ids))
    assert all(m.provider == provider for provider, m in models)


@pytest.mark.parametrize("provider,models", CATALOGS.items())
def test_explicit_defaults(provider, models):
    defaults = [m.id for m in models if m.default]
    assert defaults == ([DEFAULTS[provider]] if provider in DEFAULTS else [])


@pytest.mark.parametrize("provider,model_id", RETIRED_DEFAULTS.items())
def test_retired_defaults_are_absent(provider, model_id):
    assert model_id not in {m.id for m in CATALOGS[provider]}


@pytest.mark.parametrize("provider,path", SERVICE_FILES.items())
def test_service_fallback_matches_catalog_default(provider, path):
    tree = ast.parse((ROOT / path).read_text())
    assignments = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "model"
            for target in node.targets
        )
    ]
    assert assignments == [DEFAULTS[provider]]


def test_all_service_families_are_registered():
    tree = ast.parse((ROOT / "AgentCrew/modules/llm/service_manager.py").read_text())
    factories = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Attribute)
        and node.target.attr == "service_factories"
    )
    known_services = {ast.literal_eval(key) for key in factories.keys}
    assert all(
        m.resolved_service_name() in known_services
        for models in CATALOGS.values()
        for m in models
    )


@pytest.mark.parametrize(
    "provider", ["github_copilot", "opencode_go", "openai_codex", "xai"]
)
def test_subscription_models_remain_zero_price(provider):
    assert all(
        m.input_token_price_1m
        == m.output_token_price_1m
        == m.cached_token_price_1m
        == 0
        for m in CATALOGS[provider]
    )


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(ModelRegistry, "_initialize_models", lambda self: None)
    monkeypatch.setattr(ModelRegistry, "_instance", None)
    return ModelRegistry.get_instance()


@pytest.mark.parametrize("provider,model_id", DEFAULTS.items())
def test_registry_can_lazily_select_catalog_default(registry, provider, model_id):
    qualified_id = f"{provider}/{model_id}"
    registry.get_models_by_provider(provider)
    assert registry.set_current_model(qualified_id)
    assert registry.get_current_model().id == model_id
    assert registry.get_current_model().default


def test_custom_and_raw_model_fallbacks_are_preserved(registry):
    custom = Model(
        id="raw/private-model",
        provider="my-provider",
        name="Private model",
        description="User-defined model",
        capabilities=["tool_use", "stream"],
        service_name="my-provider",
    )
    registry.register_model(custom)
    assert registry.set_current_model("my-provider/raw/private-model")
    assert registry.get_current_model() is custom
    assert ModelRegistry.get_model_capabilities("unknown/raw-model") == [
        "tool_use",
        "stream",
    ]
    assert ModelRegistry.get_model_limit("unknown/raw-model") == 128_000
    assert ModelRegistry.get_model_sample_params("unknown/raw-model") is None


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("gemini-3.8-flash", (1.5, 7.5, 0.15, 256_000)),
        ("gemini-4-carbon", (4.0, 20.0, 0.2, 128_000)),
        ("gemini-4-argon", (4.0, 20.0, 0.2, 128_000)),
        ("gemini-4-hydrogen", (1.5, 7.5, 0.075, 256_000)),
    ],
)
def test_google_base_rates_exclude_dated_launch_discounts(model_id, expected):
    model = catalog_model("google", model_id)
    assert (
        model.input_token_price_1m,
        model.output_token_price_1m,
        model.cached_token_price_1m,
        model.max_context_token,
    ) == expected


def test_haiku_uses_long_prompt_tier_and_effective_context_not_provider_maximum():
    model = catalog_model("claude", "claude-haiku-5-5")
    assert (model.input_token_price_1m, model.output_token_price_1m) == (0.5, 2.5)
    assert model.cached_token_price_1m == 0.05
    assert model.max_context_token == 400_000


@pytest.mark.parametrize(
    "model_id", ["gpt-6.2-astra", "gpt-6.2-sol", "gpt-6.1-terra", "gpt-6-luna"]
)
def test_new_openai_models_use_responses_service(model_id):
    model = catalog_model("openai", model_id)
    assert model.resolved_service_name() == "openai_response"
    assert model.default_reasoning == "medium"


def test_deprecated_but_not_shutdown_openai_models_remain_identifiable():
    for model_id in ("gpt-5", "gpt-5.1"):
        model = catalog_model("openai", model_id)
        assert "Deprecated" in model.description
        assert not model.default
    assert not {
        "gpt-5-codex",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "gpt-5.1-codex-max",
    } & {m.id for m in CATALOGS["openai"]}


NEW_MODELS = [
    ("claude", "claude-fable-5-2"),
    ("claude", "claude-haiku-5-5"),
    ("claude", "claude-opus-5-5"),
    ("claude", "claude-sonnet-5-5"),
    ("openai", "gpt-6-luna"),
    ("openai", "gpt-6.1-terra"),
    ("openai", "gpt-6.2-astra"),
    ("openai", "gpt-6.2-sol"),
    ("google", "gemini-3.8-flash"),
    ("google", "gemini-4-argon"),
    ("google", "gemini-4-carbon"),
    ("google", "gemini-4-hydrogen"),
    ("deepinfra", "Qwen/Qwen4-Max"),
    ("deepinfra", "moonshotai/Kimi-K3.1-Pro"),
    ("deepinfra", "nvidia/NVIDIA-Nemotron-3.5-Super-VL"),
    ("together", "MiniMaxAI/MiniMax-M3.1-Flash"),
    ("together", "Qwen/Qwen4-Max"),
    ("together", "moonshotai/Kimi-K3.1-Pro"),
    ("together", "nvidia/NVIDIA-Nemotron-3.5-Super-VL"),
    ("together", "zai-org/GLM-5.3"),
    ("together", "zai-org/GLM-5.3-Flash"),
    ("opencode_go", "deepseek-v4.1-flash"),
    ("opencode_go", "hy4"),
    ("opencode_go", "minimax-m3.1-flash"),
    ("opencode_go", "step-5-preview"),
    ("fireworks", "accounts/fireworks/models/ember-1"),
    ("fireworks", "accounts/fireworks/models/kimi-k3p1-pro"),
    ("fireworks", "accounts/fireworks/models/minimax-m3p1-flash"),
    ("commandcode", "Qwen/Qwen4-Max"),
    ("commandcode", "claude-fable-5-2"),
    ("commandcode", "inclusionai/ling-3.1-flash-1019"),
    ("commandcode", "minimax/minimax-m3.1-flash"),
    ("commandcode", "mistral/mistral-large-4"),
    ("commandcode", "moonshotai/Kimi-K3.1-Pro"),
    ("commandcode", "nvidia/nemotron-3.5-super-vl"),
    ("commandcode", "tencent/hy4"),
]

REMOVED_MODELS = [
    ("claude", "claude-opus-4-1"),
    ("claude", "claude-sonnet-4-5"),
    ("openai", "gpt-5-codex"),
    ("openai", "gpt-5.1-codex"),
    ("openai", "gpt-5.1-codex-max"),
    ("openai", "gpt-5.1-codex-mini"),
    ("openai_codex", "gpt-5.3-codex-spark"),
    ("openai_codex", "gpt-5.4"),
    ("openai_codex", "gpt-5.4-mini"),
    ("openai_codex", "gpt-5.5"),
    ("google", "gemini-pro-latest"),
    ("together", "MiniMaxAI/MiniMax-M3"),
    ("together", "Qwen/Qwen3.8-2.4T-A95B"),
    ("together", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ("together", "deepseek-ai/DeepSeek-V4-Pro"),
    ("together", "google/gemma-4-31B-it"),
    ("together", "moonshotai/Kimi-K2.6"),
    ("together", "moonshotai/Kimi-K2.7-Code"),
    ("together", "nvidia/nemotron-3-ultra-550b-a55b"),
    ("together", "thinkingmachines/Inkling"),
    ("fireworks", "accounts/fireworks/models/deepseek-v4-flash-0731"),
    ("fireworks", "accounts/fireworks/models/deepseek-v4-flash-vision-exp"),
    ("fireworks", "accounts/fireworks/models/deepseek-v4-pro"),
    ("fireworks", "accounts/fireworks/models/deepseek-v4-pro-0813"),
    ("fireworks", "accounts/fireworks/models/glm-5p2"),
    ("fireworks", "accounts/fireworks/models/kimi-k2p6"),
    ("fireworks", "accounts/fireworks/models/kimi-k2p7-code"),
    ("fireworks", "accounts/fireworks/models/minimax-m2p7"),
    ("fireworks", "accounts/fireworks/models/muse-glimmer-30b"),
    ("fireworks", "accounts/fireworks/models/nemotron-3-ultra-nvfp4"),
    ("fireworks", "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b"),
    ("github_copilot", "claude-opus-5"),
    ("github_copilot", "gemini-2.5-pro"),
    ("github_copilot", "gemini-3-flash"),
    ("github_copilot", "gemini-3.1-pro"),
    ("github_copilot", "gemini-3.5-flash"),
    ("github_copilot", "gpt-5.6-terra"),
    ("github_copilot", "kimi-k2.7-code"),
    ("github_copilot", "mai-code-1-flash"),
    ("github_copilot", "raptor-mini-tertiary"),
]


@pytest.mark.parametrize("provider,model_id", NEW_MODELS)
def test_new_models_register_and_select_without_provider_initialization(
    registry, provider, model_id
):
    registry.get_models_by_provider(provider)
    assert registry.set_current_model(f"{provider}/{model_id}")
    assert registry.get_current_model().provider == provider


@pytest.mark.parametrize("provider,model_id", REMOVED_MODELS)
def test_confirmed_lifecycle_removals(provider, model_id):
    assert model_id not in {m.id for m in CATALOGS[provider]}


@pytest.mark.parametrize(
    "provider",
    ["claude", "openai", "google", "deepinfra", "together", "fireworks", "commandcode"],
)
def test_paid_catalog_effective_context_caps(provider):
    for model in CATALOGS[provider]:
        price = model.input_token_price_1m
        if price == 0:
            continue
        cap = 400_000 if price <= 0.5 else 256_000 if price < 3 else 128_000
        assert model.max_context_token <= cap, model.id


def test_gpt_56_non_promotional_prices_and_sol_base_estimate():
    for model_id, expected in {
        "gpt-5.6-terra": (2.0, 12.0, 0.2),
        "gpt-5.6-luna": (0.2, 1.2, 0.02),
    }.items():
        model = catalog_model("openai", model_id)
        assert (
            model.input_token_price_1m,
            model.output_token_price_1m,
            model.cached_token_price_1m,
        ) == expected
    sol = catalog_model("openai", "gpt-5.6-sol")
    assert (sol.input_token_price_1m, sol.output_token_price_1m) == (5.0, 30.0)
    assert "2026-11-30" in sol.description
