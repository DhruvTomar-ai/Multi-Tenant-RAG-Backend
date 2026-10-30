from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.models.tenant_models import Document, DocumentChunk, User, UserTenantMembership
from app.schemas.document import DocumentCreate, DocumentDetailRead, DocumentChunkRead, DocumentRead
from app.services.document_service import DocumentService

router = APIRouter(prefix="/api/v1", tags=["documents"])


@router.post("/tenants/{tenant_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(tenant_id: int, payload: DocumentCreate, db: Session = Depends(get_db)) -> Document:
    owner_user_id = db.execute(
        text(
            """
            SELECT user_id
            FROM user_tenant_memberships
            WHERE tenant_id = :tenant_id AND role = 'owner'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    ).scalar_one_or_none()

    if owner_user_id is None:
        raise HTTPException(status_code=404, detail="No owner found for tenant")

    try:
        return DocumentService.create_document(
            db,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            filename=f"document-{tenant_id}-{owner_user_id}.txt",
            content="This is a sample document body for the tenant. Replace with your actual ingested file content.",
            mime_type="text/plain",
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tenants/{tenant_id}/documents", response_model=list[DocumentRead])
def list_documents(tenant_id: int, db: Session = Depends(get_db)) -> list[Document]:
    return DocumentService.list_documents(db, tenant_id)


@router.get("/tenants/{tenant_id}/documents/{document_id}", response_model=DocumentDetailRead)
def get_document(tenant_id: int, document_id: int, db: Session = Depends(get_db)) -> Document:
    document = DocumentService.get_document(db, tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    chunks = DocumentService.list_chunks(db, tenant_id, document_id)
    return DocumentDetailRead(
        id=document.id,
        tenant_id=document.tenant_id,
        owner_user_id=document.owner_user_id,
        filename=document.filename,
        storage_key=document.storage_key,
        mime_type=document.mime_type,
        status=document.status,
        checksum_sha256=document.checksum_sha256,
        metadata=document.metadata_,
        created_at=document.created_at,
        updated_at=document.updated_at,
        chunks=[
            DocumentChunkRead(
                id=chunk.id,
                tenant_id=chunk.tenant_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                metadata=chunk.metadata_,
                created_at=chunk.created_at,
            )
            for chunk in chunks
        ],
    )


@router.get("/tenants/{tenant_id}/documents/{document_id}/chunks", response_model=list[DocumentChunkRead])
def list_document_chunks(tenant_id: int, document_id: int, db: Session = Depends(get_db)) -> list[DocumentChunk]:
    return DocumentService.list_chunks(db, tenant_id, document_id)
