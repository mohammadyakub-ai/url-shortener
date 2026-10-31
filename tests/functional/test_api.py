"""Functional API tests using FastAPI's TestClient and SQLite."""

import pytest
from fastapi.testclient import TestClient


def test_health_reports_database_up(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"


def test_shorten_returns_201_and_link(client: TestClient) -> None:
    response = client.post("/shorten", json={"target_url": "https://example.com/page"})

    assert response.status_code == 201
    body = response.json()
    assert body["target_url"] == "https://example.com/page"
    assert body["short_url"] == f"http://localhost:8000/{body['code']}"
    assert body["clicks"] == 0
    assert len(body["code"]) == 7


@pytest.mark.parametrize(
    "payload",
    [
        {"target_url": ""},
        {"target_url": "not-a-url"},
        {"target_url": "ftp://example.com/file"},
        {"target_url": "https://" + ("a" * 2050)},
        {},
    ],
)
def test_shorten_rejects_invalid_input(client: TestClient, payload: dict) -> None:
    response = client.post("/shorten", json=payload)

    assert response.status_code == 422


def test_redirect_returns_307_with_location(client: TestClient) -> None:
    created = client.post("/shorten", json={"target_url": "https://example.com/target"}).json()

    response = client.get(f"/{created['code']}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/target"


def test_redirect_unknown_code_returns_404(client: TestClient) -> None:
    response = client.get("/nosuchcode", follow_redirects=False)

    assert response.status_code == 404


def test_stats_returns_link_and_404_for_unknown(client: TestClient) -> None:
    created = client.post("/shorten", json={"target_url": "https://example.com/stats"}).json()

    response = client.get(f"/stats/{created['code']}")
    assert response.status_code == 200
    assert response.json()["target_url"] == "https://example.com/stats"
    assert response.json()["short_url"] == f"http://localhost:8000/{created['code']}"

    assert client.get("/stats/unknown").status_code == 404


def test_short_url_uses_configured_base_url(app, client: TestClient) -> None:
    app.state.settings.base_url = "https://sho.rt/"

    created = client.post("/shorten", json={"target_url": "https://example.com/base"}).json()
    expected = f"https://sho.rt/{created['code']}"

    assert created["short_url"] == expected
    assert client.get(f"/stats/{created['code']}").json()["short_url"] == expected


def test_clicks_increment_across_redirects(client: TestClient) -> None:
    created = client.post("/shorten", json={"target_url": "https://example.com/count"}).json()

    for _ in range(3):
        client.get(f"/{created['code']}", follow_redirects=False)

    stats = client.get(f"/stats/{created['code']}").json()
    assert stats["clicks"] == 3
