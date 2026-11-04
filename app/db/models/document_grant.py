from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.document import utc_now


class DocumentGrant(Base):
    """Read access to one document for one member of the same tenant. It grants nothing else."""

    __tablename__ = "document_grants"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(nullable=False)
    tenant_id: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        # A grant must carry the same tenant and team as its document.
        ForeignKeyConstraint(
            ["document_id", "tenant_id", "team_id"],
            ["documents.id", "documents.tenant_id", "documents.team_id"],
            ondelete="CASCADE",
            name="fk_document_grant_document_tenant_team",
        ),
        # The database refuses a grant to anyone outside the document's tenant.
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_document_grant_tenant_membership",
        ),
        UniqueConstraint("document_id", "user_id", name="uq_document_grant_document_user"),
        Index("ix_document_grants_user_tenant", "user_id", "tenant_id"),
    )
