"""Permission names and the default roles every tenant starts with.

This is the only place role names are mapped to permissions. Everything else checks
permissions, so changing what a role may do never requires touching a route.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.permission import Permission
from app.db.models.role import Role

TENANT_MANAGE = "tenant.manage"
TEAM_MANAGE = "team.manage"
# The explicit administrative override: without it, access to a team requires team membership.
TEAM_ACCESS_ALL = "team.access_all"
DOCUMENT_UPLOAD = "document.upload"
DOCUMENT_READ = "document.read"
DOCUMENT_DELETE = "document.delete"

DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({TENANT_MANAGE, TEAM_MANAGE, TEAM_ACCESS_ALL, DOCUMENT_UPLOAD, DOCUMENT_READ, DOCUMENT_DELETE}),
    "manager": frozenset({TEAM_MANAGE, DOCUMENT_UPLOAD, DOCUMENT_READ, DOCUMENT_DELETE}),
    "member": frozenset({DOCUMENT_UPLOAD, DOCUMENT_READ}),
    "viewer": frozenset({DOCUMENT_READ}),
}

DEFAULT_ROLE_DESCRIPTIONS: dict[str, str] = {
    "admin": "Full control of the tenant and every team in it.",
    "manager": "Manages a team and its documents.",
    "member": "Reads and uploads documents.",
    "viewer": "Read-only access to documents.",
}

TENANT_CREATOR_ROLE = "admin"
DEFAULT_MEMBER_ROLE = "member"


def split_permission(permission: str) -> tuple[str, str]:
    resource, action = permission.split(".", 1)
    return resource, action


def seed_default_roles(db: Session, tenant_id: int) -> dict[str, Role]:
    """Add the default roles to a new tenant. The caller owns the transaction."""
    roles: dict[str, Role] = {}
    for name, permissions in DEFAULT_ROLE_PERMISSIONS.items():
        role = Role(tenant_id=tenant_id, name=name, description=DEFAULT_ROLE_DESCRIPTIONS[name])
        for permission in sorted(permissions):
            resource, action = split_permission(permission)
            role.permissions.append(Permission(resource=resource, action=action))
        db.add(role)
        roles[name] = role
    db.flush()
    return roles
