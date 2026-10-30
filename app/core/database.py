import logging

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=settings.db_echo,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_engine() -> Engine:
    return engine


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> tuple[str, str]:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT current_database(), current_user"))
        database_name, username = result.fetchone()
        return database_name, username


if __name__ == "__main__":
    try:
        database_name, username = check_database_connection()
        logger.info("Connected to PostgreSQL successfully: %s as %s", database_name, username)
    except Exception as exc:
        logger.exception("Database connection failed: %s", exc)
        raise