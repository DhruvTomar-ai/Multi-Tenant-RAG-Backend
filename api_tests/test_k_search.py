"""POST /tenants/{tenant_id}/search against real PostgreSQL, pgvector and the real BGE model."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text

from api_tests import support
from api_tests.support import CANARIES, DOC, NOT_MEMBER, SEARCH, TEAM, TEAM_MEMBER, UPLOAD, add_to_team, new_user, text_file
from app.core.config import settings
from app.db import session as db_session
from app.db.models.document import Document
from app.db.models.tenant import Tenant
from app.db.models.user_tenant_membership import UserTenantMembership
from app.services.embeddings import model as embedding_model

ALL = list(CANARIES.values())


def ready(w):
    for job, document_id in ((w.job_hr, w.doc_hr), (w.job_eng, w.doc_eng), (w.job_b, w.doc_b)):
        support.ingest(job)
        support.embed(document_id)
    return w


def add_document(api, w, content, name, actor=None, tenant=None, team=None, **fields):
    body = api.setup(actor or w.mona, "POST", UPLOAD, path={"tenant_id": tenant or w.a, "team_id": team or w.hr}, files=text_file(content, name), data=fields).json()
    support.ingest(body["job_id"])
    support.embed(body["document_id"])
    return body["document_id"]


def search(api, actor, tenant_id, expect=200, **body):
    body.setdefault("query", "confidential data")
    body.setdefault("top_k", 50)
    return api.check(actor, "POST", SEARCH, path={"tenant_id": tenant_id}, json=body, expect=expect, behavior=f"Search with {sorted(body)}")


def documents(response):
    return {result["document_id"] for result in response.json()["results"]}


def returned_content(response):
    """Everything returned except the echoed query, which legitimately repeats what was typed."""
    return json.dumps(response.json()["results"])


@pytest.fixture
def indexed(fresh):
    return ready(fresh)


# --- Results and scores ---


def test_response_has_every_documented_field_and_no_vectors(api, indexed):
    response = search(api, indexed.mona, indexed.a, query="salary data", top_k=5)
    body = response.json()
    api.verify(set(body) == {"query", "results", "total_results", "top_k", "offset", "has_more", "score_type"}, str(sorted(body)))
    result = body["results"][0]
    api.verify(set(result) == {"chunk_id", "chunk_index", "document_id", "document_title", "team_id", "content", "similarity_score", "metadata"}, str(sorted(result)))
    api.verify(
        (result["document_id"], result["document_title"], result["team_id"]) == (indexed.doc_hr, "a-hr.txt", indexed.hr) and "CANARY-A-HR" in result["content"],
        str(result),
    )
    api.verify(result["metadata"]["file_type"] == ".txt" and result["metadata"]["access_level"] == "team", str(result["metadata"]))
    api.verify(body["score_type"] == "cosine_similarity" and body["total_results"] == len(body["results"]), str(body)[:200])
    api.verify("embedding" not in response.text and "distance" not in response.text, "A vector or distance field was returned")


def test_score_is_one_minus_cosine_distance_and_ordered(api, indexed):
    body = search(api, indexed.alice, indexed.a, query="confidential salary data").json()
    scores = [result["similarity_score"] for result in body["results"]]
    api.verify(len(scores) >= 2 and scores == sorted(scores, reverse=True), f"Scores {scores}")
    vector = embedding_model.encode_query("confidential salary data").tolist()
    literal = "[" + ",".join(map(str, vector)) + "]"
    with db_session.SessionLocal() as session:
        distance = session.execute(text(f"SELECT embedding <=> '{literal}' FROM embeddings WHERE chunk_id = :c"), {"c": body["results"][0]["chunk_id"]}).scalar_one()
    support.fact(abs((1 - distance) - scores[0]) < 1e-5, name="similarity_score", expected="1 - cosine distance", actual=f"score {scores[0]}, distance {distance}")


RELEVANCE_DOCUMENTS = {
    "leave.txt": "Employees are entitled to 24 days of paid annual leave each year. Unused vacation days may be carried over to the next year with manager approval. Sick leave is requested through the HR portal.",
    "expenses.txt": "Business travel costs are reimbursed after an expense report is submitted with receipts. Flights must be booked in economy class. Hotel costs above the limit require finance approval.",
    "security.txt": "All laptops must use full disk encryption and a password manager. Report a suspected phishing email to the security team immediately. Multi-factor authentication is mandatory.",
    "kubernetes.txt": "Deployments are rolled out to the Kubernetes cluster using Helm charts. Roll back a failed release with the Helm rollback command. Pods restart automatically when health checks fail.",
    "lunch.txt": "The office cafeteria serves vegetarian and vegan lunch options daily from noon. Coffee and snacks are free in the kitchen on every floor of the building.",
}
RELEVANCE_QUERIES = [
    ("How many vacation days do employees get?", "leave.txt"),
    ("How do I get reimbursed for a business trip?", "expenses.txt"),
    ("What should I do with a phishing email?", "security.txt"),
    ("How do I undo a bad deployment?", "kubernetes.txt"),
    ("Where can I eat at the office?", "lunch.txt"),
]


@pytest.mark.parametrize(("query", "expected"), RELEVANCE_QUERIES, ids=[q[1] for q in RELEVANCE_QUERIES])
def test_retrieval_returns_the_relevant_document_first(api, fresh, query, expected):
    for name, content in RELEVANCE_DOCUMENTS.items():
        add_document(api, fresh, content, name)
    body = search(api, fresh.mona, fresh.a, query=query, top_k=3).json()
    api.verify(body["results"][0]["document_title"] == expected, f"First: {[r['document_title'] for r in body['results']]}")


def test_top_k_and_pagination(api, fresh):
    for name, content in RELEVANCE_DOCUMENTS.items():
        add_document(api, fresh, content, name)
    first = search(api, fresh.mona, fresh.a, top_k=2).json()
    api.verify(first["total_results"] == 2 and first["has_more"] is True, str(first)[:200])
    second = search(api, fresh.mona, fresh.a, top_k=2, offset=2).json()
    api.verify({r["chunk_id"] for r in first["results"]}.isdisjoint(r["chunk_id"] for r in second["results"]), "Pages overlap")
    last = search(api, fresh.mona, fresh.a, top_k=50, offset=4).json()
    api.verify(last["has_more"] is False, str(last)[:200])


def test_default_top_k_applies(api, fresh):
    for name, content in RELEVANCE_DOCUMENTS.items():
        add_document(api, fresh, content, name)
    response = api.check(fresh.mona, "POST", SEARCH, path={"tenant_id": fresh.a}, json={"query": "office"}, expect=200, behavior="Default top_k")
    api.verify(response.json()["top_k"] == settings.search_default_top_k, str(response.json()["top_k"]))


# --- Scenarios 1 and 2: tenants ---


def test_scenario_1_tenant_a_cannot_find_tenant_b_content(api, indexed):
    support.recorder.category = "Tenant isolation"
    response = search(api, indexed.alice, indexed.a, query="CANARY-B confidential finance data")
    api.verify("CANARY-B" not in returned_content(response) and indexed.doc_b not in documents(response), response.text[:200])


@pytest.mark.parametrize(("actor", "tenant"), [("alice", "b"), ("mona", "b"), ("eli", "b"), ("bob", "a"), ("john", "a")])
def test_scenario_2_tenant_in_url_is_checked_against_membership(api, indexed, actor, tenant):
    support.recorder.category = "Tenant isolation"
    api.check(getattr(indexed, actor), "POST", SEARCH, path={"tenant_id": getattr(indexed, tenant)}, json={"query": "confidential"}, expect=403, detail=NOT_MEMBER, absent=ALL, behavior="Not a member")


@pytest.mark.parametrize("field", ["tenant_id", "tenant_ids", "user_id", "chunk_ids", "chunk_id", "role"])
def test_scenario_2_identity_fields_in_the_body_are_refused(api, indexed, field):
    support.recorder.category = "Tenant isolation"
    api.check(indexed.alice, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "x", field: indexed.b}, expect=422, absent=ALL, behavior="Unknown fields are refused")


def test_scenario_2_tenant_header_and_query_are_ignored(api, indexed):
    support.recorder.category = "Tenant isolation"
    response = api.check(
        indexed.alice, "POST", SEARCH, path={"tenant_id": indexed.a}, params={"tenant_id": indexed.b}, headers={"X-Tenant-Id": str(indexed.b)},
        json={"query": "confidential", "top_k": 50}, expect=200, absent=["CANARY-B"], behavior="Only the checked path tenant counts",
    )
    api.verify(indexed.doc_b not in documents(response), "Tenant B was returned")


def test_tenant_claim_in_token_grants_nothing(api, indexed):
    support.recorder.category = "Tenant isolation"
    forged = support.Actor("Alice (token claims tenant B)", support.bearer(support.make_token(indexed.alice.id, tenant_id=indexed.b)))
    api.check(forged, "POST", SEARCH, path={"tenant_id": indexed.b}, json={"query": "x"}, expect=403, detail=NOT_MEMBER, behavior="The claim is not trusted")


def test_same_ids_exist_only_in_own_tenant_result_space(api, indexed):
    support.recorder.category = "Tenant isolation"
    response = search(api, indexed.bob, indexed.b, team_ids=[indexed.hr, indexed.eng, indexed.tb], document_ids=[indexed.doc_hr, indexed.doc_eng, indexed.doc_b])
    api.verify(documents(response) == {indexed.doc_b}, str(documents(response)))


# --- Scenarios 3, 4, 5 and 6: teams, documents, chunks ---


@pytest.mark.parametrize(
    ("actor", "tenant", "expected"),
    [
        ("mona", "a", {"doc_hr"}), ("vera", "a", {"doc_hr"}), ("mike", "a", {"doc_hr"}), ("adam", "a", {"doc_hr"}), ("eli", "a", {"doc_eng"}),
        ("alice", "a", {"doc_hr", "doc_eng"}), ("bob", "b", {"doc_b"}), ("john", "b", {"doc_b"}), ("nora", "a", set()), ("tess", "a", set()), ("tina", "a", set()),
    ],
)
def test_scenario_3_each_user_gets_exactly_their_authorized_documents(api, indexed, actor, tenant, expected):
    support.tag_scope(actor, tenant)
    response = search(api, getattr(indexed, actor), getattr(indexed, tenant))
    api.verify(documents(response) == {getattr(indexed, key) for key in expected}, str(documents(response)))
    for key, canary in CANARIES.items():
        api.verify((canary in response.text) == (key in expected), f"{canary} presence is wrong")


def test_scenario_3_query_matching_another_team_returns_only_own_team(api, indexed):
    response = search(api, indexed.mona, indexed.a, query="CANARY-A-ENG confidential source plans")
    api.verify(documents(response) == {indexed.doc_hr} and "CANARY-A-ENG" not in returned_content(response), response.text[:200])


@pytest.mark.parametrize(
    ("actor", "tenant", "team"),
    [("mona", "a", "eng"), ("eli", "a", "hr"), ("mona", "a", "tb"), ("nora", "a", "hr"), ("bob", "b", "hr"), ("alice", "a", "tb")],
)
def test_scenario_4_unauthorized_team_id_returns_nothing(api, indexed, actor, tenant, team):
    support.tag_scope(actor, tenant, team)
    response = search(api, getattr(indexed, actor), getattr(indexed, tenant), team_ids=[getattr(indexed, team)])
    api.verify(response.json()["results"] == [] and response.json()["total_results"] == 0, response.text[:200])


def test_scenario_4_mixed_team_ids_return_only_the_authorized_part(api, indexed):
    response = search(api, indexed.mona, indexed.a, team_ids=[indexed.hr, indexed.eng, indexed.tb])
    api.verify(documents(response) == {indexed.doc_hr}, str(documents(response)))


@pytest.mark.parametrize(
    ("actor", "tenant", "doc"),
    [("mona", "a", "doc_eng"), ("mona", "a", "doc_b"), ("eli", "a", "doc_hr"), ("alice", "a", "doc_b"), ("bob", "b", "doc_hr"), ("nora", "a", "doc_hr"), ("john", "b", "doc_eng")],
)
def test_scenario_5_unauthorized_document_id_returns_nothing(api, indexed, actor, tenant, doc):
    support.tag_scope(actor, tenant, doc=doc)
    response = search(api, getattr(indexed, actor), getattr(indexed, tenant), document_ids=[getattr(indexed, doc)])
    api.verify(response.json()["results"] == [], response.text[:200])


def test_scenario_5_authorized_document_filter_works(api, indexed):
    response = search(api, indexed.alice, indexed.a, document_ids=[indexed.doc_eng])
    api.verify(documents(response) == {indexed.doc_eng}, str(documents(response)))


@pytest.mark.parametrize(
    ("actor", "tenant", "team", "doc"),
    [("mona", "a", "hr", "doc_eng"), ("eli", "a", "hr", "doc_hr"), ("bob", "a", "hr", "doc_hr"), ("bob", "b", "tb", "doc_hr"), ("alice", "a", "hr", "doc_b")],
)
def test_scenario_6_unauthorized_chunks_cannot_be_retrieved(api, indexed, actor, tenant, team, doc):
    support.tag_scope(actor, tenant, team, doc)
    canary = CANARIES[doc]
    api.check(getattr(indexed, actor), "GET", DOC + "/chunks", path={"tenant_id": getattr(indexed, tenant), "team_id": getattr(indexed, team), "document_id": getattr(indexed, doc)}, expect=(403, 404), absent=[canary], behavior="Direct chunk read refused")
    # A user outside the tenant is refused outright. Anyone else gets only what they may read.
    response = search(api, getattr(indexed, actor), getattr(indexed, tenant), expect=(200, 403), query=canary)
    if response.status_code == 200:
        api.verify(canary not in returned_content(response), f"{canary} was returned")


@pytest.mark.parametrize("kind", ["team", "document"])
def test_refusal_never_reveals_whether_something_exists(api, indexed, kind):
    field = "team_ids" if kind == "team" else "document_ids"
    real = indexed.eng if kind == "team" else indexed.doc_eng
    forbidden = search(api, indexed.mona, indexed.a, **{field: [real]})
    missing = search(api, indexed.mona, indexed.a, **{field: [999999999]})
    api.verify(forbidden.status_code == missing.status_code and forbidden.json() == missing.json(), f"{forbidden.text[:200]} vs {missing.text[:200]}")


def test_real_and_fake_tenants_look_identical(api, indexed):
    real = api.send(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.b}, json={"query": "x"})
    fake = api.send(indexed.mona, "POST", SEARCH, path={"tenant_id": 999999999}, json={"query": "x"})
    support.fact(real.status_code == fake.status_code == 403 and real.json() == fake.json(), name="tenant existence", expected="Identical 403", actual=f"{real.status_code} {real.text} / {fake.status_code} {fake.text}")


# --- Scenario 9 and 10: lifecycle ---


@pytest.mark.parametrize("change", ["soft_deleted", "inactive"])
def test_scenarios_9_and_10_hidden_documents_are_excluded(api, indexed, change):
    values = {"deleted_at": datetime.now(timezone.utc)} if change == "soft_deleted" else {"is_active": False}
    support.set_columns(Document, indexed.doc_hr, **values)
    for actor in (indexed.mona, indexed.alice):
        response = search(api, actor, indexed.a, query="CANARY-A-HR salary")
        api.verify(indexed.doc_hr not in documents(response) and "CANARY-A-HR" not in returned_content(response), response.text[:200])
    targeted = search(api, indexed.alice, indexed.a, document_ids=[indexed.doc_hr])
    api.verify(targeted.json()["results"] == [], targeted.text[:200])


def test_scenario_10_embedding_from_an_outdated_model_is_excluded(api, indexed):
    with db_session.SessionLocal() as session:
        session.execute(text("UPDATE embeddings SET model_version = 'old-revision' WHERE document_id = :d"), {"d": indexed.doc_hr})
        session.commit()
    response = search(api, indexed.mona, indexed.a)
    api.verify(response.json()["results"] == [], response.text[:200])


def test_scenario_10_partially_embedded_document_is_excluded(api, indexed):
    support.set_columns(Document, indexed.doc_hr, embedding_status="pending")
    response = search(api, indexed.mona, indexed.a)
    api.verify(response.json()["results"] == [], response.text[:200])


def test_inactive_team_is_excluded_for_everyone(api, indexed):
    api.setup(indexed.alice, "PATCH", TEAM, path={"tenant_id": indexed.a, "team_id": indexed.hr}, json={"is_active": False})
    for actor in (indexed.mona, indexed.alice):
        response = search(api, actor, indexed.a)
        api.verify(indexed.doc_hr not in documents(response), str(documents(response)))


def test_old_version_is_replaced_by_the_new_one(api, fresh):
    old = add_document(api, fresh, "OLD-VERSION: the leave allowance is ten days.", "policy.txt")
    new = add_document(api, fresh, "NEW-VERSION: the leave allowance is thirty days.", "policy.txt")
    response = search(api, fresh.mona, fresh.a, query="leave allowance")
    api.verify(new in documents(response) and old not in documents(response), str(documents(response)))
    api.verify("OLD-VERSION" not in response.text, "The outdated text was returned")
    api.verify(response.json()["results"][0]["metadata"]["version"] == 2, str(response.json()["results"][0]["metadata"]))


def test_old_version_stays_until_the_new_one_is_searchable(api, fresh):
    old = add_document(api, fresh, "OLD-VERSION: the leave allowance is ten days.", "policy.txt")
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file("NEW-VERSION: thirty days.", "policy.txt")).json()
    support.ingest(body["job_id"])
    response = search(api, fresh.mona, fresh.a, query="leave allowance")
    api.verify(old in documents(response), "No version is searchable while the new one is embedding")
    support.embed(body["document_id"])
    response = search(api, fresh.mona, fresh.a, query="leave allowance")
    api.verify(documents(response) == {body["document_id"]}, str(documents(response)))


def test_new_version_with_identical_text_does_not_erase_the_content(api, fresh):
    """The new version reuses the old version's chunks, so the old one must stay searchable."""
    words = "The leave allowance is ten days."
    old = add_document(api, fresh, words, "policy.txt")
    body = api.setup(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(words + "\n", "policy.txt")).json()
    support.ingest(body["job_id"])
    response = search(api, fresh.mona, fresh.a, query="leave allowance")
    api.verify(old in documents(response) and "ten days" in response.text, response.text[:200])


