"""Embeddings with the real BGE model and real pgvector: generation, failure handling, HNSW and isolation."""
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api_tests import support
from api_tests.support import DOC, TEAM_DOCS, TENANT_DOCS, UPLOAD, text_file
from app.core.config import settings
from app.db import session as db_session
from app.db.models.document import Document
from app.db.models.embedding import Embedding
from app.services.documents import task_queue
from app.services.embeddings import model as embedding_model
from app.services.embeddings.search import SearchFilters, build_search_statement, search_embeddings

RETRY = DOC + "/embeddings/retry"
MODEL = "/tenants/{tenant_id}/embeddings/model"
INDEX = "/tenants/{tenant_id}/embeddings/index"
LONG_TEXT = " ".join(f"Sentence number {n} describes a topic in the company handbook in some detail." for n in range(900))


def hr_doc(w, document_id=None):
    return {"tenant_id": w.a, "team_id": w.hr, "document_id": document_id or w.doc_hr}


def embedded_world(w):
    for job in (w.job_hr, w.job_eng, w.job_b):
        support.ingest(job)
    for document_id in (w.doc_hr, w.doc_eng, w.doc_b):
        support.embed(document_id)
    return w


def counts(document_id):
    return (
        support.count("SELECT count(*) FROM document_chunks WHERE document_id = :d", d=document_id),
        support.count("SELECT count(*) FROM embeddings WHERE document_id = :d", d=document_id),
    )


def stored(document_id):
    with db_session.SessionLocal() as session:
        return session.get(Document, document_id)


def query_vector(text_value="salary and confidential pay"):
    return embedding_model.encode_query(text_value).tolist()


def search(user, tenant_id, **kwargs):
    with db_session.SessionLocal() as session:
        return search_embeddings(session, user_id=user.id, tenant_id=tenant_id, query_vector=kwargs.pop("vector", query_vector()), **kwargs)


# --- Generation ---


def test_every_chunk_gets_a_384_dimension_vector_with_model_details(api, fresh):
    embedded_world(fresh)
    chunks, vectors = counts(fresh.doc_hr)
    support.fact(chunks >= 1 and chunks == vectors, name="embeddings per chunk", expected="One vector per chunk", actual=f"{chunks} chunks, {vectors} vectors")
    with db_session.SessionLocal() as session:
        row = session.execute(text(
            "SELECT vector_dims(embedding), round(vector_norm(embedding)::numeric, 3), model_name, model_version, dimension, created_at FROM embeddings WHERE document_id = :d LIMIT 1"
        ), {"d": fresh.doc_hr}).one()
    support.fact(row[0] == 384 and row[4] == 384, name="vector_dims", expected="384", actual=str(row[0]))
    support.fact(float(row[1]) == 1.0, name="vector_norm", expected="Normalized to 1.0", actual=str(row[1]))
    support.fact(row[2] == "BAAI/bge-small-en-v1.5", name="model_name", expected="BAAI/bge-small-en-v1.5", actual=row[2])
    support.fact(row[3] == settings.embedding_model_revision and len(row[3]) == 40, name="model_version", expected="The pinned commit", actual=row[3])
    support.fact(row[5] is not None, name="created_at", expected="A timestamp", actual=str(row[5]))
    support.fact(stored(fresh.doc_hr).embedding_status == "completed", name="embedding_status", expected="completed", actual=stored(fresh.doc_hr).embedding_status)


def test_real_model_is_loaded_only_once(api, fresh):
    before = embedding_model.get_model()
    embedded_world(fresh)
    support.fact(embedding_model.get_model() is before and embedding_model.load_count == 1, name="model reuse", expected="One load in this process", actual=f"{embedding_model.load_count} load(s)")


def test_embeddings_carry_real_meaning(api, fresh):
    embedded_world(fresh)
    hits = search(fresh.alice, fresh.a, vector=query_vector("confidential salary data"))
    support.fact(hits[0].document_id == fresh.doc_hr, name="semantic ranking", expected="Salary query ranks the HR document first", actual=f"First hit: document {hits[0].document_id}")


