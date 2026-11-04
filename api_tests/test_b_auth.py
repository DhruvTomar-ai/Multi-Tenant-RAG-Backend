"""Registration, login, refresh, JWT handling and authentication on every route."""
import re

import jwt
import pytest

from api_tests import support
from api_tests.support import ANON, API, BAD_TOKEN, NOT_AUTHENTICATED, NOT_MEMBER, PASSWORD, Actor, bearer, make_token, new_user
from app.core.config import settings
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.main import app

ME = "/auth/me"
LOGIN = "/auth/login"
REGISTER = "/auth/register"
REFRESH = "/auth/refresh"
SELECT = "/auth/tenant/select"
PUBLIC = {REGISTER, LOGIN, REFRESH}
USERS = "/users"


def _protected_routes():
    found = []
    for path, operations in app.openapi()["paths"].items():
        template = path[len(API):]
        if template not in PUBLIC:
            found.extend((method.upper(), template) for method in operations)
    return sorted(found)


ROUTES = _protected_routes()
assert len(ROUTES) >= 20, f"Route discovery found only {len(ROUTES)} routes"
ROUTE_IDS = [f"{method} {template}" for method, template in ROUTES]


def _path(template):
    return {name: 1 for name in re.findall(r"{(\w+)}", template)}


@pytest.mark.parametrize(("method", "template"), ROUTES, ids=ROUTE_IDS)
def test_every_route_rejects_a_missing_token(api, clean, method, template):
    api.check(
        ANON, method, template, path=_path(template), expect=401, detail=NOT_AUTHENTICATED,
        behavior="No token: request rejected before any work",
        why="F-1/F-2: this route has no authentication dependency." if template == USERS else "",
    )


@pytest.mark.parametrize("kind", ["garbage", "expired", "wrong_secret", "refresh_as_access"])
@pytest.mark.parametrize(("method", "template"), [r for r in ROUTES if r[1] != USERS], ids=[i for r, i in zip(ROUTES, ROUTE_IDS) if r[1] != USERS])
def test_every_route_rejects_a_bad_token(api, clean, method, template, kind):
    tokens = {
        "garbage": "not.a.token",
        "expired": make_token(1, exp_offset=-60),
        "wrong_secret": make_token(1, secret="another-secret-key-that-is-long-enough-for-hs256"),
        "refresh_as_access": make_token(1, secret=settings.jwt_refresh_secret_key, token_type="refresh"),
    }
    api.check(Actor(f"anonymous ({kind} token)"), method, template, path=_path(template), headers=bearer(tokens[kind]), expect=401, behavior=f"{kind} token rejected")


def _valid_request(api, world, template, method):
    """Real IDs and a valid body, so that only the ID under test can cause a rejection."""
    new_member = {"user_id": world.nora.id, "team_id": world.hr, "tenant_id": world.a, "role": "member"}
    bodies = {
        ("PATCH", support.TENANT): {"json": {"description": "x"}},
        ("POST", support.TENANT_MEMBERS): {"json": {"email": world.nora.email}},
        ("POST", support.TEAMS): {"json": {"name": "Unique team"}},
        ("PATCH", support.TEAM): {"json": {"description": "x"}},
        ("POST", support.TEAM_MEMBERS): {"json": new_member},
        ("POST", support.UPLOAD): {"files": support.text_file()},
        ("POST", support.GRANTS): {"json": {"user_id": world.nora.id}},
        ("POST", support.SEARCH): {"json": {"query": "anything"}},
        ("POST", support.CONVERSATIONS): {"json": {}},
        ("PATCH", support.CONVERSATION): {"json": {"title": "x"}},
        ("POST", support.MESSAGES): {"json": {"content": "anything"}},
    }
    conversation_id = None
    if "conversation_id" in template:
        conversation_id = api.setup(world.alice, "POST", support.CONVERSATIONS, path={"tenant_id": world.a}, json={}).json()["id"]
    path = {"tenant_id": world.a, "team_id": world.hr, "document_id": world.doc_hr, "job_id": world.job_hr, "user_id": world.mona.id, "conversation_id": conversation_id}
    names = re.findall(r"{(\w+)}", template)
    return {name: path[name] for name in names}, bodies.get((method, template), {})


