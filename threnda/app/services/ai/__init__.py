"""Optional, localhost-only AI assistance.

The default provider is disabled and performs no I/O. AI output is advisory
data only; callers must keep it in the candidate-review flow.
"""

from threnda.app.services.ai.provider import (
    AICandidate,
    AIProvider,
    AIResult,
    AIUnavailableError,
    DisabledAIProvider,
    OllamaAIProvider,
    build_ai_provider,
)

__all__ = [
    "AICandidate",
    "AIProvider",
    "AIResult",
    "AIUnavailableError",
    "DisabledAIProvider",
    "OllamaAIProvider",
    "build_ai_provider",
]
