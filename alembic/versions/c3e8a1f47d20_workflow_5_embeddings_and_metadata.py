"""workflow 5: embeddings table, HNSW index and document metadata

Revision ID: c3e8a1f47d20
Revises: b7d41c2e9a10
Create Date: 2026-11-04 12:00:00

Additive: no existing column or row is dropped.

Data changes on existing rows:
- Completed documents with chunks get embedding_status = 'pending', so the backfill command
  can find them. Completed documents that reuse another document's chunks get 'not_required'.

The HNSW index is built on an empty table here, so this takes no noticeable lock time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "c3e8a1f47d20"
down_revision: Union[str, None] = "b7d41c2e9a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("documents", sa.Column("document_type", sa.String(length=100), nullable=True))
    op.add_column("documents", sa.Column("department", sa.String(length=100), nullable=True))
    op.add_column("documents", sa.Column("category", sa.String(length=100), nullable=True))
    op.add_column("documents", sa.Column("source", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("access_level", sa.String(length=20), server_default="team", nullable=False))
    op.add_column("documents", sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), server_default=sa.text("'{}'::varchar[]"), nullable=False))
    op.add_column("documents", sa.Column("custom_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("documents", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("embedding_status", sa.String(length=20), server_default="not_started", nullable=False))
    op.add_column("documents", sa.Column("embedding_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("embedding_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE documents SET embedding_status = 'pending' WHERE status = 'completed' AND chunk_count > 0")
    op.execute("UPDATE documents SET embedding_status = 'not_required' WHERE status = 'completed' AND duplicate_of_document_id IS NOT NULL")

    op.create_check_constraint("ck_document_access_level", "documents", "access_level IN ('team', 'restricted', 'confidential')")
    op.create_check_constraint(
        "ck_document_embedding_status", "documents",
        "embedding_status IN ('not_started', 'pending', 'processing', 'completed', 'failed', 'not_required')",
    )
    op.create_index("ix_documents_tenant_document_type", "documents", ["tenant_id", "document_type"], unique=False)
    op.create_index("ix_documents_tenant_department", "documents", ["tenant_id", "department"], unique=False)
    op.create_index("ix_documents_tenant_category", "documents", ["tenant_id", "category"], unique=False)
    op.create_index("ix_documents_tenant_access_level", "documents", ["tenant_id", "access_level"], unique=False)
    op.create_index("ix_documents_tenant_file_extension", "documents", ["tenant_id", "file_extension"], unique=False)
    op.create_index("ix_documents_tenant_team_embedding_status", "documents", ["tenant_id", "team_id", "embedding_status"], unique=False)
    op.create_index("ix_documents_tags_gin", "documents", ["tags"], unique=False, postgresql_using="gin")
    op.create_index("ix_documents_custom_metadata_gin", "documents", ["custom_metadata"], unique=False, postgresql_using="gin", postgresql_ops={"custom_metadata": "jsonb_path_ops"})

    op.create_unique_constraint("uq_chunk_id_document_tenant_team", "document_chunks", ["id", "document_id", "tenant_id", "team_id"])

    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_id", "tenant_id", "team_id"],
            ["document_chunks.id", "document_chunks.document_id", "document_chunks.tenant_id", "document_chunks.team_id"],
            ondelete="CASCADE", name="fk_embedding_chunk_document_tenant_team",
        ),
        sa.UniqueConstraint("chunk_id", "model_name", "model_version", name="uq_embedding_chunk_model_version"),
        sa.CheckConstraint("dimension = 384", name="ck_embedding_dimension"),
    )
    op.create_index(op.f("ix_embeddings_id"), "embeddings", ["id"], unique=False)
    op.create_index("ix_embeddings_tenant_team", "embeddings", ["tenant_id", "team_id"], unique=False)
    op.create_index("ix_embeddings_document", "embeddings", ["document_id"], unique=False)
    op.create_index(
        "ix_embeddings_embedding_hnsw", "embeddings", ["embedding"], unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.drop_constraint("uq_chunk_id_document_tenant_team", "document_chunks", type_="unique")

    op.drop_index("ix_documents_custom_metadata_gin", table_name="documents")
    op.drop_index("ix_documents_tags_gin", table_name="documents")
    op.drop_index("ix_documents_tenant_team_embedding_status", table_name="documents")
    op.drop_index("ix_documents_tenant_file_extension", table_name="documents")
    op.drop_index("ix_documents_tenant_access_level", table_name="documents")
    op.drop_index("ix_documents_tenant_category", table_name="documents")
    op.drop_index("ix_documents_tenant_department", table_name="documents")
    op.drop_index("ix_documents_tenant_document_type", table_name="documents")
    op.drop_constraint("ck_document_embedding_status", "documents", type_="check")
    op.drop_constraint("ck_document_access_level", "documents", type_="check")

    for column in (
        "embedded_at", "embedding_attempts", "embedding_error", "embedding_status", "deleted_at", "is_active",
        "custom_metadata", "tags", "access_level", "source", "category", "department", "document_type",
    ):
        op.drop_column("documents", column)
    # The vector extension stays: other databases objects may depend on it.
