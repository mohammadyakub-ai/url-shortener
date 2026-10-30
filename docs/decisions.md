# Decision log

Short ADR-style notes for choices that are easy to question in review.

## 307 vs 302 redirect

**Decision:** use HTTP 307.

**Why:** a short link is a permanent pointer, and 307 forbids clients from
changing the method on redirect. The target is fetched with the same method the
caller used, which is the least surprising semantic for an API-style shortener.
The trade-off is that some clients cache 307 responses; that is acceptable
because links are not editable in this scope.

## Atomic SQL increment vs read-modify-write

**Decision:** `UPDATE urls SET clicks = clicks + 1 WHERE id = :id`.

**Why:** read-modify-write in Python (`row.clicks += 1; commit()`) loses updates
when two requests read the same value concurrently. The SQL form is evaluated
inside the database and is safe under concurrency. The integration suite fires
50 parallel redirects and asserts the final count is exactly 50.

**Trade-off:** every redirect performs a database write even when the target URL
comes from cache.

## Cache-aside vs write-through, and TTL

**Decision:** cache-aside on `GET /{code}`, positive TTL 3600 s (configurable),
negative TTL 60 s.

**Why:** links are read far more than they are created, so populating the cache
on read keeps creation simple and avoids caching links that are never opened.
Cache-aside also means a Redis outage degrades to database reads rather than
blocking writes. A one-hour positive TTL keeps popular links hot without
holding stale data for too long (links are immutable in this scope). The short
negative TTL absorbs repeated 404s without keeping a code "missing" for long.

**Trade-off:** the first read after expiry always pays a database round-trip,
and a cache miss after expiry is a small thundering-herd risk.

## Negative caching

**Decision:** store a `__missing__` sentinel for unknown codes for 60 s.

**Why:** a 404 flood with random codes would otherwise hit Postgres on every
request. The sentinel is distinguishable from a real cached value, and the
short TTL limits the window in which a newly created code could be masked.

## Fixed-window vs sliding-window rate limiting

**Decision:** Redis `INCR` + `EXPIRE`, fixed window, keyed by client IP.

**Why:** two commands per request, trivial to reason about, and sufficient for
abuse prevention at this scale. Sliding windows or token buckets need more
state or Lua scripts for marginal accuracy.

**Trade-off:** a client can make up to 2x the configured limit across a window
boundary (for example, the full budget at the end of one window and again at
the start of the next). This is documented rather than hidden.

## Fail open when Redis is down

**Decision:** cache and rate limiter both fail open.

**Why:** Redis is an optimization, not the system of record. During a Redis
incident the service should keep creating links and redirecting, even if it is
slower and temporarily less protected against abuse. Failures are logged as
warnings and counted as `cache_requests_total{result="error"}`.

**Trade-off:** an attacker could exploit a Redis outage to bypass rate limits.

## Random 7-character base62 codes vs counter encoding

**Decision:** random codes from `secrets.choice`, alphabet `[A-Za-z0-9]`,
length 7.

**Why:** the keyspace is 62^7 ≈ 3.5 trillion, so collisions are negligible at
this scale, and random codes are unguessable, unlike sequential counters. The
code does not leak creation order or volume. `secrets` is used instead of
`random` because codes are public identifiers that should not be predictable.

**Trade-off:** collisions are possible, so inserts rely on the unique
constraint. On `IntegrityError` the transaction rolls back and retries up to
five times; after that the API returns 500. Reserved route words are filtered
during generation.

## Every redirect still writes to the database

**Decision:** keep the synchronous atomic click update in this scope.

**Why:** it makes `GET /stats/{code}` immediately consistent and keeps the data
model simple.

**Known limitation:** the redirect path is write-bound. Under heavy traffic the
database becomes the bottleneck even with a warm cache.

**Possible improvement:** buffer per-code increments in Redis (for example
`HINCRBY clicks:{code}`) and flush batches to Postgres periodically, with a
reconciliation job. That trades exact real-time stats for much higher redirect
throughput.

## Route templates as metric labels

**Decision:** label HTTP metrics with the matched route template
(`/{code}`), not the raw path.

**Why:** raw paths would create one label value per short code and unbounded
Prometheus cardinality. Unmatched requests share the label `unmatched`.

## Load-test environment deviation

The load test was executed with locally available PostgreSQL 16.2 and Redis
6.0.16, while the Docker environment pins Redis 7. Both use the same commands
exercised here (`GET`, `SET`, `INCR`, `EXPIRE`), so the comparison is still
representative, but it is recorded rather than presented as a Redis 7 result.