def test_long_document_is_embedded_in_batches(api, fresh, monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 4)
    sizes = []
    real = embedding_model.encode_documents
    monkeypatch.setattr(embedding_model, "encode_documents", lambda texts: sizes.append(len(texts)) or real(texts))
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(LONG_TEXT)).json()
    support.ingest(body["job_id"])
    support.embed(body["document_id"])
    chunks, vectors = counts(body["document_id"])
    support.fact(chunks > 4 and chunks == vectors, name="batched embedding", expected="All chunks embedded", actual=f"{chunks} chunks, {vectors} vectors")
    support.fact(len(sizes) > 1 and max(sizes) <= 4, name="batch size", expected="No batch above 4", actual=f"Batches: {sizes}")


@pytest.mark.parametrize(
    "kind",
    ["prose", "technical", "source_code", "urls", "number_table", "chinese", "punctuation_heavy"],
)
def test_configured_chunks_fit_the_model_limit(kind):
    """Measured with BGE's own tokenizer, because text past its limit is silently not embedded."""
    from app.services.documents.processing import ExtractedSection, chunk_sections

    content = {
        "prose": " ".join(f"The committee reviewed item {n} and agreed to continue the work next quarter." for n in range(2000)),
        "technical": " ".join(f"Parameter x_{n} (array-like, shape [n_samples, n_features]) defaults to None; see fit_transform()." for n in range(2000)),
        "source_code": "\n".join(f"def fn_{n}(x_{n}, y_{n}): return some_module.call_{n}(x_{n}[{n}], key='value_{n}') + {n * 7}" for n in range(1500)),
        "urls": " ".join(f"https://example.com/a{n}/b{n * 3}?id={n * 7}&t=x{n}" for n in range(1500)),
        "number_table": "\n".join(" | ".join(f"{n * 13 % 99999}.{i}" for i in range(12)) for n in range(800)),
        "chinese": "。".join("企业文档管理系统的安全与权限控制要求说明内容" for _ in range(1500)),
        "punctuation_heavy": " ".join("a.b,c;d:e!f?g(h)i[j]k{l}m" for _ in range(3000)),
    }[kind]
    chunks = chunk_sections([ExtractedSection(text=content)], chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    model = embedding_model.get_model()
    longest = max(len(ids) for ids in model.tokenizer([c.text for c in chunks], add_special_tokens=True, truncation=False)["input_ids"])
    support.fact(longest <= model.max_seq_length, name=f"chunk length: {kind}", expected=f"At most {model.max_seq_length} BGE tokens", actual=f"Longest chunk: {longest} tokens")


# --- Failure, retry and duplicates ---


def test_failed_batch_keeps_document_and_chunks_then_retry_resumes_without_duplicates(api, fresh, monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 4)
    monkeypatch.setattr(settings, "embedding_max_retries", 0)
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(LONG_TEXT)).json()
    support.ingest(body["job_id"])
    document_id = body["document_id"]
    chunks_before, _ = counts(document_id)

    real = embedding_model.encode_documents
    calls = {"n": 0}

    def fails_on_second_batch(texts):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("temporary failure")
        return real(texts)

    monkeypatch.setattr(embedding_model, "encode_documents", fails_on_second_batch)
    support.embed(document_id)
    document = stored(document_id)
    chunks_after, partial = counts(document_id)
    support.fact(document.embedding_status == "failed", name="status after failure", expected="failed", actual=document.embedding_status)
    support.fact("temporary failure" not in (document.embedding_error or ""), name="error message", expected="No internal detail", actual=str(document.embedding_error))
    support.fact(document.status == "completed" and chunks_after == chunks_before, name="document after failure", expected="Document and chunks untouched", actual=f"{document.status}, {chunks_after} chunks")
    support.fact(partial == 4, name="partial progress", expected="First batch kept", actual=f"{partial} vectors")

    with db_session.SessionLocal() as session:
        kept = dict(session.execute(text("SELECT id, embedding::text FROM embeddings WHERE document_id = :d"), {"d": document_id}).all())

    monkeypatch.setattr(embedding_model, "encode_documents", real)
    response = api.check(fresh.mona, "POST", RETRY, path=hr_doc(fresh, document_id), expect=202, behavior="Retry accepted").json()
    api.verify(response["embedding_status"] == "pending", str(response))
    api.verify(support.EMBEDDING_TASKS[-1] == document_id, "No task was queued")
    support.embed(document_id)
    _, final = counts(document_id)
    support.fact(final == chunks_before, name="after retry", expected=f"{chunks_before} vectors, one per chunk", actual=f"{final} vectors")
    with db_session.SessionLocal() as session:
        after = dict(session.execute(text("SELECT id, embedding::text FROM embeddings WHERE document_id = :d"), {"d": document_id}).all())
    support.fact(all(after.get(key) == value for key, value in kept.items()), name="existing vectors", expected="Not rewritten by the retry", actual="Compared by ID")


