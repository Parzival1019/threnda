"""Aggregate router for API version 1."""

from __future__ import annotations

from fastapi import APIRouter

from threnda.app.api.v1 import assets, exposures, health, imports, sources

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(exposures.router)
api_router.include_router(imports.router)
api_router.include_router(sources.router)
api_router.include_router(assets.router)
