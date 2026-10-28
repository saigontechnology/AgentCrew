"""Refuseless OpenAI-compatible chat model definitions.

The GLM models and their capabilities/context are documented at
https://docs.refuseless.com/sdks.md. Prices are not published there; zeros
mean unknown, not free. Only documented Refuseless catalog IDs are listed
(https://docs.refuseless.com/models.md).
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
        max_context_token=400_000,
    ),
    Model(
        id="glm-5.3-flash-abliterated",
        provider="refuseless",
        name="GLM 5.3 Flash Abliterated",
        description="Refuseless GLM 5.3 Flash for cost-effective cyber intelligence.",
        capabilities=["tool_use", "thinking", "vision", "stream"],
        max_context_token=400_000,
    ),
    Model(
        id="deepseek-v4.1-flash-abliterated",
        provider="refuseless",
        name="Deepseek v4.1 Flash Abliterated",
        description="Refuseless Deepseek v4.1 for cost-effective cyber intelligence.",
        capabilities=["tool_use", "thinking", "vision", "stream"],
        max_context_token=1_000_000,
    ),
]
