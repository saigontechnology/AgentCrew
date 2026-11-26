"""Refuseless OpenAI-compatible chat model definitions.

Prices per million tokens and available IDs follow the supplied provider table.
Effective context limits follow AgentCrew's input-price policy rather than the
provider's 1M maximum. All listed models have a provider output limit of 128,000
tokens; Model does not currently store output limits, speed, or quantization.
Existing capabilities are preserved; blank modalities do not establish vision
support for newly listed models.
"""

from AgentCrew.modules.llm.types import Model

REFUSELESS_MODELS = [
    Model(
        id="glm-5.3-abliterated",
        provider="refuseless",
        name="GLM 5.3 Abliterated",
        description="Refuseless GLM 5.3 for coding and cybersecurity.",
        capabilities=["tool_use", "thinking", "vision", "stream"],
        default=True,
        max_context_token=256_000,
        input_token_price_1m=1.00,
        output_token_price_1m=2.00,
        cached_token_price_1m=0.15,
    ),
    Model(
        id="glm-5.3-flash-abliterated",
        provider="refuseless",
        name="GLM 5.3 Flash Abliterated",
        description="Refuseless GLM 5.3 Flash for cost-effective cyber intelligence.",
        capabilities=["tool_use", "thinking", "vision", "stream"],
        max_context_token=400_000,
        input_token_price_1m=0.12,
        output_token_price_1m=0.50,
        cached_token_price_1m=0.025,
    ),
    Model(
        id="deepseek-v4.1-flash-abliterated",
        provider="refuseless",
        name="DeepSeek V4.1 Flash 1017 Abliterated",
        description="Refuseless DeepSeek V4.1 Flash 1017 for cost-effective cyber intelligence.",
        capabilities=["tool_use", "thinking", "vision", "stream"],
        max_context_token=400_000,
        input_token_price_1m=0.12,
        output_token_price_1m=0.45,
        cached_token_price_1m=0.019,
    ),
    Model(
        id="deepseek-v4.1-flash-abliterated-fast",
        provider="refuseless",
        name="DeepSeek V4.1 Flash 1017 Fast Abliterated",
        description="Refuseless DeepSeek V4.1 Flash 1017 Fast for faster responses.",
        capabilities=["tool_use", "thinking", "stream"],
        max_context_token=400_000,
        input_token_price_1m=0.12,
        output_token_price_1m=0.45,
        cached_token_price_1m=0.025,
    ),
    Model(
        id="kimi-k3.1-pro-abliterated",
        provider="refuseless",
        name="Kimi K3.1 Pro Abliterated",
        description="Refuseless Kimi K3.1 Pro Abliterated.",
        capabilities=["tool_use", "thinking", "stream"],
        max_context_token=256_000,
        input_token_price_1m=1.50,
        output_token_price_1m=3.00,
        cached_token_price_1m=0.15,
    ),
]