def test_temporary_failure_is_retried_automatically(api, fresh, monkeypatch):
    monkeypatch.setattr(settings, "embedding_max_retries", 2)
    monkeypatch.setattr(settings, "embedding_retry_backoff_seconds", 0)
    support.ingest(fresh.job_hr)
    real = embedding_model.encode_documents
    calls = {"n": 0}

    def fails_once(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("temporary failure")
        return real(texts)

    monkeypatch.setattr(embedding_model, "encode_documents", fails_once)
    support.embed(fresh.doc_hr)
    document = stored(fresh.doc_hr)
    support.fact(document.embedding_status == "completed" and document.embedding_attempts == 2, name="automatic retry", expected="Completed on attempt 2", actual=f"{document.embedding_status}, attempt {document.embedding_attempts}")


def test_redelivered_task_changes_nothing(api, fresh):
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    before = counts(fresh.doc_hr), stored(fresh.doc_hr).embedding_attempts
    support.embed(fresh.doc_hr)
    after = counts(fresh.doc_hr), stored(fresh.doc_hr).embedding_attempts
    support.fact(before == after, name="duplicate delivery", expected="No change", actual=f"{before} then {after}")


def test_database_refuses_a_duplicate_vector(api, fresh):
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    with db_session.SessionLocal() as session:
        row = session.execute(text("SELECT chunk_id, document_id, tenant_id, team_id, model_name, model_version FROM embeddings WHERE document_id = :d LIMIT 1"), {"d": fresh.doc_hr}).one()
        session.add(Embedding(chunk_id=row[0], document_id=row[1], tenant_id=row[2], team_id=row[3], model_name=row[4], model_version=row[5], dimension=384, embedding=[0.0] * 384))
        rejected = False
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            rejected = True
    support.fact(rejected, name="uq_embedding_chunk_model_version", expected="IntegrityError", actual="Rejected" if rejected else "Accepted")


def _insert_rejected(**overrides):
    with db_session.SessionLocal() as session:
        chunk = session.execute(text("SELECT id, document_id, tenant_id, team_id FROM document_chunks ORDER BY id LIMIT 1")).one()
        values = dict(chunk_id=chunk[0], document_id=chunk[1], tenant_id=chunk[2], team_id=chunk[3], model_name="m", model_version="v", dimension=384, embedding=[0.0] * 384)
        session.add(Embedding(**{**values, **overrides}))
        try:
            session.commit()
        except Exception:
            session.rollback()
            return True
        return False


@pytest.mark.parametrize("kind", ["foreign_tenant", "foreign_team", "foreign_document", "wrong_dimension_field", "short_vector"])
def test_database_refuses_inconsistent_embeddings(api, fresh, kind):
    support.ingest(fresh.job_hr)
    overrides = {
        "foreign_tenant": {"tenant_id": fresh.b, "team_id": fresh.tb},
        "foreign_team": {"team_id": fresh.eng},
        "foreign_document": {"document_id": fresh.doc_eng},
        "wrong_dimension_field": {"dimension": 768},
        "short_vector": {"embedding": [0.0] * 10},
    }[kind]
    rejected = _insert_rejected(**overrides)
    support.fact(rejected, name=f"embeddings constraint: {kind}", expected="Rejected by the database", actual="Rejected" if rejected else "Accepted")


def test_wrong_model_output_fails_permanently_and_stores_nothing(api, fresh, monkeypatch):
    support.ingest(fresh.job_hr)
    monkeypatch.setattr(embedding_model.get_model(), "encode", lambda texts, **kwargs: np.zeros((len(texts), 10)))
    support.embed(fresh.doc_hr)
    document = stored(fresh.doc_hr)
    support.fact(document.embedding_status == "failed" and document.embedding_attempts == 1, name="wrong dimension", expected="Failed once, not retried", actual=f"{document.embedding_status}, {document.embedding_attempts} attempt(s)")
    support.fact(counts(fresh.doc_hr)[1] == 0, name="vectors after failure", expected="0", actual=str(counts(fresh.doc_hr)[1]))


def test_broker_outage_after_ingestion_keeps_the_chunks(api, fresh, monkeypatch):
    def down(document_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr(task_queue, "enqueue_embedding_job", down)
    support.ingest(fresh.job_hr)
    document = stored(fresh.doc_hr)
    support.fact(document.status == "completed" and document.embedding_status == "failed", name="queue outage", expected="Ingestion completed, embedding failed", actual=f"{document.status}, {document.embedding_status}")
    support.fact(counts(fresh.doc_hr)[0] >= 1, name="chunks after outage", expected="Chunks kept", actual=str(counts(fresh.doc_hr)[0]))


def test_retry_with_broker_down_is_a_503(api, fresh, monkeypatch):
    support.ingest(fresh.job_hr)
    support.set_columns(Document, fresh.doc_hr, embedding_status="failed")
    monkeypatch.setattr(task_queue, "enqueue_embedding_job", support.REAL_ENQUEUE_EMBEDDING)
    api.check(fresh.mona, "POST", RETRY, path=hr_doc(fresh), expect=503, behavior="Queue unavailable", absent=["redis"])


def test_reprocessing_replaces_vectors_without_orphans(api, fresh):
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    support.set_columns(support_job_model(), fresh.job_hr, status="pending")
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    chunks, vectors = counts(fresh.doc_hr)
    support.fact(chunks == vectors, name="after reprocessing", expected="One vector per new chunk", actual=f"{chunks} chunks, {vectors} vectors")
    orphans = support.count("SELECT count(*) FROM embeddings e LEFT JOIN document_chunks c ON c.id = e.chunk_id WHERE c.id IS NULL")
    support.fact(orphans == 0, name="orphan vectors", expected="0", actual=str(orphans))


def support_job_model():
    from app.db.models.document_job import DocumentJob

    return DocumentJob


def test_duplicate_text_document_needs_no_vectors(api, fresh):
    support.ingest(fresh.job_hr)
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file("CANARY-A-HR confidential salary data.\n", "copy.txt")).json()
    support.ingest(body["job_id"])
    support.embed(body["document_id"])
    document = stored(body["document_id"])
    support.fact(document.embedding_status == "not_required", name="duplicate document", expected="not_required", actual=document.embedding_status)
    support.fact(counts(body["document_id"])[1] == 0, name="duplicate vectors", expected="0", actual=str(counts(body["document_id"])[1]))


# --- Status and metadata endpoints ---


def test_status_endpoint_tracks_the_whole_lifecycle(api, fresh):
    path = hr_doc(fresh)
    pending = api.check(fresh.mona, "GET", DOC + "/status", path=path, expect=200, behavior="Before processing").json()
    api.verify((pending["status"], pending["embedding_status"], pending["embedding_count"]) == ("pending", "not_started", 0), str(pending))
    support.ingest(fresh.job_hr)
    ingested = api.check(fresh.mona, "GET", DOC + "/status", path=path, expect=200, behavior="After ingestion").json()
    api.verify((ingested["status"], ingested["embedding_status"], ingested["latest_job_id"]) == ("completed", "pending", fresh.job_hr), str(ingested))
    support.embed(fresh.doc_hr)
    done = api.check(fresh.mona, "GET", DOC + "/status", path=path, expect=200, behavior="After embedding").json()
    api.verify(done["embedding_status"] == "completed" and done["embedding_count"] == done["chunk_count"] >= 1 and done["embedded_at"], str(done))


def test_embeddings_endpoint_returns_metadata_and_never_vectors(api, fresh):
    embedded_world(fresh)
    response = api.check(fresh.mona, "GET", DOC + "/embeddings", path=hr_doc(fresh), expect=200, behavior="Metadata without vectors")
    body = response.json()
    api.verify(body["embedding_count"] == body["chunk_count"] == len(body["items"]) >= 1, str(body)[:200])
    api.verify(set(body["items"][0]) == {"id", "chunk_id", "chunk_index", "model_name", "model_version", "dimension", "created_at"}, str(body["items"][0]))
    api.verify("embedding\":" not in response.text.replace("embedding_status", "").replace("embedding_count", ""), "A vector field was returned")


def test_embeddings_endpoint_is_paginated(api, fresh):
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(LONG_TEXT)).json()
    support.ingest(body["job_id"])
    support.embed(body["document_id"])
    first = api.check(fresh.mona, "GET", DOC + "/embeddings", path=hr_doc(fresh, body["document_id"]), params={"limit": 2}, expect=200, behavior="First page").json()
    second = api.check(fresh.mona, "GET", DOC + "/embeddings", path=hr_doc(fresh, body["document_id"]), params={"limit": 2, "offset": 2}, expect=200, behavior="Second page").json()
    api.verify(len(first["items"]) == 2 and first["embedding_count"] > 2, str(first)[:200])
    api.verify({i["id"] for i in first["items"]}.isdisjoint(i["id"] for i in second["items"]), "Pages overlap")


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}], ids=["limit_0", "limit_501", "negative_offset"])
def test_embeddings_pagination_is_validated(api, fresh, params):
    api.check(fresh.mona, "GET", DOC + "/embeddings", path=hr_doc(fresh), params=params, expect=422, behavior="Validation error")


