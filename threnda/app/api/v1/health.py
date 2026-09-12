"""Health check endpoint.

This is a readiness check, not a bare liveness probe: it confirms the
database is not just reachable but actually migrated to the schema this
build of the application expects. A separate liveness endpoint isn't needed
for Community's scope — any HTTP response at all (including the 503 this
endpoint returns when the database isn't ready) already answers "is the
process up".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from threnda.app import __version__
from threnda.app.api.deps import get_db
from threnda.app.db.migration_status import get_database_status

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


@router.get("/health", response_model=HealthResponse)
def get_health(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    """Report whether the API process and its database are ready.

    ``database`` is ``"ok"``, ``"uninitialized"`` (no migrations ever
    applied) or ``"outdated"`` (behind the migrations checked into this
    build) — never raw driver or filesystem error text, since this endpoint
    is reachable without authentication.
    """
    database_status = get_database_status(db)
    if database_status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(status="ok", version=__version__, database=database_status)
