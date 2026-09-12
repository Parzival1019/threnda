"""Application configuration.

Settings are read from environment variables with the ``THRENDA_`` prefix
(see ``.env.example`` for the full list), and optionally from a ``.env``
file. Nothing here has a default that is safe for production use — see
``.env.example`` for the required per-deployment overrides.
"""

from __future__ import annotations

import enum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(enum.StrEnum):
    """Deployment environment, as set via ``THRENDA_ENV``."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class AIProvider(enum.StrEnum):
    """Which local AI backend, if any, Threnda should use.

    AI is always optional. ``DISABLED`` is the default and the only
    provider Threnda ships with today; ``OLLAMA`` is reserved for
    Milestone 6 and is not implemented yet. There is no cloud LLM provider
    and there will not be one — see CLAUDE.md.
    """

    DISABLED = "disabled"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Threnda runtime configuration.

    Field names map to ``THRENDA_<FIELD_NAME>`` environment variables, e.g.
    ``database_url`` <- ``THRENDA_DATABASE_URL``.
    """

    model_config = SettingsConfigDict(
        env_prefix="THRENDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEVELOPMENT
    secret_key: str = "change-me"  # noqa: S105 - not a credential, a placeholder to override
    database_url: str = "sqlite:///./data/threnda.db"
    ai_provider: AIProvider = AIProvider.DISABLED
    ai_model: str = ""
    # Hard cap on request body size, enforced at the ASGI layer (see
    # threnda.app.core.middleware) before any upload is parsed — this is an
    # operational safety limit against resource exhaustion, not a cap on how
    # many findings/assets Threnda can store. 20 MiB comfortably covers a
    # large CSV/SARIF export while staying well short of memory pressure on
    # the 16 GB target machine.
    max_upload_bytes: int = 20 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance.

    Application code should prefer this over constructing ``Settings()``
    directly so the environment is only parsed once. Tests that need a
    distinct configuration (e.g. an isolated database file) should
    construct ``Settings(...)`` explicitly instead of using this cache.
    """
    return Settings()
