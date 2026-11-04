from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentUploadResponse(BaseModel):
    document_id: int
    job_id: int
    status: str
    job_status: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    team_id: int
    uploaded_by_user_id: int | None = None
    original_filename: str
    file_extension: str
    content_type: str
    file_size: int
    file_hash: str
    extracted_text_hash: str | None = None
    version: int
    status: str
    chunk_count: int
    duplicate_of_document_id: int | None = None
    metadata_json: dict[str, Any] | None = None
    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    source: str | None = None
    access_level: str
    tags: list[str]
    custom_metadata: dict[str, Any]
    embedding_status: str
    created_at: datetime
    updated_at: datetime


class DocumentMetadataResponse(BaseModel):
    document_id: int
    tenant_id: int
    team_id: int
    uploaded_by: int | None = None
    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    tags: list[str]
    source: str | None = None
    file_type: str
    uploaded_at: datetime
    updated_at: datetime
    version: int
    status: str
    access_level: str
    custom_metadata: dict[str, Any]


class DocumentGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(ge=1, le=2_147_483_647)


class DocumentGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    user_id: int
    granted_by_user_id: int | None = None
    created_at: datetime


class DocumentStatusResponse(BaseModel):
    document_id: int
    status: str
    version: int
    chunk_count: int
    latest_job_id: int | None = None
    latest_job_status: str | None = None
    latest_job_error: str | None = None
    embedding_status: str
    embedding_count: int
    embedding_attempts: int
    embedding_error: str | None = None
    embedded_at: datetime | None = None
    duplicate_of_document_id: int | None = None


class EmbeddingMetadataItem(BaseModel):
    """Deliberately has no vector field."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    chunk_id: int
    chunk_index: int
    model_name: str
    model_version: str
    dimension: int
    created_at: datetime


class DocumentEmbeddingsResponse(BaseModel):
    document_id: int
    chunk_count: int
    embedding_count: int
    embedding_status: str
    limit: int
    offset: int
    items: list[EmbeddingMetadataItem]


class EmbeddingRetryResponse(BaseModel):
    document_id: int
    embedding_status: str


class EmbeddingModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    model_version: str
    dimension: int
    distance_metric: str
    normalized: bool
    visible_embedding_count: int


class HnswIndexStatus(BaseModel):
    exists: bool
    index_name: str | None = None
    index_type: str | None = None
    operator_class: str | None = None
    distance_metric: str | None = None
    column: str | None = None
    column_type: str | None = None
    is_valid: bool | None = None
    is_ready: bool | None = None
    m: int | None = None
    ef_construction: int | None = None
    ef_search: int
    iterative_scan: str
    max_scan_tuples: int
    scan_mem_multiplier: float


class DocumentTextResponse(BaseModel):
    document_id: int
    extracted_text: str | None = None


class DocumentJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    tenant_id: int
    team_id: int
    created_by_user_id: int | None = None
    celery_task_id: str | None = None
    status: str
    attempts: int
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    tenant_id: int
    team_id: int
    chunk_index: int
    chunk_text: str
    chunk_hash: str
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
