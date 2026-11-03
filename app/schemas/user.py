from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=150)
    full_name: str | None = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=255)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserTenantMembershipInput(BaseModel):
    user_id: int
    tenant_id: int
    role: str = "member"


class UserTeamMembershipInput(BaseModel):
    user_id: int
    team_id: int
    tenant_id: int
    role: str = "member"
