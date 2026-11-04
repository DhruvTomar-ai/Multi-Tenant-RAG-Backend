"""Document upload, retrieval, download and job status behaviour."""
from pathlib import Path

import pytest

from api_tests import support
from api_tests.support import DOC, DOC_NOT_FOUND, DOCX_MIME, JOB, JOBS, TEAM_DOCS, TENANT_DOCS, UPLOAD, text_file
from app.core.config import settings
from app.db.models.document import Document
from app.services.documents import task_queue
from tests.helpers import make_docx_bytes, make_pdf_bytes


def up(w):
    return {"tenant_id": w.a, "team_id": w.hr}


def doc(w, document_id=None):
    return {"tenant_id": w.a, "team_id": w.hr, "document_id": document_id or w.doc_hr}


def db_document(document_id):
    with support.db_session.SessionLocal() as session:
        return session.get(Document, document_id)


# --- Upload success ---


@pytest.mark.parametrize(
    ("name", "content", "mime", "stored"),
    [("a.txt", b"Plain text content.", "text/plain", "text/plain"), ("a.pdf", make_pdf_bytes(), "application/pdf", "application/pdf"), ("a.docx", make_docx_bytes(), DOCX_MIME, DOCX_MIME)],
    ids=["txt", "pdf", "docx"],
)
def test_upload_and_process_each_format(api, fresh, name, content, mime, stored):
    body = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files={"file": (name, content, mime)}, expect=202, behavior="Accepted, pending").json()
    api.verify(body["status"] == "pending" and body["job_status"] == "pending", f"Statuses {body}")
    support.ingest(body["job_id"])
    job = api.check(fresh.mona, "GET", JOB, path={"job_id": body["job_id"]}, expect=200, behavior="Job completes").json()
    api.verify(job["status"] == "completed", f"Job {job['status']}: {job['error_message']}")
    document = api.check(fresh.mona, "GET", DOC, path=doc(fresh, body["document_id"]), expect=200, behavior="Type decided by the server, chunks created").json()
    api.verify(document["content_type"] == stored and document["chunk_count"] >= 1, f"{document['content_type']}, {document['chunk_count']} chunks")


@pytest.mark.parametrize("actor", ["mona", "mike", "adam", "alice"])
def test_upload_allowed_roles(api, fresh, actor):
    api.check(getattr(fresh, actor), "POST", UPLOAD, path=up(fresh), files=text_file(), expect=202, behavior="Upload permission held")


def test_admin_uploads_to_a_team_they_have_not_joined(api, fresh):
    api.check(fresh.alice, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.eng}, files=text_file(), expect=202, behavior="Allowed by team.access_all")


# --- Upload validation ---


@pytest.mark.parametrize(
    ("name", "content", "mime", "expect"),
    [
        ("virus.exe", b"MZ binary", "application/octet-stream", 415), ("virus.exe", b"MZ binary", "application/pdf", 415), ("page.txt", b"<html></html>", "text/html", 415),
        ("noextension", b"data", "text/plain", 415), ("fake.pdf", b"not a pdf", "application/pdf", 400), ("fake.docx", b"not a docx", DOCX_MIME, 400),
        ("broken.pdf", b"%PDF-1.4\ncorrupted", "application/pdf", 400), ("binary.txt", b"bin\x00ary", "text/plain", 400), ("latin.txt", b"caf\xe9", "text/plain", 400), ("empty.txt", b"", "text/plain", 400),
    ],
    ids=["exe", "exe_claiming_pdf", "html_mime", "no_extension", "fake_pdf", "fake_docx", "broken_pdf", "binary_txt", "non_utf8", "empty"],
)
def test_upload_rejects_invalid_files(api, fresh, name, content, mime, expect):
    before = support.count("SELECT count(*) FROM documents")
    api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files={"file": (name, content, mime)}, expect=expect, behavior="Rejected, nothing stored")
    api.verify(support.count("SELECT count(*) FROM documents") == before, "A document was stored")


def test_upload_without_a_file(api, fresh):
    api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), expect=422, behavior="Validation error")


def test_upload_with_wrong_field_name(api, fresh):
    api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files={"document": ("a.txt", b"x", "text/plain")}, expect=422, behavior="Validation error")


def test_upload_oversized_file(api, fresh, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file(b"x" * (1024 * 1024 + 1)), expect=413, behavior="Too large")


def test_path_traversal_filename_stays_inside_storage(api, fresh):
    body = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file(name="../../../evil.txt"), expect=202, behavior="Name is sanitized").json()
    document = db_document(body["document_id"])
    api.verify(document.original_filename == "evil.txt", f"Stored name {document.original_filename}")
    api.verify(".." not in document.stored_filename, "Path characters in the stored name")


