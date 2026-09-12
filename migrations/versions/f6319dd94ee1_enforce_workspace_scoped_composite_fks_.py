"""enforce workspace scoped composite fks and enum check constraints

Revision ID: f6319dd94ee1
Revises: 1c704bf9919a
Create Date: 2026-09-12 07:16:19.811095

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6319dd94ee1"
down_revision: str | None = "1c704bf9919a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ASSET_TYPE_VALUES = (
    "'host', 'ip', 'url', 'application', 'repository', 'container_image', "
    "'container', 'kubernetes_workload', 'cloud_resource', 'unknown'"
)
ASSET_ENVIRONMENT_VALUES = "'production', 'staging', 'development', 'test', 'unknown'"
SEVERITY_VALUES = "'informational', 'low', 'medium', 'high', 'critical'"
EXPOSURE_STATUS_VALUES = "'open', 'acknowledged', 'in_progress', 'risk_accepted', 'resolved'"
SOURCE_TYPE_VALUES = (
    "'csv', 'json', 'sarif', 'pentest_report', 'trivy', 'nuclei', 'semgrep', 'other'"
)
EXPOSURE_HISTORY_EVENT_TYPE_VALUES = (
    "'status_changed', 'acknowledged', 'owner_changed', 'remediation_date_changed', "
    "'remediation_changed', 'finding_correlated', 'reopened', 'resolved', 'note_added'"
)
IMPORT_STATUS_VALUES = "'pending', 'running', 'completed', 'failed'"


def _assert_legacy_data_is_consistent() -> None:
    """Refuse to upgrade if existing data would violate a constraint this
    migration is about to introduce.

    Checked with plain read-only ``SELECT``s *before* any DDL runs, rather
    than letting the new constraints reject bad rows mid-migration: SQLite's
    batch-mode ``ALTER TABLE`` rebuilds every affected table by creating a
    new one, copying rows across, dropping the old one and renaming it —
    if that sequence is interrupted partway through (as it would be by a
    constraint violation on table N of 6), what's left behind is not always
    a clean rollback (a dangling ``_alembic_tmp_*`` table can survive even
    though the real tables and data are correctly untouched). Validating
    everything up front means a database that fails this check has had
    exactly zero DDL executed against it: nothing to roll back, nothing to
    clean up, and one clear error message naming every actual problem
    instead of a generic constraint-failure exception on whichever table
    happened to be rebuilt first.
    """
    bind = op.get_bind()

    def count(sql: str) -> int:
        return bind.execute(sa.text(sql)).scalar_one()

    problems: list[str] = []

    if count(
        "SELECT COUNT(*) FROM findings f JOIN sources s ON f.source_id = s.id "
        "WHERE f.workspace_id != s.workspace_id"
    ):
        problems.append("findings.source_id references a source in a different workspace")

    if count(
        "SELECT COUNT(*) FROM findings f JOIN assets a ON f.asset_id = a.id "
        "WHERE f.workspace_id != a.workspace_id"
    ):
        problems.append("findings.asset_id references an asset in a different workspace")

    if count(
        "SELECT COUNT(*) FROM findings f JOIN exposures e ON f.exposure_id = e.id "
        "WHERE f.workspace_id != e.workspace_id"
    ):
        problems.append("findings.exposure_id references an exposure in a different workspace")

    if count(
        "SELECT COUNT(*) FROM findings f JOIN import_runs ir ON f.import_run_id = ir.id "
        "WHERE f.workspace_id != ir.workspace_id OR f.source_id != ir.source_id"
    ):
        problems.append(
            "findings.import_run_id references an import run in a different workspace, "
            "or one that belongs to a different source than the finding itself"
        )

    if count(
        "SELECT COUNT(*) FROM import_runs ir JOIN sources s ON ir.source_id = s.id "
        "WHERE ir.workspace_id != s.workspace_id"
    ):
        problems.append("import_runs.source_id references a source in a different workspace")

    enum_checks = (
        ("assets", "asset_type", ASSET_TYPE_VALUES),
        ("assets", "environment", ASSET_ENVIRONMENT_VALUES),
        ("exposures", "severity", SEVERITY_VALUES),
        ("exposures", "status", EXPOSURE_STATUS_VALUES),
        ("sources", "source_type", SOURCE_TYPE_VALUES),
        ("exposure_history", "event_type", EXPOSURE_HISTORY_EVENT_TYPE_VALUES),
        ("import_runs", "status", IMPORT_STATUS_VALUES),
        ("findings", "severity", SEVERITY_VALUES),
    )
    for table, column, allowed_values in enum_checks:
        if count(f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN ({allowed_values})"):  # noqa: S608
            problems.append(f"{table}.{column} contains a value outside {{{allowed_values}}}")

    if problems:
        details = "\n  - ".join(problems)
        raise RuntimeError(
            "Migration aborted before making any changes — existing data would "
            "violate a constraint this migration introduces:\n"
            f"  - {details}\n"
            "Fix the underlying data, then re-run the upgrade. No schema or data "
            "changes have been made."
        )


def upgrade() -> None:
    _assert_legacy_data_is_consistent()

    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_assets_workspace_id_id", ["workspace_id", "id"])
        # --- hand-added: enums were never actually enforced by the database
        # (SAEnum needs create_constraint=True) — see Milestone 1 review #4.
        batch_op.create_check_constraint("assettype", f"asset_type IN ({ASSET_TYPE_VALUES})")
        batch_op.create_check_constraint(
            "assetenvironment", f"environment IN ({ASSET_ENVIRONMENT_VALUES})"
        )

    with op.batch_alter_table("exposures", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_exposures_workspace_id_id", ["workspace_id", "id"])
        batch_op.create_check_constraint("severitylevel", f"severity IN ({SEVERITY_VALUES})")
        batch_op.create_check_constraint("exposurestatus", f"status IN ({EXPOSURE_STATUS_VALUES})")

    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_sources_workspace_id_id", ["workspace_id", "id"])
        batch_op.create_check_constraint("sourcetype", f"source_type IN ({SOURCE_TYPE_VALUES})")

    with op.batch_alter_table("exposure_history", schema=None) as batch_op:
        # event_type's CHECK now also includes 'remediation_changed', added
        # by this same migration's audit-trail fix (review #3) — there is no
        # data migration needed since no existing row can have this value.
        batch_op.create_check_constraint(
            "exposurehistoryeventtype",
            f"event_type IN ({EXPOSURE_HISTORY_EVENT_TYPE_VALUES})",
        )

    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_import_runs_workspace_id_source_id_id", ["workspace_id", "source_id", "id"]
        )
        batch_op.drop_constraint(batch_op.f("fk_import_runs_source_id_sources"), type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_import_runs_workspace_source",
            "sources",
            ["workspace_id", "source_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint("importstatus", f"status IN ({IMPORT_STATUS_VALUES})")

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_findings_asset_id_assets"), type_="foreignkey")
        batch_op.drop_constraint(
            batch_op.f("fk_findings_import_run_id_import_runs"), type_="foreignkey"
        )
        batch_op.drop_constraint(batch_op.f("fk_findings_source_id_sources"), type_="foreignkey")
        batch_op.drop_constraint(
            batch_op.f("fk_findings_exposure_id_exposures"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            "fk_findings_workspace_source",
            "sources",
            ["workspace_id", "source_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_findings_workspace_exposure",
            "exposures",
            ["workspace_id", "exposure_id"],
            ["workspace_id", "id"],
        )
        batch_op.create_foreign_key(
            "fk_findings_workspace_asset",
            "assets",
            ["workspace_id", "asset_id"],
            ["workspace_id", "id"],
        )
        batch_op.create_foreign_key(
            "fk_findings_workspace_source_import_run",
            "import_runs",
            ["workspace_id", "source_id", "import_run_id"],
            ["workspace_id", "source_id", "id"],
        )
        batch_op.create_check_constraint("severitylevel", f"severity IN ({SEVERITY_VALUES})")

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_findings_severitylevel"), type_="check")
        batch_op.drop_constraint("fk_findings_workspace_source_import_run", type_="foreignkey")
        batch_op.drop_constraint("fk_findings_workspace_asset", type_="foreignkey")
        batch_op.drop_constraint("fk_findings_workspace_exposure", type_="foreignkey")
        batch_op.drop_constraint("fk_findings_workspace_source", type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_findings_exposure_id_exposures"),
            "exposures",
            ["exposure_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_findings_source_id_sources"),
            "sources",
            ["source_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_findings_import_run_id_import_runs"),
            "import_runs",
            ["import_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_findings_asset_id_assets"),
            "assets",
            ["asset_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_import_runs_importstatus"), type_="check")
        batch_op.drop_constraint("fk_import_runs_workspace_source", type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_import_runs_source_id_sources"),
            "sources",
            ["source_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_constraint("uq_import_runs_workspace_id_source_id_id", type_="unique")

    with op.batch_alter_table("exposure_history", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_exposure_history_exposurehistoryeventtype"), type_="check"
        )

    with op.batch_alter_table("sources", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_sources_sourcetype"), type_="check")
        batch_op.drop_constraint("uq_sources_workspace_id_id", type_="unique")

    with op.batch_alter_table("exposures", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_exposures_exposurestatus"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_exposures_severitylevel"), type_="check")
        batch_op.drop_constraint("uq_exposures_workspace_id_id", type_="unique")

    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_assets_assetenvironment"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_assets_assettype"), type_="check")
        batch_op.drop_constraint("uq_assets_workspace_id_id", type_="unique")

    # ### end Alembic commands ###
