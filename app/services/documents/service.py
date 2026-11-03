from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.document import STATUS_FAILED, STATUS_PENDING, Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.security.authorization import visible_team_ids
from app.security.permissions import DOCUMENT_READ
from app.services.documents import task_queue
from app.services.documents.errors import DuplicateDocumentError, IngestionQueueError
from app.services.documents.storage import LocalDocumentStorage, StagedFile
from app.services.documents.validation import (
    sanitize_filename,
    validate_declared_mime,
    validate_extension,
    validate_file_content,
)


@dataclass(frozen=True)
class UploadResult:
    document: Document
    job: DocumentJob


class DocumentService:
    def __init__(self, db: Session):
        self.db = db
        self.storage = LocalDocumentStorage()

    def upload_document(self, *, tenant_id: int, team_id: int, user_id: int, file: UploadFile) -> UploadResult:
        """Validate, store and queue an upload. The caller has already authorized the user."""
        filename = sanitize_filename(file.filename)
        extension = validate_extension(filename)
        validate_declared_mime(extension, file.content_type)

        file.file.seek(0)
        staged = self.storage.stage(file.file, settings.max_upload_size_bytes)
        try:
            content_type = validate_file_content(staged.path, extension)
            existing = self._find_by_file_hash(tenant_id, team_id, staged.sha256)
            if existing is None:
                result = self._create_document(
                    tenant_id=tenant_id,
                    team_id=team_id,
                    user_id=user_id,
                    filename=filename,
                    extension=extension,
                    content_type=content_type,
                    staged=staged,
                )
            elif existing.status == STATUS_FAILED:
                result = self._retry_failed_document(existing, user_id, staged)
            else:
                raise DuplicateDocumentError(existing.id, existing.status)
        finally:
            self.storage.discard(staged)

        self._enqueue(result)
        return result

    def _find_by_file_hash(self, tenant_id: int, team_id: int, file_hash: str) -> Document | None:
        return self.db.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.team_id == team_id,
                Document.file_hash == file_hash,
            )
        )

    def _next_version(self, tenant_id: int, team_id: int, filename: str) -> int:
        latest = self.db.scalar(
            select(func.max(Document.version)).where(
                Document.tenant_id == tenant_id,
                Document.team_id == team_id,
                Document.original_filename == filename,
            )
        )
        return (latest or 0) + 1

    def _create_document(
        self,
        *,
        tenant_id: int,
        team_id: int,
        user_id: int,
        filename: str,
        extension: str,
        content_type: str,
        staged: StagedFile,
    ) -> UploadResult:
        stored_filename = f"{uuid.uuid4().hex}{extension}"
        self.storage.promote(staged, tenant_id, team_id, stored_filename)
        try:
            document = Document(
                tenant_id=tenant_id,
                team_id=team_id,
                uploaded_by_user_id=user_id,
                original_filename=filename,
                stored_filename=stored_filename,
                content_type=content_type,
                file_size=staged.size,
                file_hash=staged.sha256,
                file_extension=extension,
                status=STATUS_PENDING,
                version=self._next_version(tenant_id, team_id, filename),
                metadata_json={},
            )
            self.db.add(document)
            self.db.flush()
            job = self._new_job(document, user_id)
            self.db.commit()
        except IntegrityError as exc:
            # Two identical uploads raced and the unique constraint stopped the second one.
            self.db.rollback()
            self.storage.delete(tenant_id, team_id, stored_filename)
            winner = self._find_by_file_hash(tenant_id, team_id, staged.sha256)
            if winner is None:
                raise
            raise DuplicateDocumentError(winner.id, winner.status) from exc
        except Exception:
            self.db.rollback()
            self.storage.delete(tenant_id, team_id, stored_filename)
            raise
        return UploadResult(document=document, job=job)

    def _retry_failed_document(self, document: Document, user_id: int, staged: StagedFile) -> UploadResult:
        """Uploading a previously failed file again starts a new job on the same document."""
        if not self.storage.exists(document.tenant_id, document.team_id, document.stored_filename):
            self.storage.promote(staged, document.tenant_id, document.team_id, document.stored_filename)
        document.status = STATUS_PENDING
        job = self._new_job(document, user_id)
        self.db.commit()
        return UploadResult(document=document, job=job)

    def _new_job(self, document: Document, user_id: int) -> DocumentJob:
        job = DocumentJob(
            document_id=document.id,
            tenant_id=document.tenant_id,
            team_id=document.team_id,
            created_by_user_id=user_id,
            celery_task_id=uuid.uuid4().hex,
            status=STATUS_PENDING,
            attempts=0,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def _enqueue(self, result: UploadResult) -> None:
        # Runs after the commit, so a fast worker can never look for a row that isn't saved yet.
        try:
            task_queue.enqueue_ingestion_job(result.job.id, result.job.celery_task_id)
        except Exception as exc:
            message = "The processing queue is unavailable. Upload the file again to retry."
            result.job.status = STATUS_FAILED
            result.job.error_message = message
            result.document.status = STATUS_FAILED
            self.db.commit()
            raise IngestionQueueError(message) from exc

    def list_documents(
        self,
        *,
        user_id: int,
        tenant_id: int,
        team_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        stmt = select(Document).where(
            Document.tenant_id == tenant_id,
            Document.team_id.in_(visible_team_ids(user_id, DOCUMENT_READ)),
        )
        if team_id is not None:
            stmt = stmt.where(Document.team_id == team_id)
        if status is not None:
            stmt = stmt.where(Document.status == status)
        return list(self.db.scalars(stmt.order_by(Document.id.desc()).limit(limit).offset(offset)).all())

    def get_document(self, *, user_id: int, tenant_id: int, team_id: int, document_id: int) -> Document | None:
        """Scoped in the query, so a wrong tenant or team looks exactly like a missing document."""
        return self.db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.team_id == team_id,
                Document.team_id.in_(visible_team_ids(user_id, DOCUMENT_READ)),
            )
        )

    def get_document_file_path(self, document: Document) -> Path | None:
        path = self.storage.path_for(document.tenant_id, document.team_id, document.stored_filename)
        return path if path.is_file() else None

    def list_chunks(self, document: Document, *, limit: int = 100, offset: int = 0) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.tenant_id == document.tenant_id,
                DocumentChunk.team_id == document.team_id,
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.scalars(stmt).all())

    def list_jobs(
        self,
        *,
        user_id: int,
        tenant_id: int | None = None,
        team_id: int | None = None,
        document_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DocumentJob]:
        stmt = select(DocumentJob).where(DocumentJob.team_id.in_(visible_team_ids(user_id, DOCUMENT_READ)))
        if tenant_id is not None:
            stmt = stmt.where(DocumentJob.tenant_id == tenant_id)
        if team_id is not None:
            stmt = stmt.where(DocumentJob.team_id == team_id)
        if document_id is not None:
            stmt = stmt.where(DocumentJob.document_id == document_id)
        if status is not None:
            stmt = stmt.where(DocumentJob.status == status)
        return list(self.db.scalars(stmt.order_by(DocumentJob.id.desc()).limit(limit).offset(offset)).all())

    def get_job(self, *, user_id: int, job_id: int) -> DocumentJob | None:
        return self.db.scalar(
            select(DocumentJob).where(
                DocumentJob.id == job_id,
                DocumentJob.team_id.in_(visible_team_ids(user_id, DOCUMENT_READ)),
            )
        )
