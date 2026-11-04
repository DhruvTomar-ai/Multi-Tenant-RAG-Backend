"""Parses the LLM's reply and validates its citations in backend code. The LLM is never trusted on this."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services.rag.context import ContextPassage
from app.services.rag.groq_client import LLMBadResponse

logger = logging.getLogger(__name__)

_MARKER = re.compile(r"\s*\[chunk:\s*([^\]]*)\]", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedReply:
    status: str
    answer: str
    cited_chunk_ids: tuple[Any, ...]


@dataclass(frozen=True)
class ValidatedAnswer:
    answer: str
    citations: list[dict[str, Any]]
    rejected_citations: int


def parse_reply(content: str) -> ParsedReply:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("The LLM reply was not valid JSON")
        raise LLMBadResponse() from exc
    if not isinstance(value, dict):
        raise LLMBadResponse()
    status, answer, cited = value.get("status"), value.get("answer", ""), value.get("cited_chunk_ids", [])
    if status not in ("answered", "insufficient_evidence") or not isinstance(answer, str) or not isinstance(cited, list):
        logger.warning("The LLM reply did not match the required form")
        raise LLMBadResponse()
    return ParsedReply(status=status, answer=answer, cited_chunk_ids=tuple(cited))


def _as_chunk_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def citation_from_passage(passage: ContextPassage) -> dict[str, Any]:
    """Built only from the retrieved row, so the LLM cannot influence any field."""
    hit = passage.hit
    citation: dict[str, Any] = {
        "chunk_id": hit.chunk_id,
        "chunk_index": hit.chunk_index,
        "document_id": hit.document_id,
        "document_title": hit.document_title,
        "team_id": hit.team_id,
        "similarity_score": round(hit.similarity_score, 6),
        "metadata": {
            "file_type": hit.file_type,
            "version": hit.document_version,
            "document_type": hit.document_type,
            "department": hit.department,
            "category": hit.category,
        },
    }
    # Only stated when the extractor recorded it. Page numbers are never guessed.
    if hit.page_label is not None:
        citation["page_label"] = hit.page_label
    return citation


def validate(reply: ParsedReply, passages: list[ContextPassage]) -> ValidatedAnswer:
    """Keeps only citations of passages sent in this request, and removes every other marker."""
    by_id = {passage.hit.chunk_id: passage for passage in passages}
    valid_order: list[int] = []
    rejected = 0

    def consider(value: Any) -> bool:
        nonlocal rejected
        chunk_id = _as_chunk_id(value)
        if chunk_id is None or chunk_id not in by_id:
            rejected += 1
            return False
        if chunk_id not in valid_order:
            valid_order.append(chunk_id)
        return True

    def replace_marker(match: re.Match) -> str:
        return f" [chunk:{_as_chunk_id(match.group(1))}]" if consider(match.group(1)) else ""

    answer = _MARKER.sub(replace_marker, reply.answer).strip()
    for value in reply.cited_chunk_ids:
        consider(value)
    if rejected:
        logger.warning("Removed %s citation(s) that were not in this request's retrieval", rejected)
    return ValidatedAnswer(answer=answer, citations=[citation_from_passage(by_id[chunk_id]) for chunk_id in valid_order], rejected_citations=rejected)
