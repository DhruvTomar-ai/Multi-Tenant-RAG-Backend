"""Authorization bypass attempts, immediate revocation, and inactive users, teams and tenants."""
import pytest

from api_tests import support
from api_tests.support import (
    DENIED, DOC, JOB, JOB_NOT_FOUND, JOBS, NO_READ, NO_TEAM_ACCESS, NO_UPLOAD, NOT_MEMBER, READ_SUFFIXES, TEAM, TEAM_DOCS, TEAM_MEMBER, TEAM_MEMBERS, TEAM_NOT_FOUND,
    TEAMS, TENANT, TENANT_DOCS, TENANT_HIDDEN, TENANTS, UPLOAD, add_to_team, make_token, Actor, bearer, text_file,
)
from app.db.models.team import Team
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.models.user_team_membership import UserTeamMembership
from app.db.models.user_tenant_membership import UserTenantMembership

# --- Bypass attempts ---


def test_header_cannot_widen_access(api, fresh):
    api.check(fresh.alice, "GET", TENANT_DOCS, path={"tenant_id": fresh.a}, headers={"X-Tenant-Id": str(fresh.b)}, expect=200, behavior="Header ignored: only A's data", absent=["b.txt"])


def test_header_cannot_grant_access(api, fresh):
    api.check(fresh.alice, "GET", TENANT_DOCS, path={"tenant_id": fresh.b}, headers={"X-Tenant-Id": str(fresh.a), "X-Team-Id": str(fresh.hr)}, expect=403, detail=NOT_MEMBER, behavior="Header ignored")


def test_query_parameters_cannot_change_document_list_scope(api, fresh):
    api.check(fresh.alice, "GET", TENANT_DOCS, path={"tenant_id": fresh.a}, params={"tenant_id": fresh.b, "team_id": fresh.tb}, expect=200, behavior="Path decides the scope", absent=["b.txt"])


def test_upload_form_fields_and_query_cannot_redirect_the_file(api, fresh):
    body = api.check(
        fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, params={"tenant_id": fresh.b, "team_id": fresh.tb},
        data={"tenant_id": str(fresh.b), "team_id": str(fresh.tb)}, files=text_file(), expect=202, behavior="Stored in HR, the team in the path",
    ).json()
    document = api.check(fresh.mona, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": body["document_id"]}, expect=200, behavior="Belongs to tenant A, HR").json()
    api.verify((document["tenant_id"], document["team_id"]) == (fresh.a, fresh.hr), str(document))
    api.check(fresh.bob, "GET", TEAM_DOCS, path={"tenant_id": fresh.b, "team_id": fresh.tb}, expect=200, behavior="Nothing arrived in tenant B", absent=[document["original_filename"]])


def test_team_cannot_move_to_another_tenant(api, fresh):
    response = api.check(fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"tenant_id": fresh.b, "id": fresh.tb}, expect=200, behavior="tenant_id and id are ignored")
    api.verify((response.json()["tenant_id"], response.json()["id"]) == (fresh.a, fresh.hr), str(response.json()))


@pytest.mark.parametrize(
    ("actor", "method", "template", "tenant", "team", "body", "expect", "detail"),
    [
        ("alice", "PATCH", TENANT, "b", None, {"name": "Hacked"}, 404, TENANT_HIDDEN),
        ("alice", "PATCH", TEAM, "b", "tb", {"name": "Hacked"}, 403, NOT_MEMBER),
        ("alice", "PATCH", TEAM, "a", "tb", {"name": "Hacked"}, 404, TEAM_NOT_FOUND),
        ("alice", "POST", TEAMS, "b", None, {"name": "Rogue"}, 403, NOT_MEMBER),
        ("bob", "PATCH", TEAM, "a", "hr", {"name": "Hacked"}, 403, NOT_MEMBER),
        ("bob", "PATCH", TEAM, "b", "hr", {"name": "Hacked"}, 404, TEAM_NOT_FOUND),
    ],
    ids=["alice_patch_b", "alice_patch_b_team", "alice_patch_b_team_via_a", "alice_create_team_in_b", "bob_patch_a_team", "bob_patch_a_team_via_b"],
)
def test_cross_tenant_writes_are_refused(api, fresh, actor, method, template, tenant, team, body, expect, detail):
    path = {"tenant_id": getattr(fresh, tenant)}
    if team:
        path["team_id"] = getattr(fresh, team)
    api.check(getattr(fresh, actor), method, template, path=path, json=body, expect=expect, detail=detail, behavior="Refused, nothing changed")
    names = support.count("SELECT count(*) FROM teams WHERE name IN ('Hacked', 'Rogue')") + support.count("SELECT count(*) FROM tenants WHERE name = 'Hacked'")
    api.verify(names == 0, "A cross-tenant write took effect")


