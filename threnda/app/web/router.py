"""Aggregate router for the server-rendered web UI."""

from __future__ import annotations

from fastapi import APIRouter

from threnda.app.web.routes import (
    assets,
    dashboard,
    documents,
    exposures,
    imports,
    review,
    sources,
    workspaces,
)

web_router = APIRouter()
web_router.include_router(workspaces.router)
web_router.include_router(dashboard.router)
web_router.include_router(exposures.router)
web_router.include_router(imports.router)
web_router.include_router(sources.router)
web_router.include_router(review.router)
web_router.include_router(assets.router)
web_router.include_router(documents.router)
