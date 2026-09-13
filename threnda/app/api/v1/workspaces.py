"""Workspace provisioning and listing.

``GET`` lists only workspaces the authenticated caller is a member of —
never every workspace on the instance, which before Milestone 7 (when
there was no authentication at all) would have been the only "list of
workspaces" that made sense, but is a cross-tenant information leak once
accounts exist. ``POST`` makes the creator that workspace's first admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threnda.app.api.deps import get_current_user, get_db
from threnda.app.models.enums import WorkspaceRole
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.common import Page
from threnda.app.schemas.workspace import WorkspaceCreate, WorkspaceRead
from threnda.app.services.memberships import add_member

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=Page[WorkspaceRead])
def list_workspaces(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[WorkspaceRead]:
    stmt = (
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user.id)
    )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Workspace.id).limit(limit).offset(offset)).scalars().all()
    return Page[WorkspaceRead](
        items=[WorkspaceRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=WorkspaceRead, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceRead:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"A workspace named {payload.name!r} already exists."
        ) from exc
    add_member(db, workspace, user, WorkspaceRole.ADMIN, actor=user)
    return WorkspaceRead.model_validate(workspace)
