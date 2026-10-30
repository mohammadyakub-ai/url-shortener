"""Functional coverage of caching, fail-open behaviour and rate limiting."""

import json

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app import services
from app.cache import MISSING_SENTINEL
from tests.conftest import build_app, make_settings


class BrokenRedis:
    """Simulates a Redis outage for every command the app issues."""

    def get(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def set(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def incr(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def expire(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def ttl(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def ping(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def close(self):
        return None


def test_redirect_populates_positive_cache(client: TestClient, fake_redis) -> None:
    code = client.post("/shorten", json={"target_url": "https://example.com/cached"}).json()["code"]
    assert fake_redis.get(f"url:{code}") is None

    client.get(f"/{code}", follow_redirects=False)

    raw = fake_redis.get(f"url:{code}")
    assert raw is not None
    assert json.loads(raw)["target_url"] == "https://example.com/cached"
    assert fake_redis.ttl(f"url:{code}") > 0


def test_cache_hit_does_not_need_database_row(client: TestClient, db_session) -> None:
    code = client.post("/shorten", json={"target_url": "https://example.com/hit"}).json()["code"]
    client.get(f"/{code}", follow_redirects=False)

    row = services.get_link(db_session, code)
    assert row is not None
    db_session.delete(row)
    db_session.commit()

    response = client.get(f"/{code}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/hit"


def test_unknown_code_is_negatively_cached(client: TestClient, fake_redis) -> None:
    response = client.get("/missing1", follow_redirects=False)

    assert response.status_code == 404
    assert fake_redis.get("url:missing1") == MISSING_SENTINEL
    assert 0 < fake_redis.ttl("url:missing1") <= 5


def test_cache_disabled_skips_redis(session_factory, fake_redis) -> None:
    settings = make_settings(cache_enabled=False)
    app = build_app(settings, session_factory, fake_redis)

    with TestClient(app) as test_client:
        code = test_client.post(
            "/shorten", json={"target_url": "https://example.com/nocache"}
        ).json()["code"]
        for _ in range(2):
            test_client.get(f"/{code}", follow_redirects=False)
        stats = test_client.get(f"/stats/{code}").json()

    assert fake_redis.get(f"url:{code}") is None
    assert stats["clicks"] == 2


def test_redirect_fails_open_when_redis_is_down(session_factory) -> None:
    app = build_app(make_settings(), session_factory, BrokenRedis())

    with TestClient(app) as test_client:
        code = test_client.post(
            "/shorten", json={"target_url": "https://example.com/failopen"}
        ).json()["code"]
        response = test_client.get(f"/{code}", follow_redirects=False)
        stats = test_client.get(f"/stats/{code}").json()

    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/failopen"
    assert stats["clicks"] == 1


def test_health_degrades_but_stays_up_when_redis_is_down(session_factory) -> None:
    app = build_app(make_settings(), session_factory, BrokenRedis())

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "down"}


def test_rate_limit_returns_429_with_retry_after(session_factory, fake_redis) -> None:
    settings = make_settings(rate_limit_requests=3)
    app = build_app(settings, session_factory, fake_redis)

    with TestClient(app) as test_client:
        for _ in range(3):
            assert (
                test_client.post("/shorten", json={"target_url": "https://example.com"}).status_code
                == 201
            )
        response = test_client.post("/shorten", json={"target_url": "https://example.com"})

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert response.json()["detail"] == "Rate limit exceeded"


def test_rate_limit_fails_open_when_redis_is_down(session_factory) -> None:
    settings = make_settings(rate_limit_requests=3)
    app = build_app(settings, session_factory, BrokenRedis())

    with TestClient(app) as test_client:
        statuses = {
            test_client.post("/shorten", json={"target_url": "https://example.com"}).status_code
            for _ in range(10)
        }

    assert statuses == {201}


def test_rate_limit_disabled_with_zero_budget(session_factory, fake_redis) -> None:
    settings = make_settings(rate_limit_requests=0)
    app = build_app(settings, session_factory, fake_redis)

    with TestClient(app) as test_client:
        statuses = {
            test_client.post("/shorten", json={"target_url": "https://example.com"}).status_code
            for _ in range(25)
        }

    assert statuses == {201}
