"""Cookie security defaults shared by the session and CSRF cookies."""

from __future__ import annotations

from fastapi import Request

from threnda.app.core.config import Settings

SESSION_COOKIE_NAME = "threnda_session"
WORKSPACE_COOKIE_NAME = "threnda_workspace_id"


def cookie_secure(settings: Settings, request: Request) -> bool:
    """Whether a cookie set on this response should carry the ``Secure`` flag.

    ``Secure`` whenever the deployment is production or the current
    request itself arrived over HTTPS. ``THRENDA_ALLOW_INSECURE_COOKIES``
    forces it off unconditionally — a local-development/testing override
    only; ``threnda.app.core.startup_checks`` refuses to start with it set
    in production.
    """
    if settings.allow_insecure_cookies:
        return False
    return settings.is_production or request.url.scheme == "https"
