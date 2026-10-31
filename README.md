# URL Shortener

A production-style URL shortener built with FastAPI, PostgreSQL, and Redis.

[![CI](https://github.com/mohammadyakub-ai/url-shortener/actions/workflows/ci.yml/badge.svg)](https://github.com/mohammadyakub-ai/url-shortener/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162.0-009688)

## Overview

This service shortens URLs into random seven-character codes and resolves them
with HTTP 307 redirects. PostgreSQL is the system of record; Redis provides
cache-aside reads and fixed-window rate limiting for link creation. Each
redirect increments the click count atomically, and structured logs, request
IDs, and Prometheus metrics expose runtime behavior. The project runs locally
with Docker Compose; it is not deployed.

## Features

- URL shortening with random base62 codes and collision retries
- 307 redirects that preserve HTTP method semantics
- Click statistics via an atomic PostgreSQL increment
- Redis cache-aside with positive and negative TTLs
- Fixed-window rate limiting on `POST /shorten`
- Structured JSON logs with request IDs
- Prometheus metrics with bounded route labels

## Architecture

```mermaid
flowchart LR
    Client[Client] -->|POST /shorten, GET /{code}| API[FastAPI]
    API -->|cache lookup and rate-limit counters| Redis[(Redis)]
    Redis -->|cache miss| API
    API -->|links and atomic click updates| Postgres[(PostgreSQL)]
```

A redirect reads `url:{code}` from Redis. On a hit it skips the Postgres read but
still updates the click count. On a miss it reads Postgres, caches the row, and
redirects. Unknown codes are negatively cached for 60 seconds. Redis errors fall
back to Postgres and fail open. See [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000`. The container waits for
PostgreSQL and Redis health, applies Alembic migrations, then starts Uvicorn.

```bash
curl -s -X POST http://localhost:8000/shorten \
  -H 'Content-Type: application/json' \
  -d '{"target_url": "https://example.com/some/page"}'
# {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com/some/page","clicks":0}

curl -i http://localhost:8000/Ab3xY9z
# HTTP/1.1 307 Temporary Redirect
# location: https://example.com/some/page
```

## API reference

| Method | Path | Description | Example response |
|---|---|---|---|
| GET | `/health` | Database and Redis health | `{"status":"ok","db":"ok","redis":"ok"}` |
| GET | `/metrics` | Prometheus exposition | Prometheus text |
| POST | `/shorten` | Create a link; 422 invalid URL, 429 rate limited | `201 {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com","clicks":0}` |
| GET | `/stats/{code}` | Link metadata and clicks; 404 unknown | `200 {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com","clicks":1}` |
| GET | `/{code}` | Record a click and redirect; 404 unknown | `307 Location: https://example.com` |

`short_url` is `BASE_URL` + `/` + `code`. Every response includes an
`X-Request-ID` header; an inbound value is echoed, otherwise a UUID4 is generated.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener` | SQLAlchemy connection URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for cache and rate limits |
| `BASE_URL` | `http://localhost:8000` | Public base used to build `short_url` |
| `CACHE_ENABLED` | `true` | Enable redirect caching |
| `CACHE_TTL_SECONDS` | `3600` | Positive cache TTL |
| `NEGATIVE_CACHE_TTL_SECONDS` | `60` | Unknown-code cache TTL |
| `RATE_LIMIT_REQUESTS` | `20` | Requests per IP per window; `0` disables limiting |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length |
| `LOG_LEVEL` | `INFO` | Python and structlog level |
| `API_PORT` | `8000` | Compose host port for the API |
| `POSTGRES_PORT` | `5432` | Compose host port for PostgreSQL |
| `REDIS_PORT` | `6379` | Compose host port for Redis |
| `POSTGRES_USER` | `postgres` | Compose database user |
| `POSTGRES_PASSWORD` | `postgres` | Compose database password |
| `POSTGRES_DB` | `urlshortener` | Compose database name |

The rate limiter uses the first `X-Forwarded-For` hop when present, otherwise the
socket address. No `.env` file or secret is tracked.

## Testing

```bash
make test              # unit + functional: SQLite + fakeredis
make cov               # unit + functional with the 85% coverage gate
make test-integration  # real PostgreSQL + Redis via Docker Compose
make lint              # ruff check and format check
```

Unit tests cover code generation, cache helpers, and the rate limiter.
Functional tests exercise every endpoint through `TestClient`. Integration tests
run migrations against an empty PostgreSQL database and cover create/redirect/
stats, collisions, and 50 concurrent redirects.

## Results

Current local results:

| Metric | Result |
|---|---|
| Unit + functional tests | 56 passed (7 integration deselected) |
| Integration tests | 7 passed |
| Coverage | 94.39% total, branch coverage enabled (gate 85%) |
| Lint | `ruff check` and `ruff format --check` clean |

Load test, 100 users, 10 users/s ramp, 60 seconds, 90% redirects / 10% creates:

| Run | Requests/sec | Median | p95 | p99 | Failure rate |
|---|---:|---:|---:|---:|---:|
| Cache enabled | 191.82 | 170 ms | 330 ms | 420 ms | 0.00% |
| Cache disabled | 170.26 | 230 ms | 420 ms | 520 ms | 0.00% |

Totals: 11,490 requests with cache enabled and 10,189 without; zero failures.
Conditions: PostgreSQL 16.2 and Redis 6.0.16, API and Locust on the same
16-logical-CPU local machine, rate limiting disabled. Docker pins Redis 7; the
load test used the locally available Redis 6.0.16. These are single-host,
one-run-per-variant numbers, so treat them as directional rather than capacity
planning. Full commands and interpretation are in [docs/load-test.md](docs/load-test.md).

## Design decisions and limitations

- **Atomic SQL click counter.** `clicks = clicks + 1` avoids lost updates, but
  every redirect still performs one PostgreSQL write.
- **Fail-open Redis.** Cache and rate-limiter errors fall back to the database
  and allow requests, favoring availability over strict throttling.
- **Fixed-window rate limiter.** It uses two Redis commands and can allow up to
  2x the configured limit across a window boundary.
- **Cache-aside reads.** Positive entries live for one hour and negative entries
  for 60 seconds; a cache hit still writes the click count to Postgres.
- **No deployment setup.** The repository runs locally with Docker Compose and
  includes no cloud infrastructure.
- **Not implemented:** authentication, custom aliases, link expiry, deletion,
  and batched click writes. The per-redirect write is the next scalability
  bottleneck; batching is discussed in [docs/decisions.md](docs/decisions.md).

## Project structure

```
app/            FastAPI routes, services, cache, limiter, logging, metrics
migrations/     Alembic environment and the urls migration
tests/          unit, functional, and integration tests
load/           Locust workload
docs/           architecture, decisions, and load-test details
.github/        GitHub Actions CI workflow
docker-compose.yml
Dockerfile
Makefile
```
