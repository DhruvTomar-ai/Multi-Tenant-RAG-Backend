from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.document import utc_now

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.embedding import Embedding


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(nullable=False)
    tenant_id: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="chunks")
    embeddings: Mapped[list["Embedding"]] = relationship(back_populates="chunk", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        # A chunk must carry the same tenant and team as its parent document.
        ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE",
            name="fk_chunk_document_tenant_team",
        ),
        # A retried job can never leave two copies of the same chunk.
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
        # Target of the composite foreign key on embeddings.
        UniqueConstraint("id", "document_id", "tenant_id", "team_id", name="uq_chunk_id_document_tenant_team"),
        Index("ix_document_chunks_tenant_team", "tenant_id", "team_id"),
    )
