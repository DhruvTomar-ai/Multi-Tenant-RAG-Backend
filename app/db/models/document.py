from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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

EMBEDDING_NOT_STARTED = "not_started"
EMBEDDING_PENDING = "pending"
EMBEDDING_PROCESSING = "processing"
EMBEDDING_COMPLETED = "completed"
EMBEDDING_FAILED = "failed"
# A document that reuses another document's chunks has nothing of its own to embed.
EMBEDDING_NOT_REQUIRED = "not_required"

# A descriptive label for filtering. Team membership alone decides who can read a document.
ACCESS_LEVEL_TEAM = "team"
ACCESS_LEVELS = (ACCESS_LEVEL_TEAM, "restricted", "confidential")


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
    # System-written facts about processing, such as chunk settings. Users never write this.
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    # Deferred: can be megabytes of text, so it is only loaded when explicitly read.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)

    # User-supplied descriptive metadata. It filters results but never grants access.
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_level: Mapped[str] = mapped_column(String(20), default=ACCESS_LEVEL_TEAM, server_default=ACCESS_LEVEL_TEAM, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON().with_variant(ARRAY(String(64)), "postgresql"), default=list, nullable=False)
    custom_metadata: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict, nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True, server_default=true(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    embedding_status: Mapped[str] = mapped_column(String(20), default=EMBEDDING_NOT_STARTED, server_default=EMBEDDING_NOT_STARTED, nullable=False)
    embedding_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
        CheckConstraint("access_level IN ('team', 'restricted', 'confidential')", name="ck_document_access_level"),
        CheckConstraint(
            "embedding_status IN ('not_started', 'pending', 'processing', 'completed', 'failed', 'not_required')",
            name="ck_document_embedding_status",
        ),
        Index("ix_documents_tenant_team_status", "tenant_id", "team_id", "status"),
        Index("ix_documents_tenant_team_text_hash", "tenant_id", "team_id", "extracted_text_hash"),
        # Lets search find a newer version of a document without scanning the team's documents.
        Index("ix_documents_tenant_team_filename_version", "tenant_id", "team_id", "original_filename", "version"),
        # Every filter is scoped to a tenant, so tenant_id leads each index.
        Index("ix_documents_tenant_document_type", "tenant_id", "document_type"),
        Index("ix_documents_tenant_department", "tenant_id", "department"),
        Index("ix_documents_tenant_category", "tenant_id", "category"),
        Index("ix_documents_tenant_access_level", "tenant_id", "access_level"),
        Index("ix_documents_tenant_file_extension", "tenant_id", "file_extension"),
        Index("ix_documents_tenant_team_embedding_status", "tenant_id", "team_id", "embedding_status"),
        Index("ix_documents_tags_gin", "tags", postgresql_using="gin"),
        Index("ix_documents_custom_metadata_gin", "custom_metadata", postgresql_using="gin", postgresql_ops={"custom_metadata": "jsonb_path_ops"}),
    )
