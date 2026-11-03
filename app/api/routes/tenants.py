from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.db.models.user import User
from app.schemas.tenant import TenantCreate, TenantMemberCreate, TenantMemberRead, TenantRead, TenantUpdate
from app.schemas.team import TeamCreate, TeamMemberRead, TeamRead, TeamUpdate
from app.schemas.user import UserTeamMembershipInput
from app.services.tenants.service import RoleGrantForbidden, TenantService
from app.services.teams.service import TeamService
from app.services.users.service import UserService

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        tenant = TenantService(db).create_tenant(
            payload.name,
            payload.slug,
            payload.description,
            owner_user_id=current_user.id,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A tenant with this slug already exists") from exc
    return tenant


@router.get("", response_model=list[TenantRead])
def list_tenants(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return TenantService(db).get_user_tenants(current_user.id)


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(tenant_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tenant = TenantService(db).get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found or access denied")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantRead)
def update_tenant(tenant_id: int, payload: TenantUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tenant = TenantService(db).get_tenant(tenant_id)
    if not tenant or not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found or access denied")
    if not TenantService(db).can_manage_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    updated = TenantService(db).update_tenant(tenant_id, **payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return updated


@router.post("/{tenant_id}/members", response_model=TenantMemberRead, status_code=status.HTTP_201_CREATED)
def add_tenant_member(tenant_id: int, payload: TenantMemberCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not TenantService(db).can_manage_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    user = UserService(db).get_by_email(payload.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        membership = TenantService(db).add_tenant_member(tenant_id, user, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TenantMemberRead(id=user.id, email=user.email, username=user.username, full_name=user.full_name, role=membership.role.name)


@router.get("/{tenant_id}/members", response_model=list[TenantMemberRead])
def list_tenant_members(tenant_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not TenantService(db).can_manage_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    return [
        TenantMemberRead(id=m.user.id, email=m.user.email, username=m.user.username, full_name=m.user.full_name, role=m.role.name)
        for m in TenantService(db).list_tenant_members(tenant_id)
    ]


@router.post("/{tenant_id}/teams", response_model=TeamRead, status_code=status.HTTP_201_CREATED)
def create_team(tenant_id: int, payload: TeamCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not TenantService(db).can_manage_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    try:
        team = TeamService(db).create_team(tenant_id, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A team with this name already exists") from exc
    return team


@router.get("/{tenant_id}/teams", response_model=list[TeamRead])
def list_teams(tenant_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if TenantService(db).can_see_all_teams(current_user.id, tenant_id):
        return TeamService(db).list_teams(tenant_id)
    return TenantService(db).list_user_teams(tenant_id, current_user.id)


@router.get("/{tenant_id}/teams/{team_id}", response_model=TeamRead)
def get_team(tenant_id: int, team_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    team = TeamService(db).get_team(tenant_id, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not TenantService(db).can_access_team(current_user.id, team_id, tenant_id):
        raise HTTPException(status_code=403, detail="You do not have access to this team")
    return team


@router.patch("/{tenant_id}/teams/{team_id}", response_model=TeamRead)
def update_team(tenant_id: int, team_id: int, payload: TeamUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    team = TeamService(db).get_team(tenant_id, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not TenantService(db).can_manage_team(current_user.id, team_id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    updated = TeamService(db).update_team(tenant_id, team_id, **payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Team not found")
    return updated


@router.post("/{tenant_id}/teams/{team_id}/members", response_model=dict, status_code=status.HTTP_201_CREATED)
def assign_user_to_team(tenant_id: int, team_id: int, payload: UserTeamMembershipInput, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not TeamService(db).get_team(tenant_id, team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    if not TenantService(db).can_manage_team(current_user.id, team_id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    if payload.tenant_id != tenant_id or payload.team_id != team_id:
        raise HTTPException(status_code=400, detail="Tenant or team mismatch")
    try:
        membership = TenantService(db).assign_user_to_team(
            tenant_id, team_id, payload.user_id, payload.role, acting_user_id=current_user.id
        )
    except RoleGrantForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": membership.id, "user_id": membership.user_id, "team_id": membership.team_id, "tenant_id": membership.tenant_id, "role": membership.role.name}


@router.delete("/{tenant_id}/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_from_team(tenant_id: int, team_id: int, user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    if not TenantService(db).can_manage_team(current_user.id, team_id, tenant_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    deleted = TenantService(db).remove_user_from_team(tenant_id, team_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Membership not found")


@router.get("/{tenant_id}/teams/{team_id}/members", response_model=list[TeamMemberRead])
def list_team_members(tenant_id: int, team_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not TenantService(db).is_user_member_of_tenant(current_user.id, tenant_id):
        raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    team = TeamService(db).get_team(tenant_id, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not TenantService(db).can_access_team(current_user.id, team_id, tenant_id):
        raise HTTPException(status_code=403, detail="You do not have access to this team")
    return [
        {"id": m.user.id, "email": m.user.email, "username": m.user.username, "full_name": m.user.full_name, "role": m.role.name}
        for m in TenantService(db).list_team_memberships(tenant_id, team_id)
    ]
