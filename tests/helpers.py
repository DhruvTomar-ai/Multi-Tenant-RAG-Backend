"""Builders for real test files, plus shortcuts for the API calls most tests need."""
from __future__ import annotations

import io
import zipfile

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PASSWORD = "Secret123!"


def make_pdf_bytes(text: str = "Hello from a PDF document.") -> bytes:
    """A small but valid single-page PDF. An empty text gives a page with nothing to extract."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(output)


def make_docx_bytes(text: str = "Hello from a Word document.") -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def register_user(client, email: str) -> tuple[int, dict[str, str]]:
    """Return the new user's ID and their Authorization headers."""
    response = client.post("/api/v1/auth/register", json={"name": email.split("@")[0], "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return response.json()["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_tenant(client, headers: dict[str, str], slug: str) -> int:
    response = client.post("/api/v1/tenants", json={"name": slug, "slug": slug}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_team(client, headers: dict[str, str], tenant_id: int, name: str) -> int:
    response = client.post(f"/api/v1/tenants/{tenant_id}/teams", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_tenant_member(client, headers: dict[str, str], tenant_id: int, email: str, role: str = "member") -> None:
    response = client.post(f"/api/v1/tenants/{tenant_id}/members", json={"email": email, "role": role}, headers=headers)
    assert response.status_code == 201, response.text


def add_team_member(client, headers: dict[str, str], tenant_id: int, team_id: int, user_id: int, role: str = "member") -> None:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/teams/{team_id}/members",
        json={"user_id": user_id, "team_id": team_id, "tenant_id": tenant_id, "role": role},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def upload(client, headers, tenant_id, team_id, filename="notes.txt", content=b"Some useful text.", mime="text/plain"):
    return client.post(
        f"/api/v1/tenants/{tenant_id}/teams/{team_id}/documents/upload",
        files={"file": (filename, content, mime)},
        headers=headers,
    )
