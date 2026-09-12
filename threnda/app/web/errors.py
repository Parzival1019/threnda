"""Web-layer error types and their FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse


class NoWorkspaceError(Exception):
    """Raised when a web route needs a workspace and none exists yet."""


def register_web_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NoWorkspaceError)
    async def _no_workspace(_request: Request, _exc: NoWorkspaceError) -> RedirectResponse:
        return RedirectResponse(url="/workspaces/new", status_code=302)
