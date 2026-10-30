# Project Requirements: Production-Style URL Shortener Service

> Hand this file to a coding agent as the single source of truth. Build the project milestone by milestone (Section 12), run the tests after each milestone, and make one git commit per milestone.

---

## 1. Purpose

Build a small but production-style URL shortener backend. The goal is **not** feature breadth. The goal is to demonstrate engineering fundamentals that an entry-level Software Engineer is expected to have:

- Clean API design and data modelling
- Automated testing (unit, functional, integration) with a coverage gate
- CI that blocks regressions
- Scalability basics: caching, rate limiting, atomic updates, connection handling
- Telemetry: structured logs, metrics, request tracing, load-test results

The owner will present this project in interviews and must be able to **explain every design decision**. Prefer simple, readable code over clever code, and document trade-offs.

## 2. Hard Rules for the Coding Agent

1. **Never invent numbers.** Coverage, test counts, latency, throughput and error rates must come from real runs. If a number has not been measured, write `TBD (run: <command>)` in the docs. No placeholder or example metrics anywhere.
2. Work in the milestones from Section 12. After each: run the full test suite, fix failures at the root cause, then commit.
3. Pin dependency versions in `requirements.txt` (and `requirements-dev.txt`).
4. No secrets in the repo. All configuration comes from environment variables with a committed `.env.example`.
5. Every public function and module has a short docstring explaining *why*, not just *what*.
6. If something in this document is ambiguous, choose the simplest option, and record the choice in `docs/decisions.md`.
7. Do not add features outside Section 3 scope.

## 3. Scope

**In scope**
- Create a short URL for a valid http/https URL
- Redirect from short code to target URL
- Per-link click statistics
- Redis cache-aside for redirect lookups
- Redis-based rate limiting on link creation
- Structured JSON logging with request IDs
- Prometheus metrics endpoint
- Dockerised local environment (API + PostgreSQL + Redis)
- Automated tests with coverage gate, CI pipeline
- Locust load test with documented, real results

**Out of scope** (do not build): user accounts/auth, custom aliases, link expiry, admin UI, frontend, analytics dashboards, Kubernetes, cloud deployment.

## 4. Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI + Uvicorn |
| Validation / settings | Pydantic v2, `pydantic-settings` |
| ORM | SQLAlchemy 2.x (typed `Mapped[]` style) |
| Migrations | Alembic |
| Database | PostgreSQL 16 (driver: `psycopg[binary]` v3); SQLite in-memory for fast unit tests |
| Cache / rate limit | Redis 7 via `redis-py`; `fakeredis` in unit tests |
| Logging | `structlog` (JSON output) |
| Metrics | `prometheus-client` |
| Testing | `pytest`, `pytest-cov`, `httpx` (via FastAPI `TestClient`), `fakeredis` |
| Lint / format | `ruff` (lint + format check) |
| Load testing | Locust |
| Containers | Docker, docker-compose |
| CI | GitHub Actions |

## 5. Repository Structure

```
url-shortener/
├── app/
│   ├── __init__.py
│   ├── main.py            # app factory, lifespan, router wiring, middleware registration
│   ├── config.py          # pydantic-settings Settings (env-driven)
│   ├── database.py        # engine, SessionLocal, Base, get_db dependency
│   ├── models.py          # SQLAlchemy models
│   ├── schemas.py         # Pydantic request/response models
│   ├── routes.py          # API endpoints
│   ├── services.py        # business logic: code generation, create link, resolve link
│   ├── cache.py           # Redis client + cache-aside helpers
│   ├── ratelimit.py       # Redis fixed-window rate limiter (FastAPI dependency)
│   ├── logging_config.py  # structlog setup
│   ├── middleware.py      # request-id + access-log + metrics middleware
│   └── metrics.py         # Prometheus metric definitions
├── migrations/            # Alembic (env.py, versions/0001_create_urls.py)
├── tests/
│   ├── conftest.py
│   ├── unit/              # services, code generation, cache, rate limiter
│   ├── functional/        # API endpoint tests via TestClient
│   └── integration/       # real Postgres + Redis, marked @pytest.mark.integration
├── load/
│   └── locustfile.py
├── docs/
│   ├── architecture.md    # includes a Mermaid diagram
│   ├── decisions.md       # short ADR-style decision log
│   └── load-test.md       # real measured results + exact commands + machine spec
├── .github/workflows/ci.yml
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── Makefile
├── pytest.ini
├── pyproject.toml         # ruff + coverage config
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
└── README.md
```