def test_deleting_the_new_version_brings_back_the_old_one(api, fresh):
    old = add_document(api, fresh, "OLD-VERSION text about leave.", "policy.txt")
    new = add_document(api, fresh, "NEW-VERSION text about leave.", "policy.txt")
    support.set_columns(Document, new, deleted_at=datetime.now(timezone.utc))
    response = search(api, fresh.mona, fresh.a, query="leave")
    api.verify(documents(response) == {old}, str(documents(response)))


# --- Scenarios 11, 12, 13 and 14 ---


def test_scenario_11_multiple_teams(api, indexed):
    add_to_team(api, indexed.alice, indexed.a, indexed.eng, indexed.mona)
    both = search(api, indexed.mona, indexed.a)
    api.verify(documents(both) == {indexed.doc_hr, indexed.doc_eng}, str(documents(both)))
    narrowed = search(api, indexed.mona, indexed.a, team_ids=[indexed.eng])
    api.verify(documents(narrowed) == {indexed.doc_eng}, str(documents(narrowed)))
    api.verify(search(api, indexed.mona, indexed.a, team_ids=[indexed.tb]).json()["results"] == [], "Tenant B team matched")


def test_scenario_12_multiple_tenants_are_kept_separate(api, indexed):
    support.recorder.category = "Tenant isolation"
    support.add_to_tenant(api, indexed.bob, indexed.b, indexed.mona)
    add_to_team(api, indexed.bob, indexed.b, indexed.tb, indexed.mona)
    in_b = search(api, indexed.mona, indexed.b)
    api.verify(documents(in_b) == {indexed.doc_b}, str(documents(in_b)))
    in_a = search(api, indexed.mona, indexed.a, team_ids=[indexed.tb], document_ids=[indexed.doc_b])
    api.verify(in_a.json()["results"] == [], in_a.text[:200])


