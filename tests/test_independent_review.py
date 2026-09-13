"""Independent regressions for the foundation, using synthetic fixtures only."""

import pytest
from conftest import TestUser, grant_membership
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threnda.app.models import (
    Asset,
    Exposure,
    ExposureHistory,
    Finding,
    ImportRun,
    Source,
    Workspace,
)
from threnda.app.models.enums import SeverityLevel, SourceType


def seed_exposure(db: Session, admin_user: TestUser | None = None) -> Exposure:
    workspace = Workspace(name="independent-review")
    db.add(workspace)
    db.flush()
    if admin_user is not None:
        grant_membership(db, workspace, admin_user)
    exposure = Exposure(
        workspace_id=workspace.id,
        canonical_fingerprint="synthetic-review",
        title="Synthetic review finding",
        severity=SeverityLevel.LOW,
        acknowledged=True,
        owner="original-owner",
    )
    db.add(exposure)
    db.commit()
    return exposure


@pytest.mark.parametrize("field", ["status", "acknowledged"])
def test_nonnullable_patch_rejected_atomically(
    client: TestClient, db_session: Session, field: str, admin_user: TestUser
) -> None:
    exposure = seed_exposure(db_session, admin_user)
    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": exposure.workspace_id},
        json={"owner": "must-not-persist", field: None},
    )
    assert response.status_code == 422
    db_session.refresh(exposure)
    assert exposure.owner == "original-owner"
    assert db_session.scalars(select(ExposureHistory)).all() == []


def test_deacknowledge_and_remediation_both_have_audit_records(
    client: TestClient, db_session: Session, admin_user: TestUser
) -> None:
    exposure = seed_exposure(db_session, admin_user)
    response = client.patch(
        f"/api/v1/exposures/{exposure.id}",
        params={"workspace_id": exposure.workspace_id},
        json={"acknowledged": False, "remediation": "Synthetic remediation"},
    )
    assert response.status_code == 200
    history = db_session.scalars(select(ExposureHistory)).all()
    values = {(row.previous_value, row.new_value) for row in history}
    assert ("true", "false") in values
    assert (None, "Synthetic remediation") in values


@pytest.mark.parametrize("target", ["source", "asset", "exposure", "import_run"])
def test_finding_cannot_reference_another_workspace(db_session: Session, target: str) -> None:
    first, second = Workspace(name="synthetic-one"), Workspace(name="synthetic-two")
    db_session.add_all([first, second])
    db_session.flush()
    own_source = Source(workspace_id=first.id, name="Own source", source_type=SourceType.JSON)
    other_source = Source(workspace_id=second.id, name="Other source", source_type=SourceType.JSON)
    other_asset = Asset(
        workspace_id=second.id, canonical_key="host:other.example.test", name="Other"
    )
    other_exposure = Exposure(
        workspace_id=second.id,
        canonical_fingerprint="other-synthetic",
        title="Other synthetic",
        severity=SeverityLevel.LOW,
    )
    db_session.add_all([own_source, other_source, other_asset, other_exposure])
    db_session.flush()
    other_run = ImportRun(
        workspace_id=second.id,
        source_id=other_source.id,
        filename="synthetic.json",
        file_hash="0" * 64,
    )
    db_session.add(other_run)
    db_session.commit()
    finding = Finding(
        workspace_id=first.id,
        source_id=other_source.id if target == "source" else own_source.id,
        asset_id=other_asset.id if target == "asset" else None,
        exposure_id=other_exposure.id if target == "exposure" else None,
        import_run_id=other_run.id if target == "import_run" else None,
        source_finding_id="synthetic-1",
        fingerprint="synthetic-1",
        title="Synthetic mismatch",
        severity=SeverityLevel.LOW,
    )
    db_session.add(finding)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_enum_is_enforced_by_database_not_just_python(db_session: Session) -> None:
    exposure = seed_exposure(db_session)
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE exposures SET severity = :value WHERE id = :id"),
            {"value": "not-a-valid-severity", "id": exposure.id},
        )
    db_session.rollback()
