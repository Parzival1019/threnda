"""Optional, localhost-only AI assistance.

The default provider manages Threnda's embedded runtime but performs no model
work until explicitly requested. AI output is advisory data only; callers must
keep it in the candidate-review flow.
"""

from threnda.app.services.ai.provider import (
    AICandidate,
    AIProvider,
    AIResult,
    AIUnavailableError,
    DisabledAIProvider,
    EmbeddedAIProvider,
    OllamaAIProvider,
    build_ai_provider,
)

__all__ = [
    "AICandidate",
    "AIProvider",
    "AIResult",
    "AIUnavailableError",
    "DisabledAIProvider",
    "EmbeddedAIProvider",
    "OllamaAIProvider",
    "build_ai_provider",
]
