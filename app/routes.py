"""HTTP routes for the API.

Fixed routes are declared before the catch-all ``GET /{code}`` so that words
such as ``health`` and ``stats`` are never treated as short codes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Url
from app.ratelimit import rate_limit
from app.schemas import HealthResponse, LinkResponse, ShortenRequest
from app.services import CodeGenerationError, create_link, get_link, resolve_link

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]


def _link_response(url: Url, base_url: str) -> LinkResponse:
    """Serialize a link, deriving its public short URL from the configured base."""
    return LinkResponse(
        code=url.code,
        short_url=f"{base_url.rstrip('/')}/{url.code}",
        target_url=url.target_url,
        clicks=url.clicks,
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request, db: DbSession) -> JSONResponse | HealthResponse:
    """Report process, database and Redis reachability.

    Redis is optional for serving traffic, so it is reported as degraded rather
    than failing the whole probe. A database failure is fatal for the API.
    """
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any driver error means the DB is unavailable
        db_ok = False

    redis_ok = False
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            redis_ok = bool(redis_client.ping())
        except Exception:  # noqa: BLE001 - health must never raise
            redis_ok = False

    payload = {
        "status": "ok" if db_ok else "error",
        "db": "ok" if db_ok else "down",
        "redis": "ok" if redis_ok else "down",
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return HealthResponse(**payload)


@router.get("/metrics")
def metrics() -> Response:
    """Expose Prometheus metrics in the standard text exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post(
    "/shorten",
    response_model=LinkResponse,
    status_code=201,
    dependencies=[Depends(rate_limit)],
)
def shorten(request: Request, payload: ShortenRequest, db: DbSession) -> LinkResponse:
    """Create a short link for a validated target URL."""
    try:
        url = create_link(db, payload.target_url)
    except CodeGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _link_response(url, request.app.state.settings.base_url)


@router.get("/stats/{code}", response_model=LinkResponse)
def stats(request: Request, code: str, db: DbSession) -> LinkResponse:
    """Return metadata and aggregate clicks for a code."""
    url = get_link(db, code)
    if url is None:
        raise HTTPException(status_code=404, detail="Unknown short code")
    return _link_response(url, request.app.state.settings.base_url)


@router.get("/{code}")
def redirect(request: Request, code: str, db: DbSession) -> RedirectResponse:
    """Record a click and redirect to the target URL.

    307 preserves the HTTP method semantics; a short link is a permanent
    pointer, so browsers may cache it.
    """
    cache = getattr(request.app.state, "cache", None)
    target_url = resolve_link(db, code, cache)
    if target_url is None:
        raise HTTPException(status_code=404, detail="Unknown short code")
    return RedirectResponse(url=target_url, status_code=307)