def test_embedding_status_filter(api, fresh):
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    done = api.check(fresh.alice, "GET", TENANT_DOCS, path={"tenant_id": fresh.a}, params={"embedding_status": "completed"}, expect=200, behavior="Only embedded documents").json()
    api.verify([d["id"] for d in done] == [fresh.doc_hr], str([d["id"] for d in done]))


@pytest.mark.parametrize(("actor", "tenant", "expect"), [("mona", "a", 200), ("nora", "a", 200), ("alice", "a", 200), ("alice", "b", 403), ("bob", "a", 403)])
def test_model_info_access(api, fresh, actor, tenant, expect):
    response = api.check(getattr(fresh, actor), "GET", MODEL, path={"tenant_id": getattr(fresh, tenant)}, expect=expect, behavior="Members only")
    if expect == 200:
        body = response.json()
        api.verify((body["model_name"], body["dimension"], body["distance_metric"]) == ("BAAI/bge-small-en-v1.5", 384, "cosine"), str(body))


def test_model_info_counts_only_what_the_user_can_see(api, fresh):
    embedded_world(fresh)
    hr, eng, b = (support.count("SELECT count(*) FROM embeddings WHERE document_id = :d", d=d) for d in (fresh.doc_hr, fresh.doc_eng, fresh.doc_b))
    for actor, tenant, expected in ((fresh.mona, fresh.a, hr), (fresh.eli, fresh.a, eng), (fresh.alice, fresh.a, hr + eng), (fresh.nora, fresh.a, 0), (fresh.bob, fresh.b, b)):
        body = api.check(actor, "GET", MODEL, path={"tenant_id": tenant}, expect=200, behavior=f"Counts {expected}").json()
        api.verify(body["visible_embedding_count"] == expected, f"Saw {body['visible_embedding_count']}")


