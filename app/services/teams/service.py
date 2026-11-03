from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.team import Team
from app.db.models.tenant import Tenant


class TeamService:
    def __init__(self, db: Session):
        self.db = db

    def create_team(self, tenant_id: int, name: str, description: str | None = None) -> Team:
        tenant = self.db.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        team = Team(tenant_id=tenant_id, name=name, description=description)
        self.db.add(team)
        self.db.commit()
        self.db.refresh(team)
        return team

    def list_teams(self, tenant_id: int) -> list[Team]:
        return self.db.scalars(select(Team).where(Team.tenant_id == tenant_id).order_by(Team.id)).all()

    def get_team(self, tenant_id: int, team_id: int) -> Team | None:
        return self.db.execute(select(Team).where(Team.tenant_id == tenant_id, Team.id == team_id)).scalar_one_or_none()

    def update_team(self, tenant_id: int, team_id: int, **kwargs) -> Team | None:
        team = self.get_team(tenant_id, team_id)
        if not team:
            return None
        for field, value in kwargs.items():
            if value is not None:
                setattr(team, field, value)
        self.db.commit()
        self.db.refresh(team)
        return team
