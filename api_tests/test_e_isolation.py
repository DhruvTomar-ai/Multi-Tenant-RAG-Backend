"""Tenant and team isolation, using one shared read-only world with processed documents."""
import pytest

from api_tests import support
from api_tests.support import (
    CANARIES, DENIED, DOC, DOC_NOT_FOUND, JOB, JOB_NOT_FOUND, JOBS, NO_READ, NO_TEAM_ACCESS, NO_UPLOAD, NOT_MEMBER, READ_SUFFIXES, TEAM,
    TEAM_DOCS, TEAM_MEMBERS, TEAM_NOT_FOUND, TEAMS, TENANT, TENANT_DOCS, TENANT_HIDDEN, TENANT_MEMBERS, UPLOAD, text_file,
)

ALL_CANARIES = list(CANARIES.values())

# (group, user, tenant in URL, team in URL, document, expected status, expected detail)
READ_CASES = [
    # Tenant A users against Tenant B.
    ("a_to_b", "alice", "b", "tb", "doc_b", 403, NOT_MEMBER),
    ("a_to_b", "mona", "b", "tb", "doc_b", 403, NOT_MEMBER),
    ("a_to_b", "eli", "b", "tb", "doc_b", 403, NOT_MEMBER),
    # Tenant B users against Tenant A.
    ("b_to_a", "bob", "a", "hr", "doc_hr", 403, NOT_MEMBER),
    ("b_to_a", "bob", "a", "eng", "doc_eng", 403, NOT_MEMBER),
    ("b_to_a", "john", "a", "hr", "doc_hr", 403, NOT_MEMBER),
    # Valid IDs in a wrong tenant and team combination.
    ("wrong_tenant_combo", "bob", "b", "tb", "doc_hr", 404, DOC_NOT_FOUND),
    ("wrong_tenant_combo", "bob", "b", "hr", "doc_hr", 404, TEAM_NOT_FOUND),
    ("wrong_tenant_combo", "bob", "a", "tb", "doc_b", 403, NOT_MEMBER),
    ("wrong_tenant_combo", "alice", "a", "hr", "doc_b", 404, DOC_NOT_FOUND),
    ("wrong_tenant_combo", "alice", "a", "tb", "doc_b", 404, TEAM_NOT_FOUND),
    ("wrong_tenant_combo", "alice", "b", "hr", "doc_hr", 403, NOT_MEMBER),
    # Team members against another team.
    ("cross_team", "mona", "a", "eng", "doc_eng", 403, NO_READ),
    ("cross_team", "eli", "a", "hr", "doc_hr", 403, NO_READ),
    ("cross_team", "vera", "a", "eng", "doc_eng", 403, NO_READ),
    ("cross_team", "mike", "a", "eng", "doc_eng", 403, NO_READ),
    ("cross_team", "adam", "a", "eng", "doc_eng", 403, NO_READ),
    ("no_team", "nora", "a", "hr", "doc_hr", 403, NO_READ),
    ("no_team", "tess", "a", "hr", "doc_hr", 403, NO_READ),
    # Valid document ID with the wrong team in the URL.
    ("wrong_team_url", "mona", "a", "hr", "doc_eng", 404, DOC_NOT_FOUND),
    ("wrong_team_url", "eli", "a", "eng", "doc_hr", 404, DOC_NOT_FOUND),
    ("wrong_team_url", "alice", "a", "eng", "doc_hr", 404, DOC_NOT_FOUND),
    ("wrong_team_url", "alice", "a", "hr", "doc_eng", 404, DOC_NOT_FOUND),
    ("wrong_team_url", "mona", "a", "eng", "doc_hr", 403, NO_READ),
    # Allowed access.
    ("allowed", "mona", "a", "hr", "doc_hr", 200, None),
    ("allowed", "vera", "a", "hr", "doc_hr", 200, None),
    ("allowed", "mike", "a", "hr", "doc_hr", 200, None),
    ("allowed", "adam", "a", "hr", "doc_hr", 200, None),
    ("allowed", "eli", "a", "eng", "doc_eng", 200, None),
    ("allowed", "alice", "a", "hr", "doc_hr", 200, None),
    ("allowed", "alice", "a", "eng", "doc_eng", 200, None),
    ("allowed", "bob", "b", "tb", "doc_b", 200, None),
    ("allowed", "john", "b", "tb", "doc_b", 200, None),
]

