"""Regression checks for cross-source links and ORM workspace reassignment."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threnda.app.models import Asset, Exposure, Finding, ImportRun, Source, Workspace
from threnda.app.models.enums import SeverityLevel, SourceType


def test_import_run_cannot_use_another_workspaces_source(db_session: Session) -> None:
    first, second = Workspace(name="Synthetic A"), Workspace(name="Synthetic B")
    db_session.add_all([first, second])
    db_session.flush()
    source = Source(workspace_id=second.id, name="Synthetic source", source_type=SourceType.JSON)
    db_session.add(source)
    db_session.commit()
    db_session.add(
        ImportRun(
            workspace_id=first.id,
            source_id=source.id,
            filename="synthetic.json",
            file_hash="0" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_finding_import_run_must_belong_to_same_source(db_session: Session) -> None:
    workspace = Workspace(name="Synthetic source consistency")
    db_session.add(workspace)
    db_session.flush()
    first = Source(workspace_id=workspace.id, name="Source A", source_type=SourceType.JSON)
    second = Source(workspace_id=workspace.id, name="Source B", source_type=SourceType.JSON)
    db_session.add_all([first, second])
    db_session.flush()
    run = ImportRun(
        workspace_id=workspace.id,
        source_id=second.id,
        filename="synthetic.json",
        file_hash="0" * 64,
    )
    db_session.add(run)
    db_session.commit()
    db_session.add(
        Finding(
            workspace_id=workspace.id,
            source_id=first.id,
            import_run_id=run.id,
            source_finding_id="synthetic-source-mismatch",
            fingerprint="synthetic-source-mismatch",
            title="Synthetic source mismatch",
            severity=SeverityLevel.LOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("relationship", ["source", "asset", "exposure", "import_run"])
def test_relationship_assignment_does_not_rewrite_workspace(
    db_session: Session, relationship: str
) -> None:
    first, second = Workspace(name="Synthetic owner"), Workspace(name="Synthetic other")
    db_session.add_all([first, second])
    db_session.flush()
    own_source = Source(workspace_id=first.id, name="Owned source", source_type=SourceType.JSON)
    other_source = Source(workspace_id=second.id, name="Other source", source_type=SourceType.JSON)
    other_asset = Asset(
        workspace_id=second.id, canonical_key="host:other.example.test", name="Synthetic asset"
    )
    other_exposure = Exposure(
        workspace_id=second.id,
        canonical_fingerprint="synthetic-other",
        title="Synthetic other exposure",
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
    intended_workspace_id = first.id
    finding = Finding(
        workspace_id=intended_workspace_id,
        source_id=own_source.id,
        source_finding_id="synthetic-relationship",
        fingerprint="synthetic-relationship",
        title="Synthetic relationship mismatch",
        severity=SeverityLevel.LOW,
    )
    targets = {
        "source": other_source,
        "asset": other_asset,
        "exposure": other_exposure,
        "import_run": other_run,
    }
    setattr(finding, relationship, targets[relationship])
    db_session.add(finding)
    with pytest.raises(IntegrityError):
        db_session.flush()
    assert finding.workspace_id == intended_workspace_id
    db_session.rollback()
