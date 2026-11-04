from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_document_read, require_team_permission, require_tenant_membership
from app.db.models.document import Document
from app.db.models.user import User
from app.schemas.document import (
    DocumentChunkResponse,
    DocumentEmbeddingsResponse,
    DocumentGrantCreate,
    DocumentGrantRead,
    DocumentJobResponse,
    DocumentMetadataResponse,
    DocumentRead,
    DocumentStatusResponse,
    DocumentTextResponse,
    DocumentUploadResponse,
    EmbeddingMetadataItem,
    EmbeddingRetryResponse,
)
from app.security.permissions import DOCUMENT_READ, DOCUMENT_UPLOAD, TEAM_MANAGE
from app.services.documents.errors import DuplicateDocumentError, EmbeddingRetryConflict, IngestionQueueError, UploadValidationError
from app.services.documents.grants import DocumentGrantService, GrantTargetNotAllowed
from app.services.documents.metadata import MetadataValidationError, build_metadata_input, parse_custom_metadata, parse_tags, validate_access_level
from app.services.documents.service import DocumentService
from app.services.embeddings.search import SearchFilters

router = APIRouter(prefix="/api/v1/tenants", tags=["documents"])

StatusFilter = Literal["pending", "processing", "completed", "failed"]
EmbeddingStatusFilter = Literal["not_started", "pending", "processing", "completed", "failed", "not_required"]


def document_filters(
    document_type: str | None = Query(default=None, max_length=100),
    department: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    source: str | None = Query(default=None, max_length=255),
    access_level: str | None = Query(default=None),
    file_type: str | None = Query(default=None, max_length=20),
    tag: list[str] | None = Query(default=None, description="A document must have every tag given."),
    custom: str | None = Query(default=None, description='A JSON object the custom metadata must contain, e.g. {"region": "EU"}.'),
) -> SearchFilters:
    try:
        tags = parse_tags(",".join(tag)) if tag else None
        return SearchFilters(
            document_type=document_type,
            department=department,
            category=category,
            source=source,
            access_level=validate_access_level(access_level),
            file_type=file_type,
            tags=tags,
            custom_metadata=parse_custom_metadata(custom),
        )
    except MetadataValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _get_readable_document(tenant_id: int, team_id: int, document_id: int, user: User, db: Session) -> Document:
    document = DocumentService(db).get_document(user_id=user.id, tenant_id=tenant_id, team_id=team_id, document_id=document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.post(
    "/{tenant_id}/teams/{team_id}/documents/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_document(
    tenant_id: int,
    team_id: int,
    file: UploadFile = File(...),
    document_type: str | None = Form(default=None),
    department: str | None = Form(default=None),
    category: str | None = Form(default=None),
    source: str | None = Form(default=None),
    access_level: str | None = Form(default=None),
    tags: str | None = Form(default=None, description="A JSON array or a comma-separated list."),
    custom_metadata: str | None = Form(default=None, description="A JSON object. Security-related keys are refused."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_UPLOAD)),
):
    """tenant_id, team_id and the uploader come only from the path and the token, never from form fields."""
    try:
        metadata = build_metadata_input(
            document_type=document_type,
            department=department,
            category=category,
            source=source,
            access_level=access_level,
            tags=tags,
            custom_metadata=custom_metadata,
        )
        result = DocumentService(db).upload_document(tenant_id=tenant_id, team_id=team_id, user_id=current_user.id, file=file, metadata=metadata)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "document_id": exc.document_id, "status": exc.document_status},
        ) from exc
    except IngestionQueueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return DocumentUploadResponse(
        document_id=result.document.id,
        job_id=result.job.id,
        status=result.document.status,
        job_status=result.job.status,
    )


