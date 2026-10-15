"""Clients, client projects, invitations and the pentest findings CSV template.

Clients/projects: consultancy admins only (``User.is_org_admin``).
Invitations: the project's admins. Findings CSV: pentesters and admins.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from threnda.app.api.deps import get_current_user, get_db, require_admin, require_analyst
from threnda.app.models.client import Client
from threnda.app.models.user import User
from threnda.app.models.user_invitation import UserInvitation
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.schemas.project import (
    ClientCreate,
    ClientRead,
    CsvProblemRead,
    FindingsCsvImportRead,
    InvitationCreate,
    InvitationRead,
    IssuedInvitationRead,
    ProjectCreate,
    ProjectRead,
)
from threnda.app.services.auth import InvalidUsernameError
from threnda.app.services.findings_csv import TEMPLATE_CSV, import_template_csv
from threnda.app.services.ingestion.upload import sanitize_filename
from threnda.app.services.invitations import (
    InvitationError,
    create_invitation,
    pending_invitations,
    revoke_invitation,
)
from threnda.app.services.projects import ProjectError, create_client, create_project

router = APIRouter(tags=["projects"])


def _require_org_admin(user: User) -> None:
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Consultancy admin only.")


@router.get("/clients", response_model=list[ClientRead])
def list_clients(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ClientRead]:
    _require_org_admin(user)
    rows = db.execute(select(Client).order_by(Client.name)).scalars()
    return [ClientRead.model_validate(c) for c in rows]


@router.post("/clients", response_model=ClientRead, status_code=201)
def add_client(
    payload: ClientCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClientRead:
    _require_org_admin(user)
    try:
        client = create_client(db, name=payload.name, notes=payload.notes, actor=user)
    except ProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ClientRead.model_validate(client)


@router.post("/clients/{client_id}/projects", response_model=ProjectRead, status_code=201)
def add_project(
    client_id: int,
    payload: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    _require_org_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    try:
        project = create_project(
            db, client=client, name=payload.name, description=payload.description, actor=user
        )
    except ProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProjectRead.model_validate(project)


def invite_base_url(request: Request) -> str:
    configured: str = request.app.state.settings.public_base_url
    return configured or str(request.base_url).rstrip("/")


@router.get("/invitations", response_model=list[InvitationRead])
def list_invitations(
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[InvitationRead]:
    return [
        InvitationRead.model_validate(i) for i in pending_invitations(db, membership.workspace_id)
    ]


@router.post("/invitations", response_model=IssuedInvitationRead, status_code=201)
def invite(
    request: Request,
    payload: InvitationCreate,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssuedInvitationRead:
    try:
        issued = create_invitation(
            db,
            membership.workspace,
            username=payload.username,
            role=payload.role,
            actor=membership.user,
            ttl_hours=request.app.state.settings.invitation_ttl_hours,
        )
    except (InvitationError, InvalidUsernameError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    base = InvitationRead.model_validate(issued.invitation).model_dump()
    return IssuedInvitationRead(
        **base, invite_url=f"{invite_base_url(request)}/invite/{issued.token}"
    )


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke(
    invitation_id: int,
    membership: WorkspaceMembership = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    invitation = db.get(UserInvitation, invitation_id)
    if invitation is None or invitation.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    revoke_invitation(db, invitation, actor=membership.user)


@router.get("/findings-csv/template")
def findings_csv_template(_user: User = Depends(get_current_user)) -> Response:
    return Response(
        content=TEMPLATE_CSV,
        media_type="text/csv",
        headers={"content-disposition": "attachment; filename=threnda-findings-template.csv"},
    )


@router.post("/findings-csv", response_model=FindingsCsvImportRead)
async def import_findings_csv(
    request: Request,
    file: UploadFile = File(...),
    membership: WorkspaceMembership = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> Response | FindingsCsvImportRead:
    raw = await file.read()
    result = import_template_csv(
        db,
        membership.workspace,
        raw=raw,
        filename=sanitize_filename(file.filename),
        actor=membership.user.username,
    )
    body = FindingsCsvImportRead(
        ok=result.ok,
        problems=[
            CsvProblemRead(line=p.line, column=p.column, message=p.message) for p in result.problems
        ],
        total_rows=result.total_rows,
        import_run_id=result.import_run.id if result.import_run else None,
        new=result.new,
        updated=result.updated,
        unchanged=result.unchanged,
        reopened=result.reopened,
        not_in_this_import=result.not_in_this_import,
    )
    if not result.ok:
        return Response(
            content=body.model_dump_json(), status_code=422, media_type="application/json"
        )
    return body
