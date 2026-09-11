"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from threnda.app import __version__
from threnda.app.api.deps import get_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


@router.get("/health", response_model=HealthResponse)
def get_health(db: Session = Depends(get_db)) -> HealthResponse:
    """Report whether the API process and its database are reachable."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_status = "error"
    else:
        database_status = "ok"

    return HealthResponse(status="ok", version=__version__, database=database_status)
