from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def _build_token_payload(subject: int | str, *, token_type: str, tenant_id: int | None = None, expires_delta: timedelta | None = None) -> dict:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return payload


def create_access_token(user_id: int, tenant_id: int | None = None) -> str:
    payload = _build_token_payload(
        user_id,
        token_type="access",
        tenant_id=tenant_id,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, tenant_id: int | None = None) -> str:
    payload = _build_token_payload(
        user_id,
        token_type="refresh",
        tenant_id=tenant_id,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )
    return jwt.encode(payload, settings.jwt_refresh_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, secret_key: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unexpected token type")

    if payload.get("sub") is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    return payload


def decode_access_token(token: str) -> dict:
    return decode_token(token, secret_key=settings.jwt_secret_key, expected_type="access")


def decode_refresh_token(token: str) -> dict:
    return decode_token(token, secret_key=settings.jwt_refresh_secret_key, expected_type="refresh")
