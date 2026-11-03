from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_team_permission, require_tenant_membership
from app.db.models.document import Document
from app.db.models.user import User
from app.schemas.document import (
    DocumentChunkResponse,
    DocumentJobResponse,
    DocumentRead,
    DocumentTextResponse,
    DocumentUploadResponse,
)
from app.security.permissions import DOCUMENT_READ, DOCUMENT_UPLOAD
from app.services.documents.errors import DuplicateDocumentError, IngestionQueueError, UploadValidationError
from app.services.documents.service import DocumentService

router = APIRouter(prefix="/api/v1/tenants", tags=["documents"])

StatusFilter = Literal["pending", "processing", "completed", "failed"]


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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_UPLOAD)),
):
    try:
        result = DocumentService(db).upload_document(tenant_id=tenant_id, team_id=team_id, user_id=current_user.id, file=file)
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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Only returns documents from teams the user holds document.read in."""
    return DocumentService(db).list_documents(
        user_id=current_user.id, tenant_id=tenant_id, status=status_filter, limit=limit, offset=offset
    )


@router.get("/{tenant_id}/teams/{team_id}/documents", response_model=list[DocumentRead])
def list_team_documents(
    tenant_id: int,
    team_id: int,
    status_filter: StatusFilter | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
):
    return DocumentService(db).list_documents(
        user_id=current_user.id, tenant_id=tenant_id, team_id=team_id, status=status_filter, limit=limit, offset=offset
    )


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}", response_model=DocumentRead)
def get_document(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
):
    return _get_readable_document(tenant_id, team_id, document_id, current_user, db)


@router.get("/{tenant_id}/teams/{team_id}/documents/{document_id}/download")
def download_document(
    tenant_id: int,
    team_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
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
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
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
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
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
    current_user: User = Depends(require_team_permission(DOCUMENT_READ)),
):
    document = _get_readable_document(tenant_id, team_id, document_id, current_user, db)
    return DocumentService(db).list_chunks(document, limit=limit, offset=offset)
