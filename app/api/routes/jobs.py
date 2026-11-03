from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.db.models.user import User
from app.schemas.document import DocumentJobResponse
from app.services.documents.service import DocumentService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("", response_model=list[DocumentJobResponse])
def list_jobs(
    tenant_id: int | None = Query(default=None),
    team_id: int | None = Query(default=None),
    document_id: int | None = Query(default=None),
    status_filter: Literal["pending", "processing", "completed", "failed"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Filters only narrow the results. Filtering on a tenant you can't see returns an empty list."""
    return DocumentService(db).list_jobs(
        user_id=current_user.id,
        tenant_id=tenant_id,
        team_id=team_id,
        document_id=document_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=DocumentJobResponse)
def get_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 404, not 403: a forbidden job must look the same as one that doesn't exist.
    job = DocumentService(db).get_job(user_id=current_user.id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
