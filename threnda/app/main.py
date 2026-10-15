"""FastAPI application factory.

``create_app()`` builds a self-contained application instance: its own
engine, its own sessionmaker, both stored on ``app.state`` rather than a
module global. This is what lets tests construct isolated instances against
temporary databases without any monkeypatching.

Pre-alpha: local accounts and workspace-scoped authorization exist (see
docs/security.md), but this has not had an independent security audit —
bind this only to 127.0.0.1 during local development, and see
docs/deployment.md before exposing an instance any further.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from threnda.app import __version__
from threnda.app.api.v1.router import api_router
from threnda.app.core.config import Settings, get_settings
from threnda.app.core.log_redaction import install_access_log_redaction
from threnda.app.core.middleware import MaxBodySizeMiddleware, SecurityHeadersMiddleware
from threnda.app.core.startup_checks import validate_production_readiness
from threnda.app.db.session import create_engine_from_settings, create_sessionmaker
from threnda.app.web.errors import register_web_exception_handlers
from threnda.app.web.resources import web_resource_dir
from threnda.app.web.router import web_router

STATIC_DIR = web_resource_dir("static")

DESCRIPTION = (
    "Every finding. One queue.\n\n"
    "**Pre-alpha.** Local accounts and per-workspace roles are enforced "
    "(see docs/security.md), but this has not had an independent security "
    "audit — see docs/deployment.md before exposing an instance beyond "
    "local development."
)


def create_app(
    settings: Settings | None = None, *, desktop_setup_token: str | None = None
) -> FastAPI:
    """Build a Threnda FastAPI application.

    Pass an explicit ``settings`` (e.g. pointing at a temporary SQLite file)
    to get a fully isolated instance; this is how the test suite avoids
    touching the developer's real database.
    """
    resolved_settings = settings or get_settings()
    validate_production_readiness(resolved_settings)
    install_access_log_redaction()
    engine = create_engine_from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        from threnda.app.services.ai.embedded import shutdown_embedded_ai

        shutdown_embedded_ai()
        engine.dispose()

    app = FastAPI(
        title="Threnda Community",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.desktop_mode = desktop_setup_token is not None
    app.state.desktop_setup_token = desktop_setup_token
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=resolved_settings.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    register_web_exception_handlers(app)

    return app


app = create_app()
