"""Local filesystem storage. Layout: <root>/<tenant_id>/<team_id>/<uuid><extension>

Files are first written to a staging folder and validated there, so a rejected upload
never reaches a tenant's folder. No part of a stored path comes from the client.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings
from app.services.documents.errors import UploadValidationError

_READ_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StagedFile:
    path: Path
    size: int
    sha256: str


class LocalDocumentStorage:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.document_storage_root).resolve()

    def stage(self, source: BinaryIO, max_bytes: int) -> StagedFile:
        """Stream the upload to disk, stopping as soon as it passes the size limit."""
        staging_dir = self.root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = staging_dir / f"{uuid.uuid4().hex}.upload"

        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as target:
                while block := source.read(_READ_SIZE):
                    size += len(block)
                    if size > max_bytes:
                        limit_mb = max_bytes // (1024 * 1024)
                        raise UploadValidationError(f"File exceeds the maximum allowed size of {limit_mb} MB", status_code=413)
                    digest.update(block)
                    target.write(block)
            if size == 0:
                raise UploadValidationError("Uploaded file is empty")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StagedFile(path=path, size=size, sha256=digest.hexdigest())

    def promote(self, staged: StagedFile, tenant_id: int, team_id: int, stored_filename: str) -> None:
        target = self.path_for(tenant_id, team_id, stored_filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged.path, target)

    def discard(self, staged: StagedFile) -> None:
        staged.path.unlink(missing_ok=True)

    def path_for(self, tenant_id: int, team_id: int, stored_filename: str) -> Path:
        path = (self.root / str(int(tenant_id)) / str(int(team_id)) / stored_filename).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Stored file path is outside the storage root")
        return path

    def exists(self, tenant_id: int, team_id: int, stored_filename: str) -> bool:
        return self.path_for(tenant_id, team_id, stored_filename).is_file()

    def delete(self, tenant_id: int, team_id: int, stored_filename: str) -> None:
        self.path_for(tenant_id, team_id, stored_filename).unlink(missing_ok=True)
