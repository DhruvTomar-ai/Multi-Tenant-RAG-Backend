from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.user import User
from app.security.auth import create_access_token, create_refresh_token, verify_password
from app.services.users.service import UserService


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def authenticate_user(self, email: str, password: str) -> User | None:
        user = UserService(self.db).get_by_email(email)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def issue_tokens(self, user: User, tenant_id: int | None = None) -> dict:
        return {
            "access_token": create_access_token(user.id, tenant_id=tenant_id),
            "refresh_token": create_refresh_token(user.id, tenant_id=tenant_id),
            "token_type": "bearer",
        }