def test_retry_rules(api, fresh):
    support.ingest(fresh.job_hr)
    support.embed(fresh.doc_hr)
    api.check(fresh.mona, "POST", RETRY, path=hr_doc(fresh), expect=409, behavior="Nothing to retry after completion")
    support.set_columns(Document, fresh.doc_hr, embedding_status="failed")
    api.check(fresh.vera, "POST", RETRY, path=hr_doc(fresh), expect=403, detail=support.NO_UPLOAD, behavior="A viewer cannot retry")
    api.check(fresh.eli, "POST", RETRY, path=hr_doc(fresh), expect=403, detail=support.NO_UPLOAD, behavior="Another team cannot retry")
    api.check(fresh.bob, "POST", RETRY, path=hr_doc(fresh), expect=403, detail=support.NOT_MEMBER, behavior="Another tenant cannot retry")
    api.check(fresh.eli, "POST", RETRY, path={"tenant_id": fresh.a, "team_id": fresh.eng, "document_id": fresh.doc_hr}, expect=404, detail=support.DOC_NOT_FOUND, behavior="Wrong team in the URL")


# --- HNSW ---


def test_actual_index_definition():
    with db_session.SessionLocal() as session:
        definition = session.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_embeddings_embedding_hnsw'")).scalar_one()
    wanted = ["USING hnsw", "embedding vector_cosine_ops", "m='16'", "ef_construction='64'"]
    support.fact(all(part in definition for part in wanted), name="pg_indexes definition", expected=str(wanted), actual=definition)


