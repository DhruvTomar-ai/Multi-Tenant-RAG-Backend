from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_current_user
from app.db.models.user import User
from app.db.models.user_tenant_membership import UserTenantMembership
from app.schemas.auth import LoginRequest, RefreshTokenRequest, RegisterRequest, TokenResponse, UserSessionInfo
from app.security.auth import decode_access_token, decode_refresh_token, get_password_hash, oauth2_scheme
from app.services.auth.service import AuthService
from app.services.tenants.service import TenantService
from app.services.users.service import UserService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserSessionInfo, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if UserService(db).get_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already registered")

    username = UserService(db).generate_unique_username(payload.name)
    user = UserService(db).create_user(
        email=payload.email,
        username=username,
        password_hash=get_password_hash(payload.password),
        full_name=payload.name,
    )
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = AuthService(db).authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token_data = AuthService(db).issue_tokens(user)
    return TokenResponse(**token_data)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        token_payload = decode_refresh_token(payload.refresh_token)
    except HTTPException:
        raise

    user_id = int(token_payload["sub"])
    user = UserService(db).get_user(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    tenant_id = token_payload.get("tenant_id")
    new_tokens = AuthService(db).issue_tokens(user, tenant_id=tenant_id)
    return TokenResponse(**new_tokens)


@router.get("/me", response_model=UserSessionInfo)
def get_current_user_profile(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    payload = decode_access_token(token)
    user_id = int(payload["sub"])
    user = UserService(db).get_user(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


@router.post("/tenant/select", response_model=dict)
def select_active_tenant(tenant_id: int, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    membership = db.query(UserTenantMembership).filter_by(user_id=current_user.id, tenant_id=tenant_id, is_active=True).first()
    if not membership:
        raise HTTPException(status_code=403, detail="User is not a member of the selected tenant")

    tenant = TenantService(db).get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return {"tenant_id": tenant.id, "tenant_name": tenant.name, "selected": True}
