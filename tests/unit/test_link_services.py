"""Unit tests for link creation, lookup and click counting."""

import pytest
from sqlalchemy.orm import Session

from app import services
from app.models import Url


def test_create_link_persists_and_returns_row(db_session: Session) -> None:
    url = services.create_link(db_session, "https://example.com/page")

    assert url.id is not None
    assert url.target_url == "https://example.com/page"
    assert url.clicks == 0
    assert services.get_link(db_session, url.code) is not None


def test_create_link_retries_after_collision(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Url(code="dupdup1", target_url="https://first.example"))
    db_session.commit()

    calls = iter(["dupdup1", "dupdup1", "newcode"])
    monkeypatch.setattr(services, "generate_code", lambda: next(calls))

    url = services.create_link(db_session, "https://second.example")

    assert url.code == "newcode"
    assert services.get_link(db_session, "newcode") is not None


def test_create_link_raises_after_exhausting_retries(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Url(code="fixed12", target_url="https://first.example"))
    db_session.commit()

    attempts = 0

    def always_collide() -> str:
        nonlocal attempts
        attempts += 1
        return "fixed12"

    monkeypatch.setattr(services, "generate_code", always_collide)

    with pytest.raises(services.CodeGenerationError):
        services.create_link(db_session, "https://second.example")

    assert attempts == services.MAX_CREATE_ATTEMPTS


def test_increment_clicks_is_additive(db_session: Session) -> None:
    url = services.create_link(db_session, "https://example.com")

    services.increment_clicks(db_session, url.id)
    services.increment_clicks(db_session, url.id)

    refreshed = services.get_link(db_session, url.code)
    assert refreshed is not None
    assert refreshed.clicks == 2


def test_resolve_link_returns_target_and_counts_click(db_session: Session) -> None:
    url = services.create_link(db_session, "https://example.com/target")

    assert services.resolve_link(db_session, url.code) == "https://example.com/target"

    refreshed = services.get_link(db_session, url.code)
    assert refreshed is not None
    assert refreshed.clicks == 1


def test_resolve_link_unknown_code_returns_none(db_session: Session) -> None:
    assert services.resolve_link(db_session, "missing") is None
