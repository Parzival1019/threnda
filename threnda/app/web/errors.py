"""Web-layer error types and their FastAPI exception handlers."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse


class NoWorkspaceError(Exception):
    """Raised when a web route needs a workspace and the user has none yet."""


class NotAuthenticatedError(Exception):
    """Raised when a web route needs a session and none/an invalid one is present."""

    def __init__(self, next_path: str | None = None) -> None:
        super().__init__("Not authenticated")
        self.next_path = next_path


def register_web_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoWorkspaceError)
    async def _no_workspace(_request: Request, _exc: NoWorkspaceError) -> RedirectResponse:
        return RedirectResponse(url="/workspaces/new", status_code=302)

    @app.exception_handler(NotAuthenticatedError)
    async def _not_authenticated(_request: Request, exc: NotAuthenticatedError) -> RedirectResponse:
        url = "/login"
        if exc.next_path and exc.next_path not in ("/login", "/logout"):
            url = f"/login?{urlencode({'next': exc.next_path})}"
        return RedirectResponse(url=url, status_code=302)
