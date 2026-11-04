"""The RAG flow: save the question, retrieve through Workflow 6, call Groq, validate, save the answer.

No database transaction stays open during retrieval's model call or the Groq call. The question and a
pending answer row are committed first, so a crash can never leave an answer marked completed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.conversation import (
    MESSAGE_COMPLETED,
    MESSAGE_FAILED,
    MESSAGE_INSUFFICIENT_EVIDENCE,
    MESSAGE_PENDING,
    ROLE_ASSISTANT,
    ROLE_USER,
    Conversation,
    Message,
)
from app.db.models.document import utc_now
from app.services.embeddings.search import SearchFilters, search_text
from app.services.rag import citations as citation_rules
from app.services.rag import groq_client
from app.services.rag.context import ContextPassage, build_passages, relevant_hits
from app.services.rag.conversations import ConversationStore, VisibleMessage, default_title
from app.services.rag.groq_client import LLMBadResponse, LLMError
from app.services.rag.prompts import build_messages

logger = logging.getLogger(__name__)

INSUFFICIENT_ANSWER = "I could not find sufficient information in the available documents to answer this question."
FAILED_ANSWER = "An answer could not be generated for this question."

REASON_NO_RESULTS = "no_results"
REASON_LOW_RELEVANCE = "low_relevance"
REASON_MODEL_DECLINED = "model_insufficient_evidence"
REASON_NO_VALID_CITATIONS = "no_valid_citations"


class ClientMessageIdConflict(Exception):
    """This client_message_id was already used for a different question."""


class MessageInProgress(Exception):
    """This client_message_id is still being answered by an earlier request."""


class RagGenerationFailed(Exception):
    """Answer generation failed. The question and a failed answer row are saved."""

    def __init__(self, error: LLMError, user_message: Message, assistant_message: Message):
        super().__init__(error.message)
        self.error = error
        self.user_message = user_message
        self.assistant_message = assistant_message


@dataclass(frozen=True)
class RagResult:
    conversation: Conversation
    user_message: Message
    assistant: VisibleMessage
    replayed: bool = False


# English only. A miss searches without the earlier question, and a false match adds it to the search.
# Either way, retrieval stays authorized and the LLM must still cite what it retrieved.
_FOLLOW_UP_WORDS = re.compile(r"\b(it|its|that|this|those|these|they|them|their|there|he|she|his|her|above|same|else|more|also)\b", re.IGNORECASE)
_FOLLOW_UP_START = re.compile(r"^\s*(and|but|so|then|what about|how about)\b", re.IGNORECASE)


def _depends_on_earlier_context(question: str) -> bool:
    return bool(_FOLLOW_UP_START.search(question) or _FOLLOW_UP_WORDS.search(question))


def _stale_before() -> datetime:
    """A pending answer older than the longest possible Groq call was abandoned by a crash."""
    limit = settings.groq_timeout_seconds * (settings.groq_max_retries + 1) + 60
    return datetime.now(timezone.utc) - timedelta(seconds=limit)


class RagService:
    def __init__(self, db: Session):
        self.db = db
        self.store = ConversationStore(db)

    def _save_question(self, conversation: Conversation, *, question: str, client_message_id: str | None) -> tuple[Message, Message, bool]:
        """Returns the question, its answer row, and whether this request should generate the answer."""
        if client_message_id is not None:
            existing = self._find_existing(conversation, client_message_id)
            if existing is not None:
                return self._resume(existing, question)

        if conversation.title == "New conversation":
            conversation.title = default_title(question)
        user_message = Message(conversation_id=conversation.id, tenant_id=conversation.tenant_id, role=ROLE_USER, content=question, status=MESSAGE_COMPLETED, client_message_id=client_message_id, citations=[], context_document_ids=[])
        self.db.add(user_message)
        try:
            self.db.flush()
            assistant = Message(conversation_id=conversation.id, tenant_id=conversation.tenant_id, role=ROLE_ASSISTANT, content="", status=MESSAGE_PENDING, reply_to_message_id=user_message.id, citations=[], context_document_ids=[])
            self.db.add(assistant)
            self.store.touch(conversation)
            self.db.commit()
        except IntegrityError:
            # Two requests with the same client_message_id raced, and the other one won.
            self.db.rollback()
            existing = self._find_existing(conversation, client_message_id) if client_message_id else None
            if existing is None:
                raise
            return self._resume(existing, question)
        return user_message, assistant, True

    def _find_existing(self, conversation: Conversation, client_message_id: str) -> Message | None:
        return self.db.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.client_message_id == client_message_id))

    def _resume(self, user_message: Message, question: str) -> tuple[Message, Message, bool]:
        if user_message.content != question:
            raise ClientMessageIdConflict()
        assistant = self.db.scalar(select(Message).where(Message.reply_to_message_id == user_message.id))
        if assistant is None:
            raise MessageInProgress()
        if assistant.status in (MESSAGE_COMPLETED, MESSAGE_INSUFFICIENT_EVIDENCE):
            return user_message, assistant, False
        # Only one request can take over a failed or abandoned answer.
        taken = self.db.execute(
            update(Message)
            .where(
                Message.id == assistant.id,
                or_(Message.status == MESSAGE_FAILED, (Message.status == MESSAGE_PENDING) & (Message.updated_at < _stale_before())),
            )
            .values(status=MESSAGE_PENDING, status_reason=None, updated_at=utc_now())
        )
        self.db.commit()
        if taken.rowcount != 1:
            raise MessageInProgress()
        self.db.refresh(assistant)
        return user_message, assistant, True

    def _finish(self, assistant: Message, *, status: str, content: str, reason: str | None, citations: list, context_document_ids: list[int], model: str | None) -> None:
        assistant.status = status
        assistant.status_reason = reason
        assistant.content = content
        assistant.citations = citations
        assistant.context_document_ids = context_document_ids
        assistant.llm_model = model
        self.db.commit()

    def _retrieve(self, conversation: Conversation, user_id: int, question: str, user_message: Message, filters: SearchFilters) -> tuple[list, list]:
        """Returns all hits and the relevant ones. Workflow 6 decides what this user may read, every time.

        A question that stands alone is searched alone, so a new topic cannot borrow relevance from an
        earlier one. The previous question is added only when the wording depends on it, as in "how is
        that calculated?". Previous answers are never used to search.
        """
        query = question
        if _depends_on_earlier_context(question):
            earlier = self.store.recent_questions(conversation, before_message_id=user_message.id, limit=settings.rag_retrieval_history_questions)
            query = "\n".join([*earlier, question])[-2000:]
        hits, _ = search_text(self.db, user_id=user_id, tenant_id=conversation.tenant_id, query=query, top_k=settings.rag_top_k, filters=filters)
        return hits, relevant_hits(hits, settings.rag_min_similarity)

    def _generate(self, conversation: Conversation, user_id: int, question: str, user_message: Message, assistant: Message, filters: SearchFilters) -> None:
        hits, useful = self._retrieve(conversation, user_id, question, user_message, filters)
        if not hits:
            self._finish(assistant, status=MESSAGE_INSUFFICIENT_EVIDENCE, content=INSUFFICIENT_ANSWER, reason=REASON_NO_RESULTS, citations=[], context_document_ids=[], model=None)
            return
        if not useful:
            self._finish(assistant, status=MESSAGE_INSUFFICIENT_EVIDENCE, content=INSUFFICIENT_ANSWER, reason=REASON_LOW_RELEVANCE, citations=[], context_document_ids=[], model=None)
            return

        passages = build_passages(useful, max_tokens=settings.rag_max_context_tokens)
        document_ids = sorted({passage.hit.document_id for passage in passages})
        history = self.store.history_for_prompt(conversation, user_id=user_id, before_message_id=user_message.id, limit=settings.rag_history_messages)
        messages = build_messages(question=question, passages=passages, history=history, max_history_tokens=settings.rag_max_history_tokens)
        self.db.rollback()

        result = groq_client.complete_json(messages)
        if result.finish_reason not in (None, "stop"):
            # A reply cut short is never presented as a finished answer.
            logger.warning("The LLM stopped early: %s", result.finish_reason)
            raise LLMBadResponse()
        self._apply_reply(assistant, result, passages, document_ids)

    def _apply_reply(self, assistant: Message, result: groq_client.LLMResult, passages: list[ContextPassage], document_ids: list[int]) -> None:
        reply = citation_rules.parse_reply(result.content)
        if reply.status == "insufficient_evidence":
            self._finish(assistant, status=MESSAGE_INSUFFICIENT_EVIDENCE, content=INSUFFICIENT_ANSWER, reason=REASON_MODEL_DECLINED, citations=[], context_document_ids=[], model=result.model)
            return
        validated = citation_rules.validate(reply, passages)
        if not validated.answer or not validated.citations:
            # An answer that cannot point to retrieved evidence is not grounded.
            self._finish(assistant, status=MESSAGE_INSUFFICIENT_EVIDENCE, content=INSUFFICIENT_ANSWER, reason=REASON_NO_VALID_CITATIONS, citations=[], context_document_ids=[], model=result.model)
            return
        self._finish(assistant, status=MESSAGE_COMPLETED, content=validated.answer, reason=None, citations=validated.citations, context_document_ids=document_ids, model=result.model)

    def ask(self, conversation: Conversation, *, user_id: int, question: str, client_message_id: str | None, filters: SearchFilters) -> RagResult:
        user_message, assistant, should_generate = self._save_question(conversation, question=question, client_message_id=client_message_id)
        replayed = not should_generate
        if should_generate:
            try:
                self._generate(conversation, user_id, question, user_message, assistant, filters)
            except LLMError as error:
                self.db.rollback()
                self._finish(assistant, status=MESSAGE_FAILED, content=FAILED_ANSWER, reason=error.code, citations=[], context_document_ids=[], model=None)
                raise RagGenerationFailed(error, user_message, assistant) from error
            except Exception:
                self.db.rollback()
                logger.exception("Answer generation failed for conversation %s", conversation.id)
                self._finish(assistant, status=MESSAGE_FAILED, content=FAILED_ANSWER, reason="internal_error", citations=[], context_document_ids=[], model=None)
                raise RagGenerationFailed(LLMError(), user_message, assistant) from None
        self.db.refresh(assistant)
        # Applies to replays too: a saved answer is only returned if its sources are still readable.
        visible = self.store.apply_current_access([assistant], user_id=user_id, tenant_id=conversation.tenant_id)[0]
        return RagResult(conversation=conversation, user_message=user_message, assistant=visible, replayed=replayed)
