"""Fail-closed checks run once, at application startup.

These never run in a request path — only from ``create_app()`` (see
``threnda.app.main``) and the bootstrap CLI. A production deployment with a
default or weak secret key is not "insecure but working"; it is refused
outright, since ``THRENDA_SECRET_KEY`` underpins both CSRF token binding
and webhook secret encryption at rest (see
``threnda.app.services.crypto``).
"""

from __future__ import annotations

from threnda.app.core.config import Settings

MIN_SECRET_KEY_LENGTH = 32
_DEFAULT_SECRET_KEY = "change-me"  # noqa: S105 - the literal default value, not a credential


class InsecureConfigurationError(RuntimeError):
    """Raised when a production deployment's configuration is unsafe to start."""


def validate_production_readiness(settings: Settings) -> None:
    """Raise :class:`InsecureConfigurationError` if unsafe to run as configured.

    Only enforced when ``THRENDA_ENV=production``; development stays
    permissive so a fresh local checkout works with the shipped
    ``.env.example`` defaults.
    """
    if not settings.is_production:
        return
    if settings.secret_key == _DEFAULT_SECRET_KEY:
        raise InsecureConfigurationError(
            "THRENDA_SECRET_KEY is still the placeholder 'change-me'. Set a unique, "
            'high-entropy secret (e.g. `python -c "import secrets; '
            'print(secrets.token_urlsafe(64))"`) before running in production.'
        )
    if len(settings.secret_key) < MIN_SECRET_KEY_LENGTH:
        raise InsecureConfigurationError(
            f"THRENDA_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters in production."
        )
    if settings.allow_insecure_cookies:
        raise InsecureConfigurationError(
            "THRENDA_ALLOW_INSECURE_COOKIES must not be set in production."
        )
