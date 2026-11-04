"""Runs one ingestion job and records the outcome in PostgreSQL.

Extraction happens outside any open transaction, so a slow PDF never holds database
locks. The results are then saved in one transaction that replaces any earlier chunks,
which is why running a job again cannot produce duplicates or a half-saved document.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.document import (
    EMBEDDING_NOT_REQUIRED,
    EMBEDDING_PENDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    Document,
    utc_now,
)
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.services.documents import task_queue
from app.services.documents.processing import (
    ChunkData,
    chunk_sections,
    extract_sections,
    hash_extracted_text,
    join_sections,
)
from app.services.documents.storage import LocalDocumentStorage
from app.services.embeddings.service import EmbeddingService

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, db: Session):
        self.db = db

    def process_job(self, job_id: int) -> bool:
        """Return True if the job was processed, False if there was nothing to do."""
        claimed = self._claim(job_id)
        if claimed is None:
            return False
        document_id, tenant_id, team_id, stored_filename, extension = claimed

        file_path = LocalDocumentStorage().path_for(tenant_id, team_id, stored_filename)
        sections = extract_sections(file_path, extension)
        text = join_sections(sections)
        chunks = chunk_sections(sections, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

        self._save_results(
            job_id=job_id,
            document_id=document_id,
            text=text,
            chunks=chunks,
            metadata={
                # Sections with text: blank PDF pages are not counted.
                "section_count": len(sections),
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
            },
        )
        self._enqueue_embedding(document_id)
        return True

    def _enqueue_embedding(self, document_id: int) -> None:
        """A broker outage here must not fail the ingestion: the chunks are already safely saved."""
        status = self.db.scalar(select(Document.embedding_status).where(Document.id == document_id))
        self.db.rollback()
        if status == EMBEDDING_NOT_REQUIRED:
            return
        try:
            task_queue.enqueue_embedding_job(document_id)
        except Exception:
            logger.exception("Could not queue embeddings for document %s", document_id)
            EmbeddingService(self.db).mark_failed(document_id, "The processing queue is unavailable. Retry the embedding.")

    def _claim(self, job_id: int) -> tuple[int, int, int, str, str] | None:
        job = self.db.scalar(select(DocumentJob).where(DocumentJob.id == job_id).with_for_update())
        # A finished job can be delivered again by the broker. It must not run twice.
        if job is None or job.status in {STATUS_COMPLETED, STATUS_FAILED}:
            self.db.rollback()
            return None

        document = self.db.scalar(
            select(Document).where(
                Document.id == job.document_id,
                Document.tenant_id == job.tenant_id,
                Document.team_id == job.team_id,
            )
        )
        job.status = STATUS_PROCESSING
        job.attempts += 1
        job.error_message = None
        job.started_at = job.started_at or utc_now()
        document.status = STATUS_PROCESSING
        claimed = (
            document.id,
            document.tenant_id,
            document.team_id,
            document.stored_filename,
            document.file_extension,
        )
        self.db.commit()
        return claimed

    def _save_results(self, *, job_id: int, document_id: int, text: str, chunks: list[ChunkData], metadata: dict) -> None:
        job = self.db.scalar(select(DocumentJob).where(DocumentJob.id == job_id).with_for_update())
        document = self.db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if job.status == STATUS_COMPLETED:
            self.db.rollback()
            return

        text_hash = hash_extracted_text(text)
        duplicate_of_id = self._find_same_text_in_team(document, text_hash)

        self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        if duplicate_of_id is None:
            self.db.add_all(
                DocumentChunk(
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    team_id=document.team_id,
                    chunk_index=index,
                    chunk_text=chunk.text,
                    chunk_hash=chunk.hash,
                    metadata_json=chunk.metadata or None,
                )
                for index, chunk in enumerate(chunks)
            )

        document.extracted_text = text
        document.extracted_text_hash = text_hash
        document.duplicate_of_document_id = duplicate_of_id
        document.chunk_count = 0 if duplicate_of_id is not None else len(chunks)
        document.metadata_json = metadata
        document.status = STATUS_COMPLETED
        # Replacing chunks removed their vectors through the foreign key, so start over.
        document.embedding_status = EMBEDDING_NOT_REQUIRED if duplicate_of_id is not None else EMBEDDING_PENDING
        document.embedding_error = None
        document.embedded_at = None

        job.status = STATUS_COMPLETED
        job.error_message = None
        job.finished_at = utc_now()
        self.db.commit()

    def _find_same_text_in_team(self, document: Document, text_hash: str) -> int | None:
        """Same words in the same team (e.g. a PDF and a TXT copy): reuse the existing chunks.

        Scoped to one team on purpose. Reusing across teams would let one team's upload
        reveal what another team has stored.
        """
        return self.db.scalar(
            select(Document.id)
            .where(
                Document.tenant_id == document.tenant_id,
                Document.team_id == document.team_id,
                Document.extracted_text_hash == text_hash,
                Document.status == STATUS_COMPLETED,
                Document.duplicate_of_document_id.is_(None),
                Document.id != document.id,
            )
            .order_by(Document.id)
            .limit(1)
        )

    def mark_failed(self, job_id: int, message: str) -> None:
        self._set_outcome(job_id, STATUS_FAILED, message, finished=True)

    def mark_pending_for_retry(self, job_id: int, message: str) -> None:
        self._set_outcome(job_id, STATUS_PENDING, message, finished=False)

    def _set_outcome(self, job_id: int, status: str, message: str, *, finished: bool) -> None:
        job = self.db.scalar(select(DocumentJob).where(DocumentJob.id == job_id).with_for_update())
        if job is None:
            self.db.rollback()
            return
        document = self.db.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        job.status = status
        job.error_message = message
        job.finished_at = utc_now() if finished else None
        document.status = status
        self.db.commit()
