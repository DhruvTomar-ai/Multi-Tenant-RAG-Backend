from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.db.models.role import Role
from app.db.models.user_team_membership import UserTeamMembership
from app.services.documents.ingestion import IngestionService
from app.workers.tasks import ingest_document
from tests.helpers import (
    DOCX_MIME,
    add_team_member,
    add_tenant_member,
    create_team,
    create_tenant,
    make_docx_bytes,
    make_pdf_bytes,
    register_user,
    upload,
)


@pytest.fixture
def world(client):
    """Company A (HR and Engineering teams) and Company B, with one user per situation."""
    admin_id, admin = register_user(client, "admin@a.example.com")
    tenant_a = create_tenant(client, admin, "company-a")
    hr = create_team(client, admin, tenant_a, "HR")
    engineering = create_team(client, admin, tenant_a, "Engineering")

    def join(email, team_id, role):
        user_id, headers = register_user(client, email)
        add_tenant_member(client, admin, tenant_a, email, "member")
        if team_id is not None:
            add_team_member(client, admin, tenant_a, team_id, user_id, role)
        return headers

    hr_member = join("hr.member@a.example.com", hr, "member")
    hr_viewer = join("hr.viewer@a.example.com", hr, "viewer")
    engineer = join("engineer@a.example.com", engineering, "member")
    no_team = join("no.team@a.example.com", None, None)

    _, b_admin = register_user(client, "admin@b.example.com")
    tenant_b = create_tenant(client, b_admin, "company-b")
    b_team = create_team(client, b_admin, tenant_b, "HR")

    return SimpleNamespace(
        admin=admin,
        tenant_a=tenant_a,
        hr=hr,
        engineering=engineering,
        hr_member=hr_member,
        hr_viewer=hr_viewer,
        engineer=engineer,
        no_team=no_team,
        b_admin=b_admin,
        tenant_b=tenant_b,
        b_team=b_team,
    )


def uploaded(client, headers, tenant_id, team_id, **kwargs):
    response = upload(client, headers, tenant_id, team_id, **kwargs)
    assert response.status_code == 202, response.text
    return response.json()


def process(client, world, **kwargs):
    """Upload to HR as an HR member and run the worker task on it. Returns the upload response."""
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr, **kwargs)
    ingest_document.apply(args=[body["job_id"]])
    return body


def doc_url(world, document_id, suffix=""):
    return f"/api/v1/tenants/{world.tenant_a}/teams/{world.hr}/documents/{document_id}{suffix}"


# --- Upload ---


@pytest.mark.parametrize(
    ("filename", "content", "mime", "stored_mime"),
    [
        ("report.pdf", make_pdf_bytes(), "application/pdf", "application/pdf"),
        ("report.docx", make_docx_bytes(), DOCX_MIME, DOCX_MIME),
        ("report.txt", b"Plain text.", "text/plain", "text/plain"),
    ],
)
def test_upload_supported_formats(client, world, db, queued_tasks, document_storage, filename, content, mime, stored_mime):
    response = upload(client, world.hr_member, world.tenant_a, world.hr, filename=filename, content=content, mime=mime)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["job_status"] == "pending"

    document = db.get(Document, body["document_id"])
    assert (document.tenant_id, document.team_id) == (world.tenant_a, world.hr)
    assert document.original_filename == filename
    assert document.content_type == stored_mime
    assert document.file_size == len(content)
    assert len(document.file_hash) == 64
    assert document.stored_filename != filename

    job = db.get(DocumentJob, body["job_id"])
    assert queued_tasks == [(job.id, job.celery_task_id)]
    assert (document_storage / str(world.tenant_a) / str(world.hr) / document.stored_filename).read_bytes() == content


@pytest.mark.parametrize(
    ("filename", "content", "mime", "expected_status"),
    [
        ("virus.exe", b"MZ binary", "application/octet-stream", 415),
        ("virus.exe", b"MZ binary", "application/pdf", 415),
        ("page.txt", b"<html></html>", "text/html", 415),
        ("fake.pdf", b"not really a pdf", "application/pdf", 400),
        ("fake.docx", b"not really a docx", DOCX_MIME, 400),
        ("broken.pdf", b"%PDF-1.4\ncorrupted", "application/pdf", 400),
        ("binary.txt", b"bin\x00ary", "text/plain", 400),
        ("empty.txt", b"", "text/plain", 400),
    ],
)
def test_upload_rejects_invalid_files(client, world, db, queued_tasks, filename, content, mime, expected_status):
    response = upload(client, world.hr_member, world.tenant_a, world.hr, filename=filename, content=content, mime=mime)

    assert response.status_code == expected_status, response.text
    assert db.scalars(select(Document)).all() == []
    assert queued_tasks == []


