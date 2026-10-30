"""Unit tests for the fixed-window rate limiter and client IP extraction."""

import fakeredis
import pytest
from starlette.requests import Request

from app.ratelimit import RateLimitExceeded, check_rate_limit, get_client_ip


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


def make_request(headers: dict[str, str] | None = None, client=("10.0.0.1", 1234)) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/shorten",
            "headers": raw_headers,
            "client": client,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_requests_under_limit_are_allowed(redis_client) -> None:
    assert check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60) == 2
    assert check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60) == 1
    assert check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60) == 0


def test_request_over_limit_raises_with_retry_after(redis_client) -> None:
    for _ in range(3):
        check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60)

    with pytest.raises(RateLimitExceeded) as excinfo:
        check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60)

    assert 0 < excinfo.value.retry_after <= 60


def test_window_resets_after_expiry(redis_client) -> None:
    for _ in range(3):
        check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60)
    with pytest.raises(RateLimitExceeded):
        check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60)

    redis_client.flushall()

    assert check_rate_limit(redis_client, "1.2.3.4", limit=3, window_seconds=60) == 2


def test_expiry_is_only_set_on_first_increment(redis_client) -> None:
    check_rate_limit(redis_client, "1.2.3.4", limit=10, window_seconds=60)
    redis_client.expire("ratelimit:1.2.3.4", 30)

    check_rate_limit(redis_client, "1.2.3.4", limit=10, window_seconds=60)

    assert redis_client.ttl("ratelimit:1.2.3.4") <= 30


def test_client_ip_prefers_first_forwarded_hop() -> None:
    request = make_request(headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert get_client_ip(request) == "203.0.113.9"


def test_client_ip_falls_back_to_socket_address() -> None:
    assert get_client_ip(make_request()) == "10.0.0.1"


def test_client_ip_handles_missing_client() -> None:
    assert get_client_ip(make_request(client=None)) == "unknown"


def test_client_ip_ignores_empty_forwarded_header() -> None:
    request = make_request(headers={"X-Forwarded-For": " , 10.0.0.1"})
    assert get_client_ip(request) == "10.0.0.1"
