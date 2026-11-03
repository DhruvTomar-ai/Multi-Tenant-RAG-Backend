from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.role import Role
    from app.db.models.tenant import Tenant
    from app.db.models.user import User


class UserTenantMembership(Base):
    __tablename__ = "user_tenant_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)

    user: Mapped["User"] = relationship(back_populates="tenant_memberships")
    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")
    # The join names only role_id so that `tenant` stays the single writer of tenant_id.
    role: Mapped["Role"] = relationship(primaryjoin="foreign(UserTenantMembership.role_id) == Role.id")

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_membership"),
        ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            name="fk_user_tenant_membership_role_tenant",
        ),
    )