ID_ROUTES = [r for r in ROUTES if "{" in r[1]]
ID_ROUTE_IDS = [f"{m} {t}" for m, t in ID_ROUTES]


def _id_cases():
    for method, template in ID_ROUTES:
        for name in re.findall(r"{(\w+)}", template):
            yield method, template, name


ID_CASES = list(_id_cases())
ID_CASE_NAMES = [f"{m} {t} [{n}]" for m, t, n in ID_CASES]


@pytest.mark.parametrize(("method", "template", "name"), ID_CASES, ids=ID_CASE_NAMES)
def test_every_route_rejects_a_non_integer_id(api, fresh, method, template, name):
    path, body = _valid_request(api, fresh, template, method)
    api.check(fresh.alice, method, template, path={**path, name: "abc"}, expect=422, behavior=f"{name}='abc' fails validation", **body)


@pytest.mark.parametrize(("method", "template", "name"), ID_CASES, ids=ID_CASE_NAMES)
def test_every_route_handles_a_non_existent_id(api, fresh, method, template, name):
    path, body = _valid_request(api, fresh, template, method)
    expected = (403, 404) if name in ("tenant_id", "team_id") else 404
    if template == support.TEAM_MEMBERS and method == "POST":
        expected = (403, 404)
    api.check(fresh.alice, method, template, path={**path, name: 999999999}, expect=expected, behavior=f"{name} does not exist: denied or not found, nothing else leaks", absent=["CANARY"], **body)


# --- Registration ---


def test_register_success_hides_the_password_hash(api, clean):
    response = api.check(ANON, "POST", REGISTER, expect=201, behavior="User created", json={"name": "New", "email": "new@example.com", "password": PASSWORD})
    api.verify("password" not in response.text, "Response contains a password field")


def test_register_duplicate_email_is_rejected(api, clean):
    new_user(api, "Dup")
    api.check(ANON, "POST", REGISTER, expect=400, detail="Email already registered", behavior="Duplicate rejected", json={"name": "Dup2", "email": "dup@example.com", "password": PASSWORD})


def test_register_duplicate_email_in_different_case_is_rejected(api, clean):
    new_user(api, "Case", "case@example.com")
    api.check(
        ANON, "POST", REGISTER, expect=400, behavior="Emails are treated as case-insensitive",
        json={"name": "Case2", "email": "CASE@example.com", "password": PASSWORD},
        why="BUG: a second account can exist for the same mailbox, so membership by email can reach the wrong account.",
    )


@pytest.mark.parametrize(
    "body",
    [{}, {"name": "x", "email": "x@example.com"}, {"name": "x", "password": PASSWORD}, {"email": "x@example.com", "password": PASSWORD},
     {"name": "x", "email": "not-an-email", "password": PASSWORD}, {"name": "x", "email": "x@example.com", "password": "short"}, {"name": "", "email": "x@example.com", "password": PASSWORD}],
    ids=["empty", "no_password", "no_email", "no_name", "bad_email", "short_password", "empty_name"],
)
def test_register_invalid_body_returns_422(api, clean, body):
    api.check(ANON, "POST", REGISTER, expect=422, behavior="Validation error", json=body)


def test_register_name_with_nul_character_is_rejected_cleanly(api, clean):
    api.check(
        ANON, "POST", REGISTER, expect=(400, 422), behavior="Invalid input rejected, not a server error",
        json={"name": "bad\u0000name", "email": "nul@example.com", "password": PASSWORD},
        why="BUG: PostgreSQL text cannot hold NUL and the app does not validate it.",
    )


