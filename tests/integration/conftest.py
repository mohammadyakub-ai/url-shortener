"""Fixtures for integration tests against real PostgreSQL and Redis.

The schema is reset and rebuilt through Alembic so the migration itself is
exercised. These tests are opt-in via ``pytest -m integration``.
"""

import os
import subprocess
import sys
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.cache import LinkCache
from app.config import Settings
from app.database import get_db
from app.main import create_app
from tests.conftest import make_settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Ensure the dedicated test database exists, or skip if Postgres is absent."""
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:  # noqa: BLE001 - connection failures mean skip
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        admin.dispose()
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str):
    """Reset the public schema and apply Alembic migrations to an empty database."""
    engine = create_engine(postgres_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    env = {**os.environ, "DATABASE_URL": postgres_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(migrated_engine) -> sessionmaker[Session]:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE urls RESTART IDENTITY"))
    return sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def redis_client() -> Generator[Redis, None, None]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except RedisError as exc:
        pytest.skip(f"Redis unavailable: {exc}")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def integration_settings() -> Settings:
    return make_settings(
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        rate_limit_requests=1000,
    )


@pytest.fixture
def app(integration_settings, session_factory, redis_client):
    application = create_app(integration_settings)
    application.state.redis = redis_client
    application.state.cache = LinkCache(
        redis_client,
        ttl_seconds=integration_settings.cache_ttl_seconds,
        negative_ttl_seconds=integration_settings.negative_cache_ttl_seconds,
        enabled=True,
    )

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
