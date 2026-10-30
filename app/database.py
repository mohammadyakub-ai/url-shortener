"""Database engine, session factory and FastAPI dependency.

The engine is created eagerly at import time. SQLAlchemy does not open a
connection until the first query, so importing the app never requires Postgres
to be reachable. Tests override ``get_db`` with their own engine.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by models and Alembic metadata."""


_settings = get_settings()

engine = create_engine(_settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a short-lived session and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
