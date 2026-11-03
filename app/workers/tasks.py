from __future__ import annotations

import logging

from app.core.config import settings
from app.db import session as db_session
from app.services.documents.errors import PermanentIngestionError
from app.services.documents.ingestion import IngestionService
from app.workers.celery_app import INGEST_DOCUMENT_TASK, celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name=INGEST_DOCUMENT_TASK, throws=(PermanentIngestionError,))
def ingest_document(self, job_id: int) -> None:
    db = db_session.SessionLocal()
    service = IngestionService(db)
    try:
        service.process_job(job_id)
    except PermanentIngestionError as exc:
        # Retrying can't help: the file itself is the problem.
        db.rollback()
        service.mark_failed(job_id, str(exc))
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Ingestion job %s failed", job_id)
        attempt = self.request.retries
        max_retries = settings.ingestion_max_retries
        if attempt >= max_retries:
            # The real error is in the worker log. Job errors are visible through the API.
            service.mark_failed(job_id, f"Processing failed after {attempt + 1} attempts.")
            raise
        service.mark_pending_for_retry(job_id, "A temporary error occurred. The job will be retried.")
        countdown = settings.ingestion_retry_backoff_seconds * (2**attempt)
        raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries)
    finally:
        db.close()
