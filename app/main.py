"""Application factory and ASGI entrypoint."""

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    The factory form keeps tests isolated: each test can pass its own Settings
    without mutating process-wide state.
    """
    resolved = settings or get_settings()
    app = FastAPI(title="Production-Style URL Shortener", version="0.1.0")
    app.state.settings = resolved
    app.include_router(router)
    return app


app = create_app()
