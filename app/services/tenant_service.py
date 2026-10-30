from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenant_models import Tenant as TenantModel, User, UserTenantMembership
from app.schemas.tenant import TenantCreate, TenantMembershipCreate


class TenantService:
    @staticmethod
    def create_tenant(db: Session, payload: TenantCreate) -> TenantModel:
        tenant = db.execute(select(TenantModel).where(TenantModel.slug == payload.slug)).scalar_one_or_none()
        if tenant:
            raise ValueError("Tenant with this slug already exists")

        existing_user = db.execute(select(User).where(User.email == payload.owner_email)).scalar_one_or_none()
        if existing_user is None:
            user = User(
                email=str(payload.owner_email),
                password_hash=TenantService._hash_password(payload.owner_password),
                full_name=payload.owner_full_name,
                is_active=True,
            )
            db.add(user)
            db.flush()
        else:
            user = existing_user

        tenant_model = TenantModel(
            slug=payload.slug,
            name=payload.name,
            is_active=True,
        )
        db.add(tenant_model)
        db.flush()

        membership = UserTenantMembership(
            tenant_id=tenant_model.id,
            user_id=user.id,
            role="owner",
        )
        db.add(membership)
        db.commit()
        db.refresh(tenant_model)
        return tenant_model

    @staticmethod
    def list_tenants(db: Session) -> list[TenantModel]:
        return db.execute(select(TenantModel).order_by(TenantModel.created_at.desc())).scalars().all()

    @staticmethod
    def add_member(db: Session, tenant_id: int, payload: TenantMembershipCreate) -> UserTenantMembership:
        tenant = db.get(TenantModel, tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
        if user is None:
            user = User(
                email=str(payload.email),
                password_hash=TenantService._hash_password(payload.password),
                full_name=payload.full_name,
                is_active=True,
            )
            db.add(user)
            db.flush()

        existing = db.execute(
            select(UserTenantMembership).where(
                UserTenantMembership.tenant_id == tenant_id,
                UserTenantMembership.user_id == user.id,
            )
        ).scalar_one_or_none()
        if existing:
            raise ValueError("User is already a member of this tenant")

        membership = UserTenantMembership(
            tenant_id=tenant_id,
            user_id=user.id,
            role=payload.role,
        )
        db.add(membership)
        db.commit()
        db.refresh(membership)
        return membership

    @staticmethod
    def _hash_password(password: str) -> str:
        from pwdlib.hashers.argon2 import Argon2Hasher

        return Argon2Hasher().hash(password)