def test_upload_rejects_oversized_file(client, world, db, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    response = upload(client, world.hr_member, world.tenant_a, world.hr, content=b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413
    assert db.scalars(select(Document)).all() == []


def test_rejected_upload_leaves_no_file_behind(client, world, document_storage):
    upload(client, world.hr_member, world.tenant_a, world.hr, filename="fake.pdf", content=b"nope", mime="application/pdf")
    assert list((document_storage / ".staging").iterdir()) == []
    assert not (document_storage / str(world.tenant_a)).exists()


def test_path_traversal_filename_cannot_escape_storage(client, world, db, document_storage):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr, filename="../../../evil.txt")
    document = db.get(Document, body["document_id"])

    assert document.original_filename == "evil.txt"
    assert ".." not in document.stored_filename
    assert (document_storage / str(world.tenant_a) / str(world.hr) / document.stored_filename).is_file()


def test_failed_broker_marks_the_job_failed_and_returns_503(client, world, db, monkeypatch):
    def broker_down(job_id, task_id):
        raise ConnectionError("redis is down")

    monkeypatch.setattr("app.services.documents.task_queue.enqueue_ingestion_job", broker_down)
    response = upload(client, world.hr_member, world.tenant_a, world.hr)

    assert response.status_code == 503
    assert "redis" not in response.text.lower()
    assert db.scalars(select(DocumentJob)).one().status == "failed"
    assert db.scalars(select(Document)).one().status == "failed"


# --- Duplicates ---


def test_duplicate_in_same_team_is_rejected(client, world):
    first = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    second = upload(client, world.hr_member, world.tenant_a, world.hr, filename="renamed.txt")

    assert second.status_code == 409
    assert second.json()["detail"]["document_id"] == first["document_id"]


def test_same_content_is_allowed_in_another_team_and_tenant(client, world):
    uploaded(client, world.hr_member, world.tenant_a, world.hr)
    assert upload(client, world.engineer, world.tenant_a, world.engineering).status_code == 202
    assert upload(client, world.b_admin, world.tenant_b, world.b_team).status_code == 202


def test_new_content_under_the_same_name_gets_a_new_version(client, world, db):
    uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"Version one.")
    second = uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"Version two.")
    assert db.get(Document, second["document_id"]).version == 2


def test_uploading_a_failed_file_again_starts_a_new_job(client, world, db, queued_tasks):
    first = process(client, world, filename="blank.txt", content=b"     ")
    second = upload(client, world.hr_member, world.tenant_a, world.hr, filename="blank.txt", content=b"     ")

    assert second.status_code == 202
    body = second.json()
    assert body["document_id"] == first["document_id"]
    assert body["job_id"] != first["job_id"]
    assert len(queued_tasks) == 2


# --- Tenant isolation ---


def test_company_a_user_can_upload_to_company_a(client, world):
    assert upload(client, world.admin, world.tenant_a, world.hr).status_code == 202


def test_company_a_user_cannot_upload_to_company_b(client, world, db):
    assert upload(client, world.admin, world.tenant_b, world.b_team).status_code == 403
    assert db.scalars(select(Document)).all() == []


def test_non_member_gets_the_same_answer_for_real_and_fake_tenants(client, world):
    real = upload(client, world.hr_member, world.tenant_b, world.b_team)
    fake = upload(client, world.hr_member, 999999, 999999)
    assert real.status_code == fake.status_code == 403
    assert real.json() == fake.json()


def test_upload_requires_authentication(client, world):
    assert upload(client, {}, world.tenant_a, world.hr).status_code == 401


def test_team_from_another_tenant_cannot_be_used_through_own_tenant(client, world, db):
    response = upload(client, world.admin, world.tenant_a, world.b_team)
    assert response.status_code == 404
    assert db.scalars(select(Document)).all() == []


