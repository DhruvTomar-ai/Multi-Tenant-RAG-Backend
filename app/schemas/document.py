from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    metadata: dict | None = Field(default=None)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    tenant_id: int
    owner_user_id: int
    filename: str
    storage_key: str
    mime_type: str | None = None
    status: str
    checksum_sha256: str | None = None
    metadata: dict | None = Field(
        default=None,
        alias="metadata_",
        serialization_alias="metadata",
    )
    created_at: datetime
    updated_at: datetime


class DocumentChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    tenant_id: int
    document_id: int
    chunk_index: int
    content: str
    metadata: dict | None = Field(
        default=None,
        alias="metadata_",
        serialization_alias="metadata",
    )
    created_at: datetime


class DocumentDetailRead(DocumentRead):
    chunks: list[DocumentChunkRead] = []


class DocumentListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    documents: list[DocumentRead]
