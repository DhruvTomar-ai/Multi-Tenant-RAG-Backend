from fastapi.testclient import TestClient

from app.main import app


def test_workflow4_document_routes():
    client = TestClient(app)

    tenant_response = client.post(
        "/api/v1/tenants",
        json={
            "slug": "workflow4docdemo",
            "name": "Workflow4 Doc Demo",
            "owner_email": "workflow4doc@example.com",
            "owner_full_name": "Workflow4 Owner",
            "owner_password": "StrongPass123",
        },
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = tenant_response.json()["id"]

    document_response = client.post(
        f"/api/v1/tenants/{tenant_id}/documents",
        json={"metadata": {"source": "demo"}},
    )
    assert document_response.status_code == 201, document_response.text
    document_id = document_response.json()["id"]

    list_response = client.get(f"/api/v1/tenants/{tenant_id}/documents")
    assert list_response.status_code == 200, list_response.text

    chunks_response = client.get(f"/api/v1/tenants/{tenant_id}/documents/{document_id}/chunks")
    assert chunks_response.status_code == 200, chunks_response.text
    assert isinstance(chunks_response.json(), list)