def test_index_status_endpoint(api, fresh):
    body = api.check(fresh.alice, "GET", INDEX, path={"tenant_id": fresh.a}, expect=200, behavior="Index details").json()
    wanted = {"exists": True, "index_name": "ix_embeddings_embedding_hnsw", "index_type": "hnsw", "operator_class": "vector_cosine_ops", "distance_metric": "cosine", "column": "embedding", "column_type": "vector(384)", "is_valid": True, "is_ready": True, "m": 16, "ef_construction": 64}
    api.verify(all(body[key] == value for key, value in wanted.items()), str(body))
    api.verify(not any(key in body for key in ("size", "size_bytes", "row_count")), "Platform-wide size exposed to a tenant")


@pytest.mark.parametrize(("actor", "tenant", "expect"), [("mona", "a", 403), ("mike", "a", 403), ("bob", "a", 403), ("alice", "b", 403)])
def test_index_status_is_for_tenant_admins_only(api, fresh, actor, tenant, expect):
    api.check(getattr(fresh, actor), "GET", INDEX, path={"tenant_id": getattr(fresh, tenant)}, expect=expect, behavior="Denied")


def test_new_vectors_are_found_through_the_index(api, fresh):
    embedded_world(fresh)
    vector = query_vector()
    with db_session.SessionLocal() as session:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        literal = "[" + ",".join(map(str, vector)) + "]"
        plan = "\n".join(row[0] for row in session.execute(text(f"EXPLAIN SELECT id FROM embeddings ORDER BY embedding <=> '{literal}' LIMIT 5")))
        rows = session.execute(text(f"SELECT document_id FROM embeddings ORDER BY embedding <=> '{literal}' LIMIT 5")).all()
        session.rollback()
    support.fact("ix_embeddings_embedding_hnsw" in plan, name="EXPLAIN vector query", expected="Uses the HNSW index", actual=plan.splitlines()[0][:120])
    support.fact(len(rows) >= 1, name="index scan result", expected="Newly inserted vectors are returned", actual=f"{len(rows)} row(s)")


def test_set_local_does_not_leak_past_the_transaction(api, fresh):
    embedded_world(fresh)
    with db_session.SessionLocal() as session:
        default = session.execute(text("SHOW hnsw.ef_search")).scalar_one()
        session.rollback()
        search_embeddings(session, user_id=fresh.alice.id, tenant_id=fresh.a, query_vector=query_vector(), ef_search=333)
        after = session.execute(text("SHOW hnsw.ef_search")).scalar_one()
        session.rollback()
    support.fact(after == default, name="SET LOCAL scope", expected=f"Back to {default}", actual=after)


def test_settings_are_applied_inside_the_search_transaction(api, fresh):
    embedded_world(fresh)
    from app.services.embeddings.search import apply_hnsw_settings

    with db_session.SessionLocal() as session:
        apply_hnsw_settings(session, ef_search=222, iterative_scan="relaxed_order", max_scan_tuples=5000, scan_mem_multiplier=2)
        seen = [session.execute(text(f"SHOW hnsw.{name}")).scalar_one() for name in ("ef_search", "iterative_scan", "max_scan_tuples", "scan_mem_multiplier")]
        session.rollback()
    support.fact(seen == ["222", "relaxed_order", "5000", "2"], name="hnsw settings", expected="All four applied", actual=str(seen))


def test_invalid_iterative_mode_is_refused():
    from app.services.embeddings.search import apply_hnsw_settings

    with db_session.SessionLocal() as session:
        with pytest.raises(ValueError):
            apply_hnsw_settings(session, iterative_scan="off'; DROP TABLE embeddings; --")
        session.rollback()


# --- Isolation of the vector query ---


