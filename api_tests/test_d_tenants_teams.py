"""Tenant, team and membership APIs: success, validation, roles and permissions."""
import pytest

from api_tests.support import (
    DENIED, NO_TEAM_ACCESS, NOT_MEMBER, TEAM, TEAM_DOCS, TEAM_MEMBER, TEAM_MEMBERS, TEAM_NOT_FOUND, TEAMS, TENANT, TENANT_HIDDEN,
    TENANT_MEMBERS, TENANTS, DOC, add_to_tenant, new_user,
)

LONG = "x" * 1001


def membership(user, team_id, tenant_id, role="member"):
    return {"user_id": user.id, "team_id": team_id, "tenant_id": tenant_id, "role": role}


# --- Tenants ---


def test_create_tenant_makes_the_creator_admin(api, clean):
    user = new_user(api, "Owner")
    tenant = api.check(user, "POST", TENANTS, expect=201, behavior="Tenant created", json={"name": "T", "slug": "t-one"}).json()
    members = api.setup(user, "GET", TENANT_MEMBERS, path={"tenant_id": tenant["id"]}).json()
    api.verify(members[0]["role"] == "admin", f"Creator role is {members[0]['role']}")


def test_create_tenant_duplicate_slug(api, fresh):
    api.check(fresh.eli, "POST", TENANTS, expect=400, behavior="Rejected", json={"name": "Other", "slug": "company-a"})


@pytest.mark.parametrize(
    "body",
    [{}, {"name": "x"}, {"slug": "x"}, {"name": "", "slug": "x"}, {"name": "x", "slug": ""}, {"name": "x", "slug": "s" * 101}, {"name": "n" * 256, "slug": "x"}],
    ids=["empty", "no_slug", "no_name", "empty_name", "empty_slug", "long_slug", "long_name"],
)
def test_create_tenant_invalid_body(api, fresh, body):
    api.check(fresh.eli, "POST", TENANTS, expect=422, behavior="Validation error", json=body)


def test_create_tenant_with_too_long_description(api, fresh):
    api.check(
        fresh.eli, "POST", TENANTS, expect=422, behavior="Validation error, not a server error", json={"name": "x", "slug": "long-desc", "description": LONG},
        why="BUG: the schema has no length limit, but the column holds 1000 characters.",
    )


def test_create_tenant_with_nul_character(api, fresh):
    api.check(
        fresh.eli, "POST", TENANTS, expect=(400, 422), behavior="Rejected, not a server error", json={"name": "bad\u0000", "slug": "nul-tenant"},
        why="BUG: NUL characters reach PostgreSQL unvalidated.",
    )


def test_any_logged_in_user_can_create_a_tenant_as_implemented(api, fresh):
    # create_tenant checks no permission, so this is the implemented rule.
    created = api.check(fresh.mona, "POST", TENANTS, expect=201, behavior="As implemented: any logged-in user may create a tenant", json={"name": "Side", "slug": "mona-side"}).json()
    api.check(fresh.mona, "GET", TEAMS, path={"tenant_id": fresh.b}, expect=403, detail=NOT_MEMBER, behavior="Being admin of a new tenant gives nothing in another tenant")
    api.check(fresh.alice, "GET", TENANT, path={"tenant_id": created["id"]}, expect=404, detail=TENANT_HIDDEN, behavior="Mona's new tenant is hidden from her own company's admin")


def test_list_tenants_shows_only_memberships(api, fresh):
    response = api.check(fresh.alice, "GET", TENANTS, expect=200, behavior="Only Company A", absent=["company-b"])
    api.verify([t["id"] for t in response.json()] == [fresh.a], "Unexpected tenants")


def test_user_with_no_tenants_gets_an_empty_list(api, clean):
    response = api.check(new_user(api, "Solo"), "GET", TENANTS, expect=200, behavior="Empty list")
    api.verify(response.json() == [], "List is not empty")


def test_get_tenant_as_member(api, fresh):
    api.check(fresh.mona, "GET", TENANT, path={"tenant_id": fresh.a}, expect=200, behavior="Tenant returned")


def test_get_tenant_as_outsider(api, fresh):
    api.check(fresh.bob, "GET", TENANT, path={"tenant_id": fresh.a}, expect=404, detail=TENANT_HIDDEN, behavior="Hidden", absent=["company-a"])


