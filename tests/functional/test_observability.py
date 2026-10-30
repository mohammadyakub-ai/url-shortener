"""Functional tests for request IDs, structured logging and metrics."""

import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.metrics import (
    CACHE_REQUESTS_TOTAL,
    HTTP_REQUESTS_TOTAL,
    RATE_LIMITED_REQUESTS_TOTAL,
    URLS_CREATED_TOTAL,
)
from app.middleware import ObservabilityMiddleware
from tests.conftest import build_app, make_settings


def counter_value(counter, **labels: str) -> float:
    metric = counter.labels(**labels) if labels else counter
    return metric._value.get()


def test_metrics_endpoint_exposes_all_expected_metric_names(client: TestClient) -> None:
    code = client.post("/shorten", json={"target_url": "https://example.com/metrics"}).json()[
        "code"
    ]
    client.get(f"/{code}", follow_redirects=False)
    client.get("/unknown", follow_redirects=False)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "cache_requests_total",
        "rate_limited_requests_total",
        "urls_created_total",
    ):
        assert name in body


def test_http_and_created_counters_move_with_traffic(client: TestClient) -> None:
    created_before = counter_value(URLS_CREATED_TOTAL)
    http_before = counter_value(HTTP_REQUESTS_TOTAL, method="POST", route="/shorten", status="201")

    response = client.post("/shorten", json={"target_url": "https://example.com/counters"})

    assert response.status_code == 201
    assert counter_value(URLS_CREATED_TOTAL) == created_before + 1
    assert (
        counter_value(HTTP_REQUESTS_TOTAL, method="POST", route="/shorten", status="201")
        == http_before + 1
    )


def test_cache_miss_then_hit_counters_move(client: TestClient) -> None:
    code = client.post("/shorten", json={"target_url": "https://example.com/cachehit"}).json()[
        "code"
    ]

    misses_before = counter_value(CACHE_REQUESTS_TOTAL, result="miss")
    hits_before = counter_value(CACHE_REQUESTS_TOTAL, result="hit")

    client.get(f"/{code}", follow_redirects=False)
    client.get(f"/{code}", follow_redirects=False)

    assert counter_value(CACHE_REQUESTS_TOTAL, result="miss") == misses_before + 1
    assert counter_value(CACHE_REQUESTS_TOTAL, result="hit") == hits_before + 1


def test_rate_limited_counter_moves(session_factory, fake_redis) -> None:
    settings = make_settings(rate_limit_requests=1)
    app = build_app(settings, session_factory, fake_redis)
    before = counter_value(RATE_LIMITED_REQUESTS_TOTAL)

    with TestClient(app) as test_client:
        test_client.post("/shorten", json={"target_url": "https://example.com"})
        response = test_client.post("/shorten", json={"target_url": "https://example.com"})

    assert response.status_code == 429
    assert counter_value(RATE_LIMITED_REQUESTS_TOTAL) == before + 1


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "caller-supplied-id"})

    assert response.headers["x-request-id"] == "caller-supplied-id"


def test_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health")

    request_id = response.headers["x-request-id"]
    uuid.UUID(request_id)


def test_unhandled_error_returns_request_id_header() -> None:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom", headers={"X-Request-ID": "error-request-id"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "error-request-id"
    assert response.json()["request_id"] == "error-request-id"


def test_access_log_is_json_and_includes_request_id(session_factory, fake_redis, capsys) -> None:
    app = build_app(make_settings(log_level="INFO"), session_factory, fake_redis)

    with TestClient(app) as test_client:
        test_client.get("/health", headers={"X-Request-ID": "log-correlation-id"})

    output = capsys.readouterr().out
    entries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    completed = [entry for entry in entries if entry.get("event") == "request_completed"]

    assert completed
    entry = completed[-1]
    assert entry["request_id"] == "log-correlation-id"
    assert entry["method"] == "GET"
    assert entry["path"] == "/health"
    assert entry["status"] == 200
    assert "duration_ms" in entry
    assert "client_ip" in entry