def test_scenario_12_inactive_tenant_membership_blocks_only_that_tenant(api, indexed):
    support.recorder.category = "Tenant isolation"
    support.add_to_tenant(api, indexed.bob, indexed.b, indexed.mona)
    add_to_team(api, indexed.bob, indexed.b, indexed.tb, indexed.mona)
    with db_session.SessionLocal() as session:
        session.query(UserTenantMembership).filter_by(user_id=indexed.mona.id, tenant_id=indexed.b).one().is_active = False
        session.commit()
    api.check(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.b}, json={"query": "x"}, expect=403, detail=NOT_MEMBER, absent=ALL, behavior="No active membership")
    response = search(api, indexed.mona, indexed.a)
    api.verify(documents(response) == {indexed.doc_hr}, str(documents(response)))


def test_scenario_12_inactive_tenant_blocks_search(api, indexed):
    support.recorder.category = "Tenant isolation"
    support.set_columns(Tenant, indexed.a, is_active=False)
    for actor in (indexed.mona, indexed.alice):
        api.check(actor, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "x"}, expect=403, detail=NOT_MEMBER, absent=ALL, behavior="Inactive tenant")
    api.verify(documents(search(api, indexed.bob, indexed.b)) == {indexed.doc_b}, "Tenant B was affected")


