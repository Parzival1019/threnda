"""Workspace membership management — admin only.

Adding a brand-new username creates the local account and the membership
in one step (there is no self-registration anywhere in Community — see
``threnda.app.services.user_accounts``); adding a username that already
has an account elsewhere just grants it a role in this workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from threnda.app.api.deps import get_db, require_admin
from threnda.app.models.user import User
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.membership import MemberAdd, MemberRoleUpdate, MembershipRead
from threnda.app.services.auth import InvalidUsernameError, WeakPasswordError, normalize_username
from threnda.app.services.memberships import (
    LastAdminError,
    add_member,
    remove_member,
    update_member_role,
)
from threnda.app.services.user_accounts import UsernameTakenError, create_user

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MembershipRead])
def list_members(
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[MembershipRead]:
    rows = db.execute(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.workspace_id == membership.workspace_id)
        .options(joinedload(WorkspaceMembership.user))
        .order_by(WorkspaceMembership.id)
    ).scalars()
    return [
        MembershipRead(
            user_id=row.user_id,
            role=row.role,
            created_at=row.created_at,
            username=row.user.username,
        )
        for row in rows
    ]


@router.post("", response_model=MembershipRead, status_code=201)
def add_workspace_member(
    payload: MemberAdd,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MembershipRead:
    try:
        normalized = normalize_username(payload.username)
    except InvalidUsernameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = db.execute(select(User).where(User.username == normalized)).scalar_one_or_none()
    if user is None:
        if not payload.password:
            raise HTTPException(
                status_code=422,
                detail=f"No account named {normalized!r} exists yet — a password is required "
                "to create one.",
            )
        try:
            user = create_user(db, payload.username, payload.password, actor=membership.user)
        except (InvalidUsernameError, WeakPasswordError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except UsernameTakenError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        new_membership = add_member(
            db, membership.workspace, user, payload.role, actor=membership.user
        )
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail=f"{normalized!r} is already a member of this workspace."
        ) from exc
    return MembershipRead(
        user_id=new_membership.user_id,
        role=new_membership.role,
        created_at=new_membership.created_at,
        username=user.username,
    )


def _get_target_membership(db: Session, workspace_id: int, user_id: int) -> WorkspaceMembership:
    target = db.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
        .options(joinedload(WorkspaceMembership.user))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found in this workspace.")
    return target


@router.patch("/{user_id}", response_model=MembershipRead)
def update_member(
    user_id: int,
    payload: MemberRoleUpdate,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> MembershipRead:
    target = _get_target_membership(db, membership.workspace_id, user_id)
    try:
        update_member_role(db, target, payload.role, actor=membership.user)
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MembershipRead(
        user_id=target.user_id,
        role=target.role,
        created_at=target.created_at,
        username=target.user.username,
    )


@router.delete("/{user_id}", status_code=204)
def remove_workspace_member(
    user_id: int,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    target = _get_target_membership(db, membership.workspace_id, user_id)
    try:
        remove_member(db, target, actor=membership.user)
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