def test_real_and_missing_tenant_look_identical(api, fresh):
    real = api.send(fresh.bob, "GET", TENANT, path={"tenant_id": fresh.a})
    response = api.check(fresh.bob, "GET", TENANT, path={"tenant_id": 999999999}, expect=404, behavior="Same response as a real tenant the user cannot see")
    api.verify(
        response.json() == real.json(), f"Different bodies: {real.json()} vs {response.json()}",
        why="F-6: the different message confirms which tenant IDs exist.",
    )


@pytest.mark.parametrize("bad", ["abc", "1.5"])
def test_get_tenant_invalid_id(api, fresh, bad):
    api.check(fresh.alice, "GET", TENANT, path={"tenant_id": bad}, expect=422, behavior="Validation error")


def test_patch_tenant_as_admin(api, fresh):
    response = api.check(fresh.alice, "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=200, behavior="Updated", json={"description": "new"})
    api.verify(response.json()["description"] == "new", "Not updated")


@pytest.mark.parametrize("actor", ["mona", "vera", "mike", "tess", "adam"])
def test_patch_tenant_needs_tenant_manage(api, fresh, actor):
    api.check(getattr(fresh, actor), "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Denied", json={"name": "Hacked"})


def test_patch_tenant_of_other_company(api, fresh):
    api.check(fresh.bob, "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=404, detail=TENANT_HIDDEN, behavior="Hidden", json={"name": "Hacked"})


def test_patch_tenant_duplicate_slug(api, fresh):
    api.check(
        fresh.alice, "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=400, behavior="Clean rejection", json={"slug": "company-b"},
        why="BUG: update_tenant route has no IntegrityError handling.",
    )


def test_patch_tenant_ignores_unknown_fields(api, fresh):
    response = api.check(fresh.alice, "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=200, behavior="id cannot be changed", json={"id": fresh.b})
    api.verify(response.json()["id"] == fresh.a, "ID changed")


# --- Tenant members ---


def test_admin_adds_a_tenant_member(api, fresh):
    user = new_user(api, "Newbie")
    response = api.check(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=201, behavior="Member added", json={"email": user.email})
    api.verify(response.json()["role"] == "member", "Default role is not member")


@pytest.mark.parametrize("actor", ["mona", "vera", "mike", "nora", "tess", "adam"])
def test_add_tenant_member_needs_tenant_manage(api, fresh, actor):
    api.check(getattr(fresh, actor), "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Denied", json={"email": fresh.bob.email})


def test_other_company_admin_cannot_add_members(api, fresh):
    api.check(fresh.bob, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=NOT_MEMBER, behavior="Denied", json={"email": fresh.bob.email, "role": "admin"})


def test_add_unknown_email(api, fresh):
    api.check(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=404, detail="User not found", behavior="Not found", json={"email": "nobody@example.com"})


def test_add_member_unknown_role(api, fresh):
    api.check(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=400, behavior="Rejected", json={"email": fresh.mona.email, "role": "superuser"})


@pytest.mark.parametrize("body", [{}, {"email": "bad"}], ids=["empty", "bad_email"])
def test_add_member_invalid_body(api, fresh, body):
    api.check(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=422, behavior="Validation error", json=body)


def test_add_member_does_not_reveal_which_outside_emails_exist(api, fresh):
    unknown = api.send(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, json={"email": "nobody@example.com", "role": "superuser"})
    response = api.check(fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=unknown.status_code, behavior="Same answer for known and unknown outside emails", json={"email": fresh.bob.email, "role": "superuser"}, why="F-6: 400 vs 404 reveals whether an email is registered anywhere on the platform.")
    api.verify(response.json() == unknown.json(), "Different bodies")


def test_list_tenant_members_as_admin(api, fresh):
    response = api.check(fresh.alice, "GET", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=200, behavior="A's members only", absent=[fresh.bob.email])
    api.verify(fresh.mona.email in response.text, "Mona missing")


@pytest.mark.parametrize("actor", ["mona", "mike", "tess"])
def test_list_tenant_members_needs_tenant_manage(api, fresh, actor):
    api.check(getattr(fresh, actor), "GET", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Denied")


def test_other_company_cannot_list_members(api, fresh):
    api.check(fresh.bob, "GET", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=NOT_MEMBER, behavior="Denied", absent=[fresh.alice.email])


def test_last_tenant_admin_cannot_be_demoted(api, fresh):
    api.check(
        fresh.alice, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=(400, 409), behavior="A tenant always keeps an admin", json={"email": fresh.alice.email, "role": "member"},
        why="BUG: the only admin can demote themselves, leaving nobody able to manage the tenant.",
    )


# --- Teams ---


def test_admin_creates_a_team(api, fresh):
    response = api.check(fresh.alice, "POST", TEAMS, path={"tenant_id": fresh.a}, expect=201, behavior="Created", json={"name": "Legal"})
    api.verify(response.json()["tenant_id"] == fresh.a, "Wrong tenant")


@pytest.mark.parametrize("actor", ["mona", "mike", "tess", "adam"])
def test_create_team_needs_tenant_manage(api, fresh, actor):
    api.check(getattr(fresh, actor), "POST", TEAMS, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Denied", json={"name": "Rogue"})


def test_other_company_cannot_create_a_team(api, fresh):
    api.check(fresh.bob, "POST", TEAMS, path={"tenant_id": fresh.a}, expect=403, detail=NOT_MEMBER, behavior="Denied", json={"name": "Rogue"})


def test_create_team_duplicate_name(api, fresh):
    api.check(fresh.alice, "POST", TEAMS, path={"tenant_id": fresh.a}, expect=400, behavior="Rejected", json={"name": "HR"})


def test_same_team_name_is_allowed_in_another_tenant(api, fresh):
    api.check(fresh.bob, "POST", TEAMS, path={"tenant_id": fresh.b}, expect=201, behavior="Names are scoped per tenant", json={"name": "HR"})


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "n" * 256}], ids=["empty", "empty_name", "long_name"])
def test_create_team_invalid_body(api, fresh, body):
    api.check(fresh.alice, "POST", TEAMS, path={"tenant_id": fresh.a}, expect=422, behavior="Validation error", json=body)


def test_create_team_with_too_long_description(api, fresh):
    api.check(
        fresh.alice, "POST", TEAMS, path={"tenant_id": fresh.a}, expect=422, behavior="Validation error, not a server error", json={"name": "Long", "description": LONG},
        why="BUG: the schema has no length limit, but the column holds 1000 characters.",
    )


@pytest.mark.parametrize(
    ("actor", "tenant", "visible", "hidden"),
    [("alice", "a", ["HR", "Engineering"], ["Finance"]), ("mona", "a", ["HR"], ["Engineering", "Finance"]), ("eli", "a", ["Engineering"], ["HR", "Finance"]), ("nora", "a", [], ["HR", "Engineering"]), ("tess", "a", [], ["HR", "Engineering"]), ("bob", "b", ["Finance"], ["HR", "Engineering"])],
)
def test_list_teams_by_role(api, fresh, actor, tenant, visible, hidden):
    response = api.check(getattr(fresh, actor), "GET", TEAMS, path={"tenant_id": getattr(fresh, tenant)}, expect=200, behavior=f"Sees only {visible}", contains=visible, absent=hidden)
    api.verify(len(response.json()) == len(visible), f"Saw {len(response.json())} teams")


@pytest.mark.parametrize(
    ("actor", "team", "expect", "detail"),
    [("alice", "hr", 200, None), ("alice", "eng", 200, None), ("mona", "hr", 200, None), ("vera", "hr", 200, None), ("mike", "hr", 200, None), ("adam", "hr", 200, None),
     ("mona", "eng", 403, NO_TEAM_ACCESS), ("eli", "hr", 403, NO_TEAM_ACCESS), ("nora", "hr", 403, NO_TEAM_ACCESS), ("tess", "hr", 403, NO_TEAM_ACCESS), ("mike", "eng", 403, NO_TEAM_ACCESS), ("adam", "eng", 403, NO_TEAM_ACCESS)],
)
@pytest.mark.parametrize("template", [TEAM, TEAM_MEMBERS], ids=["details", "members"])
def test_team_details_and_members_by_role(api, fresh, actor, team, expect, detail, template):
    api.check(getattr(fresh, actor), "GET", template, path={"tenant_id": fresh.a, "team_id": getattr(fresh, team)}, expect=expect, detail=detail, behavior="Allowed" if expect == 200 else "Denied")


def test_team_members_show_real_roles(api, fresh):
    response = api.check(fresh.alice, "GET", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=200, behavior="Exact members and roles")
    roles = {member["email"]: member["role"] for member in response.json()}
    expected = {fresh.mona.email: "member", fresh.vera.email: "viewer", fresh.mike.email: "manager", fresh.adam.email: "admin"}
    api.verify(roles == expected, f"Got {roles}")


@pytest.mark.parametrize("template", [TEAM, TEAM_MEMBERS], ids=["details", "members"])
def test_missing_team_is_a_404(api, fresh, template):
    api.check(fresh.alice, "GET", template, path={"tenant_id": fresh.a, "team_id": 999999999}, expect=404, detail=TEAM_NOT_FOUND, behavior="Not found")


@pytest.mark.parametrize("bad", ["abc", "1.5"])
def test_team_invalid_id(api, fresh, bad):
    api.check(fresh.alice, "GET", TEAM, path={"tenant_id": fresh.a, "team_id": bad}, expect=422, behavior="Validation error")


@pytest.mark.parametrize(("actor", "team"), [("alice", "hr"), ("mike", "hr"), ("adam", "hr")])
def test_patch_team_allowed(api, fresh, actor, team):
    response = api.check(getattr(fresh, actor), "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": getattr(fresh, team)}, expect=200, behavior="Updated", json={"description": "changed"})
    api.verify(response.json()["description"] == "changed", "Not updated")


@pytest.mark.parametrize(("actor", "team"), [("mona", "hr"), ("vera", "hr"), ("mona", "eng"), ("mike", "eng"), ("adam", "eng"), ("tess", "hr"), ("nora", "hr")])
def test_patch_team_denied(api, fresh, actor, team):
    api.check(getattr(fresh, actor), "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": getattr(fresh, team)}, expect=403, detail=DENIED, behavior="Denied", json={"name": "Hacked"})


def test_patch_team_duplicate_name(api, fresh):
    api.check(
        fresh.alice, "PATCH", TEAM, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=400, behavior="Clean rejection", json={"name": "Engineering"},
        why="BUG: update_team route has no IntegrityError handling.",
    )


# --- Team members ---


@pytest.mark.parametrize(("actor", "role"), [("alice", "manager"), ("mike", "member"), ("mike", "viewer"), ("mike", "manager"), ("adam", "admin")])
def test_assign_team_member_allowed(api, fresh, actor, role):
    response = api.check(getattr(fresh, actor), "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=201, behavior="Assigned", json=membership(fresh.nora, fresh.hr, fresh.a, role))
    api.verify(response.json()["role"] == role, "Wrong role")


@pytest.mark.parametrize(("actor", "team"), [("mona", "hr"), ("vera", "hr"), ("nora", "hr"), ("tess", "hr"), ("mike", "eng"), ("adam", "eng"), ("eli", "hr")])
def test_assign_team_member_denied(api, fresh, actor, team):
    team_id = getattr(fresh, team)
    api.check(getattr(fresh, actor), "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": team_id}, expect=403, detail=DENIED, behavior="Denied", json=membership(fresh.nora, team_id, fresh.a))


def test_manager_cannot_grant_admin(api, fresh):
    api.check(fresh.mike, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=403, detail="You cannot grant the role: admin", behavior="Cannot grant above own rights", json=membership(fresh.nora, fresh.hr, fresh.a, "admin"))


def test_manager_cannot_promote_self_to_admin(api, fresh):
    api.check(fresh.mike, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=403, behavior="No self-promotion", json=membership(fresh.mike, fresh.hr, fresh.a, "admin"))


def test_member_cannot_add_self_to_another_team(api, fresh):
    api.check(fresh.mona, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.eng}, expect=403, detail=DENIED, behavior="Denied", json=membership(fresh.mona, fresh.eng, fresh.a))


def test_manager_cannot_demote_a_higher_role(api, fresh):
    api.check(
        fresh.mike, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=403, behavior="A manager cannot downgrade a team admin", json=membership(fresh.adam, fresh.hr, fresh.a, "viewer"),
        why="F-9: only the granted role is checked, not the target's current role.",
    )


def test_manager_cannot_remove_a_higher_role(api, fresh):
    api.check(
        fresh.mike, "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.hr, "user_id": fresh.adam.id}, expect=403, behavior="A manager cannot remove a team admin",
        why="F-9: remove_user_from_team does not compare roles.",
    )


@pytest.mark.parametrize(
    ("body_change", "expect", "detail"),
    [({"team_id": "eng"}, 400, "Tenant or team mismatch"), ({"tenant_id": "b"}, 400, "Tenant or team mismatch")],
    ids=["other_team_in_body", "other_tenant_in_body"],
)
def test_assign_body_ids_must_match_url(api, fresh, body_change, expect, detail):
    body = membership(fresh.nora, fresh.hr, fresh.a)
    for name, key in body_change.items():
        body[name] = getattr(fresh, key)
    api.check(fresh.alice, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=expect, detail=detail, behavior="Mismatch rejected", json=body)


def test_assign_user_outside_the_tenant(api, fresh):
    api.check(fresh.alice, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=400, detail=NOT_MEMBER, behavior="Must be a tenant member first", json=membership(fresh.bob, fresh.hr, fresh.a))


def test_assign_unknown_user(api, fresh):
    body = {"user_id": 999999999, "team_id": fresh.hr, "tenant_id": fresh.a, "role": "member"}
    api.check(fresh.alice, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=400, detail="User not found", behavior="Rejected", json=body)


def test_assign_unknown_role(api, fresh):
    api.check(fresh.alice, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=400, behavior="Rejected", json=membership(fresh.nora, fresh.hr, fresh.a, "superuser"))


def test_assign_invalid_body(api, fresh):
    api.check(fresh.alice, "POST", TEAM_MEMBERS, path={"tenant_id": fresh.a, "team_id": fresh.hr}, expect=422, behavior="Validation error", json={"user_id": "abc"})


@pytest.mark.parametrize("actor", ["alice", "mike", "adam"])
def test_remove_team_member_allowed(api, fresh, actor):
    api.check(getattr(fresh, actor), "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.hr, "user_id": fresh.mona.id}, expect=204, behavior="Removed")


@pytest.mark.parametrize(("actor", "team", "target"), [("mona", "hr", "vera"), ("vera", "hr", "mona"), ("mona", "eng", "eli"), ("mike", "eng", "eli"), ("eli", "hr", "mona"), ("tess", "hr", "mona")])
def test_remove_team_member_denied(api, fresh, actor, team, target):
    api.check(getattr(fresh, actor), "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": getattr(fresh, team), "user_id": getattr(fresh, target).id}, expect=403, detail=DENIED, behavior="Denied")


def test_remove_non_member_is_a_404(api, fresh):
    api.check(fresh.alice, "DELETE", TEAM_MEMBER, path={"tenant_id": fresh.a, "team_id": fresh.hr, "user_id": fresh.eli.id}, expect=404, detail="Membership not found", behavior="Not found")


# --- Roles reaching documents ---


def test_team_admin_role_gives_no_reach_into_other_teams(api, fresh):
    api.check(fresh.adam, "GET", TEAM_DOCS, path={"tenant_id": fresh.a, "team_id": fresh.eng}, expect=403, behavior="An admin role on HR only applies to HR", absent=["a-eng.txt"])


def test_tenant_manager_role_without_a_team_reads_nothing(api, fresh):
    api.check(fresh.tess, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}, expect=403, behavior="A tenant manager role gives no document access")


def test_viewer_role_on_hr_is_read_only(api, fresh):
    api.check(fresh.vera, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.hr, "document_id": fresh.doc_hr}, expect=200, behavior="Viewer can read")


def test_promotion_to_admin_takes_effect_immediately(api, fresh):
    add_to_tenant(api, fresh.alice, fresh.a, fresh.nora, "admin")
    api.check(fresh.nora, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.eng, "document_id": fresh.doc_eng}, expect=200, behavior="The new role applies to the existing token")


def test_demotion_from_admin_takes_effect_immediately(api, fresh):
    add_to_tenant(api, fresh.alice, fresh.a, fresh.nora, "admin")
    add_to_tenant(api, fresh.alice, fresh.a, fresh.nora, "member")
    api.check(fresh.nora, "GET", DOC, path={"tenant_id": fresh.a, "team_id": fresh.eng, "document_id": fresh.doc_eng}, expect=403, behavior="The old role stops working on the existing token")
