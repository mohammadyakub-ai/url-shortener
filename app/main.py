"""Application factory and ASGI entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis import Redis

from app.cache import LinkCache
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.middleware import ObservabilityMiddleware
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Close the Redis connection pool on shutdown."""
    yield
    client = getattr(app.state, "redis", None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    The factory form keeps tests isolated: each test can pass its own Settings
    without mutating process-wide state.
    """
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(
        title="Production-Style URL Shortener",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved

    redis_client = Redis.from_url(
        resolved.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    app.state.redis = redis_client
    app.state.cache = LinkCache(
        redis_client,
        ttl_seconds=resolved.cache_ttl_seconds,
        negative_ttl_seconds=resolved.negative_cache_ttl_seconds,
        enabled=resolved.cache_enabled,
    )

    app.add_middleware(ObservabilityMiddleware)
    app.include_router(router)
    return app


app = create_app()
