"""FastAPI application factory.

``create_app()`` builds a self-contained application instance: its own
engine, its own sessionmaker, both stored on ``app.state`` rather than a
module global. This is what lets tests construct isolated instances against
temporary databases without any monkeypatching.

Local-only, pre-alpha: there is no authentication yet (see
docs/implementation-plan.md, Milestone 1 limitations) and this module must
never be bound to anything other than 127.0.0.1 during development.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from threnda.app import __version__
from threnda.app.api.v1.router import api_router
from threnda.app.core.config import Settings, get_settings
from threnda.app.core.middleware import MaxBodySizeMiddleware
from threnda.app.db.session import create_engine_from_settings, create_sessionmaker
from threnda.app.web.errors import register_web_exception_handlers
from threnda.app.web.router import web_router

STATIC_DIR = Path(__file__).parent / "web" / "static"

DESCRIPTION = (
    "Every finding. One queue.\n\n"
    "**Pre-alpha, local-development API.** There is no authentication yet — "
    "do not expose this beyond `127.0.0.1` and do not use it to store real "
    "findings."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a Threnda FastAPI application.

    Pass an explicit ``settings`` (e.g. pointing at a temporary SQLite file)
    to get a fully isolated instance; this is how the test suite avoids
    touching the developer's real database.
    """
    resolved_settings = settings or get_settings()
    engine = create_engine_from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="Threnda Community",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=resolved_settings.max_upload_bytes)
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    register_web_exception_handlers(app)

    return app


app = create_app()