@pytest.mark.parametrize(
    ("actor", "tenant", "visible"),
    [
        ("mona", "a", {"doc_hr"}), ("vera", "a", {"doc_hr"}), ("eli", "a", {"doc_eng"}), ("alice", "a", {"doc_hr", "doc_eng"}), ("bob", "b", {"doc_b"}), ("john", "b", {"doc_b"}),
        ("nora", "a", set()), ("tess", "a", set()), ("alice", "b", set()), ("mona", "b", set()), ("bob", "a", set()), ("john", "a", set()),
    ],
)
def test_vector_search_returns_only_authorized_documents(api, fresh, actor, tenant, visible):
    embedded_world(fresh)
    hits = search(getattr(fresh, actor), getattr(fresh, tenant), limit=50)
    seen = {hit.document_id for hit in hits}
    expected = {getattr(fresh, key) for key in visible}
    support.tag_scope(actor, tenant)
    support.fact(seen == expected, name="search_embeddings", expected=f"Documents {sorted(expected)}", actual=f"Documents {sorted(seen)}")


@pytest.mark.parametrize(
    ("actor", "tenant", "filters"),
    [
        ("mona", "a", {"team_id": "eng"}), ("mona", "a", {"document_id": "doc_eng"}), ("mona", "a", {"document_id": "doc_b"}), ("mona", "a", {"team_id": "tb"}),
        ("alice", "a", {"document_id": "doc_b"}), ("alice", "a", {"team_id": "tb"}), ("bob", "b", {"document_id": "doc_hr"}), ("bob", "b", {"team_id": "hr"}),
    ],
)
def test_vector_search_filters_cannot_widen_access(api, fresh, actor, tenant, filters):
    embedded_world(fresh)
    resolved = SearchFilters(**{name: getattr(fresh, key) for name, key in filters.items()})
    hits = search(getattr(fresh, actor), getattr(fresh, tenant), filters=resolved, limit=50)
    support.fact(hits == [], name="search_embeddings with a foreign ID", expected="No results", actual=f"{len(hits)} result(s)")


def test_search_without_a_user_filter_is_impossible():
    statement = str(build_search_statement(user_id=1, tenant_id=1, query_vector=[0.0] * 384, limit=5))
    wanted = ["embeddings.tenant_id =", "documents.team_id IN", "user_team_memberships.user_id =", "document_grants.user_id =", "documents.uploaded_by_user_id =", "documents.is_active", "documents.deleted_at IS NULL", "documents.status =", "documents.embedding_status ="]
    missing = [part for part in wanted if part not in " ".join(statement.split())]
    support.fact(not missing, name="vector query SQL", expected="Authorization inside the query", actual=f"Missing: {missing}")


def test_vector_search_metadata_filters_combine_with_similarity(api, fresh):
    tagged = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file("Salary bands for the European office."), data={"department": "Finance", "tags": "eu", "custom_metadata": '{"region":"EU"}'}).json()
    embedded_world(fresh)
    support.ingest(tagged["job_id"])
    support.embed(tagged["document_id"])
    for filters in (SearchFilters(department="Finance"), SearchFilters(tags=["eu"]), SearchFilters(custom_metadata={"region": "EU"}), SearchFilters(team_id=fresh.hr, department="Finance")):
        hits = search(fresh.mona, fresh.a, filters=filters)
        support.fact({h.document_id for h in hits} == {tagged["document_id"]}, name=f"search filter {filters}", expected="Only the tagged document", actual=str({h.document_id for h in hits}))


def test_query_vector_with_wrong_dimension_is_refused():
    with pytest.raises(ValueError):
        build_search_statement(user_id=1, tenant_id=1, query_vector=[0.0] * 10, limit=5)


# --- Inactive and deleted documents ---


