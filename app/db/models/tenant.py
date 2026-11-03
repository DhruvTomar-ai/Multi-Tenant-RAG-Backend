from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.role import Role
    from app.db.models.team import Team
    from app.db.models.user_team_membership import UserTeamMembership
    from app.db.models.user_tenant_membership import UserTenantMembership


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    teams: Mapped[list["Team"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    memberships: Mapped[list["UserTenantMembership"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    team_memberships: Mapped[list["UserTeamMembership"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    roles: Mapped[list["Role"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", "slug", name="uq_tenant_name_slug"),
    )