PARAMS = [(*case, suffix) for case in READ_CASES for suffix in READ_SUFFIXES]
IDS = [f"{c[0]}-{c[1]}-{c[2]}-{c[3]}-{c[4]}-{c[7].strip('/') or 'details'}" for c in PARAMS]


@pytest.mark.parametrize(("group", "actor", "tenant", "team", "doc", "expect", "detail", "suffix"), PARAMS, ids=IDS)
def test_document_read_matrix(api, world, group, actor, tenant, team, doc, expect, detail, suffix):
    support.tag_scope(actor, tenant, team, doc)
    path = {"tenant_id": getattr(world, tenant), "team_id": getattr(world, team), "document_id": getattr(world, doc)}
    own = CANARIES[doc]
    allowed = expect == 200
    contains = [own] if allowed and suffix in ("/download", "/text", "/chunks") else []
    absent = [c for c in ALL_CANARIES if not allowed or c != own]
    api.check(
        getattr(world, actor), "GET", DOC + suffix, path=path, expect=expect, detail=detail, contains=contains, absent=absent,
        behavior=f"Allowed: returns only this document's data" if allowed else f"{group}: denied, no document data returned",
    )


# --- Document lists ---

LIST_CASES = [
    ("alice", "b", 403, NOT_MEMBER, []), ("mona", "b", 403, NOT_MEMBER, []), ("eli", "b", 403, NOT_MEMBER, []),
    ("bob", "a", 403, NOT_MEMBER, []), ("john", "a", 403, NOT_MEMBER, []),
    ("alice", "a", 200, None, ["doc_hr", "doc_eng"]), ("mona", "a", 200, None, ["doc_hr"]), ("eli", "a", 200, None, ["doc_eng"]),
    ("vera", "a", 200, None, ["doc_hr"]), ("nora", "a", 200, None, []), ("tess", "a", 200, None, []), ("bob", "b", 200, None, ["doc_b"]),
]


@pytest.mark.parametrize(("actor", "tenant", "expect", "detail", "docs"), LIST_CASES)
def test_tenant_document_list(api, world, actor, tenant, expect, detail, docs):
    support.tag_scope(actor, tenant)
    response = api.check(getattr(world, actor), "GET", TENANT_DOCS, path={"tenant_id": getattr(world, tenant)}, expect=expect, detail=detail, behavior=f"Sees exactly {docs}" if expect == 200 else "Denied")
    if expect == 200:
        seen = {document["id"] for document in response.json()}
        api.verify(seen == {getattr(world, doc) for doc in docs}, f"Saw document IDs {sorted(seen)}")


TEAM_LIST_CASES = [
    ("mona", "a", "hr", 200, None), ("vera", "a", "hr", 200, None), ("alice", "a", "hr", 200, None), ("alice", "a", "eng", 200, None), ("bob", "b", "tb", 200, None),
    ("mona", "a", "eng", 403, NO_READ), ("eli", "a", "hr", 403, NO_READ), ("nora", "a", "hr", 403, NO_READ),
    ("alice", "b", "tb", 403, NOT_MEMBER), ("bob", "a", "hr", 403, NOT_MEMBER), ("alice", "a", "tb", 404, TEAM_NOT_FOUND), ("bob", "b", "hr", 404, TEAM_NOT_FOUND),
]


@pytest.mark.parametrize(("actor", "tenant", "team", "expect", "detail"), TEAM_LIST_CASES)
def test_team_document_list(api, world, actor, tenant, team, expect, detail):
    support.tag_scope(actor, tenant, team)
    team_id = getattr(world, team)
    response = api.check(getattr(world, actor), "GET", TEAM_DOCS, path={"tenant_id": getattr(world, tenant), "team_id": team_id}, expect=expect, detail=detail, behavior="Only this team's documents" if expect == 200 else "Denied")
    if expect == 200:
        api.verify(all(d["team_id"] == team_id for d in response.json()), "A document from another team was listed")


# --- Jobs ---

