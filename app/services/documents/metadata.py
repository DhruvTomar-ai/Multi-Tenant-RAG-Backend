"""Validates user-supplied document metadata.

Metadata describes a document and filters results. It must never be able to influence
identity or access, so keys that look like security fields are refused at any depth.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.db.models.document import ACCESS_LEVELS
from app.services.documents.errors import UploadValidationError

_MAX_LABEL_LENGTH = 100
_MAX_SOURCE_LENGTH = 255
_MAX_TAGS = 50
_MAX_TAG_LENGTH = 64
_MAX_CUSTOM_BYTES = 16 * 1024
_MAX_CUSTOM_KEYS = 100
_MAX_CUSTOM_DEPTH = 4
_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_RESERVED_KEYS = frozenset(
    {
        "id", "tenant", "tenant_id", "tenantid", "team", "team_id", "teamid", "document_id", "documentid",
        "chunk_id", "embedding_id", "user", "user_id", "userid", "owner", "owner_id", "ownerid",
        "uploaded_by", "uploaded_by_user_id", "created_by", "role", "role_id", "roles", "permission",
        "permissions", "access", "access_level", "acl", "is_admin", "admin", "is_active", "deleted_at",
        "status", "version",
    }
)


class MetadataValidationError(UploadValidationError):
    def __init__(self, message: str):
        super().__init__(message, status_code=422)


@dataclass(frozen=True)
class DocumentMetadataInput:
    """Fields left as None were not supplied, so a new version can inherit them."""

    document_type: str | None = None
    department: str | None = None
    category: str | None = None
    source: str | None = None
    access_level: str | None = None
    tags: list[str] | None = None
    custom_metadata: dict[str, Any] | None = None


def _clean_text(name: str, value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise MetadataValidationError(f"{name} cannot be empty")
    if len(cleaned) > max_length:
        raise MetadataValidationError(f"{name} cannot be longer than {max_length} characters")
    if not cleaned.isprintable():
        raise MetadataValidationError(f"{name} contains invalid characters")
    return cleaned


def parse_tags(raw: str | None) -> list[str] | None:
    """Accepts a JSON array or a comma-separated list. Tags are lowercased and deduplicated."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetadataValidationError("tags is not a valid JSON array") from exc
        if not all(isinstance(value, str) for value in values):
            raise MetadataValidationError("tags must be strings")
    else:
        values = [value for value in raw.split(",") if value.strip()]

    tags: list[str] = []
    for value in values:
        tag = _clean_text("tag", value, _MAX_TAG_LENGTH).lower()
        if tag not in tags:
            tags.append(tag)
    if len(tags) > _MAX_TAGS:
        raise MetadataValidationError(f"A document cannot have more than {_MAX_TAGS} tags")
    return tags


def _check_custom(value: Any, depth: int, counter: list[int]) -> None:
    if depth > _MAX_CUSTOM_DEPTH:
        raise MetadataValidationError(f"custom_metadata cannot be nested more than {_MAX_CUSTOM_DEPTH} levels")
    if isinstance(value, dict):
        for key, item in value.items():
            counter[0] += 1
            if not _KEY_PATTERN.match(key):
                raise MetadataValidationError(f"custom_metadata key is not allowed: {key[:64]!r}")
            if key.lower() in _RESERVED_KEYS:
                raise MetadataValidationError(f"custom_metadata cannot set the reserved field: {key}")
            _check_custom(item, depth + 1, counter)
    elif isinstance(value, list):
        for item in value:
            _check_custom(item, depth + 1, counter)
    elif isinstance(value, str):
        if "\x00" in value:
            raise MetadataValidationError("custom_metadata contains invalid characters")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise MetadataValidationError("custom_metadata contains an unsupported value")


def parse_custom_metadata(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if len(raw.encode("utf-8")) > _MAX_CUSTOM_BYTES:
        raise MetadataValidationError(f"custom_metadata cannot be larger than {_MAX_CUSTOM_BYTES} bytes")
    try:
        value = json.loads(raw, parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise MetadataValidationError("custom_metadata is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MetadataValidationError("custom_metadata must be a JSON object")
    counter = [0]
    _check_custom(value, 1, counter)
    if counter[0] > _MAX_CUSTOM_KEYS:
        raise MetadataValidationError(f"custom_metadata cannot have more than {_MAX_CUSTOM_KEYS} keys")
    return value


def validate_access_level(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in ACCESS_LEVELS:
        raise MetadataValidationError(f"access_level must be one of: {', '.join(ACCESS_LEVELS)}")
    return value


def build_metadata_input(
    *,
    document_type: str | None = None,
    department: str | None = None,
    category: str | None = None,
    source: str | None = None,
    access_level: str | None = None,
    tags: str | None = None,
    custom_metadata: str | None = None,
) -> DocumentMetadataInput:
    return DocumentMetadataInput(
        document_type=_clean_text("document_type", document_type, _MAX_LABEL_LENGTH),
        department=_clean_text("department", department, _MAX_LABEL_LENGTH),
        category=_clean_text("category", category, _MAX_LABEL_LENGTH),
        source=_clean_text("source", source, _MAX_SOURCE_LENGTH),
        access_level=validate_access_level(access_level),
        tags=parse_tags(tags),
        custom_metadata=parse_custom_metadata(custom_metadata),
    )
