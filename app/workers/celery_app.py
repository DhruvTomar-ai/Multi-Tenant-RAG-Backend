"""Celery configuration only, so the API can import this to send tasks without loading
the extraction code. Start a worker with:

    celery -A app.workers.celery_app:celery_app worker --loglevel=info --pool=solo

--pool=solo is required on native Windows, where Celery's default pool does not work.
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

INGEST_DOCUMENT_TASK = "documents.ingest"
EMBED_DOCUMENT_TASK = "documents.embed"

celery_app = Celery(
    "multi_tenant_rag",
    broker=settings.celery_broker_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # PostgreSQL is the source of truth for job status, so no result backend is used.
    task_ignore_result=True,
    # Acknowledge after the task finishes, so a crashed worker's task is delivered again.
    # This is safe because processing a job twice cannot create duplicate chunks.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 3600,
        "socket_connect_timeout": 5,
    },
)