@pytest.mark.parametrize(
    ("actor", "tenant", "team", "user", "body_tenant", "body_team", "role", "expect", "detail"),
    [
        ("alice", "b", "tb", "alice", "b", "tb", "admin", 403, NOT_MEMBER),
        ("alice", "a", "tb", "mona", "a", "tb", "member", 404, TEAM_NOT_FOUND),
        ("alice", "a", "hr", "mona", "b", "tb", "member", 400, "Tenant or team mismatch"),
        ("bob", "a", "hr", "bob", "a", "hr", "admin", 403, NOT_MEMBER),
        ("bob", "b", "tb", "mona", "b", "tb", "member", 400, NOT_MEMBER),
    ],
    ids=["alice_self_to_b", "b_team_via_a", "body_points_to_b", "bob_self_to_a", "bob_adds_outsider"],
)
def test_cross_tenant_team_assignment_is_refused(api, fresh, actor, tenant, team, user, body_tenant, body_team, role, expect, detail):
    body = {"user_id": getattr(fresh, user).id, "tenant_id": getattr(fresh, body_tenant), "team_id": getattr(fresh, body_team), "role": role}
    api.check(getattr(fresh, actor), "POST", TEAM_MEMBERS, path={"tenant_id": getattr(fresh, tenant), "team_id": getattr(fresh, team)}, json=body, expect=expect, detail=detail, behavior="Refused")


def test_cross_tenant_member_removal_is_refused(api, fresh):
    api.check(fresh.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.tb, "user_id": fresh.john.id}, expect=403, detail=DENIED, behavior="Refused")
    api.check(fresh.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.b, "team_id": fresh.tb, "user_id": fresh.john.id}, expect=403, detail=NOT_MEMBER, behavior="Refused")
    api.check(fresh.bob, "GET", TEAM_MEMBERS, path={"tenant_id": fresh.b, "team_id": fresh.tb}, expect=200, behavior="John is still a member", contains=[fresh.john.email])


def test_real_and_fake_tenants_look_identical_on_upload(api, fresh):
    real = api.send(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.b, "team_id": fresh.tb}, files=text_file())
    response = api.check(fresh.mona, "POST", UPLOAD, path={"tenant_id": 999999999, "team_id": 999999999}, files=text_file(), expect=403, behavior="Same response as a real tenant")
    api.verify(response.json() == real.json(), "The responses differ")


def test_real_and_fake_documents_look_identical_across_tenants(api, fresh):
    fake = api.send(fresh.alice, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": 999999999})
    response = api.check(fresh.alice, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_b}, expect=404, behavior="Same response as a missing document")
    api.verify(response.json() == fake.json(), "The responses differ")


def test_team_existence_is_not_revealed_inside_a_tenant(api, fresh):
    missing = api.send(fresh.mona, "GET", TEAM_DOCS, path={"tenant_id": fresh.a, "team_id": 999999999})
    api.check(
        fresh.mona, "GET", TEAM_DOCS, path={"tenant_id": fresh.a, "team_id": fresh.eng}, expect=missing.status_code, behavior="A team the user cannot see looks like a missing team",
        why="Low: 403 vs 404 lets a member confirm which team IDs exist in their own tenant.",
    )


