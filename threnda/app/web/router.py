"""Aggregate router for the server-rendered web UI."""

from __future__ import annotations

from fastapi import APIRouter

from threnda.app.web.routes import (
    assets,
    auth,
    dashboard,
    documents,
    exposures,
    imports,
    review,
    sources,
    workspaces,
)
from threnda.app.web.routes import (
    settings as settings_routes,
)

web_router = APIRouter()
web_router.include_router(auth.router)
web_router.include_router(workspaces.router)
web_router.include_router(settings_routes.router)
web_router.include_router(dashboard.router)
web_router.include_router(exposures.router)
web_router.include_router(imports.router)
web_router.include_router(sources.router)
web_router.include_router(review.router)
web_router.include_router(assets.router)
web_router.include_router(documents.router)