JOB_CASES = [
    ("mona", "job_hr", 200), ("vera", "job_hr", 200), ("alice", "job_hr", 200), ("alice", "job_eng", 200), ("eli", "job_eng", 200), ("bob", "job_b", 200),
    ("mona", "job_eng", 404), ("eli", "job_hr", 404), ("nora", "job_hr", 404), ("tess", "job_hr", 404), ("alice", "job_b", 404), ("mona", "job_b", 404), ("bob", "job_hr", 404), ("bob", "job_eng", 404), ("john", "job_hr", 404),
]


@pytest.mark.parametrize(("actor", "job", "expect"), JOB_CASES)
def test_job_details(api, world, actor, job, expect):
    support.tag_scope(actor, doc=job.replace("job_", "doc_"))
    job_id = getattr(world, job)
    response = api.check(getattr(world, actor), "GET", JOB, path={"job_id": job_id}, expect=expect, detail=JOB_NOT_FOUND if expect == 404 else None, behavior="Job returned" if expect == 200 else "Looks like a job that does not exist")
    if expect == 200:
        api.verify(response.json()["status"] == "completed", f"Status {response.json()['status']}")


def test_forbidden_and_missing_jobs_look_identical(api, world):
    support.tag_scope("alice", doc="doc_b")
    missing = api.send(world.alice, "GET", JOB, path={"job_id": 999999999})
    response = api.check(world.alice, "GET", JOB, path={"job_id": world.job_b}, expect=404, behavior="Same response as a missing job")
    api.verify(response.json() == missing.json(), "The responses differ")


JOB_LIST_CASES = [
    ("alice", {}, ["job_hr", "job_eng"]), ("mona", {}, ["job_hr"]), ("eli", {}, ["job_eng"]), ("nora", {}, []), ("bob", {}, ["job_b"]),
    ("alice", {"tenant_id": "b"}, []), ("alice", {"team_id": "tb"}, []), ("alice", {"document_id": "doc_b"}, []),
    ("mona", {"team_id": "eng"}, []), ("mona", {"document_id": "doc_eng"}, []), ("mona", {"tenant_id": "b"}, []),
    ("eli", {"team_id": "hr"}, []), ("eli", {"document_id": "doc_hr"}, []),
    ("bob", {"tenant_id": "a"}, []), ("bob", {"team_id": "hr"}, []), ("bob", {"document_id": "doc_hr"}, []),
    ("alice", {"team_id": "hr"}, ["job_hr"]), ("alice", {"tenant_id": "a"}, ["job_hr", "job_eng"]), ("mona", {"document_id": "doc_hr"}, ["job_hr"]),
    ("alice", {"tenant_id": "a", "team_id": "tb"}, []),
]


@pytest.mark.parametrize(("actor", "filters", "jobs"), JOB_LIST_CASES)
def test_job_list_filters_only_narrow(api, world, actor, filters, jobs):
    support.tag_scope(actor, filters.get("tenant_id"), filters.get("team_id"), filters.get("document_id"))
    params = {name: getattr(world, key) for name, key in filters.items()}
    response = api.check(getattr(world, actor), "GET", JOBS, params=params, expect=200, behavior=f"Sees exactly {jobs}")
    seen = {job["id"] for job in response.json()}
    api.verify(seen == {getattr(world, job) for job in jobs}, f"Saw job IDs {sorted(seen)}")


# --- Team and tenant metadata across tenants ---

