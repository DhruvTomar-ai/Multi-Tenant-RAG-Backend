"""workflow 7: conversations and messages

Revision ID: e5c9a3d7b210
Revises: d4b7f2a91c36
Create Date: 2026-11-04 16:00:00

Additive: two new tables. No existing table or row changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5c9a3d7b210"
down_revision: Union[str, None] = "d4b7f2a91c36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id", "tenant_id"], ["teams.id", "teams.tenant_id"], ondelete="CASCADE", name="fk_conversation_team_tenant"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_conversation_id_tenant"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_conversation_status"),
    )
    op.create_index(op.f("ix_conversations_id"), "conversations", ["id"], unique=False)
    op.create_index("ix_conversations_tenant_creator_updated", "conversations", ["tenant_id", "created_by_user_id", "updated_at"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("status_reason", sa.String(length=50), nullable=True),
        sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("context_document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"], ondelete="CASCADE", name="fk_message_conversation_tenant"),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_message_conversation_client_id"),
        sa.UniqueConstraint("reply_to_message_id", name="uq_message_reply_to"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        sa.CheckConstraint("status IN ('pending', 'completed', 'insufficient_evidence', 'failed')", name="ck_message_status"),
    )
    op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"], unique=False)


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
