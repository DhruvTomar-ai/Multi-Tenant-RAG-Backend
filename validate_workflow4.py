from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# create tenant
r = client.post(
    "/api/v1/tenants",
    json={
        "slug": "workflow4docdemo",
        "name": "Workflow4 Doc Demo",
        "owner_email": "workflow4doc@example.com",
        "owner_full_name": "Workflow4 Owner",
        "owner_password": "StrongPass123",
    },
)
print("TENANT", r.status_code, r.json())
assert r.status_code == 201, r.text
tenant_id = r.json()["id"]

# create document
r2 = client.post(
    f"/api/v1/tenants/{tenant_id}/documents",
    json={"metadata": {"source": "demo"}},
)
print("DOC", r2.status_code, r2.json())
assert r2.status_code == 201, r2.text
document_id = r2.json()["id"]

# list documents
r3 = client.get(f"/api/v1/tenants/{tenant_id}/documents")
print("LIST", r3.status_code, r3.json())
assert r3.status_code == 200, r3.text

# list chunks
r4 = client.get(f"/api/v1/tenants/{tenant_id}/documents/{document_id}/chunks")
print("CHUNKS", r4.status_code, r4.json())
assert r4.status_code == 200, r4.text

print("WORKFLOW4_OK")
