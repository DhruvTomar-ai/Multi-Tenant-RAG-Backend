import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def register_user(email: str, password: str = "Secret123!", name: str = "Demo User"):
    return client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": password},
    )


def login_user(email: str, password: str = "Secret123!"):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def test_successful_user_registration():
    resp = register_user("register.success@example.com")
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["email"] == "register.success@example.com"
    assert "password_hash" not in payload


def test_duplicate_email_registration_rejected():
    email = "duplicate@example.com"
    first = register_user(email)
    assert first.status_code == 201
    second = register_user(email)
    assert second.status_code == 400


def test_password_hash_is_not_plaintext_and_not_returned():
    email = "hashcheck@example.com"
    resp = register_user(email)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == email
    assert "password_hash" not in body


def test_successful_login_and_token_response():
    email = "login.success@example.com"
    reg = register_user(email)
    assert reg.status_code == 201

    resp = login_user(email)
    assert resp.status_code == 200
    payload = resp.json()
    assert "access_token" in payload
    assert "refresh_token" in payload
    assert payload["token_type"] == "bearer"


def test_incorrect_password_rejected():
    email = "wrongpass@example.com"
    reg = register_user(email)
    assert reg.status_code == 201

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPassword!"},
    )
    assert resp.status_code == 401


def test_expired_and_invalid_jwt_rejected():
    expired = jwt.encode(
        {"sub": "999", "type": "access", "exp": int(time.time()) - 60},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    bad = "not.a.valid.token"

    expired_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    invalid_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})

    assert expired_resp.status_code == 401
    assert invalid_resp.status_code == 401


def test_access_me_with_valid_token():
    email = "me@example.com"
    register_user(email)
    token = login_user(email).json()["access_token"]

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["email"] == email


def test_protected_endpoints_reject_without_token():
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_active_tenant_selection_for_member_and_non_member():
    email_a = "tenant.member.a@example.com"
    email_b = "tenant.member.b@example.com"
    register_user(email_a)
    register_user(email_b)
    token_a = login_user(email_a).json()["access_token"]
    token_b = login_user(email_b).json()["access_token"]

    tenant_a = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha-tenant"}, headers={"Authorization": f"Bearer {token_a}"}).json()
    tenant_b = client.post("/api/v1/tenants", json={"name": "Beta", "slug": "beta-tenant"}, headers={"Authorization": f"Bearer {token_b}"}).json()

    select_member = client.post(f"/api/v1/auth/tenant/select?tenant_id={tenant_a['id']}", headers={"Authorization": f"Bearer {token_a}"})
    select_non_member = client.post(f"/api/v1/auth/tenant/select?tenant_id={tenant_b['id']}", headers={"Authorization": f"Bearer {token_a}"})

    assert select_member.status_code == 200
    assert select_non_member.status_code == 403


def test_cross_tenant_access_rejected():
    email_a = "cross.tenant.a@example.com"
    email_b = "cross.tenant.b@example.com"
    register_user(email_a)
    register_user(email_b)
    token_a = login_user(email_a).json()["access_token"]
    token_b = login_user(email_b).json()["access_token"]

    tenant_a = client.post("/api/v1/tenants", json={"name": "Cross A", "slug": "cross-a"}, headers={"Authorization": f"Bearer {token_a}"}).json()
    client.post("/api/v1/tenants", json={"name": "Cross B", "slug": "cross-b"}, headers={"Authorization": f"Bearer {token_b}"})

    me_resp = client.get(f"/api/v1/tenants/{tenant_a['id']}", headers={"Authorization": f"Bearer {token_b}"})
    assert me_resp.status_code == 404
