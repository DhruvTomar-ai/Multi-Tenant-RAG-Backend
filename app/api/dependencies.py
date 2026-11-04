from collections.abc import Callable, Generator

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.team import Team
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.models.user_tenant_membership import UserTenantMembership
from app.db.session import SessionLocal
from app.security.auth import decode_access_token, oauth2_scheme
from app.security.authorization import AuthorizationService
from app.security.permissions import DOCUMENT_READ

security = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(token)
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None:
        return None
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except HTTPException:
        return None
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def get_current_tenant(
    request: Request,
    tenant_id: int | None = Header(default=None, alias="X-Tenant-Id"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Tenant:
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header is required")

    membership = (
        db.query(UserTenantMembership)
        .filter_by(user_id=current_user.id, tenant_id=tenant_id, is_active=True)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not a member of this tenant")

    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    request.state.tenant_id = tenant.id
    request.state.current_tenant = tenant
    return tenant


def require_tenant_membership(
    tenant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    # The same 403 covers "not a member", "inactive tenant" and "no such tenant",
    # so outsiders cannot use this check to discover which tenants exist.
    if not AuthorizationService(db).is_tenant_member(current_user.id, tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not a member of this tenant")
    return current_user


def require_tenant_permission(permission: str) -> Callable[..., User]:
    def dependency(
        tenant_id: int,
        current_user: User = Depends(require_tenant_membership),
        db: Session = Depends(get_db),
    ) -> User:
        if not AuthorizationService(db).has_tenant_permission(current_user.id, tenant_id, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return current_user

    return dependency


def require_document_read(
    tenant_id: int,
    team_id: int,
    document_id: int,
    current_user: User = Depends(require_tenant_membership),
    db: Session = Depends(get_db),
) -> User:
    """Team permission, upload ownership or a grant. A refusal looks the same whether or not the document exists."""
    team_in_tenant = db.scalar(select(Team.id).where(Team.id == team_id, Team.tenant_id == tenant_id))
    if team_in_tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if not AuthorizationService(db).can_read_document(current_user.id, tenant_id, team_id, document_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {DOCUMENT_READ}")
    return current_user


def require_team_permission(permission: str) -> Callable[..., User]:
    """Check, in order: authenticated, tenant member, team in tenant, permission in team."""

    def dependency(
        tenant_id: int,
        team_id: int,
        current_user: User = Depends(require_tenant_membership),
        db: Session = Depends(get_db),
    ) -> User:
        team_in_tenant = db.scalar(select(Team.id).where(Team.id == team_id, Team.tenant_id == tenant_id))
        if team_in_tenant is None:
            # Also the answer for a team in another tenant: never confirm that it exists.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        if not AuthorizationService(db).has_team_permission(current_user.id, tenant_id, team_id, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return current_user

    return dependency
