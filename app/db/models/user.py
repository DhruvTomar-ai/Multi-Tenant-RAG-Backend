from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.document_job import DocumentJob
    from app.db.models.user_team_membership import UserTeamMembership
    from app.db.models.user_tenant_membership import UserTenantMembership


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    tenant_memberships: Mapped[list["UserTenantMembership"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    team_memberships: Mapped[list["UserTeamMembership"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    # No delete cascade: documents belong to the team and outlive the person who uploaded them.
    uploaded_documents: Mapped[list["Document"]] = relationship(back_populates="uploaded_by")
    created_jobs: Mapped[list["DocumentJob"]] = relationship(back_populates="created_by")

    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        UniqueConstraint("username", name="uq_user_username"),
    )
