"""Clients and client projects — consultancy-admin operations.

A project is a ``Workspace`` with ``client_id`` set. Creating one makes the
creating consultancy admin its first ``admin`` member; everyone else is
added or invited per project (membership stays the only access grant).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from threnda.app.models.client import Client
from threnda.app.models.enums import AuditEventType, ExposureStatus, SeverityLevel, WorkspaceRole
from threnda.app.models.exposure import Exposure
from threnda.app.models.mixins import utcnow
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.models.workspace_membership import WorkspaceMembership
from threnda.app.services.audit import record_audit_event
from threnda.app.services.exposures import ACTIVE_STATUSES
from threnda.app.services.memberships import add_member


class ProjectError(ValueError):
    """A user-correctable problem creating a client or project."""


def require_org_admin(user: User) -> None:
    if not user.is_org_admin:
        raise PermissionError("Only a consultancy admin can manage clients and projects.")


def create_client(db: Session, *, name: str, notes: str | None, actor: User) -> Client:
    require_org_admin(actor)
    clean = name.strip()
    if not clean:
        raise ProjectError("Client name is required.")
    if len(clean) > 200:
        raise ProjectError("Client name can be at most 200 characters.")
    # Checked up front (rather than catching the unique-constraint error) so
    # the caller's transaction stays usable for rendering an error page.
    if db.execute(select(Client.id).where(Client.name == clean)).scalar_one_or_none():
        raise ProjectError(f"A client named {clean!r} already exists.")
    client = Client(name=clean, notes=(notes or "").strip() or None)
    db.add(client)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.CLIENT_CREATED,
        actor=actor,
        target_type="client",
        target_id=client.id,
        detail={"name": clean},
    )
    return client


def create_project(
    db: Session, *, client: Client, name: str, description: str | None, actor: User
) -> Workspace:
    require_org_admin(actor)
    clean = name.strip()
    if not clean:
        raise ProjectError("Project name is required.")
    if len(clean) > 200:
        raise ProjectError("Project name can be at most 200 characters.")
    if db.execute(select(Workspace.id).where(Workspace.name == clean)).scalar_one_or_none():
        raise ProjectError(f"A project named {clean!r} already exists (project names are unique).")
    workspace = Workspace(
        name=clean, client_id=client.id, description=(description or "").strip() or None
    )
    db.add(workspace)
    db.flush()
    add_member(db, workspace, actor, WorkspaceRole.ADMIN, actor=actor)
    record_audit_event(
        db,
        event_type=AuditEventType.PROJECT_CREATED,
        actor=actor,
        workspace=workspace,
        target_type="workspace",
        target_id=workspace.id,
        detail={"name": clean, "client": client.name},
    )
    return workspace


@dataclass(frozen=True)
class ProjectStats:
    workspace: Workspace
    role: WorkspaceRole
    total: int
    active: int
    critical_active: int
    overdue: int
    awaiting_retest: int
    verified_closed: int


def project_stats(db: Session, workspace: Workspace, role: WorkspaceRole) -> ProjectStats:
    today = utcnow().date()

    def count(*conditions: object) -> int:
        stmt = (
            select(func.count())
            .select_from(Exposure)
            .where(Exposure.workspace_id == workspace.id, *conditions)  # type: ignore[arg-type]
        )
        return int(db.execute(stmt).scalar_one())

    active = Exposure.status.in_(ACTIVE_STATUSES)
    return ProjectStats(
        workspace=workspace,
        role=role,
        total=count(),
        active=count(active),
        critical_active=count(active, Exposure.severity == SeverityLevel.CRITICAL),
        overdue=count(
            active,
            Exposure.remediation_target_date.is_not(None),
            Exposure.remediation_target_date < today,
        ),
        awaiting_retest=count(Exposure.status == ExposureStatus.FIX_REPORTED),
        verified_closed=count(Exposure.status == ExposureStatus.RESOLVED),
    )


def memberships_for(db: Session, user: User, *, client_side: bool) -> list[WorkspaceMembership]:
    """The user's memberships — client ones (portal) or staff ones (overview)."""
    role_filter = (
        WorkspaceMembership.role == WorkspaceRole.CLIENT
        if client_side
        else WorkspaceMembership.role != WorkspaceRole.CLIENT
    )
    return list(
        db.execute(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id, role_filter)
            .order_by(WorkspaceMembership.workspace_id)
        ).scalars()
    )
