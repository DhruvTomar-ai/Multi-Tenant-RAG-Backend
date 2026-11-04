from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_tenant_membership
from app.db.models.conversation import Conversation, Message
from app.db.models.team import Team
from app.db.models.user import User
from app.schemas.chat import ChatReplyResponse, ConversationCreate, ConversationRead, ConversationUpdate, MessageCreate, MessageRead
from app.security.authorization import AuthorizationService
from app.security.permissions import DOCUMENT_READ
from app.services.embeddings.search import SearchFilters
from app.services.rag.conversations import ConversationStore, VisibleMessage
from app.services.rag.service import ClientMessageIdConflict, MessageInProgress, RagGenerationFailed, RagService

router = APIRouter(prefix="/api/v1/tenants", tags=["chat"])

NOT_FOUND = "Conversation not found"
# Bounded to the column's range, so an absurd ID is a 422 and never reaches the database.
ConversationId = Annotated[int, Path(ge=1, le=2_147_483_647)]


def _get_conversation(tenant_id: int, conversation_id: int, user: User, db: Session) -> Conversation:
    # 404 for all of these: another user's, another tenant's and one that does not exist.
    conversation = ConversationStore(db).get(tenant_id=tenant_id, user_id=user.id, conversation_id=conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return conversation


def _require_team_scope(db: Session, *, user_id: int, tenant_id: int, team_id: int) -> None:
    team = db.scalar(select(Team.id).where(Team.id == team_id, Team.tenant_id == tenant_id))
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if not AuthorizationService(db).has_team_permission(user_id, tenant_id, team_id, DOCUMENT_READ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this team")


def _sources(citations: list[dict]) -> list[dict]:
    by_document: dict[int, dict] = {}
    for citation in citations:
        entry = by_document.setdefault(citation["document_id"], {"document_id": citation["document_id"], "document_title": citation["document_title"], "chunk_ids": []})
        entry["chunk_ids"].append(citation["chunk_id"])
    return list(by_document.values())


def _message_read(item: VisibleMessage) -> MessageRead:
    message = item.message
    return MessageRead(
        id=message.id,
        role=message.role,
        content=item.content,
        status=message.status,
        status_reason=message.status_reason,
        reply_to_message_id=message.reply_to_message_id,
        citations=item.citations,
        sources=_sources(item.citations),
        content_hidden=item.hidden,
        llm_model=message.llm_model,
        created_at=message.created_at,
    )


def _plain(message: Message) -> VisibleMessage:
    return VisibleMessage(message=message, content=message.content, citations=[], hidden=False)


@router.post("/{tenant_id}/chat/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    tenant_id: int,
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    if payload.team_id is not None:
        _require_team_scope(db, user_id=current_user.id, tenant_id=tenant_id, team_id=payload.team_id)
    return ConversationStore(db).create(tenant_id=tenant_id, user_id=current_user.id, title=payload.title, team_id=payload.team_id)


@router.get("/{tenant_id}/chat/conversations", response_model=list[ConversationRead])
def list_conversations(
    tenant_id: int,
    status_filter: Literal["active", "archived"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Only the user's own conversations, including for tenant admins."""
    return ConversationStore(db).list(tenant_id=tenant_id, user_id=current_user.id, status=status_filter, limit=limit, offset=offset)


@router.get("/{tenant_id}/chat/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    tenant_id: int,
    conversation_id: ConversationId,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    return _get_conversation(tenant_id, conversation_id, current_user, db)


@router.patch("/{tenant_id}/chat/conversations/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    tenant_id: int,
    conversation_id: ConversationId,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    conversation = _get_conversation(tenant_id, conversation_id, current_user, db)
    return ConversationStore(db).update(conversation, title=payload.title, status=payload.status)


@router.get("/{tenant_id}/chat/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    tenant_id: int,
    conversation_id: ConversationId,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    conversation = _get_conversation(tenant_id, conversation_id, current_user, db)
    return [_message_read(item) for item in ConversationStore(db).list_messages(conversation, user_id=current_user.id, limit=limit, offset=offset)]


@router.post("/{tenant_id}/chat/conversations/{conversation_id}/messages", response_model=ChatReplyResponse)
def send_message(
    tenant_id: int,
    conversation_id: ConversationId,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_membership),
):
    """Answers from documents the user can read right now. An unanswerable question is still a 200."""
    conversation = _get_conversation(tenant_id, conversation_id, current_user, db)
    store = ConversationStore(db)
    if not store.is_active(conversation):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This conversation is archived")
    team_ids = list(payload.team_ids)
    if conversation.team_id is not None:
        _require_team_scope(db, user_id=current_user.id, tenant_id=tenant_id, team_id=conversation.team_id)
        # A team-scoped conversation can only search that team. [0] matches nothing if it is excluded.
        team_ids = [conversation.team_id] if not team_ids or conversation.team_id in team_ids else [0]
    filters = SearchFilters(
        team_ids=team_ids,
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
    try:
        result = RagService(db).ask(conversation, user_id=current_user.id, question=payload.content, client_message_id=payload.client_message_id, filters=filters)
    except ClientMessageIdConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="client_message_id was already used for a different question") from exc
    except MessageInProgress as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This question is still being answered") from exc
    except RagGenerationFailed as exc:
        headers = {"Retry-After": exc.error.retry_after} if exc.error.retry_after else None
        return JSONResponse(
            status_code=exc.error.http_status,
            headers=headers,
            content={
                "detail": exc.error.message,
                "code": exc.error.code,
                "conversation_id": conversation.id,
                "user_message_id": exc.user_message.id,
                "assistant_message_id": exc.assistant_message.id,
            },
        )
    return ChatReplyResponse(
        conversation_id=conversation.id,
        status=result.assistant.message.status,
        replayed=result.replayed,
        user_message=_message_read(_plain(result.user_message)),
        assistant_message=_message_read(result.assistant),
    )
