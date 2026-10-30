"""Unit tests for the Redis cache-aside helpers."""

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import MISSING_SENTINEL, LinkCache


class BrokenRedis:
    """Minimal client whose every command fails, simulating an outage."""

    def get(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def set(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def close(self):
        return None


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def cache(redis_client) -> LinkCache:
    return LinkCache(redis_client, ttl_seconds=60, negative_ttl_seconds=5)


def test_positive_entry_round_trip(cache: LinkCache) -> None:
    cache.set_positive("abc1234", 42, "https://example.com/page")

    lookup = cache.get("abc1234")
    assert lookup.status == "hit"
    assert lookup.url_id == 42
    assert lookup.target_url == "https://example.com/page"


def test_positive_entry_sets_ttl(cache: LinkCache, redis_client) -> None:
    cache.set_positive("abc1234", 1, "https://example.com")

    ttl = redis_client.ttl(cache.key("abc1234"))
    assert 0 < ttl <= 60


def test_negative_entry_round_trip(cache: LinkCache, redis_client) -> None:
    cache.set_negative("missing")

    assert cache.get("missing").status == "negative"
    assert redis_client.get(cache.key("missing")) == MISSING_SENTINEL
    assert 0 < redis_client.ttl(cache.key("missing")) <= 5


def test_unknown_code_is_a_miss(cache: LinkCache) -> None:
    assert cache.get("never-seen").status == "miss"


def test_corrupt_entry_is_treated_as_miss(cache: LinkCache, redis_client) -> None:
    redis_client.set(cache.key("broken"), "not-json")

    assert cache.get("broken").status == "miss"


def test_disabled_cache_never_touches_redis(redis_client) -> None:
    cache = LinkCache(redis_client, 60, 5, enabled=False)

    cache.set_positive("abc1234", 1, "https://example.com")

    assert cache.get("abc1234").status == "disabled"
    assert redis_client.get(cache.key("abc1234")) is None


def test_redis_down_fails_open_on_read() -> None:
    cache = LinkCache(BrokenRedis(), 60, 5)

    assert cache.get("abc1234").status == "error"


def test_redis_down_fails_open_on_write() -> None:
    cache = LinkCache(BrokenRedis(), 60, 5)

    cache.set_positive("abc1234", 1, "https://example.com")
    cache.set_negative("abc1234")
