"""Tests for Settings / environment variable handling."""

from __future__ import annotations

import pytest

from threnda.app.core.config import AIProvider, Environment, Settings, get_settings


def test_settings_defaults_match_env_example() -> None:
    """Defaults must match the placeholders documented in .env.example."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env is Environment.DEVELOPMENT
    assert settings.database_url == "sqlite:///./data/threnda.db"
    assert settings.ai_provider is AIProvider.DISABLED
    assert settings.ai_model == ""
    assert settings.ai_base_url == "http://127.0.0.1:11434"
    assert settings.ai_timeout_seconds == 5.0
    assert settings.ai_max_input_characters == 50_000
    assert settings.is_production is False
    assert settings.is_sqlite is True


def test_settings_read_from_env_with_threnda_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THRENDA_ENV", "production")
    monkeypatch.setenv("THRENDA_SECRET_KEY", "unit-test-secret")
    monkeypatch.setenv("THRENDA_DATABASE_URL", "postgresql+psycopg://u:p@localhost/threnda")
    monkeypatch.setenv("THRENDA_AI_PROVIDER", "ollama")
    monkeypatch.setenv("THRENDA_AI_MODEL", "llama3")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env is Environment.PRODUCTION
    assert settings.is_production is True
    assert settings.secret_key == "unit-test-secret"  # noqa: S105 - test fixture, not a real secret
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.is_sqlite is False
    assert settings.ai_provider is AIProvider.OLLAMA
    assert settings.ai_model == "llama3"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    try:
        first = get_settings()
        second = get_settings()
        assert first is second
    finally:
        get_settings.cache_clear()


def test_invalid_environment_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THRENDA_ENV", "not-a-real-environment")
    with pytest.raises(ValueError, match="validation error"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "https://127.0.0.1:11434/",
    ],
)
def test_ai_base_url_accepts_localhost_forms(url: str) -> None:
    settings = Settings(_env_file=None, ai_base_url=url)  # type: ignore[call-arg]
    assert not settings.ai_base_url.endswith("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test:11434",
        "http://169.254.169.254",
        "http://localhost.evil.test",
        "http://127.0.0.1.evil.test",
        "ftp://127.0.0.1:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434#frag",
        "not-a-url",
    ],
)
def test_ai_base_url_rejects_non_localhost_or_credentialed_forms(url: str) -> None:
    with pytest.raises(ValueError, match="ai_base_url"):
        Settings(_env_file=None, ai_base_url=url)  # type: ignore[call-arg]


def test_ai_timeout_seconds_bounds_are_enforced() -> None:
    Settings(_env_file=None, ai_timeout_seconds=30)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_timeout_seconds=0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_timeout_seconds=31)  # type: ignore[call-arg]


def test_ai_max_input_characters_bounds_are_enforced() -> None:
    Settings(_env_file=None, ai_max_input_characters=1000)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_max_input_characters=999)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_max_input_characters=200_001)  # type: ignore[call-arg]
