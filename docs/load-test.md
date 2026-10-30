# Load test results

All numbers on this page were measured. Nothing is estimated.

## Workload

`load/locustfile.py` creates a pool of 100 short codes before the run, then
ramps 100 users at 10 users/second for 60 seconds. Each user performs ~90%
redirects (`GET /{code}` with redirects disabled) and ~10% link creation
(`POST /shorten`).

The rate limiter was disabled with `RATE_LIMIT_REQUESTS=0` so that the
redirect path, not a deliberate 429, determines throughput. The limiter is
still enabled by default in every other environment.

## Machine and software

| Item | Value |
|---|---|
| CPU | 12th Gen Intel Core i5-1240P, 16 logical CPUs |
| RAM | 15 GiB |
| Kernel | Linux 6.8.0-138-generic x86_64 |
| Python | 3.12.13 |
| API | Uvicorn 0.54.0, single process, no workers |
| PostgreSQL | 16.2 (local instance) |
| Redis | 6.0.16 (local instance; Docker pins Redis 7) |
| Load generator | Locust 2.47.0, same host as the API |

Because the API, database, Redis and Locust ran on one machine, these numbers
include CPU contention between the load generator and the service. They are a
relative comparison, not a capacity plan for production hardware.

## Commands

Start the API once per run, changing `CACHE_ENABLED`:

```bash
export DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener'
export REDIS_URL='redis://localhost:6379/15'
export RATE_LIMIT_REQUESTS=0
export CACHE_ENABLED=true   # false for the second run
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run in a second shell:

```bash
# (a) cache enabled
python -m locust -f load/locustfile.py --headless -u 100 -r 10 -t 60s \
  --host http://127.0.0.1:8000 --csv /tmp/opencode/load-cache --only-summary

# restart the API with CACHE_ENABLED=false, then:
python -m locust -f load/locustfile.py --headless -u 100 -r 10 -t 60s \
  --host http://127.0.0.1:8000 --csv /tmp/opencode/load-nocache --only-summary
```

`make loadtest` runs the cache-enabled variant and writes CSV files under
`load/results` (gitignored).

## Results

Aggregated across both endpoint tasks for each 60-second run:

| Run | Requests/sec | Median | p95 | p99 | Failure rate |
|---|---:|---:|---:|---:|---:|
| Cache enabled | 239.28 | 58 ms | 220 ms | 320 ms | 0.00% |
| Cache disabled | 246.26 | 44 ms | 220 ms | 320 ms | 0.00% |

Totals: 14,205 requests with cache enabled and 14,752 with cache disabled; zero
failures in both runs.

## Interpretation

Caching did not improve throughput in this run; it was slightly slower
(239 vs 246 req/s) with a higher median (58 vs 44 ms). This is consistent with
the design: every redirect still performs the atomic PostgreSQL click update, so
the cache only removes a cheap indexed `SELECT` while adding a Redis round-trip.
With a small, fully cached working set on local Postgres, that trade is roughly
neutral to negative.

The cache should matter more when the database is under read contention, on a
larger table, or across a network hop. The real scalability win for this
workload is batching click counts in Redis instead of writing on every redirect
(see `docs/decisions.md`). The identical p95/p99 figures across runs are
dominated by `POST /shorten` latency, not the redirect path.
