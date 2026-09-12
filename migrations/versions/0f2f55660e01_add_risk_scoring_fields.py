"""add risk scoring fields

Milestone 4 (docs/community-v0.1-spec.md, "RISK SCORING") needs two inputs
that have no home in the schema yet: "internet exposure if known" and
"manual business criticality" — both asset-level, both never set by any
importer (no public CSV/JSON/SARIF format carries either concept, and
criticality is explicitly a human judgement call), so both start out
NULL/unknown and are only ever set through the new
``PATCH /api/v1/assets/{id}`` endpoint. ``findings.exploit_evidence`` is
the third scoring input added here — also never known unless explicitly
reported, but *can* come from data: Threnda's own generic JSON importer
gained one new optional key for it (docs/importers.md).

See threnda.app.services.risk_scoring for how these three feed the score.

Revision ID: 0f2f55660e01
Revises: 32832b96f2c3
Create Date: 2026-09-12 19:27:25.137361

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f2f55660e01"
down_revision: str | None = "32832b96f2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("internet_facing", sa.Boolean(), nullable=True))
        # server_default is required, not cosmetic: `assets` can already
        # have rows (SQLite and PostgreSQL both reject adding a NOT NULL
        # column with no default to a non-empty table), and 'unknown' is
        # exactly the right value semantically too — see AssetCriticality.
        batch_op.add_column(
            sa.Column(
                "business_criticality",
                sa.Enum(
                    "unknown",
                    "low",
                    "medium",
                    "high",
                    "critical",
                    name="assetcriticality",
                    native_enum=False,
                    create_constraint=True,
                    length=50,
                ),
                nullable=False,
                server_default="unknown",
            )
        )

    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("exploit_evidence", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_column("exploit_evidence")

    with op.batch_alter_table("assets", schema=None) as batch_op:
        # The CHECK constraint from sa_enum()'s create_constraint=True must
        # be dropped explicitly before/with the column — SQLite's batch
        # mode otherwise carries it into the rebuilt table's DDL even
        # though the column it references is gone (see every other enum
        # column drop in f6319dd94ee1 for the same pattern).
        batch_op.drop_constraint(batch_op.f("ck_assets_assetcriticality"), type_="check")
        batch_op.drop_column("business_criticality")
        batch_op.drop_column("internet_facing")
