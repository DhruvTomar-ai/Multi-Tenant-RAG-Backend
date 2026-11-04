"""workflow 6: explicit document read grants, and an index for finding the latest version

Revision ID: d4b7f2a91c36
Revises: c3e8a1f47d20
Create Date: 2026-11-04 14:00:00

Additive: no existing column or row changes. The HNSW index is not touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b7f2a91c36"
down_revision: Union[str, None] = "c3e8a1f47d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE", name="fk_document_grant_document_tenant_team",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            ondelete="CASCADE", name="fk_document_grant_tenant_membership",
        ),
        sa.UniqueConstraint("document_id", "user_id", name="uq_document_grant_document_user"),
    )
    op.create_index(op.f("ix_document_grants_id"), "document_grants", ["id"], unique=False)
    op.create_index("ix_document_grants_user_tenant", "document_grants", ["user_id", "tenant_id"], unique=False)
    op.create_index("ix_documents_tenant_team_filename_version", "documents", ["tenant_id", "team_id", "original_filename", "version"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_tenant_team_filename_version", table_name="documents")
    op.drop_table("document_grants")
