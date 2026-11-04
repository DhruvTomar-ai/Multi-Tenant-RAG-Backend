import os

# Set before any app import, so tests can never touch the development database in .env.
# Point TEST_DATABASE_URL at a dedicated, disposable PostgreSQL database to run against
# PostgreSQL: every table in it is dropped and recreated for each test.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET_KEY"] = "test-access-secret-key-that-is-long-enough-for-hs256"
os.environ["JWT_REFRESH_SECRET_KEY"] = "test-refresh-secret-key-that-is-long-enough-for-hs256"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.session as db_session
from app.api.dependencies import get_db
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.security.auth import pwd_context

# The minimum bcrypt cost keeps a suite that registers many users fast.
pwd_context.update(bcrypt__rounds=4)

if TEST_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enforce_sqlite_foreign_keys(dbapi_connection, _record):
        # SQLite ignores foreign keys unless asked. The isolation tests depend on them.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
else:
    engine = create_engine(TEST_DATABASE_URL)
    # create_all does not run migrations, so it must enable the extension the embeddings table needs.
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Keep the app dependency, the worker task and the fixtures on the same database.
db_session.engine = engine
db_session.SessionLocal = TestingSessionLocal
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def document_storage(tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(settings, "document_storage_root", str(root))
    return root


@pytest.fixture(autouse=True)
def queued_tasks(monkeypatch):
    """Capture tasks instead of sending them, so tests need no Redis."""
    tasks = []
    monkeypatch.setattr(
        "app.services.documents.task_queue.enqueue_ingestion_job",
        lambda job_id, task_id: tasks.append((job_id, task_id)),
    )
    # Embeddings run as their own task and need PostgreSQL. See api_tests for those tests.
    monkeypatch.setattr("app.services.documents.task_queue.enqueue_embedding_job", lambda document_id: None)
    return tasks


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
