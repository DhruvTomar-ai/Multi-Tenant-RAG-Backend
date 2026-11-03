"""Upload validation. Client-supplied filenames and MIME types are never trusted:
the type stored for a document is decided here from the file's actual bytes."""
from __future__ import annotations

import codecs
import unicodedata
import zipfile
from pathlib import Path

from pypdf import PdfReader

from app.core.config import settings
from app.services.documents.errors import UploadValidationError

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_MIME = "text/plain"

_ACCEPTED_DECLARED_MIME = {
    ".pdf": {PDF_MIME, "application/x-pdf"},
    ".docx": {DOCX_MIME},
    ".txt": {TXT_MIME},
}
_CANONICAL_MIME = {".pdf": PDF_MIME, ".docx": DOCX_MIME, ".txt": TXT_MIME}
# Clients send these when they don't know the type, so they say nothing either way.
_UNINFORMATIVE_MIME = {"", "application/octet-stream"}

_DOCX_MAIN_PART_TYPE = b"wordprocessingml.document.main+xml"
_UNSAFE_FILENAME_CHARACTERS = set('<>:"|?*')
_MAX_FILENAME_LENGTH = 255
_READ_SIZE = 1024 * 1024


def sanitize_filename(raw_filename: str | None) -> str:
    """Return a display-only name. The name on disk is always a generated UUID."""
    if raw_filename is None:
        raise UploadValidationError("File name is required")

    name = raw_filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in _UNSAFE_FILENAME_CHARACTERS)
    name = name.strip(" .")
    if not name:
        raise UploadValidationError("File name is invalid")

    if len(name) > _MAX_FILENAME_LENGTH:
        suffix = Path(name).suffix
        name = name[: _MAX_FILENAME_LENGTH - len(suffix)] + suffix
    return name


def validate_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    allowed = {item.lower() for item in settings.allowed_document_extensions}
    if extension not in allowed or extension not in _CANONICAL_MIME:
        raise UploadValidationError(f"Unsupported file type. Allowed: {', '.join(sorted(allowed))}", status_code=415)
    return extension


def validate_declared_mime(extension: str, declared_mime: str | None) -> None:
    declared = (declared_mime or "").split(";", 1)[0].strip().lower()
    if declared in _UNINFORMATIVE_MIME:
        return
    if declared not in _ACCEPTED_DECLARED_MIME[extension]:
        raise UploadValidationError(f"Content type {declared} does not match a {extension} file", status_code=415)


def validate_file_content(path: Path, extension: str) -> str:
    """Check the bytes really are this file type and return the canonical MIME type."""
    validators = {".pdf": _validate_pdf, ".docx": _validate_docx, ".txt": _validate_txt}
    validators[extension](path)
    return _CANONICAL_MIME[extension]


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise UploadValidationError("File content is not a valid PDF")
        handle.seek(0)
        try:
            reader = PdfReader(handle)
            encrypted = reader.is_encrypted
            page_count = 0 if encrypted else len(reader.pages)
        except Exception as exc:  # pypdf raises many types on malformed input
            raise UploadValidationError("The PDF file is corrupted") from exc
    if encrypted:
        raise UploadValidationError("Password-protected PDF files are not supported")
    if page_count == 0:
        raise UploadValidationError("The PDF file has no pages")


def _validate_docx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise UploadValidationError("File content is not a valid DOCX")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UploadValidationError("File content is not a valid DOCX")

            # Checked before anything is decompressed, to stop zip bombs.
            uncompressed_size = sum(info.file_size for info in archive.infolist())
            if uncompressed_size > settings.max_docx_uncompressed_mb * 1024 * 1024:
                raise UploadValidationError("The DOCX file is too large when uncompressed", status_code=413)

            if _DOCX_MAIN_PART_TYPE not in archive.read("[Content_Types].xml"):
                raise UploadValidationError("File content is not a valid DOCX")
            if archive.testzip() is not None:
                raise UploadValidationError("The DOCX file is corrupted")
    except UploadValidationError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise UploadValidationError("The DOCX file is corrupted") from exc


def _validate_txt(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as handle:
        try:
            while block := handle.read(_READ_SIZE):
                if b"\x00" in block:
                    raise UploadValidationError("Text files must be UTF-8 and cannot contain binary data")
                decoder.decode(block)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise UploadValidationError("Text files must be UTF-8 and cannot contain binary data") from exc