def test_inactive_tenant_blocks_upload_and_reads(client, world):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    assert client.patch(f"/api/v1/tenants/{world.tenant_a}", json={"is_active": False}, headers=world.admin).status_code == 200

    assert upload(client, world.hr_member, world.tenant_a, world.hr, content=b"more").status_code == 403
    assert client.get(doc_url(world, body["document_id"]), headers=world.hr_member).status_code == 403
    assert client.get(f"/api/v1/jobs/{body['job_id']}", headers=world.hr_member).status_code == 404


# --- Team isolation ---


def test_hr_user_can_upload_to_hr(client, world):
    assert upload(client, world.hr_member, world.tenant_a, world.hr).status_code == 202


def test_hr_user_cannot_upload_to_engineering(client, world):
    assert upload(client, world.hr_member, world.tenant_a, world.engineering).status_code == 403


def test_tenant_member_without_a_team_cannot_upload(client, world):
    assert upload(client, world.no_team, world.tenant_a, world.hr).status_code == 403


def test_tenant_admin_can_upload_to_any_team_through_explicit_permission(client, world):
    assert upload(client, world.admin, world.tenant_a, world.engineering).status_code == 202


def test_inactive_team_blocks_upload(client, world):
    response = client.patch(f"/api/v1/tenants/{world.tenant_a}/teams/{world.hr}", json={"is_active": False}, headers=world.admin)
    assert response.status_code == 200
    assert upload(client, world.hr_member, world.tenant_a, world.hr).status_code == 403


def test_leaving_a_team_removes_access(client, world, db):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    membership = db.scalars(
        select(UserTeamMembership).where(UserTeamMembership.team_id == world.hr, UserTeamMembership.role.has(Role.name == "viewer"))
    ).one()
    membership.is_active = False
    db.commit()

    # The viewer did not upload this document. An uploader keeps read access to their own uploads.
    assert client.get(doc_url(world, body["document_id"]), headers=world.hr_viewer).status_code == 403
    assert client.get(f"/api/v1/jobs/{body['job_id']}", headers=world.hr_viewer).status_code == 404


# --- Role and permission isolation ---


def test_read_only_user_cannot_upload_but_can_read(client, world):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)

    response = upload(client, world.hr_viewer, world.tenant_a, world.hr, content=b"viewer tries")

    assert response.status_code == 403
    assert "document.upload" in response.json()["detail"]
    assert client.get(doc_url(world, body["document_id"]), headers=world.hr_viewer).status_code == 200


def test_permissions_come_from_the_database_not_the_role_name(client, world, db):
    """Removing the permission row is enough to take the right away."""
    from app.db.models.permission import Permission

    db.query(Permission).filter(
        Permission.tenant_id == world.tenant_a,
        Permission.resource == "document",
        Permission.action == "upload",
    ).delete()
    db.commit()

    assert upload(client, world.hr_member, world.tenant_a, world.hr).status_code == 403


def test_tenant_role_does_not_grant_rights_inside_a_team(client, world):
    """A tenant "member" role holds document.upload, but only a team role counts inside a team."""
    assert upload(client, world.engineer, world.tenant_a, world.hr).status_code == 403


# --- Processing ---


@pytest.mark.parametrize(
    ("filename", "content", "mime", "expected_text"),
    [
        ("a.txt", b"Vacation policy for all staff.", "text/plain", "Vacation policy"),
        ("a.pdf", make_pdf_bytes("Vacation policy in a PDF"), "application/pdf", "Vacation policy"),
        ("a.docx", make_docx_bytes("Vacation policy in Word"), DOCX_MIME, "Vacation policy"),
    ],
)
def test_worker_extracts_and_chunks_each_format(client, world, db, filename, content, mime, expected_text):
    body = process(client, world, filename=filename, content=content, mime=mime)

    document = db.get(Document, body["document_id"])
    job = db.get(DocumentJob, body["job_id"])
    assert job.status == "completed"
    assert job.attempts == 1
    assert job.started_at is not None
    assert job.finished_at is not None
    assert document.status == "completed"
    assert expected_text in document.extracted_text
    assert len(document.extracted_text_hash) == 64
    assert document.chunk_count >= 1