def test_method_override_header_is_ignored(api, fresh):
    api.check(fresh.mona, "POST", DOC, path={"tenant_id": fresh.a, "team_id": fresh.eng, "document_id": fresh.doc_eng}, headers={"X-HTTP-Method-Override": "GET"}, expect=405, behavior="Override not honoured", absent=["CANARY"])


def test_token_for_another_user_id_needs_the_secret(api, fresh):
    forged = support.make_token(fresh.bob.id, secret="guessed-secret-that-is-long-enough-for-hs256-sig")
    api.check(Actor("attacker forging Bob"), "GET", TENANT_DOCS, path={"tenant_id": fresh.b}, headers=bearer(forged), expect=401, behavior="Forged identity rejected")


# --- Removing a user from a team ---


def test_removal_from_team_removes_access_immediately(api, fresh):
    support.ingest(fresh.job_hr)
    doc = {"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}
    team = {"tenant_id": fresh.a, "team_id": fresh.hr}

    # Vera did not upload this document. Uploaders keep read access: see test_l_grants.py.
    api.check(fresh.vera, "GET", DOC, path=doc, expect=200, behavior="Access before removal")
    api.check(fresh.alice, "DELETE", TEAM_MEMBER, path={**team, "user_id": fresh.vera.id}, expect=204, behavior="Removed")

    for suffix in READ_SUFFIXES:
        api.check(fresh.vera, "GET", DOC + suffix, path=doc, expect=403, detail=NO_READ, behavior="Same token, access gone", absent=["CANARY"])
    api.check(fresh.vera, "GET", TEAM_DOCS, path=team, expect=403, detail=NO_READ, behavior="List gone")
    api.check(fresh.vera, "POST", UPLOAD, path=team, files=text_file(), expect=403, detail=NO_UPLOAD, behavior="Upload refused")
    api.check(fresh.vera, "GET", JOB, path={"job_id": fresh.job_hr}, expect=404, detail=JOB_NOT_FOUND, behavior="Job gone")
    jobs = api.check(fresh.vera, "GET", JOBS, expect=200, behavior="Job list empty").json()
    api.verify(jobs == [], f"{len(jobs)} jobs still listed")
    api.check(fresh.vera, "GET", TEAM, path=team, expect=403, detail=NO_TEAM_ACCESS, behavior="Team details gone")
    api.check(fresh.vera, "GET", TEAM_MEMBERS, path=team, expect=403, detail=NO_TEAM_ACCESS, behavior="Team members gone")
    teams = api.check(fresh.vera, "GET", TEAMS, path={"tenant_id": fresh.a}, expect=200, behavior="Team no longer listed").json()
    api.verify(teams == [], f"{len(teams)} teams still listed")
    documents = api.check(fresh.vera, "GET", TENANT_DOCS, path={"tenant_id": fresh.a}, expect=200, behavior="Tenant list empty").json()
    api.verify(documents == [], f"{len(documents)} documents still listed")


def test_adding_back_restores_access(api, fresh):
    doc = {"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}
    api.setup(fresh.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.hr, "user_id": fresh.mona.id})
    add_to_team(api, fresh.alice, fresh.a, fresh.hr, fresh.mona)
    api.check(fresh.mona, "GET", DOC, path=doc, expect=200, behavior="Access restored")


def test_role_downgrade_takes_effect_immediately(api, fresh):
    add_to_team(api, fresh.alice, fresh.a, fresh.hr, fresh.mona, "viewer")
    api.check(fresh.mona, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(), expect=403, detail=NO_UPLOAD, behavior="Viewer can no longer upload")


def test_inactive_team_membership_blocks_access(api, fresh):
    with support.db_session.SessionLocal() as session:
        membership = session.query(UserTeamMembership).filter_by(user_id=fresh.vera.id, team_id=fresh.hr).one()
        membership.is_active = False
        session.commit()
    api.check(fresh.vera, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}, expect=403, detail=NO_READ, behavior="Inactive membership grants nothing")
    api.check(fresh.vera, "GET", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=403, detail=NO_TEAM_ACCESS, behavior="Inactive membership grants nothing")


