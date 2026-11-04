"""Permission-aware vector search.

Authorization and metadata conditions are part of the SQL query itself, so unauthorized vectors
are never fetched and filtered afterwards. That does not make the search cheaper: an HNSW scan
walks the shared graph first and applies the conditions to what it finds. A selective filter can
therefore return too few rows unless iterative scanning is on, which is why the settings below
are applied per transaction.

Scores: pgvector's <=> returns cosine distance, from 0 (same direction) to 2 (opposite).
similarity_score = 1 - cosine distance, so 1 is most similar and it can fall to -1.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import String

from app.core.config import settings
from app.db.models.document import (
    EMBEDDING_COMPLETED,
    EMBEDDING_NOT_REQUIRED,
    STATUS_COMPLETED,
    Document,
)
from app.db.models.document_chunk import DocumentChunk
from app.db.models.embedding import EMBEDDING_DIMENSION, Embedding
from app.security.authorization import readable_document_condition
from app.services.embeddings import model as embedding_model

ITERATIVE_MODES = {"off", "strict_order", "relaxed_order"}


@dataclass(frozen=True)
class SearchFilters:
    team_id: int | None = None
    document_id: int | None = None
    team_ids: Sequence[int] | None = None
    document_ids: Sequence[int] | None = None
    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    source: str | None = None
    access_level: str | None = None
    file_type: str | None = None
    tags: Sequence[str] | None = None
    custom_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SearchHit:
    embedding_id: int
    chunk_id: int
    document_id: int
    team_id: int
    chunk_index: int
    chunk_text: str
    distance: float
    document_title: str = ""
    document_version: int = 1
    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    source: str | None = None
    access_level: str = "team"
    file_type: str = ""
    tags: tuple[str, ...] = ()
    custom_metadata: dict[str, Any] | None = None
    uploaded_at: datetime | None = None
    page_label: str | None = None

    @property
    def similarity_score(self) -> float:
        return 1.0 - self.distance


def apply_hnsw_settings(
    db: Session,
    *,
    ef_search: int | None = None,
    iterative_scan: str | None = None,
    max_scan_tuples: int | None = None,
    scan_mem_multiplier: float | None = None,
) -> None:
    """SET LOCAL through set_config: bound values, and gone when the transaction ends."""
    mode = settings.hnsw_iterative_scan if iterative_scan is None else iterative_scan
    if mode not in ITERATIVE_MODES:
        raise ValueError(f"Unknown iterative scan mode: {mode}")
    values = {
        "hnsw.ef_search": ef_search if ef_search is not None else settings.hnsw_ef_search,
        "hnsw.iterative_scan": mode,
        "hnsw.max_scan_tuples": max_scan_tuples if max_scan_tuples is not None else settings.hnsw_max_scan_tuples,
        "hnsw.scan_mem_multiplier": scan_mem_multiplier if scan_mem_multiplier is not None else settings.hnsw_scan_mem_multiplier,
    }
    for name, value in values.items():
        db.execute(text("SELECT set_config(:name, :value, true)"), {"name": name, "value": str(value)})


def active_document_conditions() -> list[Any]:
    """A document appears only once all of its chunks are embedded, never partially."""
    return [
        Document.status == STATUS_COMPLETED,
        Document.embedding_status == EMBEDDING_COMPLETED,
        Document.is_active.is_(True),
        Document.deleted_at.is_(None),
    ]


def _not_superseded():
    """An older version stays searchable until a newer one can actually replace it.

    A newer version that reuses this version's chunks as a text duplicate does not supersede it,
    because this version holds the only searchable copy of that text.
    """
    newer = aliased(Document)
    return ~(
        select(newer.id)
        .where(
            newer.tenant_id == Document.tenant_id,
            newer.team_id == Document.team_id,
            newer.original_filename == Document.original_filename,
            newer.version > Document.version,
            newer.status == STATUS_COMPLETED,
            newer.embedding_status.in_((EMBEDDING_COMPLETED, EMBEDDING_NOT_REQUIRED)),
            newer.is_active.is_(True),
            newer.deleted_at.is_(None),
            or_(newer.duplicate_of_document_id.is_(None), newer.duplicate_of_document_id != Document.id),
        )
        .exists()
    )


def build_search_statement(*, user_id: int, tenant_id: int, query_vector: Sequence[float], limit: int, offset: int = 0, filters: SearchFilters | None = None):
    """tenant_id must come from the authenticated route context, and access from the database."""
    if len(query_vector) != EMBEDDING_DIMENSION:
        raise ValueError(f"The query vector must have {EMBEDDING_DIMENSION} dimensions")
    filters = filters or SearchFilters()
    distance = Embedding.embedding.cosine_distance(list(query_vector)).label("distance")

    statement = (
        select(
            Embedding.id.label("embedding_id"),
            Embedding.chunk_id,
            Embedding.document_id,
            Embedding.team_id,
            DocumentChunk.chunk_index,
            DocumentChunk.chunk_text,
            DocumentChunk.metadata_json.label("chunk_metadata"),
            distance,
            Document.original_filename,
            Document.version,
            Document.document_type,
            Document.department,
            Document.category,
            Document.source,
            Document.access_level,
            Document.file_extension,
            Document.tags,
            Document.custom_metadata,
            Document.created_at,
        )
        .join(Document, Document.id == Embedding.document_id)
        .join(DocumentChunk, DocumentChunk.id == Embedding.chunk_id)
        .where(
            Embedding.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            readable_document_condition(user_id),
            # Vectors from another model or revision are outdated and not comparable.
            Embedding.model_name == settings.embedding_model_name,
            Embedding.model_version == settings.embedding_model_revision,
            *active_document_conditions(),
            _not_superseded(),
        )
    )
    if filters.team_id is not None:
        statement = statement.where(Embedding.team_id == filters.team_id)
    if filters.document_id is not None:
        statement = statement.where(Embedding.document_id == filters.document_id)
    if filters.team_ids:
        statement = statement.where(Embedding.team_id.in_(list(filters.team_ids)))
    if filters.document_ids:
        statement = statement.where(Embedding.document_id.in_(list(filters.document_ids)))
    statement = statement.where(*document_filter_conditions(filters))
    return statement.order_by(distance).limit(limit).offset(offset)


def document_filter_conditions(filters: SearchFilters) -> list[Any]:
    conditions: list[Any] = []
    for column, value in (
        (Document.document_type, filters.document_type),
        (Document.department, filters.department),
        (Document.category, filters.category),
        (Document.source, filters.source),
        (Document.access_level, filters.access_level),
    ):
        if value is not None:
            conditions.append(column == value)
    if filters.file_type is not None:
        extension = filters.file_type.lower()
        conditions.append(Document.file_extension == (extension if extension.startswith(".") else f".{extension}"))
    if filters.tags:
        conditions.append(Document.tags.op("@>")(cast([tag.lower() for tag in filters.tags], ARRAY(String(64)))))
    if filters.custom_metadata:
        conditions.append(Document.custom_metadata.op("@>")(cast(filters.custom_metadata, JSONB)))
    return conditions


def search_embeddings(
    db: Session,
    *,
    user_id: int,
    tenant_id: int,
    query_vector: Sequence[float],
    limit: int = 10,
    offset: int = 0,
    filters: SearchFilters | None = None,
    ef_search: int | None = None,
    iterative_scan: str | None = None,
    max_scan_tuples: int | None = None,
) -> list[SearchHit]:
    # An index scan returns at most ef_search rows per pass, so it must cover the whole page.
    wanted_ef = max(ef_search if ef_search is not None else settings.hnsw_ef_search, min(limit + offset, 1000))
    apply_hnsw_settings(db, ef_search=wanted_ef, iterative_scan=iterative_scan, max_scan_tuples=max_scan_tuples)
    statement = build_search_statement(user_id=user_id, tenant_id=tenant_id, query_vector=query_vector, limit=limit, offset=offset, filters=filters)
    rows = db.execute(statement).all()
    db.rollback()  # Ends the transaction, which also ends the SET LOCAL values.
    return [
        SearchHit(
            embedding_id=row.embedding_id,
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            team_id=row.team_id,
            chunk_index=row.chunk_index,
            chunk_text=row.chunk_text,
            distance=float(row.distance),
            document_title=row.original_filename,
            document_version=row.version,
            document_type=row.document_type,
            department=row.department,
            category=row.category,
            source=row.source,
            access_level=row.access_level,
            file_type=row.file_extension,
            tags=tuple(row.tags or ()),
            custom_metadata=row.custom_metadata,
            uploaded_at=row.created_at,
            page_label=(row.chunk_metadata or {}).get("page_label"),
        )
        for row in rows
    ]


def search_text(
    db: Session,
    *,
    user_id: int,
    tenant_id: int,
    query: str,
    top_k: int,
    offset: int = 0,
    filters: SearchFilters | None = None,
) -> tuple[list[SearchHit], bool]:
    """Embed the query with the shared model, then search. Returns one page and whether more exist."""
    # The session holds no transaction open during the model call.
    db.rollback()
    vector = embedding_model.encode_query(query).tolist()
    hits = search_embeddings(db, user_id=user_id, tenant_id=tenant_id, query_vector=vector, limit=top_k + 1, offset=offset, filters=filters)
    return hits[:top_k], len(hits) > top_k


def hnsw_index_status(db: Session) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT c.relname AS index_name, am.amname AS index_type, opc.opcname AS operator_class,
                   a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS column_type,
                   i.indisvalid AS is_valid, i.indisready AS is_ready, c.reloptions AS options
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_am am ON am.oid = c.relam
            JOIN pg_opclass opc ON opc.oid = i.indclass[0]
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
            WHERE t.relname = 'embeddings' AND am.amname = 'hnsw'
            LIMIT 1
            """
        )
    ).mappings().first()
    if row is None:
        return None
    options = dict(option.split("=", 1) for option in (row["options"] or []))
    metric = {"vector_cosine_ops": "cosine", "vector_l2_ops": "l2", "vector_ip_ops": "inner_product"}.get(row["operator_class"], row["operator_class"])
    return {
        "index_name": row["index_name"],
        "index_type": row["index_type"],
        "operator_class": row["operator_class"],
        "distance_metric": metric,
        "column": row["column_name"],
        "column_type": row["column_type"],
        "is_valid": row["is_valid"],
        "is_ready": row["is_ready"],
        "m": int(options["m"]) if "m" in options else None,
        "ef_construction": int(options["ef_construction"]) if "ef_construction" in options else None,
    }


def count_visible_embeddings(db: Session, *, user_id: int, tenant_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Embedding)
        .join(Document, Document.id == Embedding.document_id)
        .where(
            Embedding.tenant_id == tenant_id,
            readable_document_condition(user_id),
            Document.is_active.is_(True),
            Document.deleted_at.is_(None),
        )
    )
