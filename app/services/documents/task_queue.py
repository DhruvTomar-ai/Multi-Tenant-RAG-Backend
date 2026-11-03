from __future__ import annotations

from app.workers.celery_app import INGEST_DOCUMENT_TASK, celery_app


def enqueue_ingestion_job(job_id: int, task_id: str) -> None:
    """Send the job to the worker through Redis.

    Sent by name so the API process never imports the extraction code. retry=False makes
    a Redis outage fail the upload promptly rather than leaving the request hanging.
    """
    celery_app.send_task(INGEST_DOCUMENT_TASK, args=[job_id], task_id=task_id, retry=False)
