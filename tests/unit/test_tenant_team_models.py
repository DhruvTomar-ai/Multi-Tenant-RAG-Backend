from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.team import Team
from app.db.models.user import User
from app.db.models.user_tenant_membership import UserTenantMembership
from app.db.models.user_team_membership import UserTeamMembership


def test_tenant_team_relationships_are_created():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        tenant = Tenant(name="Acme", slug="acme", description="demo")
        team_a = Team(tenant=tenant, name="Engineering")
        team_b = Team(tenant=tenant, name="HR")
        session.add_all([tenant, team_a, team_b])
        session.commit()

        assert tenant.teams[0].name == "Engineering"
        assert len(tenant.teams) == 2


def test_user_can_join_tenants_and_teams():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        tenant = Tenant(name="Acme", slug="acme")
        team = Team(tenant=tenant, name="Engineering")
        role = Role(tenant=tenant, name="member")
        user = User(email="u@example.com", username="user1", password_hash="hash", full_name="Test User")
        membership = UserTenantMembership(user=user, tenant=tenant, role=role)
        team_membership = UserTeamMembership(user=user, team=team, tenant=tenant, role=role)
        session.add_all([tenant, team, role, user, membership, team_membership])
        session.commit()

        assert len(user.tenant_memberships) == 1
        assert len(user.team_memberships) == 1
        assert team_membership.tenant_id == tenant.id
        assert team_membership.role.name == "member"
