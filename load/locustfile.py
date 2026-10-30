"""Locust workload: ~90% redirects of a pre-created code pool, ~10% creation.

Run with rate limiting disabled (RATE_LIMIT_REQUESTS=0); otherwise the creation
task is throttled and the redirect response times, not the service, dominate.

Example:
    locust -f load/locustfile.py --headless -u 100 -r 10 -t 60s \
        --host http://localhost:8000 --csv load/results
"""

import random

import requests
from locust import HttpUser, between, events, task

CODE_POOL_SIZE = 100
_codes: list[str] = []


@events.test_start.add_listener
def create_code_pool(environment, **_kwargs) -> None:
    """Populate the pool before load starts so redirects hit existing links."""
    host = environment.host
    if not host:
        raise RuntimeError("--host is required to build the code pool")

    _codes.clear()
    for index in range(CODE_POOL_SIZE):
        response = requests.post(
            f"{host}/shorten",
            json={"target_url": f"https://example.com/load/{index}"},
            timeout=10,
        )
        response.raise_for_status()
        _codes.append(response.json()["code"])


class ShortenerUser(HttpUser):
    """Simulates mostly reads, with a small share of link creation."""

    wait_time = between(0.1, 0.5)

    @task(9)
    def redirect(self) -> None:
        if not _codes:
            return
        code = random.choice(_codes)
        self.client.get(f"/{code}", allow_redirects=False, name="/{code}")

    @task(1)
    def shorten(self) -> None:
        self.client.post(
            "/shorten",
            json={"target_url": "https://example.com/load/created"},
            name="/shorten",
        )
