"""Generates and stores embeddings for one document, in batches.

Each batch is its own transaction and only reads chunks that have no vector yet, so a retry
resumes where the last attempt stopped and memory stays bounded by the batch size. Only the
embeddings table and the document's embedding_* columns are ever written here, so a failure
cannot damage the document or its chunks.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.document import (
    EMBEDDING_COMPLETED,
    EMBEDDING_FAILED,
    EMBEDDING_NOT_REQUIRED,
    EMBEDDING_PENDING,
    EMBEDDING_PROCESSING,
    STATUS_COMPLETED,
    Document,
    utc_now,
)
from app.db.models.document_chunk import DocumentChunk
from app.db.models.embedding import EMBEDDING_DIMENSION, Embedding
from app.services.embeddings import model as embedding_model


class EmbeddingService:
    def __init__(self, db: Session):
        self.db = db

    def embed_document(self, document_id: int) -> bool:
        """Return True if the document finished, False if there was nothing to do."""
        if not self._claim(document_id):
            return False

        identity = embedding_model.model_identity()
        if identity.dimension != EMBEDDING_DIMENSION:
            raise embedding_model.EmbeddingDimensionError(f"Configured dimension {identity.dimension} does not match the {EMBEDDING_DIMENSION}-dimension column")

        while True:
            batch = self._next_batch(document_id, identity.name, identity.version)
            if not batch:
                break
            vectors = embedding_model.encode_documents([row.chunk_text for row in batch])
            self._store_batch(batch, vectors, identity)

        self._finish(document_id, identity.name, identity.version)
        return True

    def _claim(self, document_id: int) -> bool:
        document = self.db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is None or document.status != STATUS_COMPLETED or document.embedding_status in {EMBEDDING_COMPLETED, EMBEDDING_NOT_REQUIRED}:
            self.db.rollback()
            return False
        document.embedding_status = EMBEDDING_PROCESSING
        document.embedding_attempts += 1
        document.embedding_error = None
        self.db.commit()
        return True

    def _next_batch(self, document_id: int, model_name: str, model_version: str):
        """Chunks without a vector for this model. Keyed on the primary key, so no OFFSET scans."""
        has_vector = (
            select(Embedding.id)
            .where(Embedding.chunk_id == DocumentChunk.id, Embedding.model_name == model_name, Embedding.model_version == model_version)
            .exists()
        )
        rows = self.db.execute(
            select(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.tenant_id, DocumentChunk.team_id, DocumentChunk.chunk_text)
            .where(DocumentChunk.document_id == document_id, ~has_vector)
            .order_by(DocumentChunk.id)
            .limit(settings.embedding_batch_size)
        ).all()
        self.db.rollback()
        return rows

    def _store_batch(self, batch, vectors, identity) -> None:
        now = utc_now()
        values = [
            {
                "chunk_id": row.id,
                "document_id": row.document_id,
                "tenant_id": row.tenant_id,
                "team_id": row.team_id,
                "model_name": identity.name,
                "model_version": identity.version,
                "dimension": len(vector),
                "embedding": vector.tolist(),
                "created_at": now,
            }
            for row, vector in zip(batch, vectors, strict=True)
        ]
        # A duplicate delivery may race this one. The unique constraint decides the winner.
        statement = insert(Embedding).values(values).on_conflict_do_nothing(constraint="uq_embedding_chunk_model_version")
        self.db.execute(statement)
        self.db.commit()

    def _finish(self, document_id: int, model_name: str, model_version: str) -> None:
        document = self.db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        chunk_total = self.db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document_id))
        stored = self.db.scalar(
            select(func.count()).select_from(Embedding).where(
                Embedding.document_id == document_id, Embedding.model_name == model_name, Embedding.model_version == model_version
            )
        )
        if stored != chunk_total:
            self.db.rollback()
            raise RuntimeError(f"Document {document_id} has {chunk_total} chunks but {stored} embeddings")
        document.embedding_status = EMBEDDING_COMPLETED
        document.embedding_error = None
        document.embedded_at = utc_now()
        self.db.commit()

    def mark_failed(self, document_id: int, message: str) -> None:
        self._set_status(document_id, EMBEDDING_FAILED, message)

    def mark_pending_for_retry(self, document_id: int, message: str) -> None:
        self._set_status(document_id, EMBEDDING_PENDING, message)

    def _set_status(self, document_id: int, status: str, message: str) -> None:
        document = self.db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is None:
            self.db.rollback()
            return
        document.embedding_status = status
        document.embedding_error = message
        self.db.commit()