def test_broker_failure_is_a_503_and_marks_the_job_failed(api, fresh, monkeypatch):
    monkeypatch.setattr(task_queue, "enqueue_ingestion_job", support.REAL_ENQUEUE)
    response = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file(), expect=503, behavior="The real enqueue call fails against an unreachable broker", absent=["redis", "Redis"])
    api.verify("queue" in response.json()["detail"], "Unhelpful message")


# --- Duplicates and versions ---


def test_duplicate_in_same_team(api, fresh):
    files = text_file("Same bytes.", "one.txt")
    first = api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=files).json()
    response = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("Same bytes.", "two.txt"), expect=409, behavior="Conflict with the existing ID")
    api.verify(response.json()["detail"]["document_id"] == first["document_id"], "Wrong document ID")


def test_same_bytes_in_other_team_and_tenant_reveal_nothing(api, fresh):
    api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("Shared bytes."))
    api.check(fresh.eli, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.eng}, files=text_file("Shared bytes."), expect=202, behavior="No cross-team duplicate signal")
    api.check(fresh.bob, "POST", UPLOAD, path={"tenant_id": fresh.b, "team_id": fresh.tb}, files=text_file("Shared bytes."), expect=202, behavior="No cross-tenant duplicate signal")


def test_same_name_new_content_gets_a_new_version(api, fresh):
    api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("Version one.", "policy.txt"))
    body = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("Version two.", "policy.txt"), expect=202, behavior="New version").json()
    api.verify(db_document(body["document_id"]).version == 2, "Version is not 2")


def test_failed_document_can_be_uploaded_again(api, fresh):
    first = api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("     ", "blank.txt")).json()
    support.ingest(first["job_id"])
    job = api.check(fresh.mona, "GET", JOB, path={"job_id": first["job_id"]}, expect=200, behavior="A file with no text fails").json()
    api.verify(job["status"] == "failed" and "No readable text" in job["error_message"], f"Job {job['status']}: {job['error_message']}")
    second = api.check(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file("     ", "blank.txt"), expect=202, behavior="New job on the same document").json()
    api.verify(second["document_id"] == first["document_id"] and second["job_id"] != first["job_id"], str(second))


# --- Retrieval ---


def test_download_returns_exact_bytes_as_an_attachment(api, fresh):
    response = api.check(fresh.mona, "GET", DOC + "/download", path=doc(fresh), expect=200, behavior="Original bytes, safe headers")
    api.verify(response.text.startswith("CANARY-A-HR"), "Wrong content")
    api.verify("attachment" in response.headers["content-disposition"], "Not an attachment")
    api.verify(response.headers["x-content-type-options"] == "nosniff", "nosniff missing")


def test_download_missing_stored_file(api, fresh):
    path = Path(settings.document_storage_root) / str(fresh.a) / str(fresh.hr) / db_document(fresh.doc_hr).stored_filename
    path.unlink()
    api.check(fresh.mona, "GET", DOC + "/download", path=doc(fresh), expect=404, detail="The stored file is missing", behavior="Clean 404 without the server path", absent=[str(settings.document_storage_root)])


def test_text_and_chunks_before_processing(api, fresh):
    text = api.check(fresh.mona, "GET", DOC + "/text", path=doc(fresh), expect=200, behavior="Text is null while pending").json()
    api.verify(text["extracted_text"] is None, "Text exists before processing")
    chunks = api.check(fresh.mona, "GET", DOC + "/chunks", path=doc(fresh), expect=200, behavior="No chunks while pending").json()
    api.verify(chunks == [], "Chunks exist before processing")


def test_text_and_chunks_after_processing(api, fresh):
    support.ingest(fresh.job_hr)
    text = api.check(fresh.mona, "GET", DOC + "/text", path=doc(fresh), expect=200, behavior="Extracted text returned", contains=["CANARY-A-HR"]).json()
    api.verify(text["document_id"] == fresh.doc_hr, "Wrong document")
    chunks = api.check(fresh.mona, "GET", DOC + "/chunks", path=doc(fresh), expect=200, behavior="Chunks carry tenant and team", contains=["CANARY-A-HR"]).json()
    api.verify(all(c["tenant_id"] == fresh.a and c["team_id"] == fresh.hr for c in chunks), "Wrong scope on a chunk")


def test_document_response_hides_storage_details(api, fresh):
    api.check(fresh.mona, "GET", DOC, path=doc(fresh), expect=200, behavior="No storage path or generated file name", absent=["stored_filename", db_document(fresh.doc_hr).stored_filename])


@pytest.mark.parametrize("suffix", support.READ_SUFFIXES, ids=lambda s: s.strip("/") or "details")
def test_missing_document_is_a_404(api, fresh, suffix):
    api.check(fresh.mona, "GET", DOC + suffix, path=doc(fresh, 999999999), expect=404, detail=DOC_NOT_FOUND, behavior="Not found")


