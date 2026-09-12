"""Workspace provisioning.

Not part of the Milestone 1 API list, added in Milestone 2 because
ingestion needs a documented, non-SQL way to create the workspace/source an
upload belongs to — see the Milestone 2 acceptance checklist's "provision a
workspace and source through the documented local interface" requirement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_db
from threnda.app.models.workspace import Workspace
from threnda.app.schemas.common import Page
from threnda.app.schemas.workspace import WorkspaceCreate, WorkspaceRead

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=Page[WorkspaceRead])
def list_workspaces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[WorkspaceRead]:
    stmt = select(Workspace)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Workspace.id).limit(limit).offset(offset)).scalars().all()
    return Page[WorkspaceRead](
        items=[WorkspaceRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=WorkspaceRead, status_code=201)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)) -> WorkspaceRead:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"A workspace named {payload.name!r} already exists."
        ) from exc
    return WorkspaceRead.model_validate(workspace)
