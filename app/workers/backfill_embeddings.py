"""Queue embeddings for documents that were ingested before embeddings existed, or that failed.

    python -m app.workers.backfill_embeddings [--include-failed]

Safe to run repeatedly: a task for an already embedded document does nothing, and existing
vectors are never recreated. A running worker does the embedding.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.db.models.document import EMBEDDING_FAILED, EMBEDDING_PENDING, STATUS_COMPLETED, Document
from app.services.documents import task_queue

_PAGE_SIZE = 500


def queue_pending_documents(db: Session, *, include_failed: bool = False) -> int:
    statuses = [EMBEDDING_PENDING, EMBEDDING_FAILED] if include_failed else [EMBEDDING_PENDING]
    queued = 0
    last_id = 0
    while True:
        ids = db.scalars(
            select(Document.id)
            .where(Document.id > last_id, Document.status == STATUS_COMPLETED, Document.embedding_status.in_(statuses), Document.is_active.is_(True), Document.deleted_at.is_(None))
            .order_by(Document.id)
            .limit(_PAGE_SIZE)
        ).all()
        db.rollback()
        if not ids:
            return queued
        for document_id in ids:
            task_queue.enqueue_embedding_job(document_id)
            queued += 1
        last_id = ids[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-failed", action="store_true")
    args = parser.parse_args()
    with db_session.SessionLocal() as db:
        print(f"Queued {queue_pending_documents(db, include_failed=args.include_failed)} document(s).")


if __name__ == "__main__":
    main()
