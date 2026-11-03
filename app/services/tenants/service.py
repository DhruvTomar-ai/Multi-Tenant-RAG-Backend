from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models.role import Role
from app.db.models.team import Team
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.models.user_team_membership import UserTeamMembership
from app.db.models.user_tenant_membership import UserTenantMembership
from app.security.authorization import AuthorizationService
from app.security.permissions import (
    DEFAULT_MEMBER_ROLE,
    TEAM_ACCESS_ALL,
    TEAM_MANAGE,
    TENANT_CREATOR_ROLE,
    TENANT_MANAGE,
    seed_default_roles,
)


class RoleGrantForbidden(Exception):
    """The acting user tried to grant a role with permissions they do not hold."""


class TenantService:
    def __init__(self, db: Session):
        self.db = db
        self.authorization = AuthorizationService(db)

    def get_user_tenant_membership(self, user_id: int, tenant_id: int) -> UserTenantMembership | None:
        return self.db.execute(
            select(UserTenantMembership).where(
                UserTenantMembership.user_id == user_id,
                UserTenantMembership.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

    def get_user_team_membership(self, user_id: int, team_id: int, tenant_id: int | None = None) -> UserTeamMembership | None:
        query = select(UserTeamMembership).where(UserTeamMembership.user_id == user_id, UserTeamMembership.team_id == team_id)
        if tenant_id is not None:
            query = query.where(UserTeamMembership.tenant_id == tenant_id)
        return self.db.execute(query).scalar_one_or_none()

    def is_user_member_of_tenant(self, user_id: int, tenant_id: int) -> bool:
        return self.authorization.is_tenant_member(user_id, tenant_id)

    def can_see_all_teams(self, user_id: int, tenant_id: int) -> bool:
        return self.authorization.has_tenant_permission(user_id, tenant_id, TEAM_ACCESS_ALL)

    def can_manage_tenant(self, user_id: int, tenant_id: int) -> bool:
        return self.authorization.has_tenant_permission(user_id, tenant_id, TENANT_MANAGE)

    def can_manage_team(self, user_id: int, team_id: int, tenant_id: int) -> bool:
        # active_only=False so that a manager can reactivate an inactive team.
        return self.authorization.has_team_permission(user_id, tenant_id, team_id, TEAM_MANAGE, active_only=False)

    def can_access_team(self, user_id: int, team_id: int, tenant_id: int) -> bool:
        return self.can_see_all_teams(user_id, tenant_id) or self.authorization.is_team_member(user_id, tenant_id, team_id)

    def get_role_by_name(self, tenant_id: int, name: str) -> Role | None:
        return self.db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == name))

    def _require_role(self, tenant_id: int, name: str) -> Role:
        role = self.get_role_by_name(tenant_id, name)
        if role is None:
            raise ValueError(f"Unknown role: {name}")
        return role

    def create_tenant(self, name: str, slug: str, description: str | None = None, owner_user_id: int | None = None) -> Tenant:
        tenant = Tenant(name=name, slug=slug, description=description)
        self.db.add(tenant)
        self.db.flush()

        roles = seed_default_roles(self.db, tenant.id)
        if owner_user_id is not None:
            self.db.add(UserTenantMembership(user_id=owner_user_id, tenant_id=tenant.id, role_id=roles[TENANT_CREATOR_ROLE].id))

        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def list_tenants(self) -> list[Tenant]:
        return self.db.scalars(select(Tenant).order_by(Tenant.id)).all()

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        return self.db.get(Tenant, tenant_id)

    def update_tenant(self, tenant_id: int, **kwargs) -> Tenant | None:
        tenant = self.db.get(Tenant, tenant_id)
        if not tenant:
            return None
        for field, value in kwargs.items():
            if value is not None:
                setattr(tenant, field, value)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def add_tenant_member(self, tenant_id: int, user: User, role_name: str = DEFAULT_MEMBER_ROLE) -> UserTenantMembership:
        role = self._require_role(tenant_id, role_name)
        membership = self.get_user_tenant_membership(user.id, tenant_id)
        if membership is None:
            membership = UserTenantMembership(user_id=user.id, tenant_id=tenant_id, role_id=role.id)
            self.db.add(membership)
        else:
            membership.role_id = role.id
            membership.is_active = True
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def list_tenant_members(self, tenant_id: int) -> list[UserTenantMembership]:
        stmt = (
            select(UserTenantMembership)
            .options(joinedload(UserTenantMembership.user), joinedload(UserTenantMembership.role))
            .where(UserTenantMembership.tenant_id == tenant_id, UserTenantMembership.is_active.is_(True))
            .order_by(UserTenantMembership.id)
        )
        return list(self.db.scalars(stmt).all())

    def list_team_memberships(self, tenant_id: int, team_id: int) -> list[UserTeamMembership]:
        stmt = (
            select(UserTeamMembership)
            .options(joinedload(UserTeamMembership.user), joinedload(UserTeamMembership.role))
            .where(
                UserTeamMembership.tenant_id == tenant_id,
                UserTeamMembership.team_id == team_id,
                UserTeamMembership.is_active.is_(True),
            )
            .order_by(UserTeamMembership.user_id)
        )
        return list(self.db.scalars(stmt).all())

    def assign_user_to_team(
        self,
        tenant_id: int,
        team_id: int,
        user_id: int,
        role_name: str = DEFAULT_MEMBER_ROLE,
        *,
        acting_user_id: int,
    ) -> UserTeamMembership:
        team = self.db.get(Team, team_id)
        if team is None:
            raise ValueError("Team not found")
        if team.tenant_id != tenant_id:
            raise ValueError("Team does not belong to the tenant")

        user = self.db.get(User, user_id)
        if user is None:
            raise ValueError("User not found")

        if not self.is_user_member_of_tenant(user_id, tenant_id):
            raise ValueError("User is not a member of this tenant")

        role = self._require_role(tenant_id, role_name)
        granted = {permission.code for permission in role.permissions}
        if not granted <= self.authorization.effective_team_permissions(acting_user_id, tenant_id, team_id):
            raise RoleGrantForbidden(f"You cannot grant the role: {role_name}")

        membership = self.get_user_team_membership(user_id, team_id)
        if membership is None:
            membership = UserTeamMembership(user_id=user_id, team_id=team_id, tenant_id=tenant_id, role_id=role.id)
            self.db.add(membership)
        else:
            membership.role_id = role.id
            membership.is_active = True

        self.db.commit()
        self.db.refresh(membership)
        return membership

    def remove_user_from_team(self, tenant_id: int, team_id: int, user_id: int) -> bool:
        membership = self.get_user_team_membership(user_id, team_id, tenant_id)
        if not membership:
            return False
        self.db.delete(membership)
        self.db.commit()
        return True

    def get_user_tenants(self, user_id: int) -> list[Tenant]:
        stmt = (
            select(Tenant)
            .join(UserTenantMembership, UserTenantMembership.tenant_id == Tenant.id)
            .where(UserTenantMembership.user_id == user_id, UserTenantMembership.is_active.is_(True))
            .order_by(Tenant.id)
        )
        return self.db.scalars(stmt).all()

    def list_user_teams(self, tenant_id: int, user_id: int) -> list[Team]:
        stmt = (
            select(Team)
            .join(UserTeamMembership, UserTeamMembership.team_id == Team.id)
            .where(
                Team.tenant_id == tenant_id,
                UserTeamMembership.user_id == user_id,
                UserTeamMembership.is_active.is_(True),
            )
            .order_by(Team.id)
        )
        return self.db.scalars(stmt).all()