def test_chunks_carry_the_documents_tenant_and_team(client, world, db):
    text = " ".join(f"Sentence number {number} about company policy." for number in range(600)).encode()
    body = process(client, world, filename="long.txt", content=text)

    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == body["document_id"]).order_by(DocumentChunk.chunk_index)).all()
    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert {(chunk.tenant_id, chunk.team_id) for chunk in chunks} == {(world.tenant_a, world.hr)}
    assert db.get(Document, body["document_id"]).chunk_count == len(chunks)


def test_pdf_chunks_record_their_page(client, world, db):
    body = process(client, world, filename="a.pdf", content=make_pdf_bytes("Some page text"), mime="application/pdf")
    chunk = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == body["document_id"])).first()
    assert chunk.metadata_json["page_label"] == "1"


def test_file_with_no_text_fails_permanently_without_retrying(client, world, db):
    body = process(client, world, filename="blank.txt", content=b"     \n   ")

    job = db.get(DocumentJob, body["job_id"])
    assert job.status == "failed"
    assert job.attempts == 1
    assert "No readable text" in job.error_message
    assert db.get(Document, body["document_id"]).status == "failed"


def test_pdf_with_no_text_fails_permanently(client, world, db):
    body = process(client, world, filename="scan.pdf", content=make_pdf_bytes(""), mime="application/pdf")
    assert db.get(DocumentJob, body["job_id"]).status == "failed"


def test_missing_stored_file_fails_permanently(client, world, db, document_storage):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    document = db.get(Document, body["document_id"])
    (document_storage / str(world.tenant_a) / str(world.hr) / document.stored_filename).unlink()

    ingest_document.apply(args=[body["job_id"]])

    db.expire_all()
    job = db.get(DocumentJob, body["job_id"])
    assert job.status == "failed"
    assert str(document_storage) not in job.error_message


def test_processing_a_job_twice_does_not_duplicate_chunks(client, world, db):
    body = process(client, world, content=b"Some text to chunk once.")
    before = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == body["document_id"])).all()
    assert len(before) >= 1

    # As if the broker delivered the task again after it had finished.
    assert IngestionService(db).process_job(body["job_id"]) is False

    # As if a worker crashed after saving but before the status change was seen.
    job = db.get(DocumentJob, body["job_id"])
    job.status = "pending"
    db.commit()
    assert IngestionService(db).process_job(body["job_id"]) is True

    db.expire_all()
    after = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == body["document_id"])).all()
    assert len(after) == len(before)


def test_temporary_failure_is_retried_and_succeeds_without_duplicates(client, world, db, monkeypatch):
    from app.services.documents import ingestion as ingestion_module

    real_extract = ingestion_module.extract_sections
    calls = {"count": 0}

    def fails_once(path, extension):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("temporary disk problem")
        return real_extract(path, extension)

    monkeypatch.setattr(ingestion_module, "extract_sections", fails_once)
    body = process(client, world, content=b"Text that survives a retry.")

    job = db.get(DocumentJob, body["job_id"])
    document = db.get(Document, body["document_id"])
    assert calls["count"] == 2
    assert job.status == "completed"
    assert job.attempts == 2
    assert job.error_message is None
    assert document.status == "completed"
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)).all()
    assert len(chunks) == document.chunk_count


def test_job_fails_after_running_out_of_retries(client, world, db, monkeypatch):
    from app.services.documents import ingestion as ingestion_module

    def always_fails(path, extension):
        raise OSError("/secret/internal/path is broken")

    monkeypatch.setattr(ingestion_module, "extract_sections", always_fails)
    monkeypatch.setattr(settings, "ingestion_max_retries", 2)
    body = process(client, world, content=b"Text that never works.")

    job = db.get(DocumentJob, body["job_id"])
    assert job.status == "failed"
    assert job.attempts == 3
    assert "/secret" not in job.error_message
    assert db.get(Document, body["document_id"]).status == "failed"


def test_same_text_in_another_format_reuses_existing_chunks(client, world, db):
    first = process(client, world, filename="policy.txt", content=b"Identical words in both files.")
    second = process(client, world, filename="policy.docx", content=make_docx_bytes("Identical words in both files."), mime=DOCX_MIME)

    copy = db.get(Document, second["document_id"])
    assert copy.status == "completed"
    assert copy.duplicate_of_document_id == first["document_id"]
    assert copy.chunk_count == 0
    assert db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == copy.id)).all() == []