## 6. Functional Requirements

### 6.1 Endpoints

Fixed routes must be registered **before** the catch-all `GET /{code}` route.

| Method | Path | Behaviour |
|---|---|---|
| GET | `/health` | Returns `{"status":"ok"}`. Also reports `db` and `redis` reachability as `ok`/`down`; overall HTTP 200 if the DB is up, 503 if the DB is down. Redis being down is degraded, not fatal. |
| GET | `/metrics` | Prometheus text exposition. |
| POST | `/shorten` | Body `{"target_url": "<http/https URL>"}`. Returns 201 with `{code, target_url, clicks}`. Rate limited (6.4). |
| GET | `/stats/{code}` | 200 with `{code, target_url, clicks}`; 404 if unknown. |
| GET | `/{code}` | 307 redirect to target and increments click counter; 404 if unknown. |

### 6.2 Validation Rules
- `target_url` must be a valid `http` or `https` URL, max 2048 characters. Anything else returns 422.
- Codes are 7 characters, from `[A-Za-z0-9]`, generated with `secrets.choice` (not `random`).
- Code collisions are handled by relying on the DB `UNIQUE` constraint, catching `IntegrityError`, rolling back, and retrying up to 5 times; after that return 500 with a clear message.
- Reserved words (`health`, `metrics`, `stats`, `docs`, `openapi.json`, `redoc`) must never be issued as codes.

### 6.3 Click Counting
- Increment with a single atomic SQL statement: `UPDATE urls SET clicks = clicks + 1 WHERE id = :id`. Never read-modify-write in Python (race condition). Document why in `docs/decisions.md`.
- A redirect served from cache must still increment the counter.

### 6.4 Caching (cache-aside)
- On `GET /{code}`: look up `url:{code}` in Redis. On a hit, redirect without reading the URL row from Postgres. On a miss, read from Postgres, store in Redis with a TTL (default 3600 s, configurable), then redirect.
- Cache negative results (unknown codes) for a short TTL (default 60 s) to blunt 404 floods.
- If Redis is unavailable, **fail open**: log a warning, fall back to the database, and keep serving requests.
- Count cache hits and misses in metrics.

### 6.5 Rate Limiting
- Fixed-window limit on `POST /shorten`, keyed by client IP (`X-Forwarded-For` first hop if present, otherwise the socket address). Default: 20 requests per 60 s, configurable.
- Implement with Redis `INCR` + `EXPIRE` (set expiry only on the first increment of a window).
- Over the limit: HTTP 429 with a `Retry-After` header and JSON error body.
- If Redis is unavailable: fail open, log a warning.

## 7. Non-Functional Requirements

### 7.1 Observability
- **Logging:** JSON logs through structlog. Every request gets a request ID (accept inbound `X-Request-ID` or generate a UUID4), bind it to the log context, and return it in the `X-Request-ID` response header. Access log fields: `request_id`, `method`, `path`, `status`, `duration_ms`, `client_ip`.
- **Metrics** (`/metrics`):
  - `http_requests_total{method, route, status}` (Counter)
  - `http_request_duration_seconds{method, route}` (Histogram, buckets suited to ms-scale latencies)
  - `cache_requests_total{result="hit|miss|error"}` (Counter)
  - `rate_limited_requests_total` (Counter)
  - `urls_created_total` (Counter)
- Use the **route template** (e.g. `/{code}`) as the `route` label, never the raw path, to avoid unbounded label cardinality.

