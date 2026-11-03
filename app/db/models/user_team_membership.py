from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.role import Role
    from app.db.models.team import Team
    from app.db.models.tenant import Tenant
    from app.db.models.user import User


class UserTeamMembership(Base):
    __tablename__ = "user_team_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)

    user: Mapped["User"] = relationship(back_populates="team_memberships")
    team: Mapped["Team"] = relationship(
        back_populates="memberships",
        primaryjoin="foreign(UserTeamMembership.team_id) == Team.id",
    )
    tenant: Mapped["Tenant"] = relationship(back_populates="team_memberships")
    # The joins name a single column so that `tenant` stays the only writer of tenant_id.
    role: Mapped["Role"] = relationship(primaryjoin="foreign(UserTeamMembership.role_id) == Role.id")

    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_user_team_membership"),
        # The team must belong to this tenant.
        ForeignKeyConstraint(
            ["team_id", "tenant_id"],
            ["teams.id", "teams.tenant_id"],
            ondelete="CASCADE",
            name="fk_user_team_membership_team_tenant",
        ),
        # The user must already be a member of this tenant.
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["user_tenant_memberships.user_id", "user_tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_user_team_membership_tenant_membership",
        ),
        # The role must belong to this tenant.
        ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            name="fk_user_team_membership_role_tenant",
        ),
    )
