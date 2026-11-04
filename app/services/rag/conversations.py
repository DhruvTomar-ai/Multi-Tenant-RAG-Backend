"""Conversation and message storage, and the access rules for reading them.

A conversation belongs to its creator alone, so every lookup is scoped by tenant and creator.
Anything else looks exactly like a conversation that does not exist.

Past answers are not permanent access. An answer's text comes from the documents given to the LLM,
so if the user can no longer read any of them, that answer is hidden from history and from later
prompts. The stored row is unchanged, so it reappears if access returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.conversation import CONVERSATION_ACTIVE, MESSAGE_COMPLETED, ROLE_ASSISTANT, ROLE_USER, Conversation, Message
from app.db.models.document import Document, utc_now
from app.security.authorization import readable_document_condition

HIDDEN_ANSWER = "This answer is hidden because you no longer have access to one or more of its source documents."
_TITLE_LENGTH = 80


@dataclass(frozen=True)
class VisibleMessage:
    message: Message
    content: str
    citations: list[dict[str, Any]]
    hidden: bool


def default_title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= _TITLE_LENGTH else text[: _TITLE_LENGTH - 3].rstrip() + "..."


class ConversationStore:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, tenant_id: int, user_id: int, title: str | None, team_id: int | None) -> Conversation:
        conversation = Conversation(tenant_id=tenant_id, created_by_user_id=user_id, title=title or "New conversation", team_id=team_id)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get(self, *, tenant_id: int, user_id: int, conversation_id: int) -> Conversation | None:
        return self.db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.created_by_user_id == user_id,
            )
        )

    def list(self, *, tenant_id: int, user_id: int, status: str | None, limit: int, offset: int) -> list[Conversation]:
        stmt = select(Conversation).where(Conversation.tenant_id == tenant_id, Conversation.created_by_user_id == user_id)
        if status is not None:
            stmt = stmt.where(Conversation.status == status)
        return list(self.db.scalars(stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit).offset(offset)).all())

    def update(self, conversation: Conversation, *, title: str | None, status: str | None) -> Conversation:
        if title is not None:
            conversation.title = title
        if status is not None:
            conversation.status = status
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def is_active(self, conversation: Conversation) -> bool:
        return conversation.status == CONVERSATION_ACTIVE

    def touch(self, conversation: Conversation) -> None:
        conversation.updated_at = utc_now()

    def _unreadable_documents(self, *, user_id: int, tenant_id: int, document_ids: set[int]) -> set[int]:
        """One query for a whole page of messages. Hidden documents count as unreadable."""
        if not document_ids:
            return set()
        readable = self.db.scalars(
            select(Document.id).where(
                Document.id.in_(document_ids),
                Document.tenant_id == tenant_id,
                Document.is_active.is_(True),
                Document.deleted_at.is_(None),
                readable_document_condition(user_id),
            )
        ).all()
        return document_ids - set(readable)

    def apply_current_access(self, messages: list[Message], *, user_id: int, tenant_id: int) -> list[VisibleMessage]:
        document_ids = {int(document_id) for message in messages for document_id in (message.context_document_ids or [])}
        unreadable = self._unreadable_documents(user_id=user_id, tenant_id=tenant_id, document_ids=document_ids)
        visible = []
        for message in messages:
            hidden = bool(unreadable & {int(d) for d in (message.context_document_ids or [])})
            citations = [citation for citation in (message.citations or []) if int(citation["document_id"]) not in unreadable]
            visible.append(VisibleMessage(message=message, content=HIDDEN_ANSWER if hidden else message.content, citations=citations, hidden=hidden))
        return visible

    def list_messages(self, conversation: Conversation, *, user_id: int, limit: int, offset: int) -> list[VisibleMessage]:
        messages = list(
            self.db.scalars(
                select(Message).where(Message.conversation_id == conversation.id, Message.tenant_id == conversation.tenant_id).order_by(Message.id).limit(limit).offset(offset)
            ).all()
        )
        return self.apply_current_access(messages, user_id=user_id, tenant_id=conversation.tenant_id)

    def history_for_prompt(self, conversation: Conversation, *, user_id: int, before_message_id: int, limit: int) -> list[dict[str, str]]:
        """Recent turns, oldest first. Failed, unanswered and hidden answers are left out with their question."""
        if limit <= 0:
            return []
        recent = list(
            self.db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.tenant_id == conversation.tenant_id, Message.id < before_message_id)
                .order_by(Message.id.desc())
                .limit(limit * 2)
            ).all()
        )
        recent.reverse()
        visible = self.apply_current_access(recent, user_id=user_id, tenant_id=conversation.tenant_id)
        answers = {item.message.reply_to_message_id: item for item in visible if item.message.role == ROLE_ASSISTANT}
        history: list[dict[str, str]] = []
        for item in visible:
            if item.message.role != ROLE_USER:
                continue
            answer = answers.get(item.message.id)
            if answer is None or answer.hidden or answer.message.status != MESSAGE_COMPLETED:
                continue
            history.append({"role": "user", "content": item.message.content})
            history.append({"role": "assistant", "content": answer.message.content})
        return history[-limit:]

    def recent_questions(self, conversation: Conversation, *, before_message_id: int, limit: int) -> list[str]:
        if limit <= 0:
            return []
        questions = self.db.scalars(
            select(Message.content)
            .where(Message.conversation_id == conversation.id, Message.tenant_id == conversation.tenant_id, Message.role == ROLE_USER, Message.id < before_message_id)
            .order_by(Message.id.desc())
            .limit(limit)
        ).all()
        return list(reversed(questions))
