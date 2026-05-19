def _build_available_models():
    """Lazily build the available models list to avoid circular imports.

    Model definitions live in their respective provider modules and are
    imported here on demand to prevent circular dependency issues between
    provider services and the LLM registry.
    """
    from AgentCrew.modules.anthropic.models import ANTHROPIC_MODELS
    from AgentCrew.modules.openai.models import OPENAI_MODELS
    from AgentCrew.modules.openai_codex.models import OPENAI_CODEX_MODELS
    from AgentCrew.modules.google.models import GOOGLE_MODELS
    from AgentCrew.modules.custom_llm.deepinfra_models import DEEPINFRA_MODELS
    from AgentCrew.modules.custom_llm.fireworks_models import FIREWORKS_MODELS
    from AgentCrew.modules.custom_llm.github_copilot_models import GITHUB_COPILOT_MODELS
    from AgentCrew.modules.custom_llm.opencode_models import OPENCODE_GO_MODELS
    from AgentCrew.modules.together.models import TOGETHER_MODELS
    from AgentCrew.modules.litellm.models import LITELLM_MODELS

    return (
        ANTHROPIC_MODELS
        + OPENAI_MODELS
        + OPENAI_CODEX_MODELS
        + GOOGLE_MODELS
        + DEEPINFRA_MODELS
        + TOGETHER_MODELS
        + OPENCODE_GO_MODELS
        + FIREWORKS_MODELS
        + GITHUB_COPILOT_MODELS
        + LITELLM_MODELS
    )


AVAILABLE_MODELS = _build_available_models()
