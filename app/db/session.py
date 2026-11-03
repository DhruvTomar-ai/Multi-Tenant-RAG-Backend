from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

__all__ = ["Base", "engine", "SessionLocal"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
