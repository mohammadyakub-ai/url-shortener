"""Redis cache-aside helpers for redirect lookups.

Every Redis operation is wrapped so a cache outage degrades performance but
never availability: failures are logged and reported to the caller as an
``error`` status, which makes the service fall back to Postgres.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

MISSING_SENTINEL = "__missing__"

CacheStatus = Literal["hit", "negative", "miss", "error", "disabled"]


@dataclass(frozen=True)
class CacheLookup:
    """Outcome of a cache read.

    ``negative`` means the code is known to be absent and must not hit Postgres.
    ``error`` means Redis failed; callers should fail open to the database.
    """

    status: CacheStatus
    url_id: int | None = None
    target_url: str | None = None


class LinkCache:
    """Small wrapper around the Redis commands the redirect path needs."""

    def __init__(
        self,
        client,
        ttl_seconds: int,
        negative_ttl_seconds: int,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        self.enabled = enabled

    @staticmethod
    def key(code: str) -> str:
        """Namespace cached links by code so they cannot collide with rate limits."""
        return f"url:{code}"

    def get(self, code: str) -> CacheLookup:
        """Read a code from Redis, distinguishing misses, negatives and errors."""
        if not self.enabled:
            return CacheLookup("disabled")
        try:
            raw = self.client.get(self.key(code))
        except RedisError:
            logger.warning("cache_unavailable", operation="get", code=code, exc_info=True)
            return CacheLookup("error")

        if raw is None:
            return CacheLookup("miss")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if raw == MISSING_SENTINEL:
            return CacheLookup("negative")

        try:
            payload = json.loads(raw)
            return CacheLookup(
                "hit",
                url_id=int(payload["id"]),
                target_url=str(payload["target_url"]),
            )
        except (TypeError, ValueError, KeyError):
            logger.warning("cache_corrupt_entry", code=code)
            return CacheLookup("miss")

    def set_positive(self, code: str, url_id: int, target_url: str) -> None:
        """Cache a resolved link for the configured positive TTL."""
        if not self.enabled:
            return
        self._set(
            code,
            json.dumps({"id": url_id, "target_url": target_url}),
            self.ttl_seconds,
        )

    def set_negative(self, code: str) -> None:
        """Cache an unknown code briefly to absorb 404 floods."""
        if not self.enabled:
            return
        self._set(code, MISSING_SENTINEL, self.negative_ttl_seconds)

    def _set(self, code: str, value: str, ttl_seconds: int) -> None:
        try:
            self.client.set(self.key(code), value, ex=ttl_seconds)
        except RedisError:
            logger.warning("cache_unavailable", operation="set", code=code, exc_info=True)
