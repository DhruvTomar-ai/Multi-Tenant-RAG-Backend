import io

import pytest

from app.services.documents.errors import PermanentIngestionError, UploadValidationError
from app.services.documents.processing import ExtractedSection, chunk_sections, extract_sections, hash_extracted_text
from app.services.documents.storage import LocalDocumentStorage
from app.services.documents.validation import sanitize_filename, validate_declared_mime, validate_extension, validate_file_content
from tests.helpers import DOCX_MIME, make_docx_bytes, make_pdf_bytes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("my annual report.pdf", "my annual report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\windows\\system.txt", "system.txt"),
        ("na<me>?.txt", "name.txt"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "...", "folder/"])
def test_sanitize_filename_rejects_names_with_nothing_left(raw):
    with pytest.raises(UploadValidationError):
        sanitize_filename(raw)


@pytest.mark.parametrize("name", ["virus.exe", "archive.zip", "report.pdf.exe", "noextension", "old.doc"])
def test_unsupported_extensions_are_rejected(name):
    with pytest.raises(UploadValidationError) as error:
        validate_extension(name)
    assert error.value.status_code == 415


def test_extension_check_ignores_case():
    assert validate_extension("REPORT.PDF") == ".pdf"


@pytest.mark.parametrize(
    ("extension", "mime"),
    [(".pdf", "application/pdf"), (".docx", DOCX_MIME), (".txt", "text/plain; charset=utf-8"), (".pdf", None), (".txt", "application/octet-stream")],
)
def test_matching_or_unknown_mime_is_accepted(extension, mime):
    validate_declared_mime(extension, mime)


def test_contradicting_mime_is_rejected():
    with pytest.raises(UploadValidationError) as error:
        validate_declared_mime(".pdf", "text/html")
    assert error.value.status_code == 415


@pytest.mark.parametrize(
    ("extension", "content", "expected_mime"),
    [
        (".pdf", make_pdf_bytes(), "application/pdf"),
        (".docx", make_docx_bytes(), DOCX_MIME),
        (".txt", "Plain text with unicode: café".encode("utf-8"), "text/plain"),
    ],
)
def test_real_files_pass_content_validation(tmp_path, extension, content, expected_mime):
    path = tmp_path / f"file{extension}"
    path.write_bytes(content)
    assert validate_file_content(path, extension) == expected_mime


@pytest.mark.parametrize(
    ("extension", "content"),
    [
        (".pdf", b"this is not a pdf"),
        (".pdf", b"%PDF-1.4\nbroken with no structure"),
        (".docx", b"this is not a zip file"),
        (".docx", make_pdf_bytes()),
        (".txt", b"binary\x00data"),
        (".txt", b"\xff\xfe\xfa invalid utf-8"),
    ],
)
def test_files_that_lie_about_their_type_are_rejected(tmp_path, extension, content):
    path = tmp_path / f"file{extension}"
    path.write_bytes(content)
    with pytest.raises(UploadValidationError):
        validate_file_content(path, extension)


def test_a_plain_zip_is_not_a_docx(tmp_path):
    import zipfile

    path = tmp_path / "file.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "just a zip")
    with pytest.raises(UploadValidationError):
        validate_file_content(path, ".docx")


def test_storage_stops_reading_at_the_size_limit(tmp_path):
    storage = LocalDocumentStorage(str(tmp_path))
    with pytest.raises(UploadValidationError) as error:
        storage.stage(io.BytesIO(b"x" * 2048), max_bytes=1024)
    assert error.value.status_code == 413
    assert list((tmp_path / ".staging").iterdir()) == []


def test_storage_rejects_empty_files_and_cleans_up(tmp_path):
    storage = LocalDocumentStorage(str(tmp_path))
    with pytest.raises(UploadValidationError):
        storage.stage(io.BytesIO(b""), max_bytes=1024)
    assert list((tmp_path / ".staging").iterdir()) == []


def test_storage_refuses_paths_outside_the_root(tmp_path):
    storage = LocalDocumentStorage(str(tmp_path))
    with pytest.raises(ValueError):
        storage.path_for(1, 2, "../../../outside.txt")


def test_extract_txt(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("Hello there.", encoding="utf-8")
    assert [section.text for section in extract_sections(path, ".txt")] == ["Hello there."]


def test_extract_pdf_keeps_the_page_number(tmp_path):
    path = tmp_path / "file.pdf"
    path.write_bytes(make_pdf_bytes("Text on page one"))
    sections = extract_sections(path, ".pdf")
    assert "Text on page one" in sections[0].text
    assert sections[0].metadata["page_label"] == "1"


def test_extract_docx(tmp_path):
    path = tmp_path / "file.docx"
    path.write_bytes(make_docx_bytes("Text in a Word file"))
    assert "Text in a Word file" in extract_sections(path, ".docx")[0].text


def test_extracting_only_whitespace_is_a_permanent_failure(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(PermanentIngestionError):
        extract_sections(path, ".txt")


def test_missing_file_is_a_permanent_failure(tmp_path):
    with pytest.raises(PermanentIngestionError):
        extract_sections(tmp_path / "gone.txt", ".txt")


def test_chunking_respects_size_and_keeps_metadata():
    text = " ".join(f"This is sentence number {number}." for number in range(400))
    chunks = chunk_sections([ExtractedSection(text=text, metadata={"page_label": "7"})], chunk_size=64, chunk_overlap=8)
    assert len(chunks) > 1
    assert all(chunk.metadata == {"page_label": "7"} for chunk in chunks)
    assert all(len(chunk.hash) == 64 for chunk in chunks)


def test_text_hash_ignores_whitespace_differences():
    assert hash_extracted_text("hello   world\n") == hash_extracted_text("hello world")
    assert hash_extracted_text("hello world") != hash_extracted_text("goodbye world")