def test_inactive_tenant_membership_blocks_access(api, fresh):
    with support.db_session.SessionLocal() as session:
        membership = session.query(UserTenantMembership).filter_by(user_id=fresh.mona.id, tenant_id=fresh.a).one()
        membership.is_active = False
        session.commit()
    api.check(fresh.mona, "GET", TENANT_DOCS, path={"tenant_id": fresh.a}, expect=403, detail=NOT_MEMBER, behavior="Denied")
    api.check(fresh.mona, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}, expect=403, detail=NOT_MEMBER, behavior="Denied even with an active team membership")
    api.check(fresh.mona, "GET", JOB, path={"job_id": fresh.job_hr}, expect=404, detail=JOB_NOT_FOUND, behavior="Denied")


def test_inactive_admin_membership_blocks_admin_rights(api, fresh):
    with support.db_session.SessionLocal() as session:
        membership = session.query(UserTenantMembership).filter_by(user_id=fresh.alice.id, tenant_id=fresh.a).one()
        membership.is_active = False
        session.commit()
    api.check(fresh.alice, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.eng, "document_id": fresh.doc_eng}, expect=403, detail=NOT_MEMBER, behavior="No admin rights without active membership")
    jobs = api.check(fresh.alice, "GET", JOBS, expect=200, behavior="No jobs visible").json()
    api.verify(jobs == [], f"{len(jobs)} jobs visible")


# --- Inactive users ---


