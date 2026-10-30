from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant_models import Document, DocumentChunk, Tenant, User


class DocumentService:
    @staticmethod
    def create_document(db: Session, tenant_id: int, owner_user_id: int, filename: str, content: str, *, mime_type: str | None = None, metadata: dict | None = None) -> Document:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        owner = db.get(User, owner_user_id)
        if owner is None:
            raise ValueError("User not found")

        unique_suffix = uuid.uuid4().hex[:12]
        safe_filename = (
            f"{filename.rstrip('.txt')}-{unique_suffix}.txt"
            if filename.lower().endswith(".txt")
            else f"{filename}-{unique_suffix}"
        )
        storage_key = f"tenants/{tenant_id}/documents/{safe_filename}"
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

        document = Document(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            filename=safe_filename,
            storage_key=storage_key,
            mime_type=mime_type,
            status="uploaded",
            checksum_sha256=checksum,
            metadata_={**(metadata or {}), "source": "api"},
        )
        db.add(document)
        db.flush()

        text = content.strip()
        chunks = [chunk.strip() for chunk in text.splitlines() if chunk.strip()]
        if not chunks:
            chunks = [text]

        for index, chunk_text in enumerate(chunks):
            chunk = DocumentChunk(
                tenant_id=tenant_id,
                document_id=document.id,
                chunk_index=index,
                content=chunk_text,
                metadata_={"source": "manual", "length": len(chunk_text)},
            )
            db.add(chunk)

        document.status = "parsed"
        db.commit()
        db.refresh(document)
        return document

    @staticmethod
    def list_documents(db: Session, tenant_id: int) -> list[Document]:
        return db.execute(
            select(Document).where(Document.tenant_id == tenant_id).order_by(Document.created_at.desc())
        ).scalars().all()

    @staticmethod
    def get_document(db: Session, tenant_id: int, document_id: int) -> Document | None:
        return db.execute(
            select(Document)
            .where(Document.tenant_id == tenant_id, Document.id == document_id)
            .order_by(Document.created_at.desc())
        ).scalar_one_or_none()

    @staticmethod
    def list_chunks(db: Session, tenant_id: int, document_id: int) -> list[DocumentChunk]:
        return db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.tenant_id == tenant_id, DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
        ).scalars().all()
