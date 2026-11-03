from tests.helpers import add_team_member, add_tenant_member, create_team, create_tenant, register_user


def test_create_and_list_tenants(client):
    _, headers = register_user(client, "owner@example.com")
    response = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme", "description": "Demo tenant"}, headers=headers)
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Acme"
    assert payload["slug"] == "acme"

    list_response = client.get("/api/v1/tenants", headers=headers)
    assert list_response.status_code == 200
    assert any(item["slug"] == "acme" for item in list_response.json())


def test_tenant_endpoints_require_authentication(client):
    assert client.get("/api/v1/tenants").status_code == 401
    assert client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).status_code == 401


def test_create_team_in_tenant(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, headers, "beta")

    team_response = client.post(f"/api/v1/tenants/{tenant_id}/teams", json={"name": "Engineering", "description": "eng team"}, headers=headers)
    assert team_response.status_code == 201
    payload = team_response.json()
    assert payload["tenant_id"] == tenant_id
    assert payload["name"] == "Engineering"


def test_users_cannot_be_assigned_to_team_in_other_tenant(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_a = create_tenant(client, headers, "alpha")
    tenant_b = create_tenant(client, headers, "gamma")
    team_b = create_team(client, headers, tenant_b, "Finance")
    user_id, _ = register_user(client, "user@example.com")
    add_tenant_member(client, headers, tenant_a, "user@example.com")

    response = client.post(
        f"/api/v1/tenants/{tenant_a}/teams/{team_b}/members",
        json={"user_id": user_id, "team_id": team_b, "tenant_id": tenant_a, "role": "member"},
        headers=headers,
    )
    assert response.status_code == 404


def test_user_must_be_tenant_member_before_joining_team(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, headers, "alpha")
    team_id = create_team(client, headers, tenant_id, "HR")
    outsider_id, _ = register_user(client, "outsider@example.com")

    response = client.post(
        f"/api/v1/tenants/{tenant_id}/teams/{team_id}/members",
        json={"user_id": outsider_id, "team_id": team_id, "tenant_id": tenant_id, "role": "member"},
        headers=headers,
    )
    assert response.status_code == 400


def test_team_update_cannot_move_a_team_to_another_tenant(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_a = create_tenant(client, headers, "alpha")
    tenant_b = create_tenant(client, headers, "gamma")
    team_id = create_team(client, headers, tenant_a, "HR")

    response = client.patch(f"/api/v1/tenants/{tenant_a}/teams/{team_id}", json={"name": "People", "tenant_id": tenant_b}, headers=headers)

    assert response.status_code == 200
    assert response.json()["name"] == "People"
    assert response.json()["tenant_id"] == tenant_a


def test_team_members_are_listed_with_their_real_role(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, headers, "alpha")
    team_id = create_team(client, headers, tenant_id, "HR")
    user_id, _ = register_user(client, "viewer@example.com")
    add_tenant_member(client, headers, tenant_id, "viewer@example.com")
    add_team_member(client, headers, tenant_id, team_id, user_id, "viewer")

    response = client.get(f"/api/v1/tenants/{tenant_id}/teams/{team_id}/members", headers=headers)

    assert response.status_code == 200
    assert [member["role"] for member in response.json()] == ["viewer"]


def test_unknown_role_is_rejected(client):
    _, headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, headers, "alpha")
    register_user(client, "user@example.com")

    response = client.post(f"/api/v1/tenants/{tenant_id}/members", json={"email": "user@example.com", "role": "superuser"}, headers=headers)

    assert response.status_code == 400


def test_only_tenant_managers_can_add_tenant_members(client):
    _, owner_headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, owner_headers, "alpha")
    _, member_headers = register_user(client, "member@example.com")
    add_tenant_member(client, owner_headers, tenant_id, "member@example.com")
    register_user(client, "friend@example.com")

    response = client.post(f"/api/v1/tenants/{tenant_id}/members", json={"email": "friend@example.com"}, headers=member_headers)

    assert response.status_code == 403


def test_team_manager_cannot_grant_a_role_stronger_than_their_own(client):
    _, owner_headers = register_user(client, "owner@example.com")
    tenant_id = create_tenant(client, owner_headers, "alpha")
    team_id = create_team(client, owner_headers, tenant_id, "HR")
    manager_id, manager_headers = register_user(client, "manager@example.com")
    add_tenant_member(client, owner_headers, tenant_id, "manager@example.com")
    add_team_member(client, owner_headers, tenant_id, team_id, manager_id, "manager")
    user_id, _ = register_user(client, "user@example.com")
    add_tenant_member(client, owner_headers, tenant_id, "user@example.com")

    too_strong = client.post(
        f"/api/v1/tenants/{tenant_id}/teams/{team_id}/members",
        json={"user_id": user_id, "team_id": team_id, "tenant_id": tenant_id, "role": "admin"},
        headers=manager_headers,
    )
    allowed = client.post(
        f"/api/v1/tenants/{tenant_id}/teams/{team_id}/members",
        json={"user_id": user_id, "team_id": team_id, "tenant_id": tenant_id, "role": "member"},
        headers=manager_headers,
    )

    assert too_strong.status_code == 403
    assert allowed.status_code == 201
