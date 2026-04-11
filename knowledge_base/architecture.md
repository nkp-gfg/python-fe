# Architecture

## Tech Stack

| Layer                 | Technology                                      | Notes                                                                                                       |
| --------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **HTTP**              | FastAPI + uvicorn                               | Async handlers, sync Sabre calls via `run_in_threadpool`                                                    |
| **Background tasks**  | Celery 5.4+                                     | Redis broker, `--pool=threads --concurrency=4` (I/O bound)                                                  |
| **Primary datastore** | MongoDB (`falconeye` db)                        | Normalized flight/passenger/change data                                                                     |
| **Audit datastore**   | PostgreSQL (`falcon_eye` db)                    | OTP `flight_xml_current` table, read-only                                                                   |
| **Cache / job store** | Redis                                           | Job status (24h TTL), response cache (30s/300s), SSE pub/sub                                                |
| **SOAP client**       | `requests` + `xmltodict`                        | Synchronous HTTP, hand-crafted XML templates                                                                |
| **Validation**        | Pydantic v2                                     | Request/response models in routes + `sabre/models.py`                                                       |
| **Logging**           | structlog (partial)                             | Adopted in runner, client, most routes; stdlib `logging` in storage, differ, aggregations, postgres, events |
| **Retries**           | tenacity                                        | `@retry` on `SabreClient._post()`: 3 attempts, exponential backoff 2–15s                                    |
| **Frontend**          | Next.js 16 + React 19 + TypeScript              | App Router, Tailwind, shadcn/ui                                                                             |
| **Charts**            | Apache ECharts (primary) + Recharts (secondary) | Custom `<EChart>` wrapper, 12 option builders                                                               |
| **State management**  | React Query v5 (`@tanstack/react-query`)        | No Redux/Zustand                                                                                            |

## Service Topology

```
┌─────────────┐    ┌──────────────┐    ┌─────────┐
│   Next.js   │───▶│   FastAPI    │───▶│  Sabre   │
│  :3000      │    │  :8000       │    │  SOAP    │
└─────────────┘    └──────┬───────┘    └─────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌──────────┐ ┌─────────┐ ┌──────────┐
        │ MongoDB  │ │  Redis  │ │ Postgres │
        │falconeye │ │  :6379  │ │falcon_eye│
        └──────────┘ └────┬────┘ └──────────┘
                          │
                    ┌─────▼─────┐
                    │  Celery   │
                    │  worker   │
                    └───────────┘
```

## FastAPI App (`backend/api/main.py`)

**Lifespan sequence:**

1. Ping MongoDB → share with feeder: `feeder_storage.init_db(db)`
2. Ensure all collection indexes
3. Ping Redis
4. On shutdown: close MongoDB, PostgreSQL pool, Redis

**Middleware:**

- CORS: `allow_origins=["*"]`, `allow_credentials=False`
- Correlation ID: attaches `X-Request-ID` to every request/response

**Exception handlers:**

- `PyMongoError` → 503 (database unavailable)
- Generic `Exception` → 500 with JSON body

**Routers (12):** flights, ingestion, passengers, reservations, changes, activity, schedule, audit, otp, comparison, events, data_audit

**Health endpoints:** `/` (status), `/health/app` (process info), `/health/db` (MongoDB ping)

## Celery (`backend/celery_app.py`)

**Config:** `task_serializer="json"`, `task_acks_late=True`, `worker_prefetch_multiplier=1`, `task_track_started=True`, `result_expires=86400`

**Registered task modules:** `backend.tasks.ingestion`, `backend.tasks.comparison`

**Tasks:**
| Task name | Module | Purpose |
|-----------|--------|---------|
| `falconeye.ingest_batch` | `tasks/ingestion.py` | Batch Sabre ingestion (max_retries=2) |
| `falconeye.compare_flights` | `tasks/comparison.py` | Cross-DB flight comparison (max_retries=1) |
| `falconeye.compare_pnr_cross_db` | `tasks/comparison.py` | PNR mismatch detection (max_retries=1) |

Worker command: `celery -A backend.celery_app worker --loglevel=info --pool=threads --concurrency=4`

## Redis Usage (`backend/api/redis_client.py`)

**Three roles:**

| Role           | Prefix/Channel             | TTL                         |
| -------------- | -------------------------- | --------------------------- |
| Job store      | `job:{id}`                 | 86400s (24h)                |
| Response cache | `cache:{key}`              | 30s (short) / 300s (medium) |
| SSE pub/sub    | `falconeye:events` channel | —                           |

**Job lifecycle:** `create_job` → `set_job_running` → `set_job_progress` (publishes SSE) → `set_ingestion_job_finished` (determines completed/partial/failed from success counts)

## Database Connections

**MongoDB (`backend/api/database.py`):**

- Singleton pattern, lazy init with 10s timeouts
- Feeder receives injected DB reference via `feeder_storage.init_db(db)` — avoids second `MongoClient`
- `_owns_connection` flag prevents closing injected connections

**PostgreSQL (`backend/api/postgres.py`):**

- `psycopg2` threaded connection pool (min=1, max=5)
- `query_all(sql, params)` → `list[dict]` with auto-retry on `OperationalError`
- Read-only: OTP flight schedule data

## Input Validation (`backend/api/validators.py`)

| Validator                   | Rule                  | HTTP error |
| --------------------------- | --------------------- | ---------- |
| `validate_date(v)`          | `YYYY-MM-DD` regex    | 400        |
| `validate_origin(v)`        | 3-letter `[A-Z]` IATA | 400        |
| `validate_flight_number(v)` | 1–5 digits            | 400        |
| `validate_airline(v)`       | 2-char alphanumeric   | 400        |

## Environment Variables

| Variable                                                       | Purpose                                        |
| -------------------------------------------------------------- | ---------------------------------------------- |
| `MONGODB_URI`                                                  | MongoDB connection string                      |
| `REDIS_URL`                                                    | Redis URL (default `redis://127.0.0.1:6379/0`) |
| `POSTGRES_URI`                                                 | PostgreSQL connection for OTP data             |
| `SABRE_BASE_URL`                                               | Sabre SOAP endpoint                            |
| `SABRE_CPAID`, `SABRE_USERNAME`, `SABRE_PASSWORD`              | Sabre authentication                           |
| `SABRE_PSEUDO_CITY_CODE`, `SABRE_ORGANIZATION`, `SABRE_DOMAIN` | Sabre session context                          |
| `SABRE_API_DELAY_SECONDS`                                      | Rate limit between calls (default 0.5s)        |
| `NEXT_PUBLIC_API_URL`                                          | Frontend → backend base URL                    |

## Not Yet Implemented

- Zeep async SOAP client (still using `requests` + `xmltodict`)
- `pydantic-settings` (still using `os.environ`)
- `orjson` / `ORJSONResponse`
- Rate limiting (`slowapi`)
- Prometheus metrics
- `ruff` + `black` + `mypy` linting
- Sabre REST API migration
