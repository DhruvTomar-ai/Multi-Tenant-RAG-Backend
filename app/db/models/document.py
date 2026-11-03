from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.document_chunk import DocumentChunk
    from app.db.models.document_job import DocumentJob
    from app.db.models.user import User

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(nullable=False, index=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    chunk_count: Mapped[int] = mapped_column(default=0, nullable=False)
    duplicate_of_document_id: Mapped[int | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    # Deferred: can be megabytes of text, so it is only loaded when explicitly read.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    uploaded_by: Mapped["User"] = relationship(back_populates="uploaded_documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.chunk_index",
    )
    jobs: Mapped[list["DocumentJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # The team must belong to this tenant.
        ForeignKeyConstraint(
            ["team_id", "tenant_id"],
            ["teams.id", "teams.tenant_id"],
            ondelete="CASCADE",
            name="fk_document_team_tenant",
        ),
        # A document can only be marked as a duplicate of a document in the same team.
        ForeignKeyConstraint(
            ["duplicate_of_document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            name="fk_document_duplicate_same_team",
        ),
        UniqueConstraint("tenant_id", "team_id", "stored_filename", name="uq_document_tenant_team_stored_filename"),
        # Duplicate detection is scoped to one team, so it can never reveal another team's files.
        UniqueConstraint("tenant_id", "team_id", "file_hash", name="uq_document_tenant_team_file_hash"),
        # Target of the composite foreign keys on chunks and jobs.
        UniqueConstraint("id", "tenant_id", "team_id", name="uq_document_id_tenant_team"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_document_status"),
        CheckConstraint("file_size > 0", name="ck_document_file_size_positive"),
        Index("ix_documents_tenant_team_status", "tenant_id", "team_id", "status"),
        Index("ix_documents_tenant_team_text_hash", "tenant_id", "team_id", "extracted_text_hash"),
    )
