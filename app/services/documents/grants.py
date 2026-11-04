"""Explicit read grants on documents. The caller has already authorized the acting user."""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.document import Document
from app.db.models.document_grant import DocumentGrant
from app.security.authorization import AuthorizationService


class GrantTargetNotAllowed(Exception):
    """The target is not an active member of the document's tenant."""


class DocumentGrantService:
    def __init__(self, db: Session):
        self.db = db

    def get_grantable_document(self, *, tenant_id: int, team_id: int, document_id: int) -> Document | None:
        return self.db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.team_id == team_id,
                Document.is_active.is_(True),
                Document.deleted_at.is_(None),
            )
        )

    def _find(self, document_id: int, user_id: int) -> DocumentGrant | None:
        return self.db.scalar(select(DocumentGrant).where(DocumentGrant.document_id == document_id, DocumentGrant.user_id == user_id))

    def grant(self, document: Document, *, user_id: int, granted_by_user_id: int) -> DocumentGrant:
        """Granting twice keeps the first grant."""
        if not AuthorizationService(self.db).is_tenant_member(user_id, document.tenant_id):
            raise GrantTargetNotAllowed("User is not a member of this tenant")
        existing = self._find(document.id, user_id)
        if existing is not None:
            return existing
        grant = DocumentGrant(
            document_id=document.id,
            tenant_id=document.tenant_id,
            team_id=document.team_id,
            user_id=user_id,
            granted_by_user_id=granted_by_user_id,
        )
        self.db.add(grant)
        try:
            self.db.commit()
        except IntegrityError:
            # Two identical requests raced, or the membership disappeared in between.
            self.db.rollback()
            existing = self._find(document.id, user_id)
            if existing is None:
                raise GrantTargetNotAllowed("User is not a member of this tenant") from None
            return existing
        self.db.refresh(grant)
        return grant

    def list_grants(self, document: Document, *, limit: int = 100, offset: int = 0) -> list[DocumentGrant]:
        stmt = select(DocumentGrant).where(DocumentGrant.document_id == document.id).order_by(DocumentGrant.id).limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def revoke(self, document: Document, *, user_id: int) -> bool:
        result = self.db.execute(delete(DocumentGrant).where(DocumentGrant.document_id == document.id, DocumentGrant.user_id == user_id))
        self.db.commit()
        return result.rowcount > 0