@pytest.mark.parametrize(
    "filters",
    [{"access_level": "confidential"}, {"department": "Finance"}, {"tags": ["confidential"]}, {"custom_metadata": {"region": "EU"}}, {"file_type": "txt"}],
    ids=["access_level", "department", "tags", "custom", "file_type"],
)
def test_scenario_13_filters_cannot_reach_protected_content(api, fresh, filters):
    """The same metadata exists in another team and tenant, and the filter must not reach it."""
    meta = dict(department="Finance", access_level="confidential", tags="confidential", custom_metadata='{"region":"EU"}')
    own = add_document(api, fresh, "Own team budget notes.", "own.txt", **meta)
    other_team = add_document(api, fresh, "PROTECTED-TEAM budget notes.", "t.txt", actor=fresh.eli, team=fresh.eng, **meta)
    other_tenant = add_document(api, fresh, "PROTECTED-TENANT budget notes.", "b.txt", actor=fresh.bob, tenant=fresh.b, team=fresh.tb, **meta)
    response = search(api, fresh.mona, fresh.a, query="budget notes", filters=filters)
    api.verify(documents(response) == {own}, str(documents(response)))
    api.verify("PROTECTED" not in response.text and not {other_team, other_tenant} & documents(response), response.text[:200])


@pytest.mark.parametrize(
    "filters",
    [{"tenant_id": 1}, {"team_id": 1}, {"custom_metadata": {"tenant_id": 1}}, {"custom_metadata": {"a": {"team_id": 1}}}, {"access_level": "public"}, {"unknown": "x"}],
    ids=["tenant_id", "team_id", "reserved_custom", "nested_reserved_custom", "bad_access_level", "unknown"],
)
def test_scenario_13_invalid_filters_are_refused(api, indexed, filters):
    api.check(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "x", "filters": filters}, expect=422, absent=ALL, behavior="Validation error")


