"""The API wrapper that records expected vs actual, plus the shared test world."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import jwt
from sqlalchemy import text

from app.core.config import settings
from app.db import session as db_session

API = "/api/v1"
PASSWORD = "Secret123!"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
REAL_ENQUEUE = None
REAL_ENQUEUE_EMBEDDING = None
EMBEDDING_TASKS: list[int] = []

# Exact error messages from the application.
NOT_MEMBER = "User is not a member of this tenant"
TEAM_NOT_FOUND = "Team not found"
DOC_NOT_FOUND = "Document not found"
JOB_NOT_FOUND = "Job not found"
NO_READ = "Missing permission: document.read"
NO_UPLOAD = "Missing permission: document.upload"
NO_TEAM_ACCESS = "You do not have access to this team"
DENIED = "Permission denied"
NOT_AUTHENTICATED = "Not authenticated"
BAD_TOKEN = "Invalid or expired token"
TENANT_HIDDEN = "Tenant not found or access denied"

# Route templates.
TENANTS = "/tenants"
TENANT = "/tenants/{tenant_id}"
TENANT_MEMBERS = "/tenants/{tenant_id}/members"
TENANT_DOCS = "/tenants/{tenant_id}/documents"
TEAMS = "/tenants/{tenant_id}/teams"
TEAM = "/tenants/{tenant_id}/teams/{team_id}"
TEAM_MEMBERS = "/tenants/{tenant_id}/teams/{team_id}/members"
TEAM_MEMBER = "/tenants/{tenant_id}/teams/{team_id}/members/{user_id}"
TEAM_DOCS = "/tenants/{tenant_id}/teams/{team_id}/documents"
UPLOAD = "/tenants/{tenant_id}/teams/{team_id}/documents/upload"
DOC = "/tenants/{tenant_id}/teams/{team_id}/documents/{document_id}"
GRANTS = "/tenants/{tenant_id}/teams/{team_id}/documents/{document_id}/grants"
GRANT = "/tenants/{tenant_id}/teams/{team_id}/documents/{document_id}/grants/{user_id}"
SEARCH = "/tenants/{tenant_id}/search"
CONVERSATIONS = "/tenants/{tenant_id}/chat/conversations"
CONVERSATION = "/tenants/{tenant_id}/chat/conversations/{conversation_id}"
MESSAGES = "/tenants/{tenant_id}/chat/conversations/{conversation_id}/messages"
JOBS = "/jobs"
JOB = "/jobs/{job_id}"
READ_SUFFIXES = ["", "/download", "/text", "/chunks", "/jobs", "/metadata", "/status", "/embeddings"]
CANARIES = {"doc_hr": "CANARY-A-HR", "doc_eng": "CANARY-A-ENG", "doc_b": "CANARY-B"}


@dataclass
class Actor:
    name: str
    headers: dict[str, str] = field(default_factory=dict)
    id: int | None = None
    email: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


ANON = Actor("anonymous")


def describe(response: httpx.Response) -> str:
    """A short description of a response, with any tokens removed."""
    unhandled = getattr(response, "unhandled", None)
    if unhandled:
        return f"Unhandled server error: {unhandled}"
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return f"Body: {response.text[:120]!r}"
    if isinstance(body, list):
        return f"List of {len(body)} item(s)"
    if isinstance(body, dict):
        if "access_token" in body:
            return "Token pair returned"
        if "detail" in body:
            return f"detail: {json.dumps(body['detail'])[:160]}"
        return "Fields: " + ", ".join(sorted(body))[:160]
    return str(body)[:160]


class Recorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.routes: set[tuple[str, str]] = set()
        self.current: str | None = None
        self.category: str | None = None

    def add(self, **values: Any) -> dict[str, Any]:
        row = {
            "test": self.current,
            "category": self.category,
            "endpoint": "",
            "user": "",
            "expected_status": "",
            "actual_status": "",
            "expected_behavior": "",
            "actual_behavior": "",
            "result": "PASS",
            "explanation": "",
            **values,
        }
        self.rows.append(row)
        return row

    def rows_for(self, nodeid: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["test"] == nodeid]

    def finish_phase(self, nodeid: str, report: Any) -> None:
        rows = self.rows_for(nodeid)
        if report.passed:
            return
        message = str(report.longrepr).strip().splitlines()[-1][:300] if report.longrepr else ""
        if report.skipped:
            label, message = "SKIPPED", message
        elif report.when == "call":
            label = "FAIL"
        else:
            label = "ERROR"
        if rows and label == "FAIL":
            failed = [row for row in rows if row["result"] == "FAIL"]
            if not failed:
                rows[-1]["result"] = "FAIL"
                rows[-1]["explanation"] = rows[-1]["explanation"] or message
        else:
            self.add(
                test=nodeid,
                endpoint="(test did not reach a request)",
                result=label,
                actual_behavior=message,
                explanation=f"{report.when} phase: {message}",
            )


recorder = Recorder()

HOME_TENANT = {"bob": "b", "john": "b"}
TEAM_TENANT = {"hr": "a", "eng": "a", "tb": "b"}
DOC_TENANT = {"doc_hr": "a", "doc_eng": "a", "doc_b": "b"}


def tag_scope(actor: str, tenant: str | None = None, team: str | None = None, doc: str | None = None) -> None:
    """Report a test under tenant isolation if it touches another tenant, otherwise team isolation."""
    home = HOME_TENANT.get(actor, "a")
    touched = {tenant, TEAM_TENANT.get(team), DOC_TENANT.get(doc)} - {None}
    recorder.category = "Tenant isolation" if touched - {home} else "Team isolation"


class Api:
    """Every request goes through here, so every test reports expected vs actual."""

    def __init__(self, client: Any, in_process: bool = True):
        self.client = client
        self.in_process = in_process

    def send(self, actor: Actor, method: str, template: str, *, path: dict | None = None, headers: dict | None = None, **kwargs: Any) -> httpx.Response:
        url = API + template.format(**(path or {}))
        recorder.routes.add((method, API + template))
        merged = {**actor.headers, **(headers or {})}
        if not self.in_process:
            return self.client.request(method, url, headers=merged, **kwargs)
        try:
            return self.client.request(method, url, headers=merged, **kwargs)
        except Exception as exc:  # A real server answers an unhandled exception with a 500.
            response = httpx.Response(500, text="Internal Server Error")
            first_line = (str(exc).splitlines() or [""])[0][:160]
            response.unhandled = f"{type(exc).__name__}: {first_line}"
            return response

    def setup(self, actor: Actor, method: str, template: str, **kwargs: Any) -> httpx.Response:
        """A preparation step. If it fails, the test errors instead of reporting a result."""
        response = self.send(actor, method, template, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(f"Setup failed: {method} {template} -> {response.status_code} {describe(response)}")
        return response

    def check(
        self,
        actor: Actor,
        method: str,
        template: str,
        *,
        expect: int | tuple[int, ...],
        behavior: str,
        path: dict | None = None,
        detail: str | None = None,
        absent: tuple[str, ...] | list[str] = (),
        contains: tuple[str, ...] | list[str] = (),
        why: str = "",
        **kwargs: Any,
    ) -> httpx.Response:
        response = self.send(actor, method, template, path=path, **kwargs)
        allowed = expect if isinstance(expect, tuple) else (expect,)
        row = recorder.add(
            endpoint=f"{method} {API}{template}",
            user=actor.name,
            expected_status="/".join(map(str, allowed)),
            actual_status=response.status_code,
            expected_behavior=behavior,
            actual_behavior=describe(response),
        )
        self._assert(row, response.status_code in allowed, f"expected {allowed}, got {response.status_code}: {describe(response)}", why)
        if detail is not None:
            actual = None
            try:
                actual = response.json().get("detail")
            except (ValueError, AttributeError):
                pass
            self._assert(row, actual == detail, f"expected detail {detail!r}, got {actual!r}", why)
        for marker in absent:
            self._assert(row, marker not in response.text, f"response leaked {marker}", why)
        for marker in contains:
            self._assert(row, marker in response.text, f"response is missing {marker}", why)
        return response

    def verify(self, condition: bool, actual: str, why: str = "") -> None:
        """A further check on the most recent request of this test."""
        rows = recorder.rows_for(recorder.current)
        row = rows[-1]
        if not condition:
            row["actual_behavior"] = actual
        self._assert(row, condition, actual, why)

    @staticmethod
    def _assert(row: dict[str, Any], condition: bool, message: str, why: str) -> None:
        if not condition:
            row["result"] = "FAIL"
            row["explanation"] = f"{why} ({message})" if why else message
            raise AssertionError(message)


def fact(condition: bool, *, name: str, expected: str, actual: str, why: str = "") -> None:
    """A check with no HTTP request, such as a database constraint."""
    row = recorder.add(endpoint=name, user="(direct database check)", expected_behavior=expected, actual_behavior=actual, expected_status="n/a", actual_status="n/a")
    Api._assert(row, condition, actual, why)


def truncate() -> None:
    with db_session.SessionLocal() as session:
        session.execute(text("TRUNCATE users, tenants CASCADE"))
        session.commit()


def set_columns(model: Any, row_id: int, **values: Any) -> None:
    with db_session.SessionLocal() as session:
        row = session.get(model, row_id)
        for name, value in values.items():
            setattr(row, name, value)
        session.commit()


def count(sql: str, **params: Any) -> int:
    with db_session.SessionLocal() as session:
        return session.execute(text(sql), params).scalar_one()


def ingest(job_id: int) -> None:
    from app.workers.tasks import ingest_document

    ingest_document.apply(args=[job_id])


def embed(document_id: int) -> None:
    """Runs the real Celery task in this process."""
    from app.workers.tasks import embed_document

    embed_document.apply(args=[document_id])


def text_file(content: str | bytes | None = None, name: str | None = None) -> dict:
    data = content if content is not None else f"unique {uuid4().hex}"
    data = data.encode() if isinstance(data, str) else data
    return {"file": (name or f"{uuid4().hex}.txt", data, "text/plain")}


def make_token(subject: Any = None, *, secret: str | None = None, token_type: str = "access", exp_offset: int = 600, **claims: Any) -> str:
    import time

    now = int(time.time())
    payload: dict[str, Any] = {"type": token_type, "iat": now, "exp": now + exp_offset, **claims}
    if subject is not None:
        payload["sub"] = str(subject)
    return jwt.encode(payload, secret or settings.jwt_secret_key, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def new_user(api: Api, name: str, email: str | None = None) -> Actor:
    email = email or f"{name.lower()}@example.com"
    created = api.setup(ANON, "POST", "/auth/register", json={"name": name, "email": email, "password": PASSWORD}).json()
    tokens = api.setup(ANON, "POST", "/auth/login", json={"email": email, "password": PASSWORD}).json()
    return Actor(name, bearer(tokens["access_token"]), created["id"], email, tokens["access_token"], tokens["refresh_token"])


def add_to_tenant(api: Api, admin: Actor, tenant_id: int, user: Actor, role: str = "member") -> None:
    api.setup(admin, "POST", TENANT_MEMBERS, path={"tenant_id": tenant_id}, json={"email": user.email, "role": role})


def add_to_team(api: Api, by: Actor, tenant_id: int, team_id: int, user: Actor, role: str = "member") -> None:
    api.setup(
        by, "POST", TEAM_MEMBERS, path={"tenant_id": tenant_id, "team_id": team_id},
        json={"user_id": user.id, "team_id": team_id, "tenant_id": tenant_id, "role": role},
    )


def upload(api: Api, actor: Actor, tenant_id: int, team_id: int, content: str, name: str) -> dict:
    return api.setup(actor, "POST", UPLOAD, path={"tenant_id": tenant_id, "team_id": team_id}, files=text_file(content, name)).json()


def build_world(api: Api, *, ingest: bool) -> SimpleNamespace:
    """Company A (HR, Engineering) and Company B, built only through the public API.

    Alice: A admin, in no team. Mona: HR member. Eli: Engineering member. Vera: HR viewer.
    Mike: HR manager. Adam: admin role on the HR team only. Nora: A member with no team.
    Tess: A tenant-level manager with no team. Tina: A tenant-level viewer with no team.
    Bob: B admin. John: member of B's team.
    """
    w = SimpleNamespace()
    for name in ("Alice", "Mona", "Eli", "Vera", "Mike", "Adam", "Nora", "Tess", "Tina", "Bob", "John"):
        setattr(w, name.lower(), new_user(api, name))

    w.a = api.setup(w.alice, "POST", TENANTS, json={"name": "Company A", "slug": "company-a"}).json()["id"]
    w.hr = api.setup(w.alice, "POST", TEAMS, path={"tenant_id": w.a}, json={"name": "HR"}).json()["id"]
    w.eng = api.setup(w.alice, "POST", TEAMS, path={"tenant_id": w.a}, json={"name": "Engineering"}).json()["id"]
    for user in (w.mona, w.eli, w.vera, w.mike, w.adam, w.nora):
        add_to_tenant(api, w.alice, w.a, user)
    add_to_tenant(api, w.alice, w.a, w.tess, "manager")
    add_to_tenant(api, w.alice, w.a, w.tina, "viewer")
    add_to_team(api, w.alice, w.a, w.hr, w.mona)
    add_to_team(api, w.alice, w.a, w.eng, w.eli)
    add_to_team(api, w.alice, w.a, w.hr, w.vera, "viewer")
    add_to_team(api, w.alice, w.a, w.hr, w.mike, "manager")
    add_to_team(api, w.alice, w.a, w.hr, w.adam, "admin")

    w.b = api.setup(w.bob, "POST", TENANTS, json={"name": "Company B", "slug": "company-b"}).json()["id"]
    w.tb = api.setup(w.bob, "POST", TEAMS, path={"tenant_id": w.b}, json={"name": "Finance"}).json()["id"]
    add_to_tenant(api, w.bob, w.b, w.john)
    add_to_team(api, w.bob, w.b, w.tb, w.john)

    hr_doc = upload(api, w.mona, w.a, w.hr, "CANARY-A-HR confidential salary data.", "a-hr.txt")
    eng_doc = upload(api, w.eli, w.a, w.eng, "CANARY-A-ENG confidential source plans.", "a-eng.txt")
    b_doc = upload(api, w.bob, w.b, w.tb, "CANARY-B confidential finance data.", "b.txt")
    w.doc_hr, w.job_hr = hr_doc["document_id"], hr_doc["job_id"]
    w.doc_eng, w.job_eng = eng_doc["document_id"], eng_doc["job_id"]
    w.doc_b, w.job_b = b_doc["document_id"], b_doc["job_id"]
    if ingest:
        for job in (w.job_hr, w.job_eng, w.job_b):
            globals()["ingest"](job)
    return w
