"""Turns authorized search hits into a bounded, deduplicated list of context passages.

Token counts are estimates: one token per three characters, which runs high for English, so the
real prompt stays inside the limit. This only ever narrows what Workflow 6 already authorized.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.embeddings.search import SearchHit


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1


@dataclass(frozen=True)
class ContextPassage:
    hit: SearchHit
    text: str
    truncated: bool = False


def _fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode("utf-8")).hexdigest()


def relevant_hits(hits: list[SearchHit], min_similarity: float) -> list[SearchHit]:
    return [hit for hit in hits if hit.similarity_score >= min_similarity]


def build_passages(hits: list[SearchHit], *, max_tokens: int) -> list[ContextPassage]:
    """Hits arrive best first, so the most relevant passages survive the limit."""
    passages: list[ContextPassage] = []
    seen_chunks: set[int] = set()
    seen_text: set[str] = set()
    used = 0
    for hit in hits:
        fingerprint = _fingerprint(hit.chunk_text)
        if hit.chunk_id in seen_chunks or fingerprint in seen_text:
            continue
        seen_chunks.add(hit.chunk_id)
        seen_text.add(fingerprint)
        cost = estimate_tokens(hit.chunk_text)
        if used + cost > max_tokens:
            if passages:
                break
            # One passage larger than the whole limit: keep the start rather than nothing.
            passages.append(ContextPassage(hit=hit, text=hit.chunk_text[: max_tokens * 3], truncated=True))
            break
        passages.append(ContextPassage(hit=hit, text=hit.chunk_text))
        used += cost
    return passages
