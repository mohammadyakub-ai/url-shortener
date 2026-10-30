"""Exercise the unique-constraint collision path against real PostgreSQL."""

import pytest

from app import services
from app.models import Url

pytestmark = pytest.mark.integration


def test_collision_retries_then_succeeds(session_factory, monkeypatch) -> None:
    with session_factory() as session:
        session.add(Url(code="dupdup1", target_url="https://first.example"))
        session.commit()

        candidates = iter(["dupdup1", "dupdup1", "newcode"])
        monkeypatch.setattr(services, "generate_code", lambda: next(candidates))

        created = services.create_link(session, "https://second.example")

        assert created.code == "newcode"


def test_collision_raises_after_retry_budget(session_factory, monkeypatch) -> None:
    with session_factory() as session:
        session.add(Url(code="fixed12", target_url="https://first.example"))
        session.commit()

        monkeypatch.setattr(services, "generate_code", lambda: "fixed12")

        with pytest.raises(services.CodeGenerationError):
            services.create_link(session, "https://second.example")
