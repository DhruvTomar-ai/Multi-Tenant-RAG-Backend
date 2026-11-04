from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.db.base import Base
from app.db.models.document import utc_now

if TYPE_CHECKING:
    from app.db.models.document_chunk import DocumentChunk

# Fixed by the column type. Changing it requires a migration and re-embedding every chunk.
EMBEDDING_DIMENSION = 384
HNSW_INDEX_NAME = "ix_embeddings_embedding_hnsw"
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chunk_id: Mapped[int] = mapped_column(nullable=False)
    document_id: Mapped[int] = mapped_column(nullable=False)
    tenant_id: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dimension: Mapped[int] = mapped_column(nullable=False)
    # Deferred: metadata queries must never load vectors by accident.
    embedding: Mapped[list[float]] = deferred(mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    chunk: Mapped["DocumentChunk"] = relationship(back_populates="embeddings")

    __table_args__ = (
        # An embedding must carry the same document, tenant and team as its chunk.
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "tenant_id", "team_id"],
            ["document_chunks.id", "document_chunks.document_id", "document_chunks.tenant_id", "document_chunks.team_id"],
            ondelete="CASCADE",
            name="fk_embedding_chunk_document_tenant_team",
        ),
        # A retried job can never store a second vector for the same chunk and model.
        UniqueConstraint("chunk_id", "model_name", "model_version", name="uq_embedding_chunk_model_version"),
        CheckConstraint(f"dimension = {EMBEDDING_DIMENSION}", name="ck_embedding_dimension"),
        Index("ix_embeddings_tenant_team", "tenant_id", "team_id"),
        Index("ix_embeddings_document", "document_id"),
        Index(
            HNSW_INDEX_NAME,
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