### 7.2 Configuration
Via `pydantic-settings`, with defaults suitable for local development. Required variables documented in `.env.example`:
`DATABASE_URL`, `REDIS_URL`, `CACHE_TTL_SECONDS`, `NEGATIVE_CACHE_TTL_SECONDS`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`, `LOG_LEVEL`.

### 7.3 Data Model

Table `urls`:

| Column | Type | Notes |
|---|---|---|
| id | integer / bigserial | PK |
| code | varchar(16) | UNIQUE, indexed |
| target_url | varchar(2048) | not null |
| clicks | integer | not null, default 0 |
| created_at | timestamptz | not null, default now (UTC) |

Schema is managed by Alembic (`0001_create_urls`). Unit tests may use `Base.metadata.create_all` on SQLite; the Docker/Postgres path must use Alembic only.

### 7.4 Container Requirements
- `Dockerfile`: `python:3.12-slim`, install deps first for layer caching, run as a **non-root** user, `CMD` runs `alembic upgrade head` then Uvicorn.
- `docker-compose.yml`: services `api`, `db` (postgres:16), `redis` (redis:7); healthchecks on db and redis; `api` waits for healthy dependencies; named volume for Postgres data; ports configurable.
- `docker compose up --build` must give a working API at `http://localhost:8000` with no manual steps.

## 8. Testing Requirements

Target: **line coverage ≥ 85%**, enforced by `--cov-fail-under=85` in CI. Report the real figure in the README after measuring.

**Unit tests** (`tests/unit`, fast, no network):
- Code generator: length, alphabet, uniqueness over many samples, reserved words never produced
- Collision retry logic (mock generator to force a collision; assert retry and the exhausted-retries error path)
- Cache helpers using `fakeredis`: hit, miss, TTL set, negative cache, Redis-down fail-open
- Rate limiter using `fakeredis`: under limit, at limit, over limit (429 + `Retry-After`), window reset, Redis-down fail-open, client-IP extraction

**Functional tests** (`tests/functional`, `TestClient`, SQLite in-memory + fakeredis, DB dependency overridden):
- `/health` (db up; simulated redis down gives degraded, not failure)
- `POST /shorten` valid input; parametrized invalid inputs (empty, not-a-URL, `ftp://`, over 2048 chars, missing field)
- Redirect returns 307 with the right `Location`; unknown code returns 404
- Click counter increments across multiple redirects, including cache-served redirects
- `/stats/{code}` correct and 404 path
- Rate limit returns 429 after N requests
- `X-Request-ID` echoed or generated
- `/metrics` exposes the expected metric names and the counters move after traffic

**Integration tests** (`tests/integration`, `@pytest.mark.integration`, real Postgres + Redis):
- Alembic migration applies cleanly to an empty database
- End-to-end create, redirect, stats against real services
- Concurrency: fire at least 50 parallel redirects at one code and assert the final `clicks` equals exactly the number sent (proves the atomic update)
- Unique-constraint collision path against real Postgres

Default `pytest` run excludes `integration`. Register the marker in `pytest.ini`. `make test-integration` runs them against the compose services.

## 9. CI Requirements (`.github/workflows/ci.yml`)

Triggers: `push` and `pull_request`.

Jobs:
1. **lint**: `ruff check .` and `ruff format --check .`
2. **test**: unit + functional with `--cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=85`; upload the coverage report as an artifact
3. **integration**: GitHub Actions `services:` for `postgres:16` and `redis:7` with health checks; run `alembic upgrade head`, then `pytest -m integration`
4. **docker-build**: `docker build .` succeeds

Any failing job must fail the workflow. Add the CI status badge to the README.

## 10. Load Test Requirements (`load/locustfile.py`)

- User behaviour mix: ~90% redirects of pre-created codes, ~10% `POST /shorten`. Redirect requests use `allow_redirects=False` so the target is not followed.
- Setup creates a pool of codes before the run.
- Document the exact headless commands in the README and `docs/load-test.md`, for example:
  `locust -f load/locustfile.py --headless -u 100 -r 10 -t 60s --host http://localhost:8000 --csv load/results`
