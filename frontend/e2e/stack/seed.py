"""Fills a fresh test database with the people and documents the tests and TESTING_GUIDE.md use.

    TEST_DATABASE_URL=postgresql+psycopg://rag_test:rag_test@127.0.0.1:5434/rag_api_test python frontend/e2e/stack/seed.py

It deletes everything in that database first, so it refuses to run on anything but a test database.
Everything goes through the real services, and documents through the real ingestion and embedding tasks.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parents[1] / ".seed.json"
PASSWORD = "Demo-Pass-123!"


def require_test_database() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        sys.exit("TEST_DATABASE_URL is not set.")
    target = make_url(url)
    if target.get_backend_name() != "postgresql" or "test" not in (target.database or "") or target.port in (5432, 5433):
        sys.exit("Refusing to run: this is not a dedicated test database.")
    from dotenv import dotenv_values

    development = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if development:
        dev = make_url(development)
        if (dev.port, dev.database) == (target.port, target.database):
            sys.exit("Refusing to run: this is the development database.")
    return url


URL = require_test_database()
os.environ.update(
    DATABASE_URL=URL,
    APP_ENV="test",
    JWT_SECRET_KEY=os.environ.get("JWT_SECRET_KEY", "e2e-access-secret-key-long-enough-for-hs256"),
    JWT_REFRESH_SECRET_KEY=os.environ.get("JWT_REFRESH_SECRET_KEY", "e2e-refresh-secret-key-long-enough-for-hs256"),
    CELERY_BROKER_URL=os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6380/0"),
    DOCUMENT_STORAGE_ROOT=os.environ.get("DOCUMENT_STORAGE_ROOT", tempfile.mkdtemp(prefix="rag-e2e-")),
)
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import session as db_session  # noqa: E402
from app.db.models.document import Document  # noqa: E402
from app.main import app  # noqa: E402
from app.services.documents import task_queue  # noqa: E402
from app.workers.tasks import embed_document, ingest_document  # noqa: E402

API = "/api/v1"

USERS = {
    "ava": ("Ava Admin", "ava.admin@acme.example.com"),
    "mark": ("Mark Manager", "mark.manager@acme.example.com"),
    "mia": ("Mia Member", "mia.member@acme.example.com"),
    "vic": ("Vic Viewer", "vic.viewer@acme.example.com"),
    "eli": ("Eli Engineer", "eli.engineer@acme.example.com"),
    "nia": ("Nia Newcomer", "nia.newcomer@acme.example.com"),
    "sam": ("Sam Shared", "sam.shared@example.com"),
    "bo": ("Bo Admin", "bo.admin@globex.example.com"),
    "ben": ("Ben Member", "ben.member@globex.example.com"),
}

DOCUMENTS = {
    "leave": ("mark", "acme", "acme_hr", "leave-policy.txt", {"department": "HR", "document_type": "Policy", "tags": "leave,benefits"}, "Employees receive 24 days of paid annual leave each year. Up to five unused days may be carried forward with manager approval."),
    "salary": ("mark", "acme", "acme_hr", "salary-bands.txt", {"department": "HR", "document_type": "Reference", "access_level": "confidential"}, "The Acme salary band for senior engineers ranges from 90000 to 120000 dollars. Bands are reviewed each March."),
    "incident": ("eli", "acme", "acme_eng", "incident-runbook.txt", {"department": "Engineering", "document_type": "Runbook"}, "During a production incident the on-call engineer must page the duty manager within 15 minutes. Status updates go out every 30 minutes."),
    "failed": ("eli", "acme", "acme_eng", "deploy-guide.txt", {"department": "Engineering"}, "Deployments to production happen every Tuesday morning after the change review meeting."),
    "pricing": ("ben", "globex", "globex_sales", "globex-pricing.txt", {"department": "Sales"}, "Globex enterprise pricing starts at 5000 dollars per month. Discounts above twenty percent need director approval."),
    "tickets": ("bo", "globex", "globex_support", "support-tiers.txt", {"department": "Support"}, "Globex priority one support tickets receive a response within one hour, at any time of day."),
}


def rebuild_database() -> None:
    engine = create_engine(URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION vector"))
    engine.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


class Seeder:
    def __init__(self, client: TestClient):
        self.client = client
        self.headers: dict[str, dict] = {}
        self.ids: dict[str, int] = {}

    def call(self, actor: str | None, method: str, path: str, **kwargs):
        response = self.client.request(method, API + path, headers=self.headers.get(actor or "", {}), **kwargs)
        if response.status_code >= 300:
            sys.exit(f"Seeding failed: {method} {path} returned {response.status_code}")
        return response.json()

    def user(self, key: str) -> None:
        name, email = USERS[key]
        self.ids[key] = self.call(None, "POST", "/auth/register", json={"name": name, "email": email, "password": PASSWORD})["id"]
        tokens = self.call(None, "POST", "/auth/login", json={"email": email, "password": PASSWORD})
        self.headers[key] = {"Authorization": f"Bearer {tokens['access_token']}"}

    def tenant(self, key: str, owner: str, name: str, slug: str, description: str) -> int:
        self.ids[key] = self.call(owner, "POST", "/tenants", json={"name": name, "slug": slug, "description": description})["id"]
        return self.ids[key]

    def team(self, key: str, admin: str, tenant: str, name: str) -> int:
        self.ids[key] = self.call(admin, "POST", f"/tenants/{self.ids[tenant]}/teams", json={"name": name, "description": f"The {name} team."})["id"]
        return self.ids[key]

    def member(self, admin: str, tenant: str, user: str, role: str = "member") -> None:
        self.call(admin, "POST", f"/tenants/{self.ids[tenant]}/members", json={"email": USERS[user][1], "role": role})

    def team_member(self, admin: str, tenant: str, team: str, user: str, role: str) -> None:
        self.call(admin, "POST", f"/tenants/{self.ids[tenant]}/teams/{self.ids[team]}/members", json={"user_id": self.ids[user], "tenant_id": self.ids[tenant], "team_id": self.ids[team], "role": role})

    def document(self, key: str) -> None:
        actor, tenant, team, filename, fields, content = DOCUMENTS[key]
        body = self.call(actor, "POST", f"/tenants/{self.ids[tenant]}/teams/{self.ids[team]}/documents/upload", files={"file": (filename, content.encode(), "text/plain")}, data=fields)
        self.ids[f"doc_{key}"] = body["document_id"]
        ingest_document.apply(args=[body["job_id"]])
        if key == "failed":
            # A document left exactly as a failed embedding leaves it, so Retry does the real work.
            with db_session.SessionLocal() as session:
                document = session.get(Document, body["document_id"])
                document.embedding_status = "failed"
                document.embedding_error = "Seeded failure"
                session.commit()
        else:
            embed_document.apply(args=[body["document_id"]])


def main() -> None:
    rebuild_database()
    # Tasks run directly in this process, so no worker is needed while seeding.
    task_queue.enqueue_ingestion_job = lambda job_id, task_id: None
    task_queue.enqueue_embedding_job = lambda document_id: None

    with TestClient(app) as client:
        seed = Seeder(client)
        for key in USERS:
            seed.user(key)

        seed.tenant("acme", "ava", "Acme Corp", "acme", "Fictional company for testing.")
        seed.team("acme_hr", "ava", "acme", "People Ops")
        seed.team("acme_eng", "ava", "acme", "Engineering")
        for user in ("mark", "mia", "vic", "eli", "nia", "sam"):
            seed.member("ava", "acme", user)
        seed.team_member("ava", "acme", "acme_hr", "mark", "manager")
        seed.team_member("ava", "acme", "acme_hr", "mia", "member")
        seed.team_member("ava", "acme", "acme_hr", "vic", "viewer")
        seed.team_member("ava", "acme", "acme_hr", "sam", "member")
        seed.team_member("ava", "acme", "acme_eng", "eli", "member")

        seed.tenant("globex", "bo", "Globex Inc", "globex", "A second fictional company for testing.")
        seed.team("globex_sales", "bo", "globex", "Sales")
        seed.team("globex_support", "bo", "globex", "Support")
        for user in ("ben", "sam"):
            seed.member("bo", "globex", user)
        seed.team_member("bo", "globex", "globex_sales", "ben", "member")
        seed.team_member("bo", "globex", "globex_sales", "sam", "manager")

        for key in DOCUMENTS:
            seed.document(key)

        # Nia is in no team, and can read this one document through a grant.
        seed.call("mark", "POST", f"/tenants/{seed.ids['acme']}/teams/{seed.ids['acme_hr']}/documents/{seed.ids['doc_leave']}/grants", json={"user_id": seed.ids["nia"]})

    OUTPUT.write_text(json.dumps({"password": PASSWORD, "ids": seed.ids, "emails": {key: value[1] for key, value in USERS.items()}}, indent=1))
    print(f"Seeded {len(USERS)} users, 2 tenants, 4 teams and {len(DOCUMENTS)} documents.")


if __name__ == "__main__":
    main()
