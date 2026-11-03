from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
import re


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def generate_unique_username(self, name: str) -> str:
        raw = re.sub(r"[^a-zA-Z0-9._-]+", "", (name or "user").lower())
        base = raw[:20] or "user"
        candidate = base
        counter = 1
        while self.db.query(User).filter(User.username == candidate).first():
            candidate = f"{base}{counter}"
            counter += 1
        return candidate

    def create_user(self, email: str, username: str, password_hash: str, full_name: str | None = None) -> User:
        user = User(email=email, username=username, password_hash=password_hash, full_name=full_name)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_user(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def list_users(self) -> list[User]:
        return self.db.scalars(select(User).order_by(User.id)).all()

    def get_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email).first()
