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
