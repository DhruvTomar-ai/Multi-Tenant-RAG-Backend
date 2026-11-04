"""Environment guard, database setup, fixtures and the expected-vs-actual report."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise pytest.UsageError("TEST_DATABASE_URL is not set. See api_tests/README.md.")

_target = make_url(TEST_DATABASE_URL)
_dev = dotenv_values(ROOT / ".env").get("DATABASE_URL")
_dev_url = make_url(_dev) if _dev else None
if (
    _target.get_backend_name() != "postgresql"
    or "test" not in (_target.database or "")
    or _target.port == 5433
    or (_dev_url is not None and (_dev_url.port, _dev_url.database) == (_target.port, _target.database))
):
    raise pytest.UsageError(f"Refusing to run: {_target.render_as_string()} is not a dedicated test database.")

# Set before any app import. Environment variables take priority over .env.
os.environ.update(
    DATABASE_URL=TEST_DATABASE_URL,
    APP_ENV="test",
    JWT_SECRET_KEY="api-tests-access-secret-key-long-enough-for-hs256",
    JWT_REFRESH_SECRET_KEY="api-tests-refresh-secret-key-long-enough-for-hs256",
    JWT_ALGORITHM="HS256",
    ACCESS_TOKEN_EXPIRE_MINUTES="30",
    REFRESH_TOKEN_EXPIRE_DAYS="7",
    # Nothing listens here. The in-process tests capture tasks instead of sending them.
    CELERY_BROKER_URL="redis://127.0.0.1:1/15",
    DOCUMENT_STORAGE_ROOT=tempfile.mkdtemp(prefix="rag-api-tests-"),
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from api_tests import support  # noqa: E402
from api_tests.report import write_reports  # noqa: E402
from app.main import app  # noqa: E402
from app.security.auth import pwd_context  # noqa: E402
from app.services.documents import task_queue  # noqa: E402

# The in-process tests register many users. The real-stack test keeps the default cost.
pwd_context.update(bcrypt__rounds=4)


def _rebuild_database() -> None:
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION vector"))
    engine.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def database():
    _rebuild_database()
    yield
    # Clean up: no test rows or uploaded files outlive the run.
    support.truncate()
    shutil.rmtree(os.environ["DOCUMENT_STORAGE_ROOT"], ignore_errors=True)


@pytest.fixture(scope="session")
def client(database):
    # Exceptions are raised so that Api can report the real cause of each 500.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def api(client):
    return support.Api(client)


@pytest.fixture(scope="session", autouse=True)
def captured_tasks():
    tasks: list[tuple[int, str]] = []
    support.REAL_ENQUEUE = task_queue.enqueue_ingestion_job
    task_queue.enqueue_ingestion_job = lambda job_id, task_id: tasks.append((job_id, task_id))
    support.REAL_ENQUEUE_EMBEDDING = task_queue.enqueue_embedding_job
    task_queue.enqueue_embedding_job = support.EMBEDDING_TASKS.append
    yield tasks
    task_queue.enqueue_ingestion_job = support.REAL_ENQUEUE
    task_queue.enqueue_embedding_job = support.REAL_ENQUEUE_EMBEDDING


@pytest.fixture
def clean(database):
    support.truncate()


@pytest.fixture(scope="module")
def world(api, database, captured_tasks):
    """Shared by a module's read-only tests, with every document fully processed."""
    support.truncate()
    return support.build_world(api, ingest=True)


@pytest.fixture
def fresh(api, database):
    """A new world for each test that changes data. Documents stay pending."""
    support.truncate()
    return support.build_world(api, ingest=False)


def pytest_runtest_logstart(nodeid, location):
    support.recorder.current = nodeid
    support.recorder.category = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    support.recorder.finish_phase(item.nodeid, report)


def pytest_sessionfinish(session, exitstatus):
    write_reports(support.recorder, app, ROOT / "api_tests" / "reports", exitstatus)
