"""Application configuration.

Settings are read from environment variables with the ``THRENDA_`` prefix
(see ``.env.example`` for the full list), and optionally from a ``.env``
file. Nothing here has a default that is safe for production use — see
``.env.example`` for the required per-deployment overrides.
"""

from __future__ import annotations

import enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(enum.StrEnum):
    """Deployment environment, as set via ``THRENDA_ENV``."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class AIProvider(enum.StrEnum):
    """Which local AI backend, if any, Threnda should use.

    AI is always optional and ``DISABLED`` by default. ``OLLAMA``
    (``threnda.app.services.ai.provider.OllamaAIProvider``) talks only to a
    localhost Ollama instance — see docs/local-ai.md. There is no cloud LLM
    provider and there will not be one — see CLAUDE.md.
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

    # Cookie security. Session/CSRF cookies are always HttpOnly and
    # SameSite=Strict; the ``Secure`` flag is set whenever ``is_production``
    # is true or the current request is HTTPS (see
    # threnda.app.web.cookies.cookie_secure). Set this to explicitly force
    # ``Secure`` off for local development/testing behind plain HTTP even
    # with THRENDA_ENV=production (e.g. exercising a production config
    # locally without TLS) — never set it in a real deployment.
    allow_insecure_cookies: bool = False

    # Outbound webhook delivery (see docs/webhooks.md). Local-only override
    # to allow delivering to loopback/private addresses, which production
    # SSRF protection otherwise blocks unconditionally — needed to point a
    # webhook at a test receiver on localhost during development/tests.
    webhook_allow_private_targets: bool = False
    webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    webhook_max_response_bytes: int = Field(default=65_536, ge=1024, le=1_000_000)

    # First-admin bootstrap (see docs/deployment.md, "Bootstrapping the
    # first admin"). Read once, only when the ``users`` table is empty, by
    # ``threnda.app.cli.bootstrap_admin`` — never by the web application
    # itself. Left blank by default; Community never invents credentials.
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    ai_provider: AIProvider = AIProvider.DISABLED
    ai_model: str = ""
    ai_base_url: str = "http://127.0.0.1:11434"
    ai_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    ai_max_input_characters: int = Field(default=50_000, ge=1000, le=200_000)
    # Hard cap on request body size, enforced at the ASGI layer (see
    # threnda.app.core.middleware) before any upload is parsed — this is an
    # operational safety limit against resource exhaustion, not a cap on how
    # many findings/assets Threnda can store. 20 MiB comfortably covers a
    # large CSV/SARIF export while staying well short of memory pressure on
    # the 16 GB target machine.
    max_upload_bytes: int = 20 * 1024 * 1024

    @field_validator("ai_base_url")
    @classmethod
    def validate_local_ai_url(cls, value: str) -> str:
        """Ollama is deliberately localhost-only; cloud AI is out of scope."""
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("ai_base_url must point to localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ai_base_url must not contain credentials, query, or fragment")
        return value.rstrip("/")

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
