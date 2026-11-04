"""Explicit document grants (scenarios 7 and 8) and uploader read access, on real PostgreSQL."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from api_tests import support
from api_tests.support import (
    DENIED, DOC, DOC_NOT_FOUND, GRANT, GRANTS, JOB, JOB_NOT_FOUND, JOBS, NO_READ, NO_UPLOAD, NOT_MEMBER, READ_SUFFIXES, SEARCH, TEAM, TEAM_DOCS, TEAM_MEMBER, TEAM_NOT_FOUND, TENANT_DOCS, UPLOAD, new_user, text_file,
)
from app.db import session as db_session
from app.db.models.document import Document
from app.db.models.document_grant import DocumentGrant
from app.db.models.tenant import Tenant
from app.db.models.user_tenant_membership import UserTenantMembership

RETRY = DOC + "/embeddings/retry"


def hr_doc(w):
    return {"tenant_id": w.a, "team_id": w.hr, "document_id": w.doc_hr}


def eng_doc(w):
    return {"tenant_id": w.a, "team_id": w.eng, "document_id": w.doc_eng}


def prepare(w):
    for job, document_id in ((w.job_hr, w.doc_hr), (w.job_eng, w.doc_eng), (w.job_b, w.doc_b)):
        support.ingest(job)
        support.embed(document_id)


def grant(api, w, user, path=None, by=None):
    return api.setup(by or w.alice, "POST", GRANTS, path=path or hr_doc(w), json={"user_id": user.id}).json()


def found(api, actor, tenant_id, **body):
    response = api.send(actor, "POST", SEARCH, path={"tenant_id": tenant_id}, json={"query": "confidential", "top_k": 50, **body})
    return {result["document_id"] for result in response.json()["results"]}


@pytest.fixture
def indexed(fresh):
    prepare(fresh)
    return fresh


# --- Scenario 7: a grant gives read access ---


def test_scenario_7_grantee_retrieves_the_document_in_search(api, indexed):
    grant(api, indexed, indexed.eli)
    response = api.check(indexed.eli, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "salary", "top_k": 50}, expect=200, contains=["CANARY-A-HR"], behavior="Own team plus the granted document")
    api.verify({r["document_id"] for r in response.json()["results"]} == {indexed.doc_hr, indexed.doc_eng}, response.text[:200])


def test_scenario_7_user_with_no_team_retrieves_only_the_granted_document(api, indexed):
    grant(api, indexed, indexed.nora)
    support.fact(found(api, indexed.nora, indexed.a) == {indexed.doc_hr}, name="search as a grantee", expected="Only the granted document", actual="Compared")


def test_scenario_7_document_filter_can_target_a_granted_document(api, indexed):
    grant(api, indexed, indexed.eli)
    support.fact(found(api, indexed.eli, indexed.a, document_ids=[indexed.doc_hr]) == {indexed.doc_hr}, name="document filter", expected="The granted document", actual="Compared")


@pytest.mark.parametrize("suffix", READ_SUFFIXES, ids=lambda s: s.strip("/") or "details")
def test_scenario_7_grant_applies_to_every_read_endpoint(api, indexed, suffix):
    path = hr_doc(indexed)
    api.check(indexed.nora, "GET", DOC + suffix, path=path, expect=403, detail=NO_READ, behavior="Refused before the grant", absent=["CANARY"])
    grant(api, indexed, indexed.nora)
    api.check(indexed.nora, "GET", DOC + suffix, path=path, expect=200, behavior="Allowed with the grant")


def test_scenario_7_grant_appears_in_lists_and_jobs(api, indexed):
    grant(api, indexed, indexed.nora)
    listed = api.check(indexed.nora, "GET", TENANT_DOCS, path={"tenant_id": indexed.a}, expect=200, behavior="Granted document listed").json()
    api.verify([d["id"] for d in listed] == [indexed.doc_hr], str(listed)[:200])
    api.check(indexed.nora, "GET", JOB, path={"job_id": indexed.job_hr}, expect=200, behavior="Its job is visible")
    jobs = api.check(indexed.nora, "GET", JOBS, expect=200, behavior="Only its job").json()
    api.verify([j["id"] for j in jobs] == [indexed.job_hr], str(jobs)[:200])


# --- Scenario 8: no grant, no access ---


def test_scenario_8_without_a_grant_nothing_is_returned(api, indexed):
    support.fact(found(api, indexed.nora, indexed.a) == set(), name="search without a grant", expected="No results", actual="Compared")
    response = api.check(indexed.nora, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "confidential salary data", "document_ids": [indexed.doc_hr]}, expect=200, absent=["CANARY"], behavior="Naming the document does not help")
    api.verify(response.json()["results"] == [], response.text[:200])


def test_scenario_8_a_grant_on_one_document_gives_nothing_on_another(api, indexed):
    other = api.setup(indexed.mona, "POST", UPLOAD, path={"tenant_id": indexed.a, "team_id": indexed.hr}, files=text_file("SECOND-HR-DOC private notes.", "second.txt")).json()
    support.ingest(other["job_id"])
    support.embed(other["document_id"])
    grant(api, indexed, indexed.nora)
    response = api.check(indexed.nora, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "private notes", "top_k": 50}, expect=200, absent=["SECOND-HR-DOC"], behavior="Same team, different document")
    api.verify({r["document_id"] for r in response.json()["results"]} == {indexed.doc_hr}, response.text[:200])
    api.check(indexed.nora, "GET", DOC, path={**hr_doc(indexed), "document_id": other["document_id"]}, expect=403, detail=NO_READ, behavior="Direct read refused")


def test_scenario_8_revoking_cuts_access_immediately(api, indexed):
    grant(api, indexed, indexed.nora)
    api.check(indexed.nora, "GET", DOC, path=hr_doc(indexed), expect=200, behavior="Before revoking")
    api.check(indexed.alice, "DELETE", GRANT, path={**hr_doc(indexed), "user_id": indexed.nora.id}, expect=204, behavior="Revoked")
    api.check(indexed.nora, "GET", DOC, path=hr_doc(indexed), expect=403, detail=NO_READ, behavior="Same token, access gone")
    api.check(indexed.nora, "GET", JOB, path={"job_id": indexed.job_hr}, expect=404, detail=JOB_NOT_FOUND, behavior="Job gone")
    support.fact(found(api, indexed.nora, indexed.a) == set(), name="search after revoking", expected="No results", actual="Compared")


# --- A grant is read-only and scoped ---


def test_grant_gives_no_other_rights(api, indexed):
    grant(api, indexed, indexed.nora)
    path = {"tenant_id": indexed.a, "team_id": indexed.hr}
    api.check(indexed.nora, "POST", UPLOAD, path=path, files=text_file(), expect=403, detail=NO_UPLOAD, behavior="Cannot upload to the team")
    api.check(indexed.nora, "GET", TEAM_DOCS, path=path, expect=403, detail=NO_READ, behavior="Cannot list the team")
    api.check(indexed.nora, "GET", TEAM, path=path, expect=403, behavior="Cannot read team details")
    api.check(indexed.nora, "GET", support.TEAM_MEMBERS, path=path, expect=403, behavior="Cannot read team members")
    support.set_columns(Document, indexed.doc_hr, embedding_status="failed")
    api.check(indexed.nora, "POST", RETRY, path=hr_doc(indexed), expect=403, detail=NO_UPLOAD, behavior="Cannot retry embeddings")
    api.check(indexed.nora, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.tina.id}, expect=403, behavior="Cannot pass the grant on")


def test_grant_does_not_work_through_another_teams_path(api, indexed):
    grant(api, indexed, indexed.nora)
    api.check(indexed.nora, "GET", DOC, path={"tenant_id": indexed.a, "team_id": indexed.eng, "document_id": indexed.doc_hr}, expect=403, detail=NO_READ, behavior="The path must name the document's own team")
    api.check(indexed.nora, "GET", DOC, path={"tenant_id": indexed.b, "team_id": indexed.tb, "document_id": indexed.doc_hr}, expect=403, detail=NOT_MEMBER, behavior="Another tenant's path")


def test_grant_stops_at_inactive_membership_team_tenant_and_document(api, indexed):
    grant(api, indexed, indexed.nora)
    with db_session.SessionLocal() as session:
        session.query(UserTenantMembership).filter_by(user_id=indexed.nora.id, tenant_id=indexed.a).one().is_active = False
        session.commit()
    api.check(indexed.nora, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "x"}, expect=403, detail=NOT_MEMBER, behavior="Inactive membership")
    with db_session.SessionLocal() as session:
        session.query(UserTenantMembership).filter_by(user_id=indexed.nora.id, tenant_id=indexed.a).one().is_active = True
        session.commit()

    api.setup(indexed.alice, "PATCH", TEAM, path={"tenant_id": indexed.a, "team_id": indexed.hr}, json={"is_active": False})
    api.check(indexed.nora, "GET", DOC, path=hr_doc(indexed), expect=403, detail=NO_READ, behavior="Inactive team")
    support.fact(found(api, indexed.nora, indexed.a) == set(), name="search with an inactive team", expected="No results", actual="Compared")
    api.setup(indexed.alice, "PATCH", TEAM, path={"tenant_id": indexed.a, "team_id": indexed.hr}, json={"is_active": True})

    support.set_columns(Document, indexed.doc_hr, deleted_at=datetime.now(timezone.utc))
    support.fact(found(api, indexed.nora, indexed.a) == set(), name="search on a deleted document", expected="No results", actual="Compared")
    support.set_columns(Document, indexed.doc_hr, deleted_at=None)

    support.set_columns(Tenant, indexed.a, is_active=False)
    api.check(indexed.nora, "GET", DOC, path=hr_doc(indexed), expect=403, detail=NOT_MEMBER, behavior="Inactive tenant")


# --- Who may manage grants ---


@pytest.mark.parametrize("actor", ["alice", "mike", "adam"])
def test_team_managers_can_manage_grants(api, indexed, actor):
    user = getattr(indexed, actor)
    created = api.check(user, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.nora.id}, expect=201, behavior="Granted").json()
    api.verify((created["user_id"], created["document_id"], created["granted_by_user_id"]) == (indexed.nora.id, indexed.doc_hr, user.id), str(created))
    listed = api.check(user, "GET", GRANTS, path=hr_doc(indexed), expect=200, behavior="Listed").json()
    api.verify([g["user_id"] for g in listed] == [indexed.nora.id], str(listed))
    api.check(user, "DELETE", GRANT, path={**hr_doc(indexed), "user_id": indexed.nora.id}, expect=204, behavior="Revoked")


@pytest.mark.parametrize("actor", ["mona", "vera", "eli", "nora", "tess", "tina"])
def test_others_cannot_manage_grants(api, indexed, actor):
    user = getattr(indexed, actor)
    grant(api, indexed, indexed.tina if actor != "tina" else indexed.nora)
    api.check(user, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": user.id}, expect=403, detail="Missing permission: team.manage", behavior="Cannot grant, including to themselves")
    api.check(user, "GET", GRANTS, path=hr_doc(indexed), expect=403, detail="Missing permission: team.manage", behavior="Cannot list")
    target = indexed.tina if actor != "tina" else indexed.nora
    api.check(user, "DELETE", GRANT, path={**hr_doc(indexed), "user_id": target.id}, expect=403, detail="Missing permission: team.manage", behavior="Cannot revoke")


def test_uploader_cannot_grant_without_team_manage(api, indexed):
    api.check(indexed.mona, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.nora.id}, expect=403, detail="Missing permission: team.manage", behavior="Owning a document does not allow sharing it")


def test_manager_of_another_team_cannot_grant(api, indexed):
    api.check(indexed.mike, "POST", GRANTS, path=eng_doc(indexed), json={"user_id": indexed.nora.id}, expect=403, detail="Missing permission: team.manage", behavior="Only on their own team")


@pytest.mark.parametrize(
    ("actor", "tenant", "team", "doc", "expect", "detail"),
    [
        ("bob", "a", "hr", "doc_hr", 403, NOT_MEMBER),
        ("alice", "b", "tb", "doc_b", 403, NOT_MEMBER),
        ("bob", "b", "tb", "doc_hr", 404, DOC_NOT_FOUND),
        ("alice", "a", "tb", "doc_b", 404, TEAM_NOT_FOUND),
        ("alice", "a", "hr", "doc_b", 404, DOC_NOT_FOUND),
        ("alice", "a", "eng", "doc_hr", 404, DOC_NOT_FOUND),
    ],
)
def test_cross_tenant_and_mismatched_grant_requests_are_refused(api, indexed, actor, tenant, team, doc, expect, detail):
    support.tag_scope(actor, tenant, team, doc)
    before = support.count("SELECT count(*) FROM document_grants")
    api.check(getattr(indexed, actor), "POST", GRANTS, path={"tenant_id": getattr(indexed, tenant), "team_id": getattr(indexed, team), "document_id": getattr(indexed, doc)}, json={"user_id": indexed.nora.id}, expect=expect, detail=detail, behavior="Refused")
    api.verify(support.count("SELECT count(*) FROM document_grants") == before, "A grant was stored")


def test_cannot_grant_to_someone_outside_the_tenant(api, indexed):
    api.check(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.bob.id}, expect=400, detail=NOT_MEMBER, behavior="Bob is in another tenant")
    outsider = new_user(api, "Outsider")
    api.check(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": outsider.id}, expect=400, detail=NOT_MEMBER, behavior="Not in the tenant")
    unknown = api.check(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": 999999999}, expect=400, detail=NOT_MEMBER, behavior="Same answer for an unknown user")
    api.verify(unknown.status_code == 400, unknown.text)


def test_cannot_grant_on_a_hidden_document(api, indexed):
    support.set_columns(Document, indexed.doc_hr, is_active=False)
    api.check(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.nora.id}, expect=404, detail=DOC_NOT_FOUND, behavior="Hidden")


def test_granting_twice_keeps_one_grant(api, indexed):
    first = grant(api, indexed, indexed.nora)
    second = api.check(indexed.mike, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.nora.id}, expect=201, behavior="Same grant returned").json()
    api.verify(first["id"] == second["id"] and second["granted_by_user_id"] == indexed.alice.id, str(second))
    support.fact(support.count("SELECT count(*) FROM document_grants") == 1, name="grant rows", expected="1", actual="Counted")


@pytest.mark.parametrize("body", [{}, {"user_id": "abc"}, {"user_id": 0}, {"user_id": 99999999999999999999}, {"user_id": 1, "tenant_id": 2}, {"user_id": 1, "permission": "admin"}], ids=["empty", "text", "zero", "huge", "tenant_field", "permission_field"])
def test_invalid_grant_bodies_are_refused(api, indexed, body):
    api.check(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json=body, expect=422, behavior="Validation error")


def test_revoking_a_missing_grant_is_a_404(api, indexed):
    api.check(indexed.alice, "DELETE", GRANT, path={**hr_doc(indexed), "user_id": indexed.nora.id}, expect=404, detail="Grant not found", behavior="Not found")


def test_grant_list_is_paginated(api, indexed):
    grant(api, indexed, indexed.nora)
    grant(api, indexed, indexed.tina)
    one = api.check(indexed.alice, "GET", GRANTS, path=hr_doc(indexed), params={"limit": 1}, expect=200, behavior="limit").json()
    two = api.check(indexed.alice, "GET", GRANTS, path=hr_doc(indexed), params={"limit": 1, "offset": 1}, expect=200, behavior="offset").json()
    api.verify(len(one) == len(two) == 1 and one[0]["id"] != two[0]["id"], f"{one} {two}")


# --- The database enforces the same rules ---


def _rejected(**overrides):
    with db_session.SessionLocal() as session:
        session.add(DocumentGrant(**overrides))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return True
        return False


@pytest.mark.parametrize("kind", ["user_outside_tenant", "wrong_tenant_on_document", "wrong_team_on_document"])
def test_database_refuses_inconsistent_grants(api, fresh, kind):
    good = dict(document_id=fresh.doc_hr, tenant_id=fresh.a, team_id=fresh.hr, user_id=fresh.nora.id)
    changes = {
        "user_outside_tenant": {"user_id": fresh.bob.id},
        "wrong_tenant_on_document": {"tenant_id": fresh.b, "team_id": fresh.tb, "user_id": fresh.bob.id},
        "wrong_team_on_document": {"team_id": fresh.eng},
    }[kind]
    rejected = _rejected(**{**good, **changes})
    support.fact(rejected, name=f"document_grants constraint: {kind}", expected="IntegrityError", actual="Rejected" if rejected else "Accepted")


def test_refused_grant_requests_change_nothing(api, indexed):
    before = support.count("SELECT count(*) FROM document_grants")
    api.send(indexed.mona, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.nora.id})
    api.send(indexed.bob, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.bob.id})
    api.send(indexed.alice, "POST", GRANTS, path=hr_doc(indexed), json={"user_id": indexed.bob.id})
    support.fact(support.count("SELECT count(*) FROM document_grants") == before, name="grant rows", expected=str(before), actual="Counted")


# --- Uploader read access ---


def remove_mona(api, w):
    api.setup(w.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": w.a, "team_id": w.hr, "user_id": w.mona.id})


@pytest.mark.parametrize("suffix", READ_SUFFIXES, ids=lambda s: s.strip("/") or "details")
def test_uploader_keeps_read_access_after_leaving_the_team(api, indexed, suffix):
    remove_mona(api, indexed)
    api.check(indexed.mona, "GET", DOC + suffix, path=hr_doc(indexed), expect=200, behavior="Own upload stays readable")


def test_uploader_keeps_search_access_only_to_their_own_uploads(api, indexed):
    other = api.setup(indexed.mike, "POST", UPLOAD, path={"tenant_id": indexed.a, "team_id": indexed.hr}, files=text_file("COLLEAGUE-UPLOAD private notes.", "colleague.txt")).json()
    support.ingest(other["job_id"])
    support.embed(other["document_id"])
    remove_mona(api, indexed)
    response = api.check(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "private notes", "top_k": 50}, expect=200, absent=["COLLEAGUE-UPLOAD"], behavior="Own upload only")
    api.verify({r["document_id"] for r in response.json()["results"]} == {indexed.doc_hr}, response.text[:200])
    api.check(indexed.mona, "GET", DOC, path={**hr_doc(indexed), "document_id": other["document_id"]}, expect=403, detail=NO_READ, behavior="A colleague's upload is refused")


def test_former_member_loses_everything_except_reading_their_uploads(api, indexed):
    remove_mona(api, indexed)
    path = {"tenant_id": indexed.a, "team_id": indexed.hr}
    api.check(indexed.mona, "POST", UPLOAD, path=path, files=text_file(), expect=403, detail=NO_UPLOAD, behavior="Cannot upload")
    api.check(indexed.mona, "GET", TEAM_DOCS, path=path, expect=403, detail=NO_READ, behavior="Cannot list the team")
    api.check(indexed.mona, "GET", TEAM, path=path, expect=403, behavior="Cannot read team details")
    support.set_columns(Document, indexed.doc_hr, embedding_status="failed")
    api.check(indexed.mona, "POST", RETRY, path=hr_doc(indexed), expect=403, detail=NO_UPLOAD, behavior="Cannot retry embeddings")


def test_uploader_loses_access_when_their_tenant_membership_ends(api, indexed):
    remove_mona(api, indexed)
    with db_session.SessionLocal() as session:
        session.query(UserTenantMembership).filter_by(user_id=indexed.mona.id, tenant_id=indexed.a).one().is_active = False
        session.commit()
    api.check(indexed.mona, "GET", DOC, path=hr_doc(indexed), expect=403, detail=NOT_MEMBER, behavior="No active membership")
    api.check(indexed.mona, "POST", SEARCH, path={"tenant_id": indexed.a}, json={"query": "x"}, expect=403, detail=NOT_MEMBER, behavior="No active membership")
    jobs = api.check(indexed.mona, "GET", JOBS, expect=200, behavior="No jobs").json()
    api.verify(jobs == [], f"{len(jobs)} jobs")


def test_uploader_access_stops_at_an_inactive_team(api, indexed):
    remove_mona(api, indexed)
    api.setup(indexed.alice, "PATCH", TEAM, path={"tenant_id": indexed.a, "team_id": indexed.hr}, json={"is_active": False})
    api.check(indexed.mona, "GET", DOC, path=hr_doc(indexed), expect=403, detail=NO_READ, behavior="Inactive team")
    support.fact(found(api, indexed.mona, indexed.a) == set(), name="search with an inactive team", expected="No results", actual="Compared")


def test_uploader_cannot_read_a_hidden_document(api, indexed):
    support.set_columns(Document, indexed.doc_hr, deleted_at=datetime.now(timezone.utc))
    api.check(indexed.mona, "GET", DOC, path=hr_doc(indexed), expect=404, detail=DOC_NOT_FOUND, behavior="Deleted")
    support.fact(found(api, indexed.mona, indexed.a) == set(), name="search on a deleted document", expected="No results", actual="Compared")


def test_ownership_is_not_transferred_by_upload_fields(api, indexed):
    response = api.setup(indexed.mona, "POST", UPLOAD, path={"tenant_id": indexed.a, "team_id": indexed.hr}, files=text_file("Spoof attempt."), data={"uploaded_by": str(indexed.nora.id), "uploaded_by_user_id": str(indexed.nora.id)}).json()
    api.check(indexed.nora, "GET", DOC, path={**hr_doc(indexed), "document_id": response["document_id"]}, expect=403, detail=NO_READ, behavior="The real uploader comes from the token")
