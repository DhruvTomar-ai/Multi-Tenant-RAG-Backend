from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.role import Role


class Permission(Base):
    """One action a role may perform, e.g. resource="document", action="upload"."""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), nullable=False)

    role: Mapped["Role"] = relationship(back_populates="permissions")

    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            ondelete="CASCADE",
            name="fk_permission_role_tenant",
        ),
        UniqueConstraint("tenant_id", "role_id", "resource", "action", name="uq_permission_scope_action"),
    )

    @property
    def code(self) -> str:
        return f"{self.resource}.{self.action}"
