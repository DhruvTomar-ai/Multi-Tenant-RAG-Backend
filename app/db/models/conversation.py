from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.document import utc_now

CONVERSATION_ACTIVE = "active"
CONVERSATION_ARCHIVED = "archived"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# An assistant message is "completed" only once a validated answer exists.
MESSAGE_PENDING = "pending"
MESSAGE_COMPLETED = "completed"
MESSAGE_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
MESSAGE_FAILED = "failed"


class Conversation(Base):
    """Private to its creator. A team only narrows what retrieval may search."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=CONVERSATION_ACTIVE, server_default=CONVERSATION_ACTIVE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        # The team must belong to this tenant.
        ForeignKeyConstraint(["team_id", "tenant_id"], ["teams.id", "teams.tenant_id"], ondelete="CASCADE", name="fk_conversation_team_tenant"),
        UniqueConstraint("id", "tenant_id", name="uq_conversation_id_tenant"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_conversation_status"),
        Index("ix_conversations_tenant_creator_updated", "tenant_id", "created_by_user_id", "updated_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(nullable=False)
    tenant_id: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=MESSAGE_COMPLETED, nullable=False)
    # Why an assistant message is insufficient_evidence or failed.
    status_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The assistant message answering this user message.
    reply_to_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    # Sent by the client, so a retried request cannot create a second message.
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list, nullable=False)
    # Every document given to the LLM. The answer text may come from any of them.
    context_document_ids: Mapped[list[int]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"], ondelete="CASCADE", name="fk_message_conversation_tenant"),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_message_conversation_client_id"),
        UniqueConstraint("reply_to_message_id", name="uq_message_reply_to"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        CheckConstraint("status IN ('pending', 'completed', 'insufficient_evidence', 'failed')", name="ck_message_status"),
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )
