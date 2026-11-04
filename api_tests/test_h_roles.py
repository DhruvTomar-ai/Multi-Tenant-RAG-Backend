"""Role matrix. Expected results are computed from DEFAULT_ROLE_PERMISSIONS, the code's own source of truth."""
import importlib.util
from pathlib import Path

import pytest

from api_tests import support
from api_tests.support import ANON, DOC, DENIED, JOB, PASSWORD, TEAM, TEAM_DOCS, TEAM_MEMBER, TEAM_MEMBERS, TEAMS, TENANT, TENANT_MEMBERS, TENANTS, UPLOAD, add_to_team, new_user, text_file
from app.security.permissions import DEFAULT_ROLE_PERMISSIONS, DOCUMENT_READ, DOCUMENT_UPLOAD, TEAM_ACCESS_ALL, TEAM_MANAGE, TENANT_MANAGE

ROOT = Path(__file__).resolve().parent.parent
ROLES = sorted(DEFAULT_ROLE_PERMISSIONS)
TEAM_ACTORS = {"admin": "adam", "manager": "mike", "member": "mona", "viewer": "vera"}
TENANT_ACTORS = {"admin": "alice", "manager": "tess", "member": "nora", "viewer": "tina"}


def _team(w):
    return {"tenant_id": w.a, "team_id": w.hr}


def _doc(w):
    return {**_team(w), "document_id": w.doc_hr}


def _read(suffix):
    return lambda w: dict(method="GET", template=DOC + suffix, path=_doc(w))


def _add_member(w):
    return dict(method="POST", template=TEAM_MEMBERS, path=_team(w), json={"user_id": w.nora.id, "team_id": w.hr, "tenant_id": w.a, "role": "viewer"})


def _remove_member(w):
    return dict(method="DELETE", template=TEAM_MEMBER, path={**_team(w), "user_id": w.eli.id})


# Operation name, permission the code requires, success status, request builder.
TEAM_OPERATIONS = [
    ("list_documents", DOCUMENT_READ, 200, lambda w: dict(method="GET", template=TEAM_DOCS, path=_team(w))),
    ("read_document", DOCUMENT_READ, 200, _read("")),
    ("download_document", DOCUMENT_READ, 200, _read("/download")),
    ("read_text", DOCUMENT_READ, 200, _read("/text")),
    ("read_chunks", DOCUMENT_READ, 200, _read("/chunks")),
    ("read_document_jobs", DOCUMENT_READ, 200, _read("/jobs")),
    ("read_document_metadata", DOCUMENT_READ, 200, _read("/metadata")),
    ("read_document_status", DOCUMENT_READ, 200, _read("/status")),
    ("read_embedding_metadata", DOCUMENT_READ, 200, _read("/embeddings")),
    ("upload_document", DOCUMENT_UPLOAD, 202, lambda w: dict(method="POST", template=UPLOAD, path=_team(w), files=text_file())),
    ("grant_document_read", TEAM_MANAGE, 201, lambda w: dict(method="POST", template=support.GRANTS, path=_doc(w), json={"user_id": w.nora.id})),
    ("list_document_grants", TEAM_MANAGE, 200, lambda w: dict(method="GET", template=support.GRANTS, path=_doc(w))),
    ("update_team", TEAM_MANAGE, 200, lambda w: dict(method="PATCH", template=TEAM, path=_team(w), json={"description": "changed"})),
    ("add_team_member", TEAM_MANAGE, 201, _add_member),
    ("remove_team_member", TEAM_MANAGE, 204, _remove_member),
]
TENANT_OPERATIONS = [
    ("update_tenant", TENANT_MANAGE, 200, lambda w: dict(method="PATCH", template=TENANT, path={"tenant_id": w.a}, json={"description": "changed"})),
    ("add_tenant_member", TENANT_MANAGE, 201, lambda w: dict(method="POST", template=TENANT_MEMBERS, path={"tenant_id": w.a}, json={"email": w.bob.email, "role": "viewer"})),
    ("list_tenant_members", TENANT_MANAGE, 200, lambda w: dict(method="GET", template=TENANT_MEMBERS, path={"tenant_id": w.a})),
    ("create_team", TENANT_MANAGE, 201, lambda w: dict(method="POST", template=TEAMS, path={"tenant_id": w.a}, json={"name": "New team"})),
    ("read_hnsw_index_status", TENANT_MANAGE, 200, lambda w: dict(method="GET", template="/tenants/{tenant_id}/embeddings/index", path={"tenant_id": w.a})),
]


def _prepare(api, w, operation):
    if operation == "remove_team_member":
        support.add_to_team(api, w.alice, w.a, w.hr, w.eli)


def _run(api, w, actor_key, role, operation, permission, success, build, allowed):
    _prepare(api, w, operation)
    request = build(w)
    expect = success if allowed else 403
    behavior = f"{role} role {'holds' if allowed else 'does not hold'} {permission}: {operation} {'allowed' if allowed else 'denied'}"
    api.check(getattr(w, actor_key), request.pop("method"), request.pop("template"), expect=expect, behavior=behavior, **request)


@pytest.mark.parametrize(("operation", "permission", "success", "build"), TEAM_OPERATIONS, ids=[o[0] for o in TEAM_OPERATIONS])
@pytest.mark.parametrize("role", ROLES)
def test_team_role_matrix(api, fresh, role, operation, permission, success, build):
    allowed = permission in DEFAULT_ROLE_PERMISSIONS[role]
    _run(api, fresh, TEAM_ACTORS[role], role, operation, permission, success, build, allowed)


@pytest.mark.parametrize(("operation", "permission", "success", "build"), TEAM_OPERATIONS, ids=[o[0] for o in TEAM_OPERATIONS])
@pytest.mark.parametrize("role", ROLES)
def test_tenant_role_reaches_into_a_team_only_with_access_all(api, fresh, role, operation, permission, success, build):
    """These users are in no team. Their tenant role counts only if it holds team.access_all."""
    permissions = DEFAULT_ROLE_PERMISSIONS[role]
    allowed = TEAM_ACCESS_ALL in permissions and permission in permissions
    _run(api, fresh, TENANT_ACTORS[role], f"tenant {role}", operation, permission, success, build, allowed)


@pytest.mark.parametrize(("operation", "permission", "success", "build"), TENANT_OPERATIONS, ids=[o[0] for o in TENANT_OPERATIONS])
@pytest.mark.parametrize("role", ROLES)
def test_tenant_role_matrix(api, fresh, role, operation, permission, success, build):
    allowed = permission in DEFAULT_ROLE_PERMISSIONS[role]
    _run(api, fresh, TENANT_ACTORS[role], f"tenant {role}", operation, permission, success, build, allowed)


@pytest.mark.parametrize(("operation", "permission", "success", "build"), TENANT_OPERATIONS, ids=[o[0] for o in TENANT_OPERATIONS])
@pytest.mark.parametrize("role", ROLES)
def test_team_role_gives_no_tenant_level_rights(api, fresh, role, operation, permission, success, build):
    """These users are plain tenant members whose team role varies, including a team-level admin."""
    _run(api, fresh, TEAM_ACTORS[role], f"team {role}", operation, permission, success, build, False)


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_create_a_tenant_as_implemented(api, fresh, role):
    api.check(getattr(fresh, TENANT_ACTORS[role]), "POST", TENANTS, expect=201, behavior="create_tenant checks only for a login", json={"name": "Own", "slug": f"own-{role}"})


# --- The permission data itself ---


def _permissions_in_database(tenant_id):
    with support.db_session.SessionLocal() as session:
        rows = session.execute(
            support.text("SELECT r.name, p.resource, p.action FROM permissions p JOIN roles r ON r.id = p.role_id WHERE r.tenant_id = :t"),
            {"t": tenant_id},
        ).all()
    found = {}
    for role, resource, action in rows:
        found.setdefault(role, set()).add(f"{resource}.{action}")
    return found


def test_new_tenant_is_seeded_with_exactly_the_coded_permissions(api, fresh):
    found = _permissions_in_database(fresh.a)
    expected = {role: set(permissions) for role, permissions in DEFAULT_ROLE_PERMISSIONS.items()}
    support.fact(found == expected, name="seed_default_roles", expected="Database rows equal DEFAULT_ROLE_PERMISSIONS", actual=f"{found}")