def test_scenario_13_filter_injection_is_inert(api, indexed):
    response = search(api, indexed.mona, indexed.a, filters={"department": "' OR 1=1 --"})
    api.verify(response.json()["results"] == [], response.text[:200])
    response = search(api, indexed.mona, indexed.a, query="'; DROP TABLE embeddings; --")
    api.verify(documents(response) == {indexed.doc_hr}, str(documents(response)))
    support.fact(support.count("SELECT count(*) FROM embeddings") > 0, name="embeddings table", expected="Still present with rows", actual="Rows present")


def test_metadata_filters_return_matching_documents(api, fresh):
    finance = add_document(api, fresh, "Budget notes for the year.", "a.txt", department="Finance", category="plans", source="drive", document_type="memo", tags="q1,eu", custom_metadata='{"region":"EU"}')
    add_document(api, fresh, "Budget notes for the region.", "b.txt", department="Legal", tags="q2", custom_metadata='{"region":"US"}')
    for filters in ({"department": "Finance"}, {"category": "plans"}, {"source": "drive"}, {"document_type": "memo"}, {"tags": ["q1", "EU"]}, {"custom_metadata": {"region": "EU"}}):
        response = search(api, fresh.mona, fresh.a, query="budget notes", filters=filters)
        api.verify(documents(response) == {finance}, f"{filters}: {documents(response)}")
    metadata = search(api, fresh.mona, fresh.a, query="budget", filters={"department": "Finance"}).json()["results"][0]["metadata"]
    api.verify((metadata["department"], metadata["tags"], metadata["custom_metadata"]) == ("Finance", ["q1", "eu"], {"region": "EU"}), str(metadata))


