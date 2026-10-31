# Architecture

The service is a single FastAPI process backed by PostgreSQL (system of record)
and Redis (cache and rate-limit counters). Every request passes through the
observability middleware, which assigns a request ID, logs one JSON access line
and records HTTP metrics.

```mermaid
flowchart LR
    Client --> API[FastAPI / Uvicorn]
    API --> MW[Observability middleware]
    MW --> Routes
    Routes --> Cache[Redis]
    Routes --> DB[(PostgreSQL)]
    Routes --> Metrics[Prometheus /metrics]
```

## Redirect: cache hit

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant R as Redis
    participant D as PostgreSQL
    C->>A: GET /{code}
    A->>R: GET url:{code}
    R-->>A: id + target_url
    A->>D: UPDATE urls SET clicks = clicks + 1 WHERE id = :id
    A-->>C: 307 Location: target_url
```

The cached value carries both the row id and the target URL, so a hit avoids a
Postgres `SELECT` while still allowing the atomic click update. Redis is not the
system of record for clicks.

## Redirect: cache miss

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant R as Redis
    participant D as PostgreSQL
    C->>A: GET /{code}
    A->>R: GET url:{code}
    R-->>A: (nil)
    A->>D: SELECT * FROM urls WHERE code = :code
    alt found
        D-->>A: row
        A->>R: SET url:{code} TTL 3600
        A->>D: UPDATE clicks = clicks + 1
        A-->>C: 307 Location: target_url
    else not found
        A->>R: SET url:{code} = "__missing__" TTL 60
        A-->>C: 404
    end
```

If Redis raises an error at any point, the API logs a warning, skips the cache
and continues against Postgres. Negative caching protects the database from
repeated 404s for the same code.

## Create

```mermaid
flowchart TD
    A[POST /shorten] --> B[Validate target_url]
    B -->|invalid| V[422]
    B --> C[Redis INCR ratelimit:client_ip]
    C -->|over budget| L[429 + Retry-After]
    C -->|allowed| D[Generate 7-char base62 code]
    D --> E[INSERT urls]
    E -->|unique constraint violated| D
    E -->|success| F[201 code, short_url, target_url, clicks]
    E -->|5 collisions| G[500]
```

Code uniqueness is enforced by the database, not by a pre-flight query, because
a check-then-insert would race. Collisions are rare (62^7 keyspace) but handled
correctly.
