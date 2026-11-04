from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_tenant_membership, require_tenant_permission
from app.core.config import settings
from app.db.models.user import User
from app.schemas.document import EmbeddingModelInfo, HnswIndexStatus
from app.security.permissions import TENANT_MANAGE
from app.services.embeddings.search import count_visible_embeddings, hnsw_index_status

router = APIRouter(prefix="/api/v1/tenants", tags=["embeddings"])


@router.get("/{tenant_id}/embeddings/model", response_model=EmbeddingModelInfo)
def get_embedding_model_info(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Reads configuration only, so this never loads the model into the API process."""
    return EmbeddingModelInfo(
        model_name=settings.embedding_model_name,
        model_version=settings.embedding_model_revision,
        dimension=settings.embedding_dimension,
        distance_metric="cosine",
        normalized=True,
        visible_embedding_count=count_visible_embeddings(db, user_id=current_user.id, tenant_id=tenant_id),
    )


@router.get("/{tenant_id}/embeddings/index", response_model=HnswIndexStatus)
def get_hnsw_index_status(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_permission(TENANT_MANAGE)),
):
    """Describes the index but not its size or row count: the index is shared across tenants."""
    status = hnsw_index_status(db)
    return HnswIndexStatus(
        exists=status is not None,
        ef_search=settings.hnsw_ef_search,
        iterative_scan=settings.hnsw_iterative_scan,
        max_scan_tuples=settings.hnsw_max_scan_tuples,
        scan_mem_multiplier=settings.hnsw_scan_mem_multiplier,
        **(status or {}),
    )
