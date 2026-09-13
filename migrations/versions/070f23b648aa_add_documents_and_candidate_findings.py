"""add documents and candidate findings

Milestone 6 (docs/pentest-and-ai.md): the local PDF/text extraction and
candidate-finding review framework. Both tables are brand new, so none of
Milestone 4's deferred-column hazard applies here — no historical
migration selects a table that doesn't exist yet.

Revision ID: 070f23b648aa
Revises: 0f2f55660e01
Create Date: 2026-09-12 20:13:05.859531

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from threnda.app.models.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "070f23b648aa"
down_revision: str | None = "0f2f55660e01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "content_type",
            sa.Enum(
                "pdf",
                "text",
                name="documentcontenttype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("file_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processed",
                "failed",
                name="documentstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["sources.workspace_id", "sources.id"],
            name="fk_documents_workspace_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_documents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id_id"),
    )
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_documents_file_hash"), ["file_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_source_id"), ["source_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_documents_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "candidate_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "rejected",
                name="candidatefindingstatus",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("extracted_title", sa.String(length=500), nullable=False),
        sa.Column(
            "extracted_severity",
            sa.Enum(
                "informational",
                "low",
                "medium",
                "high",
                "critical",
                name="severitylevel",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("ai_assisted", sa.Boolean(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.String(length=200), nullable=True),
        sa.Column("resulting_finding_id", sa.Integer(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_candidate_findings_workspace_document",
            ondelete="CASCADE",
        ),
        # Set only once a candidate is accepted (see
        # threnda.app.services.documents.review); no cascade delete —
        # findings/history rows are never deleted anywhere in this schema.
        sa.ForeignKeyConstraint(
            ["workspace_id", "resulting_finding_id"],
            ["findings.workspace_id", "findings.id"],
            name="fk_candidate_findings_workspace_finding",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_candidate_findings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_findings")),
    )
    with op.batch_alter_table("candidate_findings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_candidate_findings_document_id"), ["document_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_candidate_findings_workspace_id"), ["workspace_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate_findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_findings_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_candidate_findings_document_id"))

    op.drop_table("candidate_findings")

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_documents_source_id"))
        batch_op.drop_index(batch_op.f("ix_documents_file_hash"))

    op.drop_table("documents")