def test_deactivated_user_loses_everything_on_the_same_token(api, fresh):
    support.set_columns(User, fresh.mona.id, is_active=False)
    for template, path in [(TENANTS, None), (TENANT_DOCS, {"tenant_id": fresh.a}), (DOC, {"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}), (JOB, {"job_id": fresh.job_hr}), (JOBS, None)]:
        api.check(fresh.mona, "GET", template, path=path, expect=401, detail="User not found or inactive", behavior="A valid token for an inactive user fails")


# --- Inactive teams ---


def test_inactive_team_blocks_documents_and_jobs_for_all_roles(api, fresh):
    api.setup(fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"is_active": False})
    doc = {"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}
    for actor in (fresh.mona, fresh.alice):
        api.check(actor, "GET", DOC, path=doc, expect=403, detail=NO_READ, behavior="Documents blocked")
        api.check(actor, "POST", UPLOAD, path={"tenant_id": fresh.a, "team_id": fresh.hr}, files=text_file(), expect=403, detail=NO_UPLOAD, behavior="Upload blocked")
        api.check(actor, "GET", JOB, path={"job_id": fresh.job_hr}, expect=404, detail=JOB_NOT_FOUND, behavior="Job hidden")


def test_inactive_team_details_are_hidden_from_members(api, fresh):
    api.setup(fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"is_active": False})
    api.check(
        fresh.mona, "GET", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=(403, 404), behavior="Blocked like the team's documents",
        why="F-8: can_access_team and is_team_member do not check Team.is_active.",
    )


def test_inactive_team_members_are_hidden_from_members(api, fresh):
    api.setup(fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"is_active": False})
    api.check(
        fresh.mona, "GET", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=(403, 404), behavior="Member list blocked",
        why="F-8: same missing check, and this list exposes colleagues' emails.",
    )


def test_inactive_team_is_not_listed_for_members(api, fresh):
    api.setup(fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, json={"is_active": False})
    teams = api.check(fresh.mona, "GET", TEAMS, path={"tenant_id": fresh.a}, expect=200, behavior="Inactive team not listed").json()
    api.verify(teams == [], f"{len(teams)} inactive team(s) listed", why="F-8: list_user_teams does not filter on Team.is_active.")


def test_inactive_team_can_be_reactivated(api, fresh):
    team = {"tenant_id": fresh.a, "team_id": fresh.hr}
    api.setup(fresh.alice, "PATCH", TEAM, path=team, json={"is_active": False})
    api.check(fresh.alice, "PATCH", TEAM, path=team, json={"is_active": True}, expect=200, behavior="Admin can reactivate")
    api.check(fresh.mona, "GET", DOC, path={**team, "document_id": fresh.doc_hr}, expect=200, behavior="Access returns")


def test_plain_member_cannot_reactivate_a_team(api, fresh):
    team = {"tenant_id": fresh.a, "team_id": fresh.hr}
    api.setup(fresh.alice, "PATCH", TEAM, path=team, json={"is_active": False})
    api.check(fresh.mona, "PATCH", TEAM, path=team, json={"is_active": True}, expect=403, detail=DENIED, behavior="Denied")


# --- Inactive tenants ---


@pytest.fixture
def closed(fresh):
    support.set_columns(Tenant, fresh.a, is_active=False)
    return fresh


@pytest.mark.parametrize(
    ("actor", "template", "keys", "detail"),
    [
        ("mona", TENANT_DOCS, ["a"], NOT_MEMBER), ("alice", TENANT_DOCS, ["a"], NOT_MEMBER), ("mona", TEAM_DOCS, ["a", "hr"], NOT_MEMBER), ("alice", TEAMS, ["a"], NOT_MEMBER),
        ("alice", TEAM, ["a", "hr"], NOT_MEMBER), ("alice", TEAM_MEMBERS, ["a", "hr"], NOT_MEMBER), ("alice", "/tenants/{tenant_id}/members", ["a"], NOT_MEMBER),
    ],
)
def test_inactive_tenant_blocks_data(api, closed, actor, template, keys, detail):
    names = ["tenant_id", "team_id"]
    path = {names[i]: getattr(closed, key) for i, key in enumerate(keys)}
    api.check(getattr(closed, actor), "GET", template, path=path, expect=403, detail=detail, behavior="Everything in an inactive tenant is blocked")


def test_inactive_tenant_blocks_document_details_and_upload(api, closed):
    doc = {"tenant_id": closed.a, "team_id": closed.hr, "document_id": closed.doc_hr}
    for suffix in READ_SUFFIXES:
        api.check(closed.mona, "GET", DOC + suffix, path=doc, expect=403, detail=NOT_MEMBER, behavior="Blocked", absent=["CANARY"])
    api.check(closed.mona, "POST", UPLOAD, path={"tenant_id": closed.a, "team_id": closed.hr}, files=text_file(), expect=403, detail=NOT_MEMBER, behavior="Upload blocked")
    api.check(closed.mona, "GET", JOB, path={"job_id": closed.job_hr}, expect=404, detail=JOB_NOT_FOUND, behavior="Job hidden")


def test_inactive_tenant_does_not_affect_other_tenants(api, closed):
    api.check(closed.bob, "GET", DOC, path={"tenant_id": closed.b, "team_id": closed.tb, "document_id": closed.doc_b}, expect=200, behavior="Tenant B is unaffected")


def test_inactive_tenant_is_not_listed(api, closed):
    tenants = api.check(closed.mona, "GET", TENANTS, expect=200, behavior="Inactive tenant not listed").json()
    api.verify(tenants == [], f"{len(tenants)} inactive tenant(s) listed", why="F-8: get_user_tenants does not filter on Tenant.is_active.")


def test_inactive_tenant_can_be_reactivated_by_its_admin(api, closed):
    api.check(
        closed.alice, "PATCH", TENANT, path={"tenant_id": closed.a}, json={"is_active": True}, expect=200, behavior="An admin can reverse deactivation",
        why="F-8 BUG: the membership check requires an active tenant, so deactivation can never be undone through the API.",
    )
