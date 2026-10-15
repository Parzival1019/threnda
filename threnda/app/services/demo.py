"""The fictional, resettable demo dataset (docs/demo.md).

Two independent safeguards keep this away from real client data:

1. Demo seeding/reset only runs when ``THRENDA_DEMO_MODE=true`` (checked by
   the CLI and the in-app reset route).
2. :func:`reset_demo_data` refuses any database that does not carry the
   ``instance_kind = demo`` marker row, and :func:`seed_demo_data` refuses
   any database that already has accounts. So even with demo mode wrongly
   enabled on a production instance, its data cannot be wiped: that
   database never had the marker.

Every name, host and address below is invented: ``.example`` domains (RFC
2606) and ``192.0.2.0/24`` / ``198.51.100.0/24`` documentation addresses
(RFC 5737). No real company, person or finding is represented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from threnda.app.db.base import Base
from threnda.app.models.enums import (
    AuditEventType,
    ExposureStatus,
    RetestOutcome,
    WorkspaceRole,
)
from threnda.app.models.exposure import Exposure
from threnda.app.models.instance_meta import InstanceMeta
from threnda.app.models.mixins import utcnow
from threnda.app.models.user import User
from threnda.app.models.workspace import Workspace
from threnda.app.services.audit import record_audit_event
from threnda.app.services.collaboration import add_attachment, add_comment, client_set_status
from threnda.app.services.exposures import apply_exposure_update
from threnda.app.services.findings_csv import import_template_csv
from threnda.app.services.memberships import add_member
from threnda.app.services.projects import create_client, create_project
from threnda.app.services.retests import record_retest
from threnda.app.services.user_accounts import create_user

DEMO_MARKER_KEY = "instance_kind"
DEMO_MARKER_VALUE = "demo"
#: Shared demo password — a *demo-only* credential for fictional accounts,
#: documented in docs/demo.md. Override with THRENDA_DEMO_PASSWORD for a demo
#: instance that is reachable by more people than the presenter.
DEFAULT_DEMO_PASSWORD = "Demo-Threnda-2026!"  # noqa: S105 - documented demo credential

DEMO_ADMIN = "demo-admin"
DEMO_PENTESTER = "demo-pentester"
DEMO_CLIENT_KESTREL = "kestrel-client"
DEMO_CLIENT_BELLWETHER = "bellwether-client"
DEMO_CLIENT_HARBOR = "harbor-client"


class DemoResetRefusedError(RuntimeError):
    """The target database is not a demo database (or is not empty)."""


def demo_password() -> str:
    return os.environ.get("THRENDA_DEMO_PASSWORD") or DEFAULT_DEMO_PASSWORD


def is_demo_database(db: Session) -> bool:
    marker = db.get(InstanceMeta, DEMO_MARKER_KEY)
    return marker is not None and marker.value == DEMO_MARKER_VALUE


def _has_accounts(db: Session) -> bool:
    return db.execute(select(func.count()).select_from(User)).scalar_one() > 0


# --- fictional findings --------------------------------------------------------
# Template CSVs with {past_due}/{soon}/{later} placeholders for due dates
# relative to the day the demo is (re)loaded.
_DATA_DIR = Path(__file__).with_name("demo_data")

_KESTREL_CSV = (_DATA_DIR / "kestrel-web-pentest.csv").read_text(encoding="utf-8")

_BELLWETHER_CSV = (_DATA_DIR / "bellwether-internal.csv").read_text(encoding="utf-8")

# A tiny but valid PNG (1x1) standing in for a fictional screenshot.
_DEMO_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e2219bc5"
    "0000000049454e44ae426082"
)


@dataclass(frozen=True)
class DemoSummary:
    users: tuple[str, ...]
    projects: tuple[str, ...]


def _dates() -> dict[str, str]:
    today = utcnow().date()
    return {
        "past_due": (today - timedelta(days=9)).isoformat(),
        "soon": (today + timedelta(days=12)).isoformat(),
        "later": (today + timedelta(days=45)).isoformat(),
    }


def _by_title(db: Session, workspace: Workspace, prefix: str) -> Exposure:
    exposure = db.execute(
        select(Exposure).where(
            Exposure.workspace_id == workspace.id, Exposure.title.startswith(prefix)
        )
    ).scalar_one()
    return exposure


def seed_demo_data(db: Session) -> DemoSummary:
    """Fill an *empty* database with the fictional demo dataset."""
    if _has_accounts(db):
        raise DemoResetRefusedError(
            "Refusing to seed demo data into a database that already has accounts."
        )
    db.add(InstanceMeta(key=DEMO_MARKER_KEY, value=DEMO_MARKER_VALUE))
    db.flush()

    password = demo_password()
    admin = create_user(db, DEMO_ADMIN, password, actor=None)
    admin.is_org_admin = True
    pentester = create_user(db, DEMO_PENTESTER, password, actor=admin)

    kestrel = create_client(
        db, name="Kestrel Logistics (fictional)", notes="Fictional demo client.", actor=admin
    )
    bellwether = create_client(
        db, name="Bellwether Dental (fictional)", notes="Fictional demo client.", actor=admin
    )
    harbor = create_client(
        db,
        name="Harbor Analytics (fictional)",
        notes="Fictional demo client — no project yet; created live during the demo.",
        actor=admin,
    )

    kestrel_client = create_user(db, DEMO_CLIENT_KESTREL, password, actor=admin)
    kestrel_client.client_id = kestrel.id
    bellwether_client = create_user(db, DEMO_CLIENT_BELLWETHER, password, actor=admin)
    bellwether_client.client_id = bellwether.id
    harbor_client = create_user(db, DEMO_CLIENT_HARBOR, password, actor=admin)
    harbor_client.client_id = harbor.id
    db.flush()

    dates = _dates()
    p1 = create_project(
        db,
        client=kestrel,
        name="Kestrel — external web pentest 2026-Q3",
        description="Fictional engagement: customer portal, tracking and API.",
        actor=admin,
    )
    add_member(db, p1, pentester, WorkspaceRole.ANALYST, actor=admin)
    add_member(db, p1, kestrel_client, WorkspaceRole.CLIENT, actor=admin)
    result = import_template_csv(
        db,
        p1,
        raw=_KESTREL_CSV.format(**dates).encode(),
        filename="kestrel-web-pentest-2026q3.csv",
        actor=DEMO_PENTESTER,
    )
    if not result.ok:  # pragma: no cover - fixed fictional data
        raise RuntimeError(f"demo seed CSV invalid: {result.problems}")

    # A realistic spread of lifecycle states.
    tls = _by_title(db, p1, "Outdated TLS")
    client_set_status(
        db,
        tls,
        status=ExposureStatus.FIX_REPORTED,
        note="TLS 1.0/1.1 disabled on the gateway on Monday; config change attached.",
        actor=kestrel_client,
    )
    add_attachment(
        db,
        tls,
        filename="gateway-tls-config-diff.txt",
        raw=b"- ssl_protocols TLSv1 TLSv1.1 TLSv1.2;\n+ ssl_protocols TLSv1.2 TLSv1.3;\n",
        internal=False,
        actor=kestrel_client,
        actor_role=WorkspaceRole.CLIENT,
        max_bytes=1_000_000,
    )
    cookie = _by_title(db, p1, "Session cookie")
    client_set_status(
        db, cookie, status=ExposureStatus.FIX_REPORTED, note=None, actor=kestrel_client
    )
    record_retest(
        db,
        cookie,
        outcome=RetestOutcome.PASSED,
        notes="Cookie now carries Secure, HttpOnly and SameSite=Lax.",
        actor=pentester,
        actor_role=WorkspaceRole.ANALYST,
    )
    listing = _by_title(db, p1, "Directory listing")
    client_set_status(
        db, listing, status=ExposureStatus.FIX_REPORTED, note=None, actor=kestrel_client
    )
    record_retest(
        db,
        listing,
        outcome=RetestOutcome.FAILED,
        notes="/assets/ is fixed, but /assets/old/ still returns an index.",
        actor=pentester,
        actor_role=WorkspaceRole.ANALYST,
    )
    xss = _by_title(db, p1, "Stored XSS")
    client_set_status(db, xss, status=ExposureStatus.IN_PROGRESS, note=None, actor=kestrel_client)
    add_comment(
        db,
        xss,
        body="Fix is in code review; we expect to deploy next sprint.",
        internal=False,
        actor=kestrel_client,
        actor_role=WorkspaceRole.CLIENT,
    )
    add_comment(
        db,
        xss,
        body="Check the admin dashboard too when retesting — same template engine.",
        internal=True,
        actor=pentester,
        actor_role=WorkspaceRole.ANALYST,
    )
    verbose = _by_title(db, p1, "Verbose error")
    apply_exposure_update(
        db,
        verbose,
        {"status": ExposureStatus.RISK_ACCEPTED},
        note="Accepted by the client until the Q1 platform upgrade.",
        actor=DEMO_ADMIN,
    )
    sqli = _by_title(db, p1, "SQL injection")
    add_attachment(
        db,
        sqli,
        filename="sqli-timing-proof.png",
        raw=_DEMO_PNG,
        internal=False,
        actor=pentester,
        actor_role=WorkspaceRole.ANALYST,
        max_bytes=1_000_000,
    )

    p2 = create_project(
        db,
        client=bellwether,
        name="Bellwether — internal network assessment 2026-Q4",
        description="Fictional engagement: office network and directory service.",
        actor=admin,
    )
    add_member(db, p2, pentester, WorkspaceRole.ANALYST, actor=admin)
    add_member(db, p2, bellwether_client, WorkspaceRole.CLIENT, actor=admin)
    result = import_template_csv(
        db,
        p2,
        raw=_BELLWETHER_CSV.format(**dates).encode(),
        filename="bellwether-internal-2026q4.csv",
        actor=DEMO_PENTESTER,
    )
    if not result.ok:  # pragma: no cover - fixed fictional data
        raise RuntimeError(f"demo seed CSV invalid: {result.problems}")
    smb = _by_title(db, p2, "SMB signing")
    client_set_status(
        db,
        smb,
        status=ExposureStatus.FIX_REPORTED,
        note="Group policy updated and applied to all servers.",
        actor=bellwether_client,
    )
    db.flush()
    return DemoSummary(
        users=(
            DEMO_ADMIN,
            DEMO_PENTESTER,
            DEMO_CLIENT_KESTREL,
            DEMO_CLIENT_BELLWETHER,
            DEMO_CLIENT_HARBOR,
        ),
        projects=(p1.name, p2.name),
    )


def reset_demo_data(db: Session, *, actor_username: str | None = None) -> DemoSummary:
    """Wipe and re-seed — only ever on a database marked as a demo database.

    An *empty* database (no accounts at all, e.g. a fresh demo container) is
    also accepted, so the first ``threnda demo reset`` can create the demo.
    """
    if not is_demo_database(db):
        if _has_accounts(db):
            raise DemoResetRefusedError(
                "This database is not marked as a demo database — refusing to delete anything. "
                "Demo reset only works on a database created by `threnda demo reset`."
            )
    else:
        # Flush pending changes first (e.g. the caller's own session row
        # being touched), then forget every loaded object: after the bulk
        # DELETEs they all refer to rows that no longer exist.
        db.flush()
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.expunge_all()
    summary = seed_demo_data(db)
    admin = db.execute(select(User).where(User.username == DEMO_ADMIN)).scalar_one()
    record_audit_event(
        db,
        event_type=AuditEventType.DEMO_RESET,
        actor=admin,
        detail={"requested_by": actor_username or "cli"},
    )
    return summary
