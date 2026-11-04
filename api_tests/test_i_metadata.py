"""Document metadata against real PostgreSQL: persistence, inheritance, filters, indexes and abuse."""
import json

import pytest
from sqlalchemy import text

from api_tests import support
from api_tests.support import DOC, TEAM_DOCS, TENANT_DOCS, UPLOAD, text_file
from app.db import session as db_session


def team(w):
    return {"tenant_id": w.a, "team_id": w.hr}


def up(api, w, actor=None, content=None, name=None, tenant=None, team_id=None, **fields):
    response = api.check(
        actor or w.mona, "POST", UPLOAD, path={"tenant_id": tenant or w.a, "team_id": team_id or w.hr}, files=text_file(content, name),
        data=fields, expect=202, behavior="Upload with metadata accepted",
    )
    return response.json()


FULL = {
    "document_type": "policy", "department": "People", "category": "benefits", "source": "sharepoint", "access_level": "restricted",
    "tags": "Handbook, 2026", "custom_metadata": json.dumps({"region": "EU", "review": {"year": 2026}}),
}


def test_metadata_endpoint_returns_every_required_field(api, fresh):
    body = up(api, fresh, **FULL)
    response = api.check(fresh.mona, "GET", DOC + "/metadata", path={**team(fresh), "document_id": body["document_id"]}, expect=200, behavior="All metadata fields").json()
    required = {"tenant_id", "team_id", "document_id", "uploaded_by", "document_type", "department", "category", "tags", "source", "file_type", "uploaded_at", "updated_at", "version", "status", "access_level", "custom_metadata"}
    api.verify(required <= set(response), f"Missing {required - set(response)}")
    api.verify(
        (response["tenant_id"], response["team_id"], response["uploaded_by"]) == (fresh.a, fresh.hr, fresh.mona.id),
        f"Wrong ownership {response}",
    )
    api.verify(
        (response["document_type"], response["department"], response["category"], response["source"], response["access_level"], response["file_type"]) == ("policy", "People", "benefits", "sharepoint", "restricted", ".txt"),
        f"Wrong values {response}",
    )
    api.verify(response["tags"] == ["handbook", "2026"], f"Tags {response['tags']}")
    api.verify(response["custom_metadata"] == {"region": "EU", "review": {"year": 2026}}, f"Custom {response['custom_metadata']}")


def test_upload_without_metadata_gets_safe_defaults(api, fresh):
    response = api.check(fresh.mona, "GET", DOC + "/metadata", path={**team(fresh), "document_id": fresh.doc_hr}, expect=200, behavior="Defaults").json()
    api.verify((response["access_level"], response["tags"], response["custom_metadata"]) == ("team", [], {}), str(response))


def test_ingestion_preserves_user_metadata_and_keeps_system_metadata_separate(api, fresh):
    body = up(api, fresh, **FULL)
    support.ingest(body["job_id"])
    document = api.check(fresh.mona, "GET", DOC, path={**team(fresh), "document_id": body["document_id"]}, expect=200, behavior="User and system metadata both present").json()
    api.verify(document["custom_metadata"] == {"region": "EU", "review": {"year": 2026}}, f"Custom {document['custom_metadata']}")
    api.verify("section_count" in document["metadata_json"], f"System {document['metadata_json']}")


def test_new_version_inherits_metadata(api, fresh):
    up(api, fresh, content="Version one.", name="policy.txt", **FULL)
    second = up(api, fresh, content="Version two.", name="policy.txt")
    response = api.check(fresh.mona, "GET", DOC + "/metadata", path={**team(fresh), "document_id": second["document_id"]}, expect=200, behavior="Version 2 inherits").json()
    api.verify(response["version"] == 2, f"Version {response['version']}")
    api.verify(response["department"] == "People" and response["tags"] == ["handbook", "2026"] and response["custom_metadata"]["region"] == "EU", str(response))


def test_new_version_can_override_one_field(api, fresh):
    up(api, fresh, content="Version one.", name="policy.txt", **FULL)
    second = up(api, fresh, content="Version two.", name="policy.txt", department="Legal")
    response = api.check(fresh.mona, "GET", DOC + "/metadata", path={**team(fresh), "document_id": second["document_id"]}, expect=200, behavior="Only the supplied field changes").json()
    api.verify(response["department"] == "Legal" and response["category"] == "benefits", str(response))


