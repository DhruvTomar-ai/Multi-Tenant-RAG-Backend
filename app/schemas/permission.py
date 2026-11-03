from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionBase(BaseModel):
    resource: str = Field(..., min_length=1, max_length=100)
    action: str = Field(..., min_length=1, max_length=100)


class PermissionCreate(PermissionBase):
    role_id: int


class PermissionRead(PermissionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    role_id: int
    created_at: datetime


class PermissionUpdate(BaseModel):
    resource: str | None = Field(default=None, min_length=1, max_length=100)
    action: str | None = Field(default=None, min_length=1, max_length=100)
    role_id: int | None = None