CROSS_TENANT_META = [
    ("alice", TENANT, {"tenant_id": "b"}, 404, TENANT_HIDDEN), ("alice", TEAMS, {"tenant_id": "b"}, 403, NOT_MEMBER), ("alice", TEAM, {"tenant_id": "b", "team_id": "tb"}, 403, NOT_MEMBER),
    ("alice", TEAM_MEMBERS, {"tenant_id": "b", "team_id": "tb"}, 403, NOT_MEMBER), ("alice", TENANT_MEMBERS, {"tenant_id": "b"}, 403, NOT_MEMBER),
    ("alice", TEAM, {"tenant_id": "a", "team_id": "tb"}, 404, TEAM_NOT_FOUND), ("alice", TEAM_MEMBERS, {"tenant_id": "a", "team_id": "tb"}, 404, TEAM_NOT_FOUND),
    ("bob", TENANT, {"tenant_id": "a"}, 404, TENANT_HIDDEN), ("bob", TEAMS, {"tenant_id": "a"}, 403, NOT_MEMBER), ("bob", TEAM, {"tenant_id": "a", "team_id": "hr"}, 403, NOT_MEMBER),
    ("bob", TEAM_MEMBERS, {"tenant_id": "a", "team_id": "hr"}, 403, NOT_MEMBER), ("bob", TENANT_MEMBERS, {"tenant_id": "a"}, 403, NOT_MEMBER),
    ("bob", TEAM, {"tenant_id": "b", "team_id": "hr"}, 404, TEAM_NOT_FOUND), ("bob", TEAM_MEMBERS, {"tenant_id": "b", "team_id": "hr"}, 404, TEAM_NOT_FOUND),
    ("mona", TEAMS, {"tenant_id": "b"}, 403, NOT_MEMBER), ("mona", TEAM_MEMBERS, {"tenant_id": "b", "team_id": "tb"}, 403, NOT_MEMBER),
    ("mona", TEAM, {"tenant_id": "a", "team_id": "eng"}, 403, NO_TEAM_ACCESS), ("mona", TEAM_MEMBERS, {"tenant_id": "a", "team_id": "eng"}, 403, NO_TEAM_ACCESS),
    ("eli", TEAM, {"tenant_id": "a", "team_id": "hr"}, 403, NO_TEAM_ACCESS), ("eli", TEAM_MEMBERS, {"tenant_id": "a", "team_id": "hr"}, 403, NO_TEAM_ACCESS),
    ("mona", TENANT_MEMBERS, {"tenant_id": "a"}, 403, DENIED), ("eli", TENANT_MEMBERS, {"tenant_id": "a"}, 403, DENIED),
]


@pytest.mark.parametrize(("actor", "template", "keys", "expect", "detail"), CROSS_TENANT_META, ids=[f"{c[0]}-{c[1]}-{'-'.join(c[2].values())}" for c in CROSS_TENANT_META])
def test_metadata_isolation(api, world, actor, template, keys, expect, detail):
    support.tag_scope(actor, keys.get("tenant_id"), keys.get("team_id"))
    path = {name: getattr(world, key) for name, key in keys.items()}
    api.check(getattr(world, actor), "GET", template, path=path, expect=expect, detail=detail, behavior="Denied or hidden", absent=["company-a" if actor == "bob" else "company-b"])


# --- Uploads that must be refused ---

UPLOAD_CASES = [
    ("alice", "b", "tb", 403, NOT_MEMBER), ("mona", "b", "tb", 403, NOT_MEMBER), ("eli", "b", "tb", 403, NOT_MEMBER),
    ("bob", "a", "hr", 403, NOT_MEMBER), ("bob", "a", "eng", 403, NOT_MEMBER), ("john", "a", "hr", 403, NOT_MEMBER),
    ("alice", "a", "tb", 404, TEAM_NOT_FOUND), ("bob", "b", "hr", 404, TEAM_NOT_FOUND),
    ("mona", "a", "eng", 403, NO_UPLOAD), ("eli", "a", "hr", 403, NO_UPLOAD), ("vera", "a", "hr", 403, NO_UPLOAD), ("mike", "a", "eng", 403, NO_UPLOAD), ("adam", "a", "eng", 403, NO_UPLOAD),
    ("nora", "a", "hr", 403, NO_UPLOAD), ("tess", "a", "hr", 403, NO_UPLOAD),
]


@pytest.mark.parametrize(("actor", "tenant", "team", "expect", "detail"), UPLOAD_CASES)
def test_upload_is_refused_and_stores_nothing(api, world, actor, tenant, team, expect, detail):
    support.tag_scope(actor, tenant, team)
    before = support.count("SELECT count(*) FROM documents")
    api.check(getattr(world, actor), "POST", UPLOAD, path={"tenant_id": getattr(world, tenant), "team_id": getattr(world, team)}, files=text_file(), expect=expect, detail=detail, behavior="Refused, nothing stored")
    after = support.count("SELECT count(*) FROM documents")
    api.verify(before == after, f"Document count went from {before} to {after}")
