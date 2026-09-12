"""add differential engine and dedup review fields

Revision ID: 32832b96f2c3
Revises: f6319dd94ee1
Create Date: 2026-09-12 08:20:36.963735

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

from threnda.app.models.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "32832b96f2c3"
down_revision: str | None = "f6319dd94ee1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Identifies ExposureHistory/PossibleDuplicate rows this migration's backfill
# created, distinct from a live import's default "system" actor.
_BACKFILL_ACTOR = f"migration:{revision}"


def _assert_no_duplicate_source_finding_ids() -> None:
    """Refuse to upgrade if existing data would violate the new
    ``uq_findings_workspace_source_finding_id`` constraint.

    Milestone 2's importer, and the first cut of Milestone 3's differential
    engine, both matched "the same finding, seen again" by ``fingerprint``
    rather than the source-local ``source_finding_id`` — which meant a
    content edit that changes the fingerprint (e.g. a title rename with a
    stable JSON/SARIF id, or any CSV row re-exported with a changed field)
    looked like "the old finding disappeared and a new one appeared",
    leaving two ``Finding`` rows for the same source-local identity: one
    resolved, one active (see docs/milestone-3-review.md, finding 2). A
    database that hit that bug before this migration could already have
    more than one finding sharing a (workspace_id, source_id,
    source_finding_id), which this migration's new unique constraint would
    reject. Checked with a plain read-only query *before* any DDL runs, for
    the same reason as the equivalent check in this migration's previous
    revision: a database that fails this check has had zero DDL executed
    against it. This migration does not attempt to automatically merge or
    pick a winner between such rows — that would mean inventing merge
    semantics the spec reserves for human review (POSSIBLE matches are
    never auto-merged), not something a schema migration should decide.
    """
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT workspace_id, source_id, source_finding_id, COUNT(*) AS c "
            "FROM findings GROUP BY workspace_id, source_id, source_finding_id "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            f"Migration aborted before making any changes — {len(duplicates)} "
            "(workspace, source, source_finding_id) group(s) already have more than "
            "one finding row, which the new uq_findings_workspace_source_finding_id "
            "constraint would reject. This can happen if a finding's identifying "
            "content (e.g. its title) was edited and re-imported before this "
            "migration existed. Resolve or merge the duplicate findings manually, "
            "then re-run the upgrade. No schema changes have been made."
        )


def _backfill_legacy_findings_into_the_queue() -> None:
    """Give every pre-Milestone-3 unlinked finding a way into the queue.

    Before this migration, nothing ever ran an existing ``Finding`` through
    correlation unless it was created by the Milestone 3 differential
    engine itself — a database populated under Milestone 1/2 could have
    findings with ``exposure_id IS NULL`` that no ``GET /exposures`` call
    and no review queue would ever surface (docs/milestone-3-review.md,
    finding 1). This runs those rows through the same
    ``correlate_new_finding`` used for a live import's genuinely new
    findings: deterministic EXACT/HIGH matches auto-correlate, uncertain
    POSSIBLE matches create a review candidate and are never auto-merged,
    and a finding with no signal at all gets its own standalone exposure —
    identical, safe rules, not a special "migration mode".

    Idempotent by construction: only findings with ``exposure_id IS NULL``
    and no existing ``PossibleDuplicate`` row are considered, so a finding
    already linked (by a prior run of this backfill, or by anything else)
    or already sitting in the review queue is never touched or re-flagged
    a second time. No existing link is ever overwritten — this only ever
    acts on findings that have none.

    Runs after all of this migration's DDL, on the same connection/
    transaction Alembic already opened for the upgrade — ``session.flush()``
    only, deliberately never ``session.commit()``, so this remains part of
    the single transaction Alembic commits (or rolls back as a whole) for
    the migration; see migrations/env.py's ``context.begin_transaction()``.
    Reuses the application's real ORM models and correlation service rather
    than reimplementing the same matching logic in raw SQL — safe here
    specifically because this step runs at the tail end of the same
    migration that brings the schema to exactly the shape those models
    already assume.
    """
    from threnda.app.models.finding import Finding
    from threnda.app.models.possible_duplicate import PossibleDuplicate
    from threnda.app.services.dedup.correlation import correlate_new_finding

    session = Session(bind=op.get_bind())
    already_flagged = sa.select(PossibleDuplicate.finding_id)
    unlinked = (
        session.execute(
            sa.select(Finding)
            .where(Finding.exposure_id.is_(None))
            .where(Finding.id.not_in(already_flagged))
            .order_by(Finding.id)
        )
        .scalars()
        .all()
    )
    for finding in unlinked:
        correlate_new_finding(session, finding, actor=_BACKFILL_ACTOR)
    session.flush()


def upgrade() -> None:
    _assert_no_duplicate_source_finding_ids()

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "possible_duplicates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("candidate_exposure_id", sa.Integer(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "merged",
                "kept_separate",
                name="possibleduplicatestatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "candidate_exposure_id"],
            ["exposures.workspace_id", "exposures.id"],
            name="fk_possible_duplicates_workspace_candidate_exposure",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "finding_id"],
            ["findings.workspace_id", "findings.id"],
            name="fk_possible_duplicates_workspace_finding",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_possible_duplicates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_possible_duplicates")),
    )
    with op.batch_alter_table("possible_duplicates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_possible_duplicates_candidate_exposure_id"),
            ["candidate_exposure_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_possible_duplicates_finding_id"), ["finding_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_possible_duplicates_workspace_id"), ["workspace_id"], unique=False
        )

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolved_at", UTCDateTime(), nullable=True))
        batch_op.create_unique_constraint("uq_findings_workspace_id_id", ["workspace_id", "id"])
        # Keyed on source_finding_id (the source-local identity), not
        # fingerprint (the cross-source correlation signal, which is
        # allowed to change with identifying content) — see
        # _assert_no_duplicate_source_finding_ids and finding.py.
        batch_op.create_unique_constraint(
            "uq_findings_workspace_source_finding_id",
            ["workspace_id", "source_id", "source_finding_id"],
        )

    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        # server_default='0' is required, not cosmetic: this table can
        # already have rows from before Milestone 3 existed, and SQLite (like
        # PostgreSQL) rejects adding a NOT NULL column with no default to a
        # non-empty table. 0 is exactly right semantically too — "the
        # differential breakdown was not computed for imports run before this
        # migration existed", not a fabricated real count.
        batch_op.add_column(
            sa.Column("new_findings", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("unchanged_findings", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("updated_findings", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("resolved_findings", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("reopened_findings", sa.Integer(), nullable=False, server_default="0")
        )

    # ### end Alembic commands ###

    _backfill_legacy_findings_into_the_queue()


def downgrade() -> None:
    # Schema-only reversal, like every other migration here: any Exposure/
    # ExposureHistory/PossibleDuplicate rows the backfill created are left
    # in place rather than deleted — those tables/columns aren't touched by
    # this downgrade, and the spec requires findings/history to never be
    # deleted regardless of what a later downgrade does to unrelated schema.

    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        batch_op.drop_column("reopened_findings")
        batch_op.drop_column("resolved_findings")
        batch_op.drop_column("updated_findings")
        batch_op.drop_column("unchanged_findings")
        batch_op.drop_column("new_findings")

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_constraint("uq_findings_workspace_source_finding_id", type_="unique")
        batch_op.drop_constraint("uq_findings_workspace_id_id", type_="unique")
        batch_op.drop_column("resolved_at")

    with op.batch_alter_table("possible_duplicates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_possible_duplicates_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_possible_duplicates_finding_id"))
        batch_op.drop_index(batch_op.f("ix_possible_duplicates_candidate_exposure_id"))

    op.drop_table("possible_duplicates")
    # ### end Alembic commands ###