def test_same_text_in_another_team_is_processed_independently(client, world, db):
    process(client, world, filename="policy.txt", content=b"Identical words in both teams.")
    body = uploaded(
        client,
        world.engineer,
        world.tenant_a,
        world.engineering,
        filename="policy.docx",
        content=make_docx_bytes("Identical words in both teams."),
        mime=DOCX_MIME,
    )
    ingest_document.apply(args=[body["job_id"]])

    document = db.get(Document, body["document_id"])
    assert document.duplicate_of_document_id is None
    assert document.chunk_count >= 1


# --- Access control ---


READ_SUFFIXES = ["", "/chunks", "/text", "/download", "/jobs"]


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_team_member_and_viewer_and_admin_can_read(client, world, suffix):
    body = process(client, world)
    for headers in (world.hr_member, world.hr_viewer, world.admin):
        assert client.get(doc_url(world, body["document_id"], suffix), headers=headers).status_code == 200


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_other_team_cannot_read(client, world, suffix):
    body = process(client, world)
    assert client.get(doc_url(world, body["document_id"], suffix), headers=world.engineer).status_code == 403


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_other_tenant_cannot_read(client, world, suffix):
    body = process(client, world)
    assert client.get(doc_url(world, body["document_id"], suffix), headers=world.b_admin).status_code == 403


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_document_cannot_be_reached_through_a_different_team_path(client, world, suffix):
    """The engineer has real read access to Engineering, but the document lives in HR."""
    body = process(client, world)
    url = f"/api/v1/tenants/{world.tenant_a}/teams/{world.engineering}/documents/{body['document_id']}{suffix}"
    assert client.get(url, headers=world.engineer).status_code == 404


def test_download_returns_the_original_bytes(client, world):
    body = process(client, world, filename="my notes.txt", content=b"Exact bytes.")
    response = client.get(doc_url(world, body["document_id"], "/download"), headers=world.hr_member)

    assert response.content == b"Exact bytes."
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_chunks_and_text_endpoints_return_the_content(client, world):
    body = process(client, world, content=b"Readable chunk content.")

    chunks = client.get(doc_url(world, body["document_id"], "/chunks"), headers=world.hr_member).json()
    text = client.get(doc_url(world, body["document_id"], "/text"), headers=world.hr_member).json()

    assert "Readable chunk content." in chunks[0]["chunk_text"]
    assert chunks[0]["tenant_id"] == world.tenant_a
    assert text["extracted_text"] == "Readable chunk content."


def test_tenant_document_list_only_shows_the_users_teams(client, world):
    hr_doc = uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"HR text")
    engineering_doc = uploaded(client, world.engineer, world.tenant_a, world.engineering, content=b"Engineering text")
    url = f"/api/v1/tenants/{world.tenant_a}/documents"

    def ids(headers):
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        return {document["id"] for document in response.json()}

    assert ids(world.hr_member) == {hr_doc["document_id"]}
    assert ids(world.engineer) == {engineering_doc["document_id"]}
    assert ids(world.admin) == {hr_doc["document_id"], engineering_doc["document_id"]}
    assert ids(world.no_team) == set()
    assert client.get(url, headers=world.b_admin).status_code == 403


def test_document_list_filters_and_paginates(client, world):
    process(client, world, content=b"Finished text.")
    uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"Waiting text.")
    url = f"/api/v1/tenants/{world.tenant_a}/teams/{world.hr}/documents"

    assert [d["status"] for d in client.get(url, params={"status": "pending"}, headers=world.hr_member).json()] == ["pending"]
    assert len(client.get(url, params={"limit": 1}, headers=world.hr_member).json()) == 1
    assert client.get(url, params={"status": "nonsense"}, headers=world.hr_member).status_code == 422


# --- Job APIs ---