@router.get("/{tenant_id}/documents", response_model=list[DocumentRead])
def list_tenant_documents(
    tenant_id: int,
    status_filter: StatusFilter | None = Query(default=None, alias="status"),
    embedding_status: EmbeddingStatusFilter | None = Query(default=None),
    filters: SearchFilters = Depends(document_filters),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Only returns documents from teams the user holds document.read in."""
    return DocumentService(db).list_documents(
        user_id=current_user.id,
        tenant_id=tenant_id,
        status=status_filter,
        embedding_status=embedding_status,
        filters=filters,
        limit=limit,
        offset=offset,
    )


@router.get("/{tenant_id}/teams/{team_id}/documents", response_model=list[DocumentRead])
def list_team_documents(
    tenant_id: int,
    team_id: int,
    status_filter: StatusFilter | None = Query(default=None, alias="status"),
    embedding_status: EmbeddingStatusFilter | None = Query(default=None),
    filters: SearchFilters = Depends(document_filters),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
):
    return DocumentService(db).list_documents(
        user_id=current_user.id,
        tenant_id=tenant_id,
        team_id=team_id,
        status=status_filter,
        embedding_status=embedding_status,
        filters=filters,
        limit=limit,
        offset=offset,
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}", response_model=DocumentRead)
def get_document(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    return _get_readable_document(tenant_id, team_id, document_id, current_user, db)


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/download")
def download_document(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    path = DocumentService(db).get_document_file_path(document)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The stored file is missing")
    return FileResponse(
        path,
        media_type=document.content_type,
        filename=document.original_filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/text", response_model=DocumentTextResponse)
def get_document_text(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    return DocumentTextResponse(document_id=document.id, extracted_text=document.extracted_text)


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/jobs", response_model=list[DocumentJobResponse])
def list_document_jobs(
    tenant_id: int,
    team_id: int,
    document_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    return DocumentService(db).list_jobs(
        user_id=current_user.id, tenant_id=tenant_id, team_id=team_id, document_id=document_id, limit=limit, offset=offset
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/chunks", response_model=list[DocumentChunkResponse])
def list_document_chunks(
    tenant_id: int,
    team_id: int,
    document_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    return DocumentService(db).list_chunks(document, limit=limit, offset=offset)


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/metadata", response_model=DocumentMetadataResponse)
def get_document_metadata(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    return DocumentMetadataResponse(
        document_id=document.id,
        tenant_id=document.tenant_id,
        team_id=document.team_id,
        uploaded_by=document.uploaded_by_user_id,
        document_type=document.document_type,
        department=document.department,
        category=document.category,
        tags=document.tags,
        source=document.source,
        file_type=document.file_extension,
        uploaded_at=document.created_at,
        updated_at=document.updated_at,
        version=document.version,
        status=document.status,
        access_level=document.access_level,
        custom_metadata=document.custom_metadata,
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/status", response_model=DocumentStatusResponse)
def get_document_processing_status(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    service = DocumentService(db)
    job = service.latest_job(document)
    return DocumentStatusResponse(
        document_id=document.id,
        status=document.status,
        version=document.version,
        chunk_count=document.chunk_count,
        latest_job_id=job.id if job else None,
        latest_job_status=job.status if job else None,
        latest_job_error=job.error_message if job else None,
        embedding_status=document.embedding_status,
        embedding_count=service.embedding_count(document),
        embedding_attempts=document.embedding_attempts,
        embedding_error=document.embedding_error,
        embedded_at=document.embedded_at,
        duplicate_of_document_id=document.duplicate_of_document_id,
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/embeddings", response_model=DocumentEmbeddingsResponse)
def list_document_embeddings(
    tenant_id: int,
    team_id: int,
    document_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_document_read),
):
    """Metadata only. The vectors themselves are never returned."""
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    service = DocumentService(db)
    return DocumentEmbeddingsResponse(
        document_id=document.id,
        chunk_count=document.chunk_count,
        embedding_count=service.embedding_count(document),
        embedding_status=document.embedding_status,
        limit=limit,
        offset=offset,
        items=[EmbeddingMetadataItem.model_validate(row) for row in service.list_embedding_metadata(document, limit=limit, offset=offset)],
    )


@router.post(
    "/{tenant_id}/teams/{team_id}/documents/{document_id}/embeddings/retry",
    response_model=EmbeddingRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_document_embeddings(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_UPLOAD)),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    try:
        document = DocumentService(db).retry_embedding(document)
    except EmbeddingRetryConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IngestionQueueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return EmbeddingRetryResponse(document_id=document.id, embedding_status=document.embedding_status)


def _get_grantable_document(tenant_id: int, team_id: int, document_id: int, db: Session) -> Document:
    document = DocumentGrantService(db).get_grantable_document(tenant_id=tenant_id, team_id=team_id, document_id=document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.post("/{tenant_id}/teams/{team_id}/documents/{document_id}/grants", response_model=DocumentGrantRead, status_code=status.HTTP_201_CREATED)
def create_document_grant(
    tenant_id: int,
    team_id: int,
    document_id: int,
    payload: DocumentGrantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(TEAM_MANAGE)),
):
    """Gives one member of this tenant read access to this one document."""
    document = _get_grantable_document(tenant_id, team_id, document_id, db)
    try:
        return DocumentGrantService(db).grant(document, user_id=payload.user_id, granted_by_user_id=current_user.id)
    except GrantTargetNotAllowed as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/grants", response_model=list[DocumentGrantRead])
def list_document_grants(
    tenant_id: int,
    team_id: int,
    document_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(TEAM_MANAGE)),
):
    document = _get_grantable_document(tenant_id, team_id, document_id, db)
    return DocumentGrantService(db).list_grants(document, limit=limit, offset=offset)


@router.delete("/{tenant_id}/teams/{team_id}/documents/{document_id}/grants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_document_grant(
    tenant_id: int,
    team_id: int,
    document_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(TEAM_MANAGE)),
):
    document = _get_grantable_document(tenant_id, team_id, document_id, db)
    if not DocumentGrantService(db).revoke(document, user_id=user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