def test_scenario_14_empty_results_are_not_errors(api, indexed):
    response = search(api, indexed.nora, indexed.a)
    api.verify(response.json() == {"query": "confidential data", "results": [], "total_results": 0, "top_k": 50, "offset": 0, "has_more": False, "score_type": "cosine_similarity"}, response.text)


def test_scenario_14_tenant_with_no_documents(api, clean):
    user = new_user(api, "Solo")
    tenant = api.setup(user, "POST", support.TENANTS, json={"name": "Empty", "slug": "empty-tenant"}).json()["id"]
    response = search(api, user, tenant)
    api.verify(response.json()["results"] == [], response.text)


@pytest.mark.parametrize(
    "body",
    [{}, {"query": ""}, {"query": "   "}, {"query": "x", "top_k": 0}, {"query": "x", "top_k": 51}, {"query": "x", "offset": -1}, {"query": "x", "team_ids": ["abc"]}, {"query": "x", "team_ids": [99999999999999999999]}, {"query": "bad\u0000query"}, {"query": "x" * 2001}],
    ids=["empty", "empty_query", "blank", "top_k_0", "top_k_51", "negative_offset", "text_team", "huge_team", "nul", "long_query"],
)
def test_invalid_requests_return_422_not_500(api, indexed, body):
    api.check(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.a}, json=body, expect=422, behavior="Validation error")


