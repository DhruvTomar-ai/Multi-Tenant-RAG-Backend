from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, ConfigDict


class TenantBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class TenantCreate(TenantBase):
    pass


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class TenantRead(TenantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantMemberCreate(BaseModel):
    email: EmailStr
    role: str = "member"


class TenantMemberRead(BaseModel):
    id: int
    email: str
    username: str
    full_name: str | None = None
    role: str
