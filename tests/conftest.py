"""Shared pytest fixtures.

Unit and functional tests use an in-memory SQLite database and never touch the
network. Integration tests (marked ``integration``) override these fixtures with
real Postgres and Redis connections.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.main import create_app


def make_settings(**overrides: object) -> Settings:
    """Build test settings with permissive defaults for fast iteration."""
    defaults: dict[str, object] = {
        "database_url": "sqlite+pysqlite://",
        "redis_url": "redis://localhost:6379/0",
        "cache_enabled": True,
        "cache_ttl_seconds": 60,
        "negative_cache_ttl_seconds": 5,
        "rate_limit_requests": 1000,
        "rate_limit_window_seconds": 60,
        "log_level": "WARNING",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def engine():
    """A single shared in-memory database for the whole test."""
    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(settings: Settings, session_factory: sessionmaker[Session]):
    application = create_app(settings)

    def override_get_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