def test_password_longer_than_72_bytes_is_not_silently_truncated(api, clean):
    email = "long@example.com"
    api.setup(ANON, "POST", REGISTER, json={"name": "Long", "email": email, "password": "a" * 72 + "RIGHT-TAIL"})
    api.check(
        ANON, "POST", LOGIN, expect=401, behavior="A password with a different ending is wrong",
        json={"email": email, "password": "a" * 72 + "WRONG-TAIL"},
        why="Weakness: bcrypt only uses 72 bytes while the schema allows 255.",
    )


# --- Login ---


def test_login_success_returns_a_token_pair(api, clean):
    new_user(api, "Lena")
    response = api.check(ANON, "POST", LOGIN, expect=200, behavior="Token pair returned", json={"email": "lena@example.com", "password": PASSWORD})
    api.verify(response.json()["token_type"] == "bearer", "token_type is not bearer")


def test_login_wrong_password(api, clean):
    new_user(api, "Lena")
    api.check(ANON, "POST", LOGIN, expect=401, detail="Invalid email or password", behavior="Rejected", json={"email": "lena@example.com", "password": "WrongPass123"})


def test_login_unknown_email_gets_the_same_answer(api, clean):
    api.check(ANON, "POST", LOGIN, expect=401, detail="Invalid email or password", behavior="Same answer as a wrong password", json={"email": "nobody@example.com", "password": PASSWORD})


@pytest.mark.parametrize("body", [{}, {"email": "x@example.com"}, {"password": PASSWORD}, {"email": "bad", "password": PASSWORD}], ids=["empty", "no_password", "no_email", "bad_email"])
def test_login_invalid_body_returns_422(api, clean, body):
    api.check(ANON, "POST", LOGIN, expect=422, behavior="Validation error", json=body)


def test_login_inactive_user_is_rejected(api, clean):
    user = new_user(api, "Gone")
    support.set_columns(User, user.id, is_active=False)
    api.check(ANON, "POST", LOGIN, expect=401, behavior="Inactive user cannot log in", json={"email": user.email, "password": PASSWORD})


# --- Tokens ---


def test_me_returns_the_token_owner(api, clean):
    user = new_user(api, "Mel")
    response = api.check(user, "GET", ME, expect=200, behavior="Own profile returned")
    api.verify(response.json()["id"] == user.id, "Wrong user returned")


@pytest.mark.parametrize(
    ("kind", "detail"),
    [("garbage", BAD_TOKEN), ("expired", BAD_TOKEN), ("wrong_secret", BAD_TOKEN), ("alg_none", BAD_TOKEN), ("tampered", BAD_TOKEN),
     ("refresh_token", BAD_TOKEN), ("refresh_type_signed_with_access_secret", "Unexpected token type"), ("no_subject", "Token missing subject"), ("deleted_user", "User not found or inactive")],
)
def test_me_rejects_bad_tokens(api, clean, kind, detail):
    user = new_user(api, "Tok")
    tokens = {
        "garbage": "not.a.token",
        "expired": make_token(user.id, exp_offset=-60),
        "wrong_secret": make_token(user.id, secret="another-secret-key-that-is-long-enough-for-hs256"),
        "alg_none": jwt.encode({"sub": str(user.id), "type": "access", "exp": 9999999999}, None, algorithm="none"),
        "tampered": user.access_token[:-2] + ("aa" if not user.access_token.endswith("aa") else "bb"),
        "refresh_token": user.refresh_token,
        "refresh_type_signed_with_access_secret": make_token(user.id, token_type="refresh"),
        "no_subject": make_token(None),
        "deleted_user": make_token(999999999),
    }
    api.check(Actor(f"Tok ({kind})"), "GET", ME, headers=bearer(tokens[kind]), expect=401, detail=detail, behavior=f"{kind} rejected")


def test_me_rejects_basic_auth_scheme(api, clean):
    api.check(Actor("anonymous (Basic scheme)"), "GET", ME, headers={"Authorization": "Basic dXNlcjpwYXNz"}, expect=401, behavior="Only Bearer is accepted")


