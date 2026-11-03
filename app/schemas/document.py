from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
    created_at: datetime
    updated_at: datetime


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
