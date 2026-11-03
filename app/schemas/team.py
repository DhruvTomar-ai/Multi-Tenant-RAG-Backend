from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class TeamBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class TeamCreate(TeamBase):
    pass


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None


class TeamRead(TeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TeamMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    username: str
    full_name: str | None = None
    role: str