def test_signed_token_with_non_numeric_subject_is_a_401(api, clean):
    api.check(
        Actor("holder of a signed token"), "GET", ME, headers=bearer(make_token("not-a-number")), expect=401, behavior="Rejected as invalid",
        why="BUG: int(payload['sub']) is not guarded, so the request crashes.",
    )


def test_inactive_user_token_stops_working_immediately(api, clean):
    user = new_user(api, "Gone")
    api.setup(user, "GET", ME)
    support.set_columns(User, user.id, is_active=False)
    api.check(user, "GET", ME, expect=401, detail="User not found or inactive", behavior="A still-valid token no longer works")


# --- Refresh ---


def test_refresh_returns_tokens_that_work(api, clean):
    user = new_user(api, "Ref")
    response = api.check(ANON, "POST", REFRESH, expect=200, behavior="New token pair", json={"refresh_token": user.refresh_token})
    api.setup(Actor("Ref", bearer(response.json()["access_token"])), "GET", ME)


@pytest.mark.parametrize("kind", ["access_token", "garbage", "expired"])
def test_refresh_rejects_bad_tokens(api, clean, kind):
    user = new_user(api, "Ref")
    tokens = {
        "access_token": user.access_token,
        "garbage": "not.a.token",
        "expired": make_token(user.id, secret=settings.jwt_refresh_secret_key, token_type="refresh", exp_offset=-60),
    }
    api.check(ANON, "POST", REFRESH, expect=401, behavior=f"{kind} is not accepted as a refresh token", json={"refresh_token": tokens[kind]})


def test_refresh_missing_body_returns_422(api, clean):
    api.check(ANON, "POST", REFRESH, expect=422, behavior="Validation error", json={})


def test_refresh_for_inactive_user_is_rejected(api, clean):
    user = new_user(api, "Gone")
    support.set_columns(User, user.id, is_active=False)
    api.check(ANON, "POST", REFRESH, expect=401, detail="Invalid refresh token", behavior="Inactive user cannot refresh", json={"refresh_token": user.refresh_token})


# --- Active tenant selection ---


def test_select_tenant_as_member(api, fresh):
    response = api.check(fresh.alice, "POST", SELECT, params={"tenant_id": fresh.a}, expect=200, behavior="Selected")
    api.verify(response.json()["selected"] is True, "selected is not true")


def test_select_tenant_as_non_member(api, fresh):
    api.check(fresh.alice, "POST", SELECT, params={"tenant_id": fresh.b}, expect=403, detail="User is not a member of the selected tenant", behavior="Denied", absent=["Company B"])


def test_select_unknown_tenant_looks_the_same(api, fresh):
    api.check(fresh.alice, "POST", SELECT, params={"tenant_id": 999999999}, expect=403, detail="User is not a member of the selected tenant", behavior="Same answer as a real tenant")


@pytest.mark.parametrize(("params", "label"), [({}, "missing"), ({"tenant_id": "abc"}, "text")], ids=["missing", "text"])
def test_select_tenant_invalid_id(api, fresh, params, label):
    api.check(fresh.alice, "POST", SELECT, params=params, expect=422, behavior=f"{label} ID rejected")


def test_select_inactive_tenant_is_denied(api, fresh):
    support.set_columns(Tenant, fresh.a, is_active=False)
    api.check(
        fresh.alice, "POST", SELECT, params={"tenant_id": fresh.a}, expect=(403, 404), behavior="An inactive tenant cannot be selected",
        why="F-8: the route checks membership directly and ignores Tenant.is_active.",
    )


def test_tenant_claim_inside_a_token_grants_nothing(api, fresh):
    forged = Actor("Alice (token claims tenant B)", bearer(make_token(fresh.alice.id, tenant_id=fresh.b)))
    api.check(forged, "GET", support.TENANT_DOCS, path={"tenant_id": fresh.b}, expect=403, detail=NOT_MEMBER, behavior="The tenant_id claim is not trusted")
