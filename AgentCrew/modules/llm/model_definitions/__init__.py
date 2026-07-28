"""Model definitions for all LLM providers.

Lightweight module that only imports Model types — no heavy service imports.
Each provider's models can be loaded independently.
"""

from .anthropic import ANTHROPIC_MODELS
from .commandcode import COMMANDCODE_MODELS
from .crofai import CROFAI_MODELS
from .deepinfra import DEEPINFRA_MODELS
from .fireworks import FIREWORKS_MODELS
from .github_copilot import GITHUB_COPILOT_MODELS
from .google import GOOGLE_MODELS
from .openai import OPENAI_MODELS
from .openai_codex import OPENAI_CODEX_MODELS
from .opencode import OPENCODE_GO_MODELS
from .together import TOGETHER_MODELS

__all__ = [
    "ANTHROPIC_MODELS",
    "COMMANDCODE_MODELS",
    "CROFAI_MODELS",
    "DEEPINFRA_MODELS",
    "FIREWORKS_MODELS",
    "GITHUB_COPILOT_MODELS",
    "GOOGLE_MODELS",
    "OPENAI_CODEX_MODELS",
    "OPENAI_MODELS",
    "OPENCODE_GO_MODELS",
    "TOGETHER_MODELS",
]
