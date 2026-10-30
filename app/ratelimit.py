"""Redis fixed-window rate limiting for link creation.

A fixed window is intentionally simple: one INCR plus one EXPIRE per window,
which is cheap and easy to explain. Its known weakness is that a client can
burst up to 2x the limit around a window boundary.
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from app.metrics import RATE_LIMITED_REQUESTS_TOTAL

logger = structlog.get_logger(__name__)


class RateLimitExceeded(Exception):
    """Raised with the number of seconds the caller should wait."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after = retry_after


def get_client_ip(request: Request) -> str:
    """Prefer the first X-Forwarded-For hop, falling back to the socket address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    return request.client.host if request.client is not None else "unknown"


def check_rate_limit(client, client_ip: str, limit: int, window_seconds: int) -> int:
    """Increment the fixed-window counter and return the remaining budget.

    ``EXPIRE`` is only issued when the counter is created, so the window does
    not slide forward on every request.
    """
    key = f"ratelimit:{client_ip}"
    current = client.incr(key)
    if current == 1:
        client.expire(key, window_seconds)
    if current > limit:
        ttl = client.ttl(key)
        retry_after = ttl if isinstance(ttl, int) and ttl > 0 else window_seconds
        raise RateLimitExceeded(retry_after)
    return limit - current


def rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing the creation rate limit.

    Redis outages fail open: availability of link creation is more important
    than strict throttling during a cache incident.
    """
    settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return

    client = getattr(request.app.state, "redis", None)
    if client is None:
        return

    client_ip = get_client_ip(request)
    try:
        check_rate_limit(
            client,
            client_ip,
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
        )
    except RateLimitExceeded as exc:
        RATE_LIMITED_REQUESTS_TOTAL.inc()
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except RedisError:
        logger.warning("rate_limit_unavailable", client_ip=client_ip, exc_info=True)
