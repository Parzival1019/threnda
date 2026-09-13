"""Aggregate router for API version 1."""

from __future__ import annotations

from fastapi import APIRouter

from threnda.app.api.v1 import (
    assets,
    audit,
    auth,
    documents,
    exports,
    exposures,
    health,
    imports,
    members,
    possible_duplicates,
    sources,
    webhooks,
    workspaces,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(exposures.router)
api_router.include_router(imports.router)
api_router.include_router(sources.router)
api_router.include_router(assets.router)
api_router.include_router(documents.router)
api_router.include_router(possible_duplicates.router)
api_router.include_router(members.router)
api_router.include_router(webhooks.router)
api_router.include_router(audit.router)
api_router.include_router(exports.router)
