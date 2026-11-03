"""The single source of truth for "may this user do this?".

The rules:
- Tenant membership (active user membership in an active tenant) is always required.
- Inside a team, a user holds the permissions of their team role.
- A tenant role only reaches into teams if it holds TEAM_ACCESS_ALL. A plain tenant member
  therefore gets nothing from their tenant role inside a team they have not joined.
"""
from __future__ import annotations

from sqlalchemy import and_, select, union
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.permission import Permission
from app.db.models.team import Team
from app.db.models.tenant import Tenant
from app.db.models.user_team_membership import UserTeamMembership
from app.db.models.user_tenant_membership import UserTenantMembership
from app.security.permissions import TEAM_ACCESS_ALL, split_permission


def _role_grants(role_id_column, permission: str) -> ColumnElement[bool]:
    resource, action = split_permission(permission)
    return (
        select(Permission.id)
        .where(Permission.role_id == role_id_column, Permission.resource == resource, Permission.action == action)
        .exists()
    )


def visible_team_ids(user_id: int, permission: str, *, active_only: bool = True):
    """A subquery of team IDs where the user holds the permission.

    Team IDs are globally unique and composite foreign keys tie every row to one tenant,
    so filtering a table with `Model.team_id.in_(...)` enforces tenant and team isolation.
    """
    through_team_role = (
        select(UserTeamMembership.team_id)
        .join(Team, Team.id == UserTeamMembership.team_id)
        .join(Tenant, Tenant.id == Team.tenant_id)
        .join(
            UserTenantMembership,
            and_(
                UserTenantMembership.user_id == UserTeamMembership.user_id,
                UserTenantMembership.tenant_id == UserTeamMembership.tenant_id,
            ),
        )
        .where(
            UserTeamMembership.user_id == user_id,
            UserTeamMembership.is_active.is_(True),
            UserTenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
            _role_grants(UserTeamMembership.role_id, permission),
        )
    )
    through_tenant_role = (
        select(Team.id)
        .join(Tenant, Tenant.id == Team.tenant_id)
        .join(UserTenantMembership, UserTenantMembership.tenant_id == Team.tenant_id)
        .where(
            UserTenantMembership.user_id == user_id,
            UserTenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
            _role_grants(UserTenantMembership.role_id, TEAM_ACCESS_ALL),
            _role_grants(UserTenantMembership.role_id, permission),
        )
    )
    if active_only:
        through_team_role = through_team_role.where(Team.is_active.is_(True))
        through_tenant_role = through_tenant_role.where(Team.is_active.is_(True))
    return union(through_team_role, through_tenant_role)


class AuthorizationService:
    def __init__(self, db: Session):
        self.db = db

    def _active_tenant_membership(self, user_id: int, tenant_id: int) -> UserTenantMembership | None:
        return self.db.scalar(
            select(UserTenantMembership)
            .join(Tenant, Tenant.id == UserTenantMembership.tenant_id)
            .where(
                UserTenantMembership.user_id == user_id,
                UserTenantMembership.tenant_id == tenant_id,
                UserTenantMembership.is_active.is_(True),
                Tenant.is_active.is_(True),
            )
        )

    def _role_permissions(self, role_id: int) -> set[str]:
        rows = self.db.execute(select(Permission.resource, Permission.action).where(Permission.role_id == role_id))
        return {f"{resource}.{action}" for resource, action in rows}

    def is_tenant_member(self, user_id: int, tenant_id: int) -> bool:
        return self._active_tenant_membership(user_id, tenant_id) is not None

    def has_tenant_permission(self, user_id: int, tenant_id: int, permission: str) -> bool:
        membership = self._active_tenant_membership(user_id, tenant_id)
        return membership is not None and permission in self._role_permissions(membership.role_id)

    def has_team_permission(self, user_id: int, tenant_id: int, team_id: int, permission: str, *, active_only: bool = True) -> bool:
        stmt = select(Team.id).where(
            Team.id == team_id,
            Team.tenant_id == tenant_id,
            Team.id.in_(visible_team_ids(user_id, permission, active_only=active_only)),
        )
        return self.db.scalar(stmt) is not None

    def is_team_member(self, user_id: int, tenant_id: int, team_id: int) -> bool:
        if not self.is_tenant_member(user_id, tenant_id):
            return False
        stmt = select(UserTeamMembership.id).where(
            UserTeamMembership.user_id == user_id,
            UserTeamMembership.tenant_id == tenant_id,
            UserTeamMembership.team_id == team_id,
            UserTeamMembership.is_active.is_(True),
        )
        return self.db.scalar(stmt) is not None

    def effective_team_permissions(self, user_id: int, tenant_id: int, team_id: int) -> set[str]:
        tenant_membership = self._active_tenant_membership(user_id, tenant_id)
        if tenant_membership is None:
            return set()

        effective: set[str] = set()
        tenant_permissions = self._role_permissions(tenant_membership.role_id)
        if TEAM_ACCESS_ALL in tenant_permissions:
            effective |= tenant_permissions

        team_role_id = self.db.scalar(
            select(UserTeamMembership.role_id).where(
                UserTeamMembership.user_id == user_id,
                UserTeamMembership.tenant_id == tenant_id,
                UserTeamMembership.team_id == team_id,
                UserTeamMembership.is_active.is_(True),
            )
        )
        if team_role_id is not None:
            effective |= self._role_permissions(team_role_id)
        return effective
