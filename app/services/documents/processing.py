"""Text extraction and chunking with LlamaIndex. Has no database or HTTP code.

LlamaIndex is imported inside the functions so that the API process, which never
extracts text, does not pay its import cost at startup.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.documents.errors import PermanentIngestionError


@dataclass(frozen=True)
class ExtractedSection:
    """One unit of extracted text: a page for PDFs, the whole file for DOCX and TXT."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkData:
    text: str
    hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _load_documents(file_path: Path, extension: str) -> list[Any]:
    from llama_index.core import Document as LlamaDocument

    if extension == ".txt":
        return [LlamaDocument(text=file_path.read_text(encoding="utf-8-sig"))]

    from llama_index.readers.file import DocxReader, PDFReader

    if extension == ".pdf":
        return PDFReader().load_data(file=file_path)
    if extension == ".docx":
        return DocxReader().load_data(file=file_path)
    raise PermanentIngestionError(f"Unsupported file type: {extension}")


def extract_sections(file_path: Path, extension: str) -> list[ExtractedSection]:
    if not file_path.is_file():
        raise PermanentIngestionError("The stored file is missing")

    try:
        documents = _load_documents(file_path, extension)
    except PermanentIngestionError:
        raise
    except (ImportError, OSError):
        # A broken install or a disk problem can be temporary: let the task retry.
        raise
    except Exception as exc:
        raise PermanentIngestionError("The file could not be parsed") from exc

    sections: list[ExtractedSection] = []
    for document in documents:
        # PostgreSQL TEXT columns reject NUL characters, which some PDFs produce.
        text = document.text.replace("\x00", "").strip()
        if not text:
            continue
        metadata: dict[str, Any] = {}
        page_label = document.metadata.get("page_label")
        if page_label is not None:
            metadata["page_label"] = str(page_label)
        sections.append(ExtractedSection(text=text, metadata=metadata))

    if not sections:
        raise PermanentIngestionError("No readable text was found in the file")
    return sections


def join_sections(sections: list[ExtractedSection]) -> str:
    return "\n\n".join(section.text for section in sections)


def hash_extracted_text(text: str) -> str:
    """Whitespace is normalized so the same words hash alike across file formats."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def chunk_sections(sections: list[ExtractedSection], *, chunk_size: int, chunk_overlap: int) -> list[ChunkData]:
    from llama_index.core.node_parser import SentenceSplitter

    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks: list[ChunkData] = []
    # Sections are split separately so each chunk keeps an accurate page number.
    for section in sections:
        for piece in splitter.split_text(section.text):
            text = piece.strip()
            if text:
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                chunks.append(ChunkData(text=text, hash=digest, metadata=dict(section.metadata)))

    if not chunks:
        raise PermanentIngestionError("No text chunks could be generated from the file")
    return chunks
