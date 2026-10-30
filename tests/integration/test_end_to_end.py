"""End-to-end tests against real PostgreSQL and Redis."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_alembic_migration_applied_to_empty_database(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        urls_table = connection.scalar(text("SELECT to_regclass('public.urls')"))

    assert version == "0001_create_urls"
    assert urls_table == "urls"


def test_health_checks_real_dependencies(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


def test_create_redirect_and_stats_round_trip(client: TestClient, redis_client) -> None:
    created = client.post("/shorten", json={"target_url": "https://example.com/integration"}).json()

    redirect = client.get(f"/{created['code']}", follow_redirects=False)
    stats = client.get(f"/stats/{created['code']}")

    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://example.com/integration"
    assert stats.status_code == 200
    assert stats.json()["clicks"] == 1
    assert redis_client.exists(f"url:{created['code']}") == 1


def test_unknown_code_is_not_found(client: TestClient) -> None:
    assert client.get("/doesnotexist", follow_redirects=False).status_code == 404
    assert client.get("/stats/doesnotexist").status_code == 404
