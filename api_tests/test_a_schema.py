"""The test database is built by the real migrations. Does it match the application?"""
import hashlib
import re
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401
from api_tests import support
from app.core.config import settings
from app.db import session as db_session
from app.db.base import Base
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.db.models.user_team_membership import UserTeamMembership
from app.db.models.user_tenant_membership import UserTenantMembership


def _insert_is_rejected(row) -> bool:
    with db_session.SessionLocal() as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return True
        return False


def _document(tenant_id, team_id, **overrides):
    values = dict(
        tenant_id=tenant_id, team_id=team_id, original_filename="x.txt", stored_filename="x.txt",
        content_type="text/plain", file_size=1, file_hash="a" * 64, file_extension=".txt",
    )
    return Document(**{**values, **overrides})


def test_migrations_reach_head_on_postgresql():
    with db_session.SessionLocal() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    support.fact(version == "e5c9a3d7b210", name="alembic upgrade head", expected="Head revision e5c9a3d7b210 applied", actual=f"Revision {version}")


def test_pgvector_extension_is_available():
    installed = support.count("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    support.fact(installed == 1, name="CREATE EXTENSION vector", expected="pgvector installs in the test database", actual=f"{installed} extension row(s)")


def test_embeddings_table_has_the_only_vector_column():
    columns = support.count("SELECT count(*) FROM information_schema.columns WHERE udt_name = 'vector' AND table_name = 'embeddings'")
    support.fact(columns == 1, name="vector columns", expected="embeddings.embedding is the vector column", actual=f"{columns} vector column(s)")


def test_migrations_match_the_models():
    with db_session.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    support.fact(
        not differences, name="alembic compare_metadata", expected="The migrated schema matches the SQLAlchemy models",
        actual=f"{len(differences)} difference(s): {str(differences)[:400]}",
        why="Models and migrations have drifted, so tests using create_all would not match production.",
    )


def test_database_rejects_document_in_a_team_of_another_tenant(api, fresh):
    rejected = _insert_is_rejected(_document(fresh.a, fresh.tb))
    support.fact(rejected, name="fk_document_team_tenant", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_chunk_with_foreign_tenant(api, fresh):
    row = DocumentChunk(document_id=fresh.doc_hr, tenant_id=fresh.b, team_id=fresh.tb, chunk_index=0, chunk_text="x", chunk_hash="a" * 64)
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_chunk_document_tenant_team", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_job_with_foreign_team(api, fresh):
    row = DocumentJob(document_id=fresh.doc_hr, tenant_id=fresh.a, team_id=fresh.eng, status="pending")
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_job_document_tenant_team", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_team_membership_in_another_tenants_team(api, fresh):
    role_id = support.count("SELECT id FROM roles WHERE tenant_id = :t AND name = 'member'", t=fresh.a)
    row = UserTeamMembership(user_id=fresh.mona.id, team_id=fresh.tb, tenant_id=fresh.a, role_id=role_id)
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_user_team_membership_team_tenant", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_team_membership_without_tenant_membership(api, fresh):
    role_id = support.count("SELECT id FROM roles WHERE tenant_id = :t AND name = 'member'", t=fresh.b)
    row = UserTeamMembership(user_id=fresh.mona.id, team_id=fresh.tb, tenant_id=fresh.b, role_id=role_id)
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_user_team_membership_tenant_membership", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_tenant_membership_with_another_tenants_role(api, fresh):
    role_id = support.count("SELECT id FROM roles WHERE tenant_id = :t AND name = 'admin'", t=fresh.b)
    row = UserTenantMembership(user_id=fresh.eli.id, tenant_id=fresh.a, role_id=role_id)
    with db_session.SessionLocal() as session:
        session.query(UserTenantMembership).filter_by(user_id=fresh.eli.id, tenant_id=fresh.a).delete()
        session.commit()
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_user_tenant_membership_role_tenant", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


def test_database_rejects_duplicate_pointing_at_another_team(api, fresh):
    row = _document(fresh.a, fresh.eng, duplicate_of_document_id=fresh.doc_hr)
    rejected = _insert_is_rejected(row)
    support.fact(rejected, name="fk_document_duplicate_same_team", expected="IntegrityError", actual="Rejected" if rejected else "Row was accepted")


# --- Do refused requests really change nothing? ---


def _database_fingerprint() -> str:
    """A hash of every row in every application table."""
    digest = hashlib.sha256()
    with db_session.SessionLocal() as session:
        for table in sorted(Base.metadata.tables):
            rows = session.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            digest.update(f"{table}:{rows!r}".encode())
    return digest.hexdigest()


def _stored_files() -> list[str]:
    root = Path(settings.document_storage_root)
    return sorted(str(path) for path in root.rglob("*") if path.is_file())


def test_refused_requests_change_no_database_row_or_stored_file(api, fresh):
    before, files = _database_fingerprint(), _stored_files()
    a, b, hr, eng, tb = fresh.a, fresh.b, fresh.hr, fresh.eng, fresh.tb
    attempts = [
        (fresh.alice, "PATCH", support.TENANT, {"tenant_id": b}, {"json": {"name": "Hacked"}}),
        (fresh.alice, "PATCH", support.TEAM, {"tenant_id": b, "team_id": tb}, {"json": {"name": "Hacked"}}),
        (fresh.alice, "PATCH", support.TEAM, {"tenant_id": a, "team_id": tb}, {"json": {"name": "Hacked"}}),
        (fresh.alice, "POST", support.TEAMS, {"tenant_id": b}, {"json": {"name": "Rogue"}}),
        (fresh.alice, "POST", support.TENANT_MEMBERS, {"tenant_id": b}, {"json": {"email": fresh.alice.email, "role": "admin"}}),
        (fresh.alice, "POST", support.TEAM_MEMBERS, {"tenant_id": b, "team_id": tb}, {"json": {"user_id": fresh.alice.id, "team_id": tb, "tenant_id": b, "role": "admin"}}),
        (fresh.alice, "DELETE", support.TEAM_MEMBER, {"tenant_id": b, "team_id": tb, "user_id": fresh.john.id}, {}),
        (fresh.alice, "POST", support.UPLOAD, {"tenant_id": b, "team_id": tb}, {"files": support.text_file()}),
        (fresh.bob, "PATCH", support.TENANT, {"tenant_id": a}, {"json": {"name": "Hacked"}}),
        (fresh.bob, "PATCH", support.TEAM, {"tenant_id": a, "team_id": hr}, {"json": {"name": "Hacked"}}),
        (fresh.bob, "POST", support.TENANT_MEMBERS, {"tenant_id": a}, {"json": {"email": fresh.bob.email, "role": "admin"}}),
        (fresh.bob, "DELETE", support.TEAM_MEMBER, {"tenant_id": a, "team_id": hr, "user_id": fresh.mona.id}, {}),
        (fresh.john, "POST", support.UPLOAD, {"tenant_id": a, "team_id": hr}, {"files": support.text_file()}),
        (fresh.mona, "PATCH", support.TEAM, {"tenant_id": a, "team_id": eng}, {"json": {"name": "Hacked"}}),
        (fresh.mona, "POST", support.TEAM_MEMBERS, {"tenant_id": a, "team_id": eng}, {"json": {"user_id": fresh.mona.id, "team_id": eng, "tenant_id": a, "role": "admin"}}),
        (fresh.mona, "POST", support.TENANT_MEMBERS, {"tenant_id": a}, {"json": {"email": fresh.mona.email, "role": "admin"}}),
        (fresh.mona, "DELETE", support.TEAM_MEMBER, {"tenant_id": a, "team_id": eng, "user_id": fresh.eli.id}, {}),
        (fresh.mona, "POST", support.UPLOAD, {"tenant_id": a, "team_id": eng}, {"files": support.text_file()}),
        (fresh.eli, "POST", support.UPLOAD, {"tenant_id": a, "team_id": hr}, {"files": support.text_file()}),
        (fresh.vera, "POST", support.UPLOAD, {"tenant_id": a, "team_id": hr}, {"files": support.text_file()}),
    ]
    for actor, method, template, path, kwargs in attempts:
        api.check(actor, method, template, path=path, expect=(403, 404), behavior="Refused", **kwargs)
    after = _database_fingerprint()
    api.verify(before == after, "A refused request changed a database row", why="SECURITY: an unauthorized request had an effect.")
    api.verify(files == _stored_files(), "A refused upload left a file in storage", why="SECURITY: an unauthorized upload reached storage.")


def _sql_for(action) -> list[str]:
    captured: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(db_session.engine, "before_cursor_execute", listener)
    try:
        action()
    finally:
        event.remove(db_session.engine, "before_cursor_execute", listener)
    return captured


@pytest.mark.parametrize(
    ("template", "params", "table", "fragments"),
    [
        (support.DOC, {}, "documents", ["documents.id =", "documents.tenant_id =", "documents.team_id =", "documents.team_id IN", "user_team_memberships.user_id ="]),
        (support.TENANT_DOCS, {}, "documents", ["documents.tenant_id =", "documents.team_id IN", "user_team_memberships.user_id ="]),
        (support.TEAM_DOCS, {}, "documents", ["documents.tenant_id =", "documents.team_id =", "documents.team_id IN"]),
        (support.DOC + "/chunks", {}, "document_chunks", ["document_chunks.document_id =", "document_chunks.tenant_id =", "document_chunks.team_id ="]),
        (support.JOB, {}, "document_jobs", ["document_jobs.id =", "documents.team_id IN", "user_team_memberships.user_id ="]),
        (support.JOBS, {}, "document_jobs", ["documents.team_id IN", "user_team_memberships.user_id ="]),
    ],
    ids=["document", "tenant_documents", "team_documents", "chunks", "job", "jobs"],
)
def test_sql_sent_to_postgresql_contains_the_isolation_filters(api, fresh, template, params, table, fragments):
    path = {"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr, "job_id": fresh.job_hr}
    needed = {name: path[name] for name in re.findall(r"{(\w+)}", template)}
    statements = _sql_for(lambda: api.setup(fresh.mona, "GET", template, path=needed, params=params))
    selects = [s for s in statements if f"FROM {table}" in s]
    joined = " ".join(" ".join(s.split()) for s in selects)
    missing = [fragment for fragment in fragments if fragment not in joined]
    support.fact(
        bool(selects) and not missing, name=f"SQL for GET {support.API}{template}", expected=f"The {table} query filters on {fragments}",
        actual=f"Missing: {missing}" if missing or not selects else "All filters present in the executed SQL",
    )
