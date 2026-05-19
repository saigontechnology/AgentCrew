from AgentCrew.modules.llm.types import Model

# LiteLLM supports 100+ providers with thousands of models.
# Users configure models via the custom_llm_providers config file
# or environment. No hardcoded defaults — the provider string
# (e.g. "openai/gpt-4o", "anthropic/claude-sonnet-4-6") is the model ID.
LITELLM_MODELS: list[Model] = []
