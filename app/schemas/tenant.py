from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TenantCreate(BaseModel):
    slug: str = Field(..., min_length=3, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    owner_email: EmailStr
    owner_full_name: str = Field(..., min_length=2, max_length=255)
    owner_password: str = Field(..., min_length=8, max_length=255)


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


class TenantMembershipCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8, max_length=255)
    role: str = Field(default="member", min_length=1, max_length=50)


class TenantMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    user_id: int
    role: str
    user_email: str
    user_full_name: str


class TenantDetailRead(TenantRead):
    members: list[TenantMembershipRead] = []
