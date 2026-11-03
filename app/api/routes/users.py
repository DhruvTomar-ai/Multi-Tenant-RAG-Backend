from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas.user import UserCreate, UserRead
from app.services.users.service import UserService

router = APIRouter(prefix="/api/v1", tags=["users"])


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    try:
        user = UserService(db).create_user(
            email=payload.email,
            username=payload.username,
            password_hash="placeholder_hash",
            full_name=payload.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db)):
    return UserService(db).list_users()
