from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.tenant import Tenant
    from app.db.models.user_team_membership import UserTeamMembership


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="teams")
    memberships: Mapped[list["UserTeamMembership"]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
        primaryjoin="Team.id == foreign(UserTeamMembership.team_id)",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_team_tenant_name"),
        # Target of composite foreign keys: lets the database reject any row whose
        # team belongs to a different tenant than the row claims.
        UniqueConstraint("id", "tenant_id", name="uq_team_id_tenant"),
    )
