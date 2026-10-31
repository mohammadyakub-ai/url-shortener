<div align="center">

# URL Shortener

**A production-style URL shortener — cache-aside reads, atomic click counts, rate limiting, and real observability.**

[![CI](https://github.com/mohammadyakub-ai/url-shortener/actions/workflows/ci.yml/badge.svg)](https://github.com/mohammadyakub-ai/url-shortener/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162.0-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Cache-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

<p>
  <a href="#-why-this-project">Why</a> •
  <a href="#-features">Features</a> •
  <a href="#-installation">Installation</a> •
  <a href="#-usage">Usage</a> •
  <a href="#-testing">Testing</a> •
  <a href="#-results">Results</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-contributing">Contributing</a>
</p>

</div>

---

## 🧐 Why This Project?

A URL shortener looks trivial until you treat it like a service. Redirects are the hot path, every click is a write, link creation can be abused, and when something breaks you need to know *which request* broke it.

This project builds the small version of that service properly: **PostgreSQL as the system of record, Redis for cache-aside reads and rate limiting, atomic click counting, and structured logs, request IDs, and Prometheus metrics from day one.** It runs locally with Docker Compose and is **not deployed** — the goal is a service whose trade-offs are explicit and measured, not a hosted demo.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔗 **Short Codes** | Random seven-character base62 codes with collision retries |
| ↪️ **307 Redirects** | Preserves HTTP method semantics |
| 📈 **Click Statistics** | Atomic PostgreSQL increment — no lost updates |
| ⚡ **Cache-Aside Reads** | Redis with positive and negative TTLs |
| 🚦 **Rate Limiting** | Fixed-window limiter on `POST /shorten` |
| 🪵 **Structured Logs** | JSON logs with a request ID on every response |
| 📊 **Prometheus Metrics** | Bounded route labels, so cardinality stays under control |
| 🛟 **Fail-Open Redis** | Cache or limiter errors fall back to PostgreSQL instead of failing requests |

---

## 📋 Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker + Docker Compose | recent | Runs the API, PostgreSQL, and Redis |
| Python | 3.12 | Only for running the test suite and linter outside Docker |
| Make | any | Wraps the test and lint commands |

---

## ⚙️ Installation

```bash
# 1. Clone the repository
git clone https://github.com/mohammadyakub-ai/url-shortener.git
cd url-shortener

# 2. Configure the environment
cp .env.example .env

# 3. Start everything
docker compose up --build
```

The API is available at `http://localhost:8000`. The container waits for PostgreSQL and Redis health, applies Alembic migrations, then starts Uvicorn.

`.env` is git-ignored; no secret is tracked. Supported variables:

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

The rate limiter uses the first `X-Forwarded-For` hop when present, otherwise the socket address.

---

## 🚀 Usage

Create a link, then follow it:

```bash
curl -s -X POST http://localhost:8000/shorten \
  -H 'Content-Type: application/json' \
  -d '{"target_url": "https://example.com/some/page"}'
# {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com/some/page","clicks":0}

curl -i http://localhost:8000/Ab3xY9z
# HTTP/1.1 307 Temporary Redirect
# location: https://example.com/some/page
```

### API Reference

| Method | Path | Description | Example response |
|---|---|---|---|
| GET | `/health` | Database and Redis health | `{"status":"ok","db":"ok","redis":"ok"}` |
| GET | `/metrics` | Prometheus exposition | Prometheus text |
| POST | `/shorten` | Create a link; 422 invalid URL, 429 rate limited | `201 {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com","clicks":0}` |
| GET | `/stats/{code}` | Link metadata and clicks; 404 unknown | `200 {"code":"Ab3xY9z","short_url":"http://localhost:8000/Ab3xY9z","target_url":"https://example.com","clicks":1}` |
| GET | `/{code}` | Record a click and redirect; 404 unknown | `307 Location: https://example.com` |

`short_url` is `BASE_URL` + `/` + `code`. Every response includes an `X-Request-ID` header; an inbound value is echoed, otherwise a UUID4 is generated.

---

## 🧪 Testing

```bash
make test              # unit + functional: SQLite + fakeredis
make cov               # unit + functional with the 85% coverage gate
make test-integration  # real PostgreSQL + Redis via Docker Compose
make lint              # ruff check and format check
```

- **Unit tests** — code generation, cache helpers, and the rate limiter
- **Functional tests** — every endpoint through `TestClient`
- **Integration tests** — migrations against an empty PostgreSQL database; create/redirect/stats, collisions, and 50 concurrent redirects

---

## 📊 Results

Current local results:

| Metric | Result |
|---|---|
| Unit + functional tests | 56 passed (7 integration deselected) |
| Integration tests | 7 passed |
| Coverage | 94.39% total, branch coverage enabled (gate 85%) |
| Lint | `ruff check` and `ruff format --check` clean |

**Load test** — 100 users, 10 users/s ramp, 60 seconds, 90% redirects / 10% creates:

| Run | Requests/sec | Median | p95 | p99 | Failure rate |
|---|---:|---:|---:|---:|---:|
| Cache enabled | 191.82 | 170 ms | 330 ms | 420 ms | 0.00% |
| Cache disabled | 170.26 | 230 ms | 420 ms | 520 ms | 0.00% |

Totals: 11,490 requests with cache enabled and 10,189 without; zero failures.

> **Read these as directional, not as capacity planning.** PostgreSQL 16.2 and Redis 6.0.16, with the API and Locust on the same 16-logical-CPU local machine, rate limiting disabled. Docker pins Redis 7; the load test used the locally available Redis 6.0.16. One run per variant. Full commands and interpretation: [docs/load-test.md](docs/load-test.md).

---


## 🏗️ Architecture

### System Flow

```mermaid
flowchart LR
    Client["Client"] -->|"POST /shorten"| API["FastAPI"]
    Client -->|"GET short code"| API

    API -->|"Cache lookup"| Redis[("Redis")]
    Redis -->|"Cache hit or miss"| API

    API -->|"Read URL on cache miss"| Postgres[("PostgreSQL")]
    API -->|"Atomic click increment"| Postgres
    API -->|"Rate-limit counters"| Redis

    API -->|"307 Redirect"| Client
```

A redirect checks Redis first. On a cache hit, it skips the PostgreSQL read but still updates the click count. On a cache miss, it reads PostgreSQL, caches the result, and redirects. Unknown codes are negatively cached for 60 seconds. Redis errors fall back to PostgreSQL.


### Project Structure

```text
.
├── app/                 # FastAPI routes, services, cache, limiter, logging, metrics
├── migrations/          # Alembic environment and the urls migration
├── tests/               # Unit, functional, and integration tests
├── load/                # Locust workload
├── docs/                # Architecture, decisions, and load-test details
├── .github/             # GitHub Actions CI workflow
├── docker-compose.yml   # API + PostgreSQL + Redis
├── Dockerfile           # API image
└── Makefile             # test / cov / test-integration / lint
```

---

## 🧠 Design Decisions

| Decision | Rationale |
|---|---|
| **Atomic SQL click counter** | `clicks = clicks + 1` avoids lost updates. The cost: every redirect still performs one PostgreSQL write. |
| **Cache-aside reads** | Positive entries live for one hour, negative entries for 60 seconds. A cache hit skips the Postgres read but still writes the click count. |
| **Fail-open Redis** | Cache and limiter errors fall back to the database and allow requests — availability over strict throttling. |
| **Fixed-window rate limiter** | Simple and cheap, at two Redis commands per check. Trade-off: it can allow up to 2x the configured limit across a window boundary. |
| **307 over 301/302** | Preserves the HTTP method on redirect. |
| **Bounded metric labels** | Route labels are bounded so Prometheus cardinality can't grow with the number of codes. |
| **Request IDs on every response** | An inbound `X-Request-ID` is echoed, otherwise a UUID4 is generated — one ID ties a response to its logs. |

---

## 🚧 Known Limitations

- **No deployment setup.** Runs locally with Docker Compose; no cloud infrastructure is included.
- **Not implemented:** authentication, custom aliases, link expiry, and deletion.
- **Click writes are not batched.** The per-redirect write is the next scalability bottleneck; batching is discussed in [docs/decisions.md](docs/decisions.md).
- **Load-test numbers are single-host and single-run** — see [Results](#-results).

---

## 🤝 Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

1. Fork the repository
2. Create your feature branch: `git checkout -b feature/amazing-feature`
3. Run `make lint` and `make test` before committing
4. Commit and push: `git commit -m "Add amazing feature"` then `git push origin feature/amazing-feature`
5. Open a Pull Request

---

## 👤 Author

**Yakub Mohammad**
GitHub: [@mohammadyakub-ai](https://github.com/mohammadyakub-ai)

Built to explore **the trade-offs behind a small, observable backend service** — caching, rate limiting, and honest load testing.