def test_form_fields_cannot_set_ownership(api, fresh):
    body = up(api, fresh, tenant_id=str(fresh.b), team_id_form=str(fresh.tb), uploaded_by=str(fresh.bob.id), uploaded_by_user_id=str(fresh.bob.id), status="completed", version="99", is_active="false")
    response = api.check(fresh.mona, "GET", DOC + "/metadata", path={**team(fresh), "document_id": body["document_id"]}, expect=200, behavior="Ownership comes from the path and token").json()
    api.verify((response["tenant_id"], response["team_id"], response["uploaded_by"], response["version"], response["status"]) == (fresh.a, fresh.hr, fresh.mona.id, 1, "pending"), str(response))


@pytest.mark.parametrize(
    "fields",
    [
        {"custom_metadata": '{"tenant_id": 999}'}, {"custom_metadata": '{"nested": {"team_id": 999}}'}, {"custom_metadata": '{"owner": "x"}'},
        {"custom_metadata": '{"permissions": ["all"]}'}, {"custom_metadata": "not json"}, {"custom_metadata": "[1]"},
        {"access_level": "public"}, {"tags": "x" * 65}, {"department": "x" * 101},
    ],
    ids=["tenant_id", "nested_team_id", "owner", "permissions", "bad_json", "array", "bad_access_level", "long_tag", "long_department"],
)
def test_invalid_or_security_metadata_is_refused_and_stores_nothing(api, fresh, fields):
    before = support.count("SELECT count(*) FROM documents")
    api.check(fresh.mona, "POST", UPLOAD, path=team(fresh), files=text_file(), data=fields, expect=422, behavior="Refused before storage")
    api.verify(support.count("SELECT count(*) FROM documents") == before, "A document was stored")


# --- Filtering ---


@pytest.fixture
def catalog(api, fresh):
    w = fresh
    w.d1 = up(api, w, name="d1.txt", document_type="policy", department="People", category="benefits", source="sharepoint", access_level="restricted", tags="handbook,eu", custom_metadata='{"region":"EU","year":2026}')["document_id"]
    w.d2 = up(api, w, name="d2.txt", document_type="contract", department="People", category="legal", source="email", tags="eu", custom_metadata='{"region":"US"}')["document_id"]
    w.d3 = up(api, w, name="d3.txt", document_type="policy", department="Legal", tags="handbook")["document_id"]
    w.d4 = up(api, w, actor=w.eli, team_id=w.eng, name="d4.txt", document_type="policy", department="People", tags="handbook,eu", custom_metadata='{"region":"EU"}')["document_id"]
    w.d5 = up(api, w, actor=w.bob, tenant=w.b, team_id=w.tb, name="d5.txt", document_type="policy", department="People", tags="handbook,eu", custom_metadata='{"region":"EU","year":2026}')["document_id"]
    return w


FILTERS = [
    ({"document_type": "policy"}, ["d1", "d3"]),
    ({"department": "People"}, ["d1", "d2"]),
    ({"category": "legal"}, ["d2"]),
    ({"source": "email"}, ["d2"]),
    ({"access_level": "restricted"}, ["d1"]),
    ({"access_level": "team"}, ["d2", "d3"]),
    ({"file_type": "txt"}, ["d1", "d2", "d3"]),
    ({"tag": ["eu"]}, ["d1", "d2"]),
    ({"tag": ["EU", "handbook"]}, ["d1"]),
    ({"custom": '{"region":"EU"}'}, ["d1"]),
    ({"custom": '{"region":"EU","year":2026}'}, ["d1"]),
    ({"document_type": "policy", "department": "Legal"}, ["d3"]),
    ({"department": "None of these"}, []),
]


@pytest.mark.parametrize(("params", "expected"), FILTERS, ids=[json.dumps(f[0])[:48] for f in FILTERS])
def test_team_list_filters(api, catalog, params, expected):
    response = api.check(catalog.mona, "GET", TEAM_DOCS, path=team(catalog), params=params, expect=200, behavior=f"Exactly {expected}").json()
    seen = {d["id"] for d in response if d["id"] != catalog.doc_hr}
    api.verify(seen == {getattr(catalog, name) for name in expected}, f"Saw {sorted(seen)}")