# --- The query itself ---


def _statements(action):
    captured = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        captured.append(" ".join(statement.split()))

    event.listen(db_session.engine, "before_cursor_execute", listener)
    try:
        action()
    finally:
        event.remove(db_session.engine, "before_cursor_execute", listener)
    return captured


def test_authorization_is_inside_the_one_sql_query_that_reads_vectors(api, indexed):
    statements = _statements(lambda: search(api, indexed.mona, indexed.a, team_ids=[indexed.hr], filters={"file_type": "txt"}))
    readers = [s for s in statements if "FROM embeddings" in s]
    support.fact(len(readers) == 1, name="vector queries per search", expected="Exactly 1", actual=str(len(readers)))
    wanted = ["embeddings.tenant_id =", "documents.tenant_id =", "documents.team_id IN", "user_team_memberships.user_id =", "user_tenant_memberships.is_active", "tenants.is_active", "document_grants.user_id =", "documents.uploaded_by_user_id =", "documents.deleted_at IS NULL", "documents.is_active", "embeddings.model_version =", "embeddings.team_id IN", "LIMIT"]
    missing = [part for part in wanted if part not in readers[0]]
    support.fact(not missing, name="vector query", expected=str(wanted), actual=f"Missing: {missing}")


def test_query_count_does_not_grow_with_results(api, fresh):
    for name, content in RELEVANCE_DOCUMENTS.items():
        add_document(api, fresh, content, name)
    small = _statements(lambda: search(api, fresh.mona, fresh.a, top_k=1))
    large = _statements(lambda: search(api, fresh.mona, fresh.a, top_k=50))
    support.fact(len(small) == len(large), name="N+1 check", expected="Same statement count for 1 and 50 results", actual=f"{len(small)} and {len(large)}")


def test_hnsw_settings_do_not_leak_after_a_search(api, indexed):
    search(api, indexed.mona, indexed.a)
    with db_session.SessionLocal() as session:
        value = session.execute(text("SHOW hnsw.iterative_scan")).scalar_one()
    support.fact(value == "off", name="hnsw.iterative_scan afterwards", expected="Back to the server default", actual=value)


def test_model_is_reused_across_searches(api, indexed):
    before = embedding_model.load_count
    for _ in range(3):
        search(api, indexed.mona, indexed.a)
    support.fact(embedding_model.load_count == before == 1, name="model loads", expected="1 load in this process", actual=str(embedding_model.load_count))


def test_removal_from_team_cuts_search_immediately(api, indexed):
    before = search(api, indexed.vera, indexed.a)
    api.verify(documents(before) == {indexed.doc_hr}, str(documents(before)))
    api.setup(indexed.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": indexed.a, "team_id": indexed.hr, "user_id": indexed.vera.id})
    after = search(api, indexed.vera, indexed.a)
    api.verify(after.json()["results"] == [], after.text[:200])