def test_job_detail_for_authorized_users(client, world):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    for headers in (world.hr_member, world.hr_viewer, world.admin):
        response = client.get(f"/api/v1/jobs/{body['job_id']}", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "pending"


def test_job_detail_is_hidden_from_everyone_else(client, world):
    body = uploaded(client, world.hr_member, world.tenant_a, world.hr)
    missing = client.get("/api/v1/jobs/999999", headers=world.engineer)

    for headers in (world.engineer, world.no_team, world.b_admin):
        response = client.get(f"/api/v1/jobs/{body['job_id']}", headers=headers)
        assert response.status_code == 404
        assert response.json() == missing.json()
    assert client.get(f"/api/v1/jobs/{body['job_id']}").status_code == 401


def test_job_list_only_contains_accessible_jobs(client, world):
    hr_job = uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"HR text")["job_id"]
    engineering_job = uploaded(client, world.engineer, world.tenant_a, world.engineering, content=b"Eng text")["job_id"]
    b_job = uploaded(client, world.b_admin, world.tenant_b, world.b_team, content=b"B text")["job_id"]

    def ids(headers, **params):
        response = client.get("/api/v1/jobs", params=params, headers=headers)
        assert response.status_code == 200
        return {job["id"] for job in response.json()}

    assert ids(world.hr_member) == {hr_job}
    assert ids(world.engineer) == {engineering_job}
    assert ids(world.admin) == {hr_job, engineering_job}
    assert ids(world.b_admin) == {b_job}
    assert ids(world.no_team) == set()
    # Filters can't widen access.
    assert ids(world.hr_member, tenant_id=world.tenant_b) == set()
    assert ids(world.hr_member, team_id=world.engineering) == set()
    assert ids(world.admin, team_id=world.hr) == {hr_job}
    assert client.get("/api/v1/jobs").status_code == 401


def test_job_list_filters_by_status_and_document(client, world):
    done = process(client, world, content=b"Finished text.")
    waiting = uploaded(client, world.hr_member, world.tenant_a, world.hr, content=b"Waiting text.")

    def ids(**params):
        return {job["id"] for job in client.get("/api/v1/jobs", params=params, headers=world.hr_member).json()}

    assert ids(status="completed") == {done["job_id"]}
    assert ids(status="pending") == {waiting["job_id"]}
    assert ids(document_id=done["document_id"]) == {done["job_id"]}
    assert len(client.get("/api/v1/jobs", params={"limit": 1}, headers=world.hr_member).json()) == 1


# --- Database constraints: these hold even if application code has a bug ---


def _document(world, **overrides):
    values = {
        "tenant_id": world.tenant_a,
        "team_id": world.hr,
        "original_filename": "a.txt",
        "stored_filename": "a.txt",
        "content_type": "text/plain",
        "file_size": 1,
        "file_hash": "a" * 64,
        "file_extension": ".txt",
    }
    return Document(**{**values, **overrides})


def test_database_rejects_document_with_team_from_another_tenant(world, db):
    db.add(_document(world, team_id=world.b_team))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_chunk_with_mismatched_tenant(world, db):
    document = _document(world)
    db.add(document)
    db.commit()

    db.add(DocumentChunk(document_id=document.id, tenant_id=world.tenant_b, team_id=world.b_team, chunk_index=0, chunk_text="x", chunk_hash="a" * 64))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_duplicate_chunk_index(world, db):
    document = _document(world)
    db.add(document)
    db.commit()

    for _ in range(2):
        db.add(DocumentChunk(document_id=document.id, tenant_id=world.tenant_a, team_id=world.hr, chunk_index=0, chunk_text="x", chunk_hash="a" * 64))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_job_with_mismatched_team(world, db):
    document = _document(world)
    db.add(document)
    db.commit()

    db.add(DocumentJob(document_id=document.id, tenant_id=world.tenant_a, team_id=world.engineering, status="pending"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_duplicate_file_hash_in_a_team(world, db):
    db.add(_document(world))
    db.commit()

    db.add(_document(world, stored_filename="b.txt"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_unknown_status(world, db):
    db.add(_document(world, status="done"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_database_rejects_team_membership_with_another_tenants_role(client, world, db):
    b_role_id = db.scalar(select(Role.id).where(Role.tenant_id == world.tenant_b, Role.name == "admin"))
    membership = db.scalars(select(UserTeamMembership).where(UserTeamMembership.team_id == world.hr)).first()
    membership.role_id = b_role_id
    with pytest.raises(IntegrityError):
        db.commit()