@pytest.mark.parametrize("change", [{"is_active": False}, {"deleted": True}], ids=["inactive", "soft_deleted"])
def test_hidden_documents_disappear_everywhere(api, fresh, change):
    from datetime import datetime, timezone

    embedded_world(fresh)
    values = {"is_active": False} if "is_active" in change else {"deleted_at": datetime.now(timezone.utc)}
    support.set_columns(Document, fresh.doc_hr, **values)
    for suffix in support.READ_SUFFIXES:
        api.check(fresh.mona, "GET", DOC + suffix, path=hr_doc(fresh), expect=404, detail=support.DOC_NOT_FOUND, behavior="Hidden", absent=["CANARY"])
    api.check(fresh.alice, "GET", DOC, path=hr_doc(fresh), expect=404, detail=support.DOC_NOT_FOUND, behavior="Hidden from admins too")
    listed = api.check(fresh.mona, "GET", TEAM_DOCS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=200, behavior="Not listed").json()
    api.verify(listed == [], f"{len(listed)} listed")
    api.check(fresh.mona, "GET", support.JOB, path={"job_id": fresh.job_hr}, expect=404, detail=support.JOB_NOT_FOUND, behavior="Job hidden")
    jobs = api.check(fresh.mona, "GET", support.JOBS, expect=200, behavior="Job list empty").json()
    api.verify(jobs == [], f"{len(jobs)} jobs listed")
    hits = search(fresh.alice, fresh.a, limit=50)
    support.fact(fresh.doc_hr not in {h.document_id for h in hits}, name="search_embeddings", expected="Hidden document excluded", actual=str({h.document_id for h in hits}))
    support.fact(bool(hits), name="other documents", expected="Still searchable", actual=f"{len(hits)} hit(s)")
    count = api.check(fresh.mona, "GET", MODEL, path={"tenant_id": fresh.a}, expect=200, behavior="Not counted").json()
    api.verify(count["visible_embedding_count"] == 0, str(count))


def test_inactive_team_vectors_disappear_from_search(api, fresh):
    embedded_world(fresh)
    api.setup(fresh.alice, "PATCH", support.TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"is_active": False})
    hits = search(fresh.alice, fresh.a, limit=50)
    support.fact(fresh.doc_hr not in {h.document_id for h in hits}, name="inactive team", expected="Vectors excluded", actual=str({h.document_id for h in hits}))


def test_removed_user_loses_search_immediately(api, fresh):
    embedded_world(fresh)
    api.setup(fresh.alice, "DELETE", support.TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.hr, "user_id": fresh.vera.id})
    hits = search(fresh.vera, fresh.a, limit=50)
    support.fact(hits == [], name="search after removal", expected="No results", actual=f"{len(hits)} result(s)")


def test_backfill_queues_only_documents_that_need_embeddings(api, fresh):
    from app.workers.backfill_embeddings import queue_pending_documents

    support.ingest(fresh.job_hr)
    support.ingest(fresh.job_eng)
    support.embed(fresh.doc_eng)
    support.ingest(fresh.job_b)
    support.set_columns(Document, fresh.doc_b, embedding_status="failed")
    support.EMBEDDING_TASKS.clear()
    with db_session.SessionLocal() as session:
        count = queue_pending_documents(session)
    support.fact(count == 1 and support.EMBEDDING_TASKS == [fresh.doc_hr], name="backfill", expected="Only the pending HR document", actual=str(support.EMBEDDING_TASKS))
    support.EMBEDDING_TASKS.clear()
    with db_session.SessionLocal() as session:
        queue_pending_documents(session, include_failed=True)
    support.fact(set(support.EMBEDDING_TASKS) == {fresh.doc_hr, fresh.doc_b}, name="backfill --include-failed", expected="Pending and failed documents", actual=str(support.EMBEDDING_TASKS))
    support.embed(fresh.doc_hr)
    support.fact(counts(fresh.doc_hr)[0] == counts(fresh.doc_hr)[1], name="backfilled document", expected="Embedded by the worker task", actual=str(counts(fresh.doc_hr)))


def test_chunk_and_embedding_trace_to_their_parents(api, fresh):
    embedded_world(fresh)
    wrong = support.count(
        "SELECT count(*) FROM embeddings e JOIN document_chunks c ON c.id = e.chunk_id JOIN documents d ON d.id = c.document_id "
        "WHERE e.document_id <> c.document_id OR e.tenant_id <> d.tenant_id OR e.team_id <> d.team_id OR c.tenant_id <> d.tenant_id OR c.team_id <> d.team_id"
    )
    support.fact(wrong == 0, name="traceability", expected="0 mismatches", actual=str(wrong))
