from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.document import STATUS_PENDING, utc_now

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.user import User


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="jobs")
    created_by: Mapped["User"] = relationship(back_populates="created_jobs")

    __table_args__ = (
        # A job must carry the same tenant and team as its document.
        ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE",
            name="fk_job_document_tenant_team",
        ),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_document_job_status"),
        Index("ix_document_jobs_tenant_team_status", "tenant_id", "team_id", "status"),
    )
