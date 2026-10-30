# Production-Style URL Shortener

A small URL shortener backend built to demonstrate production fundamentals
rather than feature breadth: a clean FastAPI API, PostgreSQL as the system of
record, Redis cache-aside and rate limiting, atomic click counting, structured
JSON logs, Prometheus metrics, a multi-tier test suite with a coverage gate, CI,
and a measured Locust load test.

<!-- Replace OWNER/REPO with the GitHub repository once pushed. -->
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

## Architecture

```mermaid
flowchart LR
    Client --> API[FastAPI / Uvicorn]
    API --> MW[Request ID + access log + metrics]
    MW --> Routes
    Routes -->|cache-aside| Redis[(Redis 7)]
    Routes -->|SQL| PG[(PostgreSQL 16)]
    Routes -->|/metrics| Prom[Prometheus]
```

A redirect reads `url:{code}` from Redis. On a hit it skips the Postgres
`SELECT` but still performs the atomic `UPDATE clicks = clicks + 1`. On a miss
it reads Postgres, caches the row, then redirects. Unknown codes are negatively
cached for 60 seconds. Redis failures fall back to Postgres (fail open). See
[docs/architecture.md](docs/architecture.md) for sequence diagrams.

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000`; the `api` container waits for
healthy Postgres and Redis and runs `alembic upgrade head` before starting.

```bash
curl -X POST http://localhost:8000/shorten \
  -H 'Content-Type: application/json' \
  -d '{"target_url": "https://example.com/some/page"}'
# {"code":"Ab3xY9z","target_url":"https://example.com/some/page","clicks":0}

curl -i http://localhost:8000/Ab3xY9z      # 307 Location: https://example.com/some/page
curl http://localhost:8000/stats/Ab3xY9z   # {"code":...,"target_url":...,"clicks":1}
curl http://localhost:8000/metrics         # Prometheus text exposition
```

## Local development without Docker

You still need PostgreSQL 16 and Redis 7 running locally; only the API process
is run natively.

```bash
make install                 # creates .venv and installs pinned dev deps
cp .env.example .env         # edit DATABASE_URL / REDIS_URL if needed
make migrate                 # alembic upgrade head
make run                     # uvicorn on :8000
```

## API

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| GET | `/health` | DB and Redis reachability | 200 | 503 if DB is down |
| GET | `/metrics` | Prometheus metrics | 200 | — |
| POST | `/shorten` | Create a short link | 201 | 422 invalid URL, 429 rate limited, 500 code exhaustion |
| GET | `/stats/{code}` | Link metadata and click count | 200 | 404 unknown code |
| GET | `/{code}` | Record a click and redirect | 307 | 404 unknown code |

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/shorten -H 'Content-Type: application/json' \
  -d '{"target_url": "https://example.com"}'
curl http://localhost:8000/stats/<code>
curl -i http://localhost:8000/<code>
curl http://localhost:8000/metrics
```

Every response includes an `X-Request-ID` header (the inbound value is echoed,
otherwise a UUID4 is generated), and every access log line carries the same ID.

## Configuration

All settings come from environment variables; see `.env.example`. No secrets
are committed.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener` | SQLAlchemy URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL used for cache and rate limits |
| `CACHE_ENABLED` | `true` | Disable redirect caching entirely |
| `CACHE_TTL_SECONDS` | `3600` | Positive cache TTL |
| `NEGATIVE_CACHE_TTL_SECONDS` | `60` | Unknown-code cache TTL |
| `RATE_LIMIT_REQUESTS` | `20` | Fixed-window budget per client IP; `0` disables limiting |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length |
| `LOG_LEVEL` | `INFO` | Python/structlog level |

`RATE_LIMIT_REQUESTS=0` is the supported way to disable throttling for load
tests. The limiter uses the first `X-Forwarded-For` hop when present, otherwise
the socket address.

## Tests

```bash
make test              # unit + functional (SQLite + fakeredis); integration excluded
make cov               # same, with coverage and the 85% gate
make test-integration  # real Postgres + Redis via docker compose
make lint              # ruff check + format check
```

- **Unit** (`tests/unit`): code generation, collision retries, cache helpers
  with `fakeredis`, fixed-window limiter, client-IP extraction.
- **Functional** (`tests/functional`): all endpoints through `TestClient`, cache
  hit/miss/negative/disabled, fail-open for cache and limiter, metrics, JSON
  logs and request IDs.
- **Integration** (`tests/integration`, marked `integration`): Alembic migration
  against an empty database, end-to-end create/redirect/stats, unique-constraint
  collisions, and a 50-way concurrent redirect asserting exactly 50 clicks.

## Results (measured)

Last local run on the machine described in [docs/load-test.md](docs/load-test.md):

| Metric | Value |
|---|---|
| Unit + functional tests | 55 passed (7 integration deselected) |
| Integration tests | 7 passed |
| Line coverage | 94% (`pytest --cov=app`, gate 85%) |
| Lint | `ruff check` and `ruff format --check` clean |

Load test, 100 users, 10 users/s ramp, 60 s, 90% redirects / 10% creates:

| Run | Requests/sec | Median | p95 | p99 | Failure rate |
|---|---:|---:|---:|---:|---:|
| Cache enabled | 239.28 | 58 ms | 220 ms | 320 ms | 0.00% |
| Cache disabled | 246.26 | 44 ms | 220 ms | 320 ms | 0.00% |

The cache did not improve throughput in this environment because every redirect
still writes the click count to Postgres; the Redis hop replaces a cheap indexed
`SELECT`. Full commands, hardware details and interpretation are in
[docs/load-test.md](docs/load-test.md).

## Design decisions and trade-offs

- **307 redirect** preserves method semantics and treats links as permanent.
- **Atomic SQL increment** (`clicks = clicks + 1`) avoids lost updates; verified
  by the 50-way concurrency test. The cost is one write per redirect.
- **Cache-aside** on read with a 1-hour positive TTL and 60-second negative TTL.
- **Fixed-window rate limiting** is two Redis commands; its known weakness is a
  burst of up to 2x the limit across a window boundary.
- **Fail open** on Redis errors: availability over strict throttling/caching.
- **Random 7-char base62 codes** from `secrets.choice`: ~3.5 trillion keyspace,
  unguessable, with unique-constraint retries on collision.
- **Bounded metric cardinality** by labelling HTTP metrics with the route
  template, never the raw path.

Full rationale, including the per-redirect write limitation and how to batch
click counts in Redis, is in [docs/decisions.md](docs/decisions.md).

## Repository layout

```
app/            FastAPI app, services, cache, rate limiter, observability
migrations/     Alembic environment and 0001_create_urls
tests/          unit, functional, integration
load/           Locust workload
docs/           architecture, decisions, load-test results
.github/        CI workflow (lint, test, integration, docker build)
```

## Notes on verification

- Unit, functional and integration suites were executed locally; the numbers
  above come from those runs.
- The load test used local PostgreSQL 16.2 and Redis 6.0.16 (the project's
  Docker image pins Redis 7) with API and Locust on the same host.
- The Docker image build and `docker compose up --build` are exercised by the CI
  `docker-build` job; a Docker daemon was not available in the local
  development environment used to write this repository.