def test_filter_on_hr_values_never_shows_engineering_to_mona(api, catalog):
    response = api.check(catalog.mona, "GET", TENANT_DOCS, path={"tenant_id": catalog.a}, params={"department": "People", "tag": ["eu"]}, expect=200, behavior="Only Mona's team").json()
    api.verify({d["id"] for d in response} == {catalog.d1, catalog.d2}, f"Saw {[d['id'] for d in response]}")


def test_admin_tenant_filter_spans_own_teams_only(api, catalog):
    response = api.check(catalog.alice, "GET", TENANT_DOCS, path={"tenant_id": catalog.a}, params={"custom": '{"region":"EU"}'}, expect=200, behavior="A's teams, never B").json()
    api.verify({d["id"] for d in response} == {catalog.d1, catalog.d4}, f"Saw {[d['id'] for d in response]}")


def test_metadata_matching_another_tenant_exposes_nothing(api, catalog):
    response = api.check(catalog.alice, "GET", TENANT_DOCS, path={"tenant_id": catalog.a}, params={"custom": '{"year":2026}', "department": "People", "tag": ["handbook", "eu"]}, expect=200, behavior="B's identical metadata stays hidden").json()
    api.verify(catalog.d5 not in {d["id"] for d in response}, "Tenant B's document was returned")


@pytest.mark.parametrize("params", [{"custom": "not json"}, {"custom": "[1]"}, {"custom": '{"tenant_id": 1}'}, {"access_level": "public"}, {"embedding_status": "nonsense"}], ids=["bad_json", "array", "reserved_key", "bad_access_level", "bad_embedding_status"])
def test_invalid_filters_are_rejected(api, catalog, params):
    api.check(catalog.mona, "GET", TEAM_DOCS, path=team(catalog), params=params, expect=422, behavior="Validation error")


def test_filter_injection_is_inert(api, catalog):
    response = api.check(catalog.mona, "GET", TEAM_DOCS, path=team(catalog), params={"department": "' OR 1=1 --"}, expect=200, behavior="Treated as a plain value").json()
    api.verify(response == [], f"{len(response)} documents returned")


@pytest.mark.parametrize(
    ("condition", "index"),
    [
        ("tenant_id = :t AND department = 'd7'", "ix_documents_tenant_department"),
        ("tenant_id = :t AND document_type = 't7'", "ix_documents_tenant_document_type"),
        ("tenant_id = :t AND category = 'c7'", "ix_documents_tenant_category"),
        ("tags @> ARRAY['tag7']::varchar[]", "ix_documents_tags_gin"),
        ("custom_metadata @> '{\"k\": 7}'::jsonb", "ix_documents_custom_metadata_gin"),
    ],
    ids=["department", "document_type", "category", "tags_gin", "custom_gin"],
)
def test_planner_chooses_the_metadata_index_on_realistic_data(api, fresh, condition, index):
    """5,000 varied rows and fresh statistics. The planner is not forced, and the rows are rolled back."""
    with db_session.SessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO documents (tenant_id, team_id, original_filename, stored_filename, content_type, file_size, file_hash,
                    file_extension, status, version, chunk_count, created_at, updated_at, department, document_type, category, tags, custom_metadata)
                SELECT :t, :team, 'f' || g, 'f' || g, 'text/plain', 1, md5(g::text) || md5(g::text), '.txt', 'completed', 1, 0, now(), now(),
                    'd' || (g % 50), 't' || (g % 50), 'c' || (g % 50), ARRAY['tag' || (g % 50)]::varchar[], jsonb_build_object('k', g % 50)
                FROM generate_series(1, 5000) AS g
                """
            ),
            {"t": fresh.a, "team": fresh.hr},
        )
        session.execute(text("ANALYZE documents"))
        plan = "\n".join(row[0] for row in session.execute(text(f"EXPLAIN SELECT id FROM documents WHERE {condition}"), {"t": fresh.a}))
        session.rollback()
    support.fact(index in plan, name=f"EXPLAIN {condition}", expected=f"The planner chooses {index}", actual=plan.splitlines()[0][:150])
