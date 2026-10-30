"""Pydantic request/response schemas.

Validation is deliberately explicit (rather than relying on ``HttpUrl``) so the
stored target URL stays exactly what the caller supplied and the rules are easy
to explain: http/https scheme, a host, no whitespace, at most 2048 characters.
"""

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShortenRequest(BaseModel):
    """Payload accepted by POST /shorten."""

    target_url: str = Field(min_length=1, max_length=2048)

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        """Reject anything that is not an absolute HTTP(S) URL."""
        candidate = value.strip()
        if any(char.isspace() for char in candidate):
            raise ValueError("target_url must not contain whitespace")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("target_url must be a valid http or https URL")
        return candidate


class LinkResponse(BaseModel):
    """Representation of a shortened link returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    target_url: str
    clicks: int


class HealthResponse(BaseModel):
    """Liveness/readiness details for the health endpoint."""

    status: str
    db: str
    redis: str