- Run it twice: **(a)** with the Redis cache enabled and **(b)** with caching disabled via an env flag (add `CACHE_ENABLED`, default true). Record both in `docs/load-test.md`.
- `docs/load-test.md` must contain: the machine spec, user count/ramp/duration, and a table of **measured** requests/sec, median latency, p95 latency, p99 latency, and failure rate for both runs, plus two or three sentences of honest interpretation. If the agent cannot run the load test itself, write `TBD (run: make loadtest)` and leave the numbers for the owner to fill in.
- Because rate limiting would throttle the load test, support a way to raise or disable the limit via config for the test run. Document it.

## 11. Documentation Requirements

**README.md** must include: one-paragraph description, CI badge, architecture diagram (Mermaid), quick start (`docker compose up --build`), local dev setup without Docker, API reference table with example `curl` commands, configuration table, how to run each test tier, a "Results" section with measured coverage, test count and load-test numbers (or `TBD` markers), and a "Design decisions and trade-offs" section.

**docs/decisions.md** must record at least:
- 307 vs 302 redirect
- Atomic SQL increment vs read-modify-write
- Cache-aside vs write-through, and TTL choice
- Fixed-window vs sliding-window rate limiting (and the burst-at-window-boundary limitation)
- Fail-open when Redis is down
- Random 7-char base62 codes vs counter-based encoding (collision handling, keyspace of 62^7)
- The known limitation that every redirect still does one DB write for the click count, and how it could be improved (e.g. buffer counts in Redis and flush in batches)

**docs/architecture.md:** request flow for a redirect (cache hit path and miss path) and for a create, as a Mermaid diagram plus a short explanation.

**Makefile targets:** `install`, `run`, `test`, `test-integration`, `cov`, `lint`, `format`, `up`, `down`, `migrate`, `loadtest`.

## 12. Milestones

Commit after each, with all tests green.

| # | Milestone | Done when |
|---|---|---|
| M1 | Core API on SQLite: config, models, schemas, services, routes, `/health`, `/shorten`, `/stats`, redirect, atomic click counter, unit + functional tests | Tests pass; coverage reported |
| M2 | PostgreSQL + Alembic + Docker: `Dockerfile`, `docker-compose.yml`, migration `0001`, Postgres-backed run | `docker compose up --build` works; migration applies |
| M3 | Redis: cache-aside, negative caching, rate limiter, fail-open behaviour, `CACHE_ENABLED` flag, tests with `fakeredis` | Cache and rate-limit tests pass |
| M4 | Observability: structlog JSON logs, request IDs, Prometheus metrics, `/metrics`, tests | Metrics move under traffic in tests |
| M5 | Integration tests + CI: real Postgres/Redis tests (incl. 50-way concurrency), GitHub Actions with the 85% coverage gate | CI workflow is valid and all jobs pass |
| M6 | Load test + docs: Locust file, load-test doc with real or `TBD` numbers, README, architecture, decisions | Docs complete, no invented numbers |

## 13. Acceptance Checklist

- [ ] `docker compose up --build` gives a working API with no manual steps
- [ ] All five endpoints behave as in Section 6
- [ ] Atomic click counter verified by the 50-way concurrency integration test
- [ ] Cache hit and miss paths both tested; fail-open verified for cache and rate limiter
- [ ] `pytest --cov-fail-under=85` passes
- [ ] CI runs lint, tests, integration and Docker build, all green
- [ ] `/metrics` exposes all metrics listed in 7.1 with bounded label cardinality
- [ ] Logs are JSON, with a request ID on every line
- [ ] `docs/load-test.md` has measured numbers or explicit `TBD` markers, nothing invented
- [ ] README results section matches reality
- [ ] `.env.example` present; no secrets committed

## 14. Final Output Expected From the Agent

1. The complete repository meeting the above
2. A closing summary containing: what was built per milestone, the actual coverage percentage and test count from the last run, anything that could not be verified (for example, the load test if it could not be run), and any deviations from this document with reasons
