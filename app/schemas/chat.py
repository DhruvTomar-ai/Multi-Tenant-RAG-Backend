from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.search import ResourceId, SearchFilterInput

MAX_QUESTION_LENGTH = 4000


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned or "\x00" in cleaned:
        raise ValueError("title cannot be blank")
    return cleaned


class ConversationCreate(BaseModel):
    """The creator and tenant come from the token and the checked path, never from the body."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    team_id: ResourceId | None = None

    _title = field_validator("title")(_clean_title)


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    status: Literal["active", "archived"] | None = None

    _title = field_validator("title")(_clean_title)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    team_id: int | None = None
    created_by_user_id: int
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    team_ids: list[ResourceId] = Field(default_factory=list, max_length=50)
    document_ids: list[ResourceId] = Field(default_factory=list, max_length=100)
    filters: SearchFilterInput = Field(default_factory=SearchFilterInput)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$", description="Send the same value when retrying, so the question is not saved or answered twice.")

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content cannot be blank")
        if "\x00" in value:
            raise ValueError("content contains invalid characters")
        return value


class CitationRead(BaseModel):
    chunk_id: int
    chunk_index: int
    document_id: int
    document_title: str
    team_id: int
    similarity_score: float = Field(description="1 minus cosine distance, at retrieval time.")
    page_label: str | None = Field(default=None, description="Present only when the source file recorded it.")
    metadata: dict[str, Any]


class SourceRead(BaseModel):
    document_id: int
    document_title: str
    chunk_ids: list[int]


class MessageRead(BaseModel):
    id: int
    role: str
    content: str
    status: str
    status_reason: str | None = None
    reply_to_message_id: int | None = None
    citations: list[CitationRead]
    sources: list[SourceRead]
    content_hidden: bool = Field(default=False, description="True when the user can no longer read a document behind this answer.")
    llm_model: str | None = None
    created_at: datetime


class ChatReplyResponse(BaseModel):
    conversation_id: int
    status: str
    replayed: bool = Field(description="True when a retry returned the saved answer without generating a new one.")
    user_message: MessageRead
    assistant_message: MessageRead
