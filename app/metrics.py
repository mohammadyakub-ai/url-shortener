"""Prometheus metric definitions.

All metrics are module-level singletons so repeated ``create_app`` calls (tests)
reuse the same collectors instead of registering duplicates.
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "route", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "route"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

CACHE_REQUESTS_TOTAL = Counter(
    "cache_requests_total",
    "Redirect cache lookups by outcome.",
    ["result"],
)

RATE_LIMITED_REQUESTS_TOTAL = Counter(
    "rate_limited_requests_total",
    "Requests rejected by the rate limiter.",
)

URLS_CREATED_TOTAL = Counter(
    "urls_created_total",
    "Short links successfully created.",
)
