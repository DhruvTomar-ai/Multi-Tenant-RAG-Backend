from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_tenant_membership
from app.db.models.user import User
from app.schemas.search import SearchRequest, SearchResponse, SearchResult, SearchResultMetadata
from app.services.embeddings.search import SearchFilters, search_text

router = APIRouter(prefix="/api/v1/tenants", tags=["search"])


@router.post("/{tenant_id}/search", response_model=SearchResponse)
def search_documents(
    tenant_id: int,
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Returns only chunks the user may read.

    A team or document the user cannot read simply matches nothing, exactly like one that does
    not exist, so the response never reveals whether it exists.
    """
    filters = SearchFilters(
        team_ids=payload.team_ids,
        document_ids=payload.document_ids,
        document_type=payload.filters.document_type,
        department=payload.filters.department,
        category=payload.filters.category,
        source=payload.filters.source,
        access_level=payload.filters.access_level,
        file_type=payload.filters.file_type,
        tags=payload.filters.tags,
        custom_metadata=payload.filters.custom_metadata,
    )
    hits, has_more = search_text(
        db,
        user_id=current_user.id,
        tenant_id=tenant_id,
        query=payload.query,
        top_k=payload.top_k,
        offset=payload.offset,
        filters=filters,
    )
    results = [
        SearchResult(
            chunk_id=hit.chunk_id,
            chunk_index=hit.chunk_index,
            document_id=hit.document_id,
            document_title=hit.document_title,
            team_id=hit.team_id,
            content=hit.chunk_text,
            similarity_score=round(hit.similarity_score, 6),
            metadata=SearchResultMetadata(
                document_type=hit.document_type,
                department=hit.department,
                category=hit.category,
                source=hit.source,
                access_level=hit.access_level,
                file_type=hit.file_type,
                tags=list(hit.tags),
                custom_metadata=hit.custom_metadata or {},
                version=hit.document_version,
                uploaded_at=hit.uploaded_at,
            ),
        )
        for hit in hits
    ]
    return SearchResponse(query=payload.query, results=results, total_results=len(results), top_k=payload.top_k, offset=payload.offset, has_more=has_more)