def test_migration_seed_matches_the_coded_permissions():
    spec = importlib.util.spec_from_file_location("seed_migration", ROOT / "alembic" / "versions" / "b7d41c2e9a10_workflow_4_rbac_and_document_ingestion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = {role: {f"{r}.{a}" for r, a in items} for role, items in module.ROLE_PERMISSIONS.items()}
    expected = {role: set(permissions) for role, permissions in DEFAULT_ROLE_PERMISSIONS.items()}
    support.fact(frozen == expected, name="migration ROLE_PERMISSIONS", expected="Existing tenants get the same rights as new tenants", actual=f"{frozen}")


# --- Privilege escalation ---


@pytest.mark.parametrize(("actor", "expect", "detail"), [("mona", 403, DENIED), ("vera", 403, DENIED), ("mike", 403, "You cannot grant the role: admin"), ("nora", 403, DENIED), ("tess", 403, DENIED)])
def test_user_cannot_give_themselves_team_admin(api, fresh, actor, expect, detail):
    user = getattr(fresh, actor)
    api.check(user, "POST", TEAM_MEMBERS, path=_team(fresh), expect=expect, detail=detail, behavior="No self-promotion", json={"user_id": user.id, "team_id": fresh.hr, "tenant_id": fresh.a, "role": "admin"})


@pytest.mark.parametrize("actor", ["mona", "vera", "mike", "adam", "nora", "tess", "tina"])
def test_user_cannot_give_themselves_tenant_admin(api, fresh, actor):
    user = getattr(fresh, actor)
    api.check(user, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="No self-promotion", json={"email": user.email, "role": "admin"})
    role = api.setup(fresh.alice, "GET", TENANT_MEMBERS, path={"tenant_id": fresh.a}).json()
    api.verify(next(m["role"] for m in role if m["email"] == user.email) != "admin", "The user became a tenant admin")


@pytest.mark.parametrize(("actor", "target"), [("mona", "mike"), ("vera", "mike"), ("mona", "adam"), ("nora", "mike"), ("eli", "mike")])
def test_lower_role_cannot_change_a_manager_or_admin(api, fresh, actor, target):
    user = getattr(fresh, target)
    api.check(getattr(fresh, actor), "POST", TEAM_MEMBERS, path=_team(fresh), expect=403, detail=DENIED, behavior="Denied", json={"user_id": user.id, "team_id": fresh.hr, "tenant_id": fresh.a, "role": "viewer"})


@pytest.mark.parametrize(("actor", "target"), [("mona", "mike"), ("vera", "adam"), ("nora", "mike")])
def test_lower_role_cannot_remove_a_manager_or_admin(api, fresh, actor, target):
    api.check(getattr(fresh, actor), "DELETE", TEAM_MEMBER, path={**_team(fresh), "user_id": getattr(fresh, target).id}, expect=403, detail=DENIED, behavior="Denied")


@pytest.mark.parametrize("target", ["alice", "tess"])
def test_member_cannot_change_a_tenant_admin_or_manager(api, fresh, target):
    api.check(fresh.mona, "POST", TENANT_MEMBERS, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Denied", json={"email": getattr(fresh, target).email, "role": "viewer"})


def test_manager_cannot_downgrade_another_manager(api, fresh):
    other = new_user(api, "Otto")
    support.add_to_tenant(api, fresh.alice, fresh.a, other)
    add_to_team(api, fresh.alice, fresh.a, fresh.hr, other, "manager")
    api.check(
        fresh.mike, "POST", TEAM_MEMBERS, path=_team(fresh), expect=403, behavior="A manager cannot change an equal without explicit permission",
        json={"user_id": other.id, "team_id": fresh.hr, "tenant_id": fresh.a, "role": "viewer"},
        why="F-9: assign_user_to_team checks only the granted role, not the target's current role.",
    )


@pytest.mark.parametrize(
    ("template", "body_extra"),
    [("register", {"role": "admin", "is_superuser": True, "tenant_id": 1}), ("team", {"role": "admin", "role_id": 1}), ("tenant", {"role": "admin", "owner_user_id": 1})],
    ids=["register", "update_team", "create_tenant"],
)
def test_extra_role_fields_in_a_body_grant_nothing(api, fresh, template, body_extra):
    if template == "register":
        created = api.check(ANON, "POST", "/auth/register", expect=201, behavior="Extra fields ignored", json={"name": "Sly", "email": "sly@example.com", "password": PASSWORD, **body_extra}).json()
        tokens = api.setup(ANON, "POST", "/auth/login", json={"email": "sly@example.com", "password": PASSWORD}).json()
        actor = support.Actor("Sly", support.bearer(tokens["access_token"]), created["id"])
        api.verify(api.setup(actor, "GET", TENANTS).json() == [], "The new user joined a tenant")
    elif template == "team":
        api.check(fresh.mike, "PATCH", TEAM, path=_team(fresh), expect=200, behavior="Extra fields ignored", json={"description": "x", **body_extra})
        api.check(fresh.mike, "PATCH", TENANT, path={"tenant_id": fresh.a}, expect=403, detail=DENIED, behavior="Still not a tenant manager", json={"name": "Hacked"})
    else:
        tenant = api.check(fresh.mona, "POST", TENANTS, expect=201, behavior="owner_user_id in the body is ignored", json={"name": "Mine", "slug": "mona-mine", **{**body_extra, "owner_user_id": fresh.alice.id}}).json()
        members = api.setup(fresh.mona, "GET", TENANT_MEMBERS, path={"tenant_id": tenant["id"]}).json()
        api.verify([m["email"] for m in members] == [fresh.mona.email], f"Members: {members}")


def test_tenant_admin_rights_stop_at_the_tenant_boundary(api, fresh):
    for template, path in [(TEAM_DOCS, {"tenant_id": fresh.b, "team_id": fresh.tb}), (TEAM, {"tenant_id": fresh.b, "team_id": fresh.tb}), (TEAMS, {"tenant_id": fresh.b}), (TENANT_MEMBERS, {"tenant_id": fresh.b})]:
        api.check(fresh.alice, "GET", template, path=path, expect=403, detail=support.NOT_MEMBER, behavior="Admin of A gets nothing in B")


def test_job_is_hidden_without_read_permission(api, fresh):
    api.check(fresh.tina, "GET", JOB, path={"job_id": fresh.job_hr}, expect=404, detail=support.JOB_NOT_FOUND, behavior="Tenant viewer without a team sees no jobs")
