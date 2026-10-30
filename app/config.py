"""Environment-driven application configuration.

Settings live in one place so that tests, the Docker image and local development
all read the same variables. Defaults target local development; production should
always supply explicit environment variables.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated configuration loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener"
    redis_url: str = "redis://localhost:6379/0"

    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=3600, ge=1)
    negative_cache_ttl_seconds: int = Field(default=60, ge=1)

    rate_limit_requests: int = Field(default=20, ge=0)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    log_level: str = "INFO"

    @property
    def rate_limit_enabled(self) -> bool:
        """A zero request budget is the supported way to disable limiting."""
        return self.rate_limit_requests > 0


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide Settings instance so env parsing happens once."""
    return Settings()
