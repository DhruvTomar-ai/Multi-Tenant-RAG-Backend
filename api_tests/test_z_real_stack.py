"""The full stack: real uvicorn, real Redis, real Celery worker, real HTTP, default bcrypt cost."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from api_tests import support
from api_tests.support import ANON, DOC, JOB, JOBS, NO_READ, NOT_MEMBER, PASSWORD, TEAM_MEMBER, TENANT_DOCS, UPLOAD, text_file

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
LOGS = Path(tempfile.gettempdir()) / "rag-api-audit"


def _stop(process):
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="module")
def stack(database):
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.fail("TEST_REDIS_URL is not set. Start the test Redis. See api_tests/README.md.")
    if redis_url.rstrip("/").endswith((":6379", ":6379/0")):
        pytest.fail("Refusing to use the development Redis on port 6379.")

    support.truncate()
    env = {**os.environ, "CELERY_BROKER_URL": redis_url, "PYTHONPATH": str(ROOT)}
    LOGS.mkdir(parents=True, exist_ok=True)
    api_log = (LOGS / "uvicorn.log").open("w")
    worker_log = (LOGS / "worker.log").open("w")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT)
    worker = subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.workers.celery_app:celery_app", "worker", "--pool=solo", "--loglevel=warning"], cwd=ROOT, env=env, stdout=worker_log, stderr=subprocess.STDOUT)
    client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
    try:
        for _ in range(120):
            try:
                if client.get("/openapi.json").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.5)
        else:
            pytest.fail(f"The API did not start. See {LOGS / 'uvicorn.log'}")
        api = support.Api(client, in_process=False)
        yield api, support.build_world(api, ingest=False)
    finally:
        client.close()
        _stop(worker)
        _stop(server)
        api_log.close()
        worker_log.close()


def _wait_for_job(api, actor, job_id, timeout=180):
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = api.send(actor, "GET", JOB, path={"job_id": job_id}).json()
        if job.get("status") in ("completed", "failed"):
            return job
        time.sleep(1)
    return job


def test_worker_processes_a_real_upload_end_to_end(stack):
    api, w = stack
    job = _wait_for_job(api, w.mona, w.job_hr)
    response = api.check(w.mona, "GET", JOB, path={"job_id": w.job_hr}, expect=200, behavior="Job processed by the real Celery worker through Redis")
    api.verify(job.get("status") == "completed", f"Job status {job.get('status')}: {job.get('error_message')}. See the worker log.")
    assert response.json()["attempts"] >= 1


@pytest.mark.parametrize("suffix", ["/text", "/chunks", "/download"])
def test_real_server_returns_content_to_the_owner_team(stack, suffix):
    api, w = stack
    _wait_for_job(api, w.mona, w.job_hr)
    api.check(w.mona, "GET", DOC + suffix, path={"tenant_id": w.a, "team_id": w.hr, "document_id": w.doc_hr}, expect=200, contains=["CANARY-A-HR"], behavior="Real content over real HTTP")


@pytest.mark.parametrize(("actor", "tenant", "team", "expect", "detail"), [("eli", "a", "hr", 403, NO_READ), ("bob", "a", "hr", 403, NOT_MEMBER)])
@pytest.mark.parametrize("suffix", support.READ_SUFFIXES, ids=lambda s: s.strip("/") or "details")
def test_real_server_blocks_other_team_and_tenant(stack, actor, tenant, team, expect, detail, suffix):
    api, w = stack
    api.check(getattr(w, actor), "GET", DOC + suffix, path={"tenant_id": getattr(w, tenant), "team_id": getattr(w, team), "document_id": w.doc_hr}, expect=expect, detail=detail, absent=["CANARY"], behavior="Denied over real HTTP")


def test_real_worker_generates_embeddings_after_ingestion(stack):
    api, w = stack
    _wait_for_job(api, w.mona, w.job_hr)
    path = {"tenant_id": w.a, "team_id": w.hr, "document_id": w.doc_hr}
    deadline = time.time() + 300
    body = {}
    while time.time() < deadline:
        body = api.send(w.mona, "GET", DOC + "/status", path=path).json()
        if body.get("embedding_status") in ("completed", "failed"):
            break
        time.sleep(2)
    api.check(w.mona, "GET", DOC + "/status", path=path, expect=200, behavior="Real worker, real model, through real Redis")
    api.verify(body.get("embedding_status") == "completed" and body["embedding_count"] == body["chunk_count"] >= 1, f"Status: {body}")


@pytest.mark.parametrize(("actor", "tenant", "team", "expect"), [("eli", "a", "hr", 403), ("bob", "a", "hr", 403), ("mona", "a", "hr", 200)])
def test_real_server_embedding_metadata_access(stack, actor, tenant, team, expect):
    api, w = stack
    api.check(getattr(w, actor), "GET", DOC + "/embeddings", path={"tenant_id": getattr(w, tenant), "team_id": getattr(w, team), "document_id": w.doc_hr}, expect=expect, absent=["CANARY"], behavior="Allowed for the team only")


def test_real_server_search_returns_only_authorized_chunks(stack):
    api, w = stack
    for document_id, actor, job in ((w.doc_hr, w.mona, w.job_hr), (w.doc_eng, w.eli, w.job_eng), (w.doc_b, w.bob, w.job_b)):
        _wait_for_job(api, actor, job)
        path = {"tenant_id": w.b if actor is w.bob else w.a, "team_id": w.tb if actor is w.bob else (w.hr if actor is w.mona else w.eng), "document_id": document_id}
        deadline = time.time() + 300
        while time.time() < deadline and api.send(actor, "GET", DOC + "/status", path=path).json().get("embedding_status") not in ("completed", "failed"):
            time.sleep(2)
    body = api.check(w.mona, "POST", support.SEARCH, path={"tenant_id": w.a}, json={"query": "confidential salary data"}, expect=200, contains=["CANARY-A-HR"], absent=["CANARY-A-ENG", "CANARY-B"], behavior="Mona finds HR only, using the real model in the real API process").json()
    api.verify({r["document_id"] for r in body["results"]} == {w.doc_hr}, str(body["results"])[:200])
    admin = api.check(w.alice, "POST", support.SEARCH, path={"tenant_id": w.a}, json={"query": "confidential"}, expect=200, contains=["CANARY-A-HR", "CANARY-A-ENG"], absent=["CANARY-B"], behavior="The admin finds both teams, never Tenant B").json()
    api.verify(len(admin["results"]) >= 2, str(admin)[:200])
    api.check(w.bob, "POST", support.SEARCH, path={"tenant_id": w.a}, json={"query": "salary"}, expect=403, detail=NOT_MEMBER, absent=["CANARY"], behavior="Another tenant is refused")


def test_real_server_upload_to_other_tenant_is_refused(stack):
    api, w = stack
    api.check(w.alice, "POST", UPLOAD, path={"tenant_id": w.b, "team_id": w.tb}, files=text_file(), expect=403, detail=NOT_MEMBER, behavior="Refused over real HTTP")


def test_real_server_user_list_requires_a_login(stack):
    api, w = stack
    api.check(ANON, "GET", "/users", expect=401, absent=[w.bob.email], behavior="No anonymous user directory", why="F-1 HIGH, confirmed on the real server.")


def test_real_server_returns_500_for_a_duplicate_on_users_route(stack):
    api, w = stack
    api.check(
        ANON, "POST", "/users", json={"email": w.mona.email, "username": "anotherone", "password": PASSWORD}, expect=(400, 401), behavior="Clean rejection, not a server error",
        why="BUG, confirmed on the real server: this validates that the in-process 500 results match a real deployment.",
    )


def test_real_server_removal_applies_to_the_existing_token(stack):
    api, w = stack
    doc = {"tenant_id": w.a, "team_id": w.hr, "document_id": w.doc_hr}
    api.check(w.vera, "GET", DOC, path=doc, expect=200, behavior="Access before removal")
    api.check(w.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": w.a, "team_id": w.hr, "user_id": w.vera.id}, expect=204, behavior="Removed")
    api.check(w.vera, "GET", DOC, path=doc, expect=403, detail=NO_READ, behavior="Access gone on the same token")
    jobs = api.check(w.vera, "GET", JOBS, expect=200, behavior="No visible jobs").json()
    api.verify(jobs == [], f"{len(jobs)} jobs visible")
    documents = api.check(w.vera, "GET", TENANT_DOCS, path={"tenant_id": w.a}, expect=200, behavior="No visible documents").json()
    api.verify(documents == [], f"{len(documents)} documents visible")
    results = api.check(w.vera, "POST", support.SEARCH, path={"tenant_id": w.a}, json={"query": "salary"}, expect=200, behavior="No search results", absent=["CANARY"]).json()
    api.verify(results["results"] == [], f"{len(results['results'])} results")
