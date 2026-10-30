from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.tenant_models import Tenant, User, UserTenantMembership
from app.schemas.tenant import TenantCreate, TenantDetailRead, TenantMembershipCreate, TenantMembershipRead, TenantRead
from app.services.tenant_service import TenantService

router = APIRouter(prefix="/api/v1", tags=["tenants"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "multi-tenant-rag-backend"}


@router.post("/tenants", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)) -> Tenant:
    try:
        return TenantService.create_tenant(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/tenants", response_model=list[TenantRead])
def list_tenants(db: Session = Depends(get_db)) -> list[Tenant]:
    return TenantService.list_tenants(db)


@router.get("/tenants/{tenant_id}", response_model=TenantDetailRead)
def get_tenant(tenant_id: int, db: Session = Depends(get_db)) -> TenantDetailRead:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    memberships = (
        db.query(UserTenantMembership)
        .filter(UserTenantMembership.tenant_id == tenant_id)
        .order_by(UserTenantMembership.created_at.asc())
        .all()
    )
    members = []
    for membership in memberships:
        user = db.get(User, membership.user_id)
        members.append(
            TenantMembershipRead(
                id=membership.id,
                tenant_id=membership.tenant_id,
                user_id=membership.user_id,
                role=membership.role,
                user_email=user.email if user else "",
                user_full_name=user.full_name if user else "",
            )
        )

    return TenantDetailRead(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        members=members,
    )


@router.post("/tenants/{tenant_id}/members", response_model=TenantMembershipRead, status_code=status.HTTP_201_CREATED)
def add_member(tenant_id: int, payload: TenantMembershipCreate, db: Session = Depends(get_db)) -> TenantMembershipRead:
    try:
        membership = TenantService.add_member(db, tenant_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = db.get(User, membership.user_id)
    return TenantMembershipRead(
        id=membership.id,
        tenant_id=membership.tenant_id,
        user_id=membership.user_id,
        role=membership.role,
        user_email=user.email if user else "",
        user_full_name=user.full_name if user else "",
    )
