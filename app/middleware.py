"""Request ID, access logging and HTTP metrics middleware."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from app.ratelimit import get_client_ip

logger = structlog.get_logger("app.access")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to every request, response and log line.

    The same pass records the two HTTP metrics. Route templates are used as the
    metric label so arbitrary paths (for example typo'd short codes) cannot
    create unbounded cardinality.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        client_ip = get_client_ip(request)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            self._record_metrics(request, 500, duration)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=round(duration * 1000, 3),
                client_ip=client_ip,
            )
            error_response = JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error", "request_id": request_id},
            )
            error_response.headers["X-Request-ID"] = request_id
            return error_response

        duration = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        self._record_metrics(request, response.status_code, duration)
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration * 1000, 3),
            client_ip=client_ip,
        )
        return response

    @staticmethod
    def _route_template(request: Request) -> str:
        route = request.scope.get("route")
        return getattr(route, "path", "unmatched")

    def _record_metrics(self, request: Request, status_code: int, duration: float) -> None:
        route = self._route_template(request)
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method, route=route, status=str(status_code)
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, route=route).observe(duration)
