"""Web-layer error types and their FastAPI exception handlers."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse


class NoWorkspaceError(Exception):
    """Raised when a web route needs a workspace and the user has none yet."""


class ClientPortalOnlyError(Exception):
    """Raised when a client-side user (only ``client`` memberships) reaches a
    consultancy page — they are sent to the client portal instead."""


class NotAuthenticatedError(Exception):
    """Raised when a web route needs a session and none/an invalid one is present."""

    def __init__(self, next_path: str | None = None) -> None:
        super().__init__("Not authenticated")
        self.next_path = next_path


def register_web_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoWorkspaceError)
    async def _no_workspace(_request: Request, _exc: NoWorkspaceError) -> RedirectResponse:
        # The all-projects overview explains what to do next: a consultancy
        # admin can create a client and project there; anyone else is told
        # they are not a member of a project yet.
        return RedirectResponse(url="/overview", status_code=302)

    @app.exception_handler(ClientPortalOnlyError)
    async def _client_portal(_request: Request, _exc: ClientPortalOnlyError) -> RedirectResponse:
        return RedirectResponse(url="/portal", status_code=302)

    @app.exception_handler(NotAuthenticatedError)
    async def _not_authenticated(_request: Request, exc: NotAuthenticatedError) -> RedirectResponse:
        url = "/login"
        if exc.next_path and exc.next_path not in ("/login", "/logout"):
            url = f"/login?{urlencode({'next': exc.next_path})}"
        return RedirectResponse(url=url, status_code=302)
