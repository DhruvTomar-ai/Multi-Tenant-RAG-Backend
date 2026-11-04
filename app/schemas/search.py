from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.services.documents.metadata import MetadataValidationError, parse_custom_metadata, parse_tags, validate_access_level

ResourceId = Annotated[int, Field(ge=1, le=2_147_483_647)]
MAX_QUERY_LENGTH = 2000


class SearchFilterInput(BaseModel):
    """Filters only narrow what the user may already read. Unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    document_type: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    source: str | None = Field(default=None, min_length=1, max_length=255)
    access_level: str | None = None
    file_type: str | None = Field(default=None, min_length=1, max_length=20)
    tags: list[str] | None = Field(default=None, max_length=50)
    custom_metadata: dict[str, Any] | None = None

    @field_validator("access_level")
    @classmethod
    def _access_level(cls, value: str | None) -> str | None:
        try:
            return validate_access_level(value)
        except MetadataValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        try:
            return parse_tags(json.dumps(value))
        except MetadataValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("custom_metadata")
    @classmethod
    def _custom_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return parse_custom_metadata(json.dumps(value))
        except MetadataValidationError as exc:
            raise ValueError(exc.message) from exc


class SearchRequest(BaseModel):
    """There is deliberately no tenant_id or chunk_id field, and unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    team_ids: list[ResourceId] = Field(default_factory=list, max_length=50)
    document_ids: list[ResourceId] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default_factory=lambda: settings.search_default_top_k, ge=1)
    offset: int = Field(default=0, ge=0)
    filters: SearchFilterInput = Field(default_factory=SearchFilterInput)

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be blank")
        if "\x00" in value:
            raise ValueError("query contains invalid characters")
        return value

    @field_validator("top_k")
    @classmethod
    def _top_k(cls, value: int) -> int:
        if value > settings.search_max_top_k:
            raise ValueError(f"top_k cannot be larger than {settings.search_max_top_k}")
        return value

    @field_validator("offset")
    @classmethod
    def _offset(cls, value: int) -> int:
        if value > settings.search_max_offset:
            raise ValueError(f"offset cannot be larger than {settings.search_max_offset}")
        return value


class SearchResultMetadata(BaseModel):
    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    source: str | None = None
    access_level: str
    file_type: str
    tags: list[str]
    custom_metadata: dict[str, Any]
    version: int
    uploaded_at: datetime | None = None


class SearchResult(BaseModel):
    """Deliberately has no vector field."""

    chunk_id: int
    chunk_index: int
    document_id: int
    document_title: str
    team_id: int
    content: str
    similarity_score: float = Field(description="1 minus pgvector's cosine distance. 1 is most similar, and it can fall to -1.")
    metadata: SearchResultMetadata


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total_results: int = Field(description="Results on this page. It is not a count of all matches.")
    top_k: int
    offset: int
    has_more: bool
    score_type: str = "cosine_similarity"