@pytest.mark.parametrize("bad", ["abc", "1.5", "1 OR 1=1", "1;DROP TABLE documents"])
def test_invalid_document_id(api, fresh, bad):
    api.check(fresh.mona, "GET", DOC, path=doc(fresh, bad), expect=422, behavior="Rejected before reaching the database")


@pytest.mark.parametrize("value", [99999999999999999999, -1, 0])
def test_out_of_range_document_id_does_not_crash(api, fresh, value):
    api.check(
        fresh.mona, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": value}, expect=(404, 422), behavior="Clean rejection, not a server error",
        why="BUG: the value exceeds the PostgreSQL integer range and the app does not bound path IDs.",
    )


@pytest.mark.parametrize("value", [99999999999999999999, -1, 0])
def test_out_of_range_job_id_does_not_crash(api, fresh, value):
    api.check(fresh.mona, "GET", JOB, path={"job_id": value}, expect=(404, 422), behavior="Clean rejection, not a server error", why="BUG: the value exceeds the PostgreSQL integer range.")


@pytest.mark.parametrize("value", [99999999999999999999, -1])
def test_out_of_range_tenant_id_does_not_crash(api, fresh, value):
    api.check(fresh.mona, "GET", TENANT_DOCS, path={"tenant_id": value}, expect=(403, 404, 422), behavior="Clean rejection, not a server error", why="BUG: the value exceeds the PostgreSQL integer range.")


# --- Filters and pagination ---


@pytest.mark.parametrize("params", [{"status": "nonsense"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "abc"}], ids=["bad_status", "limit_0", "limit_201", "negative_offset", "text_limit"])
@pytest.mark.parametrize("template", [TENANT_DOCS, TEAM_DOCS], ids=["tenant_docs", "team_docs"])
def test_document_list_invalid_filters(api, fresh, template, params):
    api.check(fresh.mona, "GET", template, path=up(fresh) if template == TEAM_DOCS else {"tenant_id": fresh.a}, params=params, expect=422, behavior="Validation error")


def test_document_list_status_filter_and_pagination(api, fresh):
    support.ingest(fresh.job_hr)
    api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file())
    pending = api.check(fresh.mona, "GET", TEAM_DOCS, path=up(fresh), params={"status": "pending"}, expect=200, behavior="Only pending").json()
    api.verify([d["status"] for d in pending] == ["pending"], str([d["status"] for d in pending]))
    one = api.check(fresh.mona, "GET", TEAM_DOCS, path=up(fresh), params={"limit": 1}, expect=200, behavior="limit applies").json()
    api.verify(len(one) == 1, f"{len(one)} rows")
    rest = api.check(fresh.mona, "GET", TEAM_DOCS, path=up(fresh), params={"offset": 1}, expect=200, behavior="offset applies").json()
    api.verify(len(rest) == 1 and rest[0]["id"] != one[0]["id"], f"{len(rest)} rows")


@pytest.mark.parametrize("params", [{"status": "nonsense"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"tenant_id": "abc"}, {"team_id": "abc"}, {"document_id": "abc"}], ids=["bad_status", "limit_0", "limit_201", "negative_offset", "text_tenant", "text_team", "text_document"])
def test_job_list_invalid_filters(api, fresh, params):
    api.check(fresh.mona, "GET", JOBS, params=params, expect=422, behavior="Validation error")


def test_job_list_status_filter(api, fresh):
    support.ingest(fresh.job_hr)
    second = api.setup(fresh.mona, "POST", UPLOAD, path=up(fresh), files=text_file()).json()
    jobs = api.check(fresh.mona, "GET", JOBS, params={"status": "completed"}, expect=200, behavior="Only completed jobs").json()
    api.verify({j["id"] for j in jobs} == {fresh.job_hr}, str([j["id"] for j in jobs]))
    jobs = api.check(fresh.mona, "GET", JOBS, params={"status": "pending"}, expect=200, behavior="Only pending jobs").json()
    api.verify({j["id"] for j in jobs} == {second["job_id"]}, str([j["id"] for j in jobs]))


@pytest.mark.parametrize("bad", ["abc", "1.5"])
def test_invalid_job_id(api, fresh, bad):
    api.check(fresh.mona, "GET", JOB, path={"job_id": bad}, expect=422, behavior="Validation error")


# --- Missing document operations ---


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
def test_documents_cannot_be_modified_through_the_api(api, fresh, method):
    api.check(fresh.alice, method, DOC, path=doc(fresh), expect=405, detail="Method Not Allowed", behavior="No such operation exists")
    api.verify(db_document(fresh.doc_hr) is not None, "Document was changed")
