"""Prove the click counter is atomic under parallel redirects."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app import services
from app.cache import LinkCache

pytestmark = pytest.mark.integration


def test_fifty_parallel_redirects_count_exactly_fifty_clicks(
    session_factory, redis_client, integration_settings
) -> None:
    with session_factory() as setup:
        url = services.create_link(setup, "https://example.com/concurrent")
        code = url.code

    cache = LinkCache(
        redis_client,
        ttl_seconds=integration_settings.cache_ttl_seconds,
        negative_ttl_seconds=integration_settings.negative_cache_ttl_seconds,
        enabled=True,
    )

    def redirect_once() -> str | None:
        session = session_factory()
        try:
            return services.resolve_link(session, code, cache)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(lambda _: redirect_once(), range(50)))

    assert results == ["https://example.com/concurrent"] * 50

    with session_factory() as verification:
        stored = services.get_link(verification, code)
        assert stored is not None
        assert stored.clicks == 50
