# FalconEye — AI Agent Guide

**FalconEye** is a Flight Operations Data Platform for Gulf Air, built with FastAPI + Next.js + Celery, integrating real-time data from Sabre GDSs and detecting changes across flight snapshots.

## Architecture Overview

### Services & Dependencies

- **Backend**: Python FastAPI (`backend.api.main:app`)
- **Queue**: Celery (Redis broker, Redis backend)
- **Datastores**: MongoDB (normalized), PostgreSQL (audit), Redis (jobs/cache)
- **Frontend**: Next.js 16.2 (React 19.2.4, TypeScript, Tailwind + shadcn/ui)
- **Dashboard**: Flower (`http://localhost:5555`)

**Bootstrap Dependencies**:
- `fastapi>=0.110` + `uvicorn>=0.29` (async HTTP server)
- `celery>=5.4` with `redis>=5.0` (background tasks)
- `pymongo>=4.6` + `psycopg2-binary>=2.9` (datastores)
- `pydantic>=2.0` (validation/serialization)
- `structlog>=24.1` (structured logging)
- `tenacity>=8.2` (retries)

### Data Flow

```
Sabre SOAP API → SabreClient (backend.sabre.client) 
  ↓
Feeder Runner (backend.feeder.runner) — orchestrates multi-layer ingestion
  ↓
Layer 1: Raw Archive (sabre_requests collection)
  ↓
Layer 2: Snapshots (snapshots collection) — JSON normalized + checksums
  ↓
Layer 3: Change Detection (changes collection) — diffs between snapshots
  ↓
Layer 4: Current State (flights/passengers/reservations) — materialized view
```

## Starting Development

### Prerequisites
- Python 3.11+ (venv at `./venv`)
- Node.js 18+ (node_modules at `./frontend/node_modules`)
- Docker/Docker Compose (for MongoDB, Redis, Flower)

### Quick Start

**Database & Message Queue** (one terminal):
```bash
docker-compose up -d
```

**Backend** (one terminal):
```powershell
.\start-backend.ps1   # Windows — starts uvicorn + Celery worker
# macOS/Linux:
# celery -A backend.celery_app worker --loglevel=info --pool=threads --concurrency=4 &
# uvicorn backend.api.main:app --reload
```

**Frontend** (one terminal):
```bash
cd frontend
npm run dev
```

Then:
- API: http://127.0.0.1:8000
- Frontend: http://127.0.0.1:3000
- Flower (job monitor): http://127.0.0.1:5555

## Code Organization

### Backend Structure

```
backend/
├── api/                  # FastAPI layer (request handlers)
│   ├── main.py          # App setup, lifespan, exception handlers
│   ├── routes/          # 12+ routers (flights, passengers, ingestion, etc.)
│   ├── database.py      # MongoDB singleton
│   ├── postgres.py      # PostgreSQL pool (audit logs)
│   ├── redis_client.py  # Redis job store + cache invalidation
│   └── snapshot_versioning.py  # Query data as-of a snapshot version
├── feeder/              # Data ingestion pipeline
│   ├── runner.py        # Orchestrator (calls SabreClient, detects changes)
│   ├── converter.py     # XML→JSON normalization (5 APIs)
│   ├── differ.py        # Snapshot comparison + delta detection
│   └── storage.py       # MongoDB write layer
├── sabre/               # Sabre GDS integration
│   ├── client.py        # SOAP client (GetPassengerListRQ, etc.)
│   ├── models.py        # Pydantic request/response shapes
│   └── templates.py     # XML request templates
├── tasks/               # Celery task definitions
│   ├── ingestion.py     # Background batch ingestion
│   └── comparison.py    # Background flight comparisons
└── celery_app.py        # Celery app config + broker setup
```

### Frontend Structure

```
frontend/
├── app/                 # Next.js App Router (file-based routing)
│   ├── layout.tsx       # Root layout (fonts, providers, error boundary)
│   ├── flights/         # Flight dashboard pages
│   ├── compare/         # Flight comparison tools
│   ├── data-audit/      # Change audit & versioning UI
│   └── today/           # Scheduled flights for current date
├── components/
│   ├── dashboard/       # Chart/table components (ECharts, Recharts)
│   ├── ui/              # Shadcn/radix primitives
│   ├── error-boundary.tsx
│   └── providers.tsx    # React Query + theme provider setup
├── lib/
│   ├── api.ts           # Fetch utilities + 30+ API methods
│   ├── types.ts         # TypeScript interfaces (mirrors FastAPI)
│   ├── hooks.ts         # React Query hooks (@tanstack/react-query v5.91)
│   └── utils.ts         # UI/formatting utilities
└── components.json      # Shadcn config
```

## Critical Patterns & Conventions

### Logging

**Backend**: Use `structlog` everywhere, configured in `feeder/runner.py`:
```python
import structlog
logger = structlog.get_logger(__name__)
logger.info("event", key="value", flight_number="2057")  # JSON output
```
**NOT** `logging` — structlog is standard for this codebase.

### MongoDB Patterns

- **Shared connection**: The FastAPI app creates one `MongoClient` in `lifespan()` and shares it with feeder worker via `feeder_storage.init_db(db)` — avoids multiple connections causing DNS timeouts.
- **Remove `_id` & `_raw` from responses**: Use `_strip_id(doc)` helper (see `flights.py:43`).
- **Checksums**: Snapshot storage computes MD5 of flight data for change detection — never modify without updating differ logic.

### Celery Tasks

- **Registered in `celery_app.py`'s `include` list** (line 23–26) — new tasks must be added there.
- **Use `bind=True` for retries**: `@app.task(bind=True, max_retries=2)` — allows `self.retry(exc=exc)`.
- **Worker pool**: `--pool=threads` (not prefork) because Sabre calls are I/O-bound and requests aren't thread-safe for multiple workers anyway.
- **Job tracking**: Backend stores job status in Redis with `create_job()`, `set_job_running()`, `set_ingestion_job_finished()`.

### FastAPI Patterns

- **Lifespan setup** (lines 30–58 in `main.py`): Verify MongoDB, Redis, and share DB with feeder layer.
- **Correlation ID middleware** (lines 81–88): Every request/response gets `X-Request-ID` for tracing.
- **Exception handlers**: PyMongoError → 503 (database unavailable), everything else → 500 with JSON.
- **Validators in routes**: Use `validate_date()`, `validate_origin()` from `api/validators.py` before querying.

### Frontend Patterns

- **API calls**: `lib/api.ts` exports ~30 typed fetch functions (e.g., `getFlight()`, `batchIngestFlights()`).
- **Request timeout**: 30 seconds hardcoded; job polling timeout 10 minutes (see lines 22–24).
- **React Query**: No hooks file yet — but `TanStack v5.91` requires `enabled: boolean` in `useQuery()` options; old `skip` pattern won't work.
- **Types mirror FastAPI**: `frontend/lib/types.ts` defines all response interfaces — always keep in sync with `backend/api/routes/*.py` return types.
- **Dark mode**: `next-themes` configured; toggle in `theme-toggle.tsx`.

### Sabre Integration

- **SOAP client**: `backend.sabre.client.SabreClient` (non-async, uses `requests`).
- **XML templates**: Hard-coded in `backend/sabre/templates.py` — change these to adapt to Sabre WSDL updates.
- **Normalization**: Each API response is converted by function in `backend/feeder/converter.py` (e.g., `convert_flight_status()`, `convert_passenger_list()`).
- **Session management**: `SabreClient.create_session()` / `close_session()` — session token stored in client state.

## Testing & Validation

- **Unit tests**: `backend/tests/test_differ.py` (change detection logic).
- **Integration**: Use Bruno REST client (`backend_api_collection/`) for endpoint testing.
- **Validation**: Pydantic models are the source of truth — use `@field_validator` for complex checks.

## Common Development Tasks

### Adding a New API Endpoint

1. Create a route file (or add to existing) in `backend/api/routes/`
2. Define request/response Pydantic models
3. Import and include router in `backend/api/main.py`
4. Add TypeScript types to `frontend/lib/types.ts`
5. Add fetch function to `frontend/lib/api.ts`
6. Use in React component via React Query hook

Example:
```python
# backend/api/routes/new_feature.py
from fastapi import APIRouter
from pydantic import BaseModel
router = APIRouter(prefix="/feature", tags=["feature"])

class FeatureRequest(BaseModel):
    flight_id: str

class FeatureResponse(BaseModel):
    result: str

@router.post("/", response_model=FeatureResponse)
def create_feature(req: FeatureRequest):
    return FeatureResponse(result="ok")
```

### Background Job (Batch Processing)

1. Define task in `backend/tasks/ingestion.py` or `comparison.py`
2. Register in `celery_app.py`'s `include` list
3. Call via `trigger_async_job()` endpoint, store job ID in Redis
4. Frontend polls `/flights/ingest/jobs/{id}` until terminal status

### Modifying Sabre Integration

1. Edit `backend/sabre/templates.py` to change XML request shape
2. Update `backend/sabre/client.py` method signature if needed
3. Update `backend/feeder/converter.py` to handle new response fields
4. Test with sample XML in `sample_xmlfiles/` folder

## Key Files to Know

| File                                    | Purpose |
| --------------------------------------- | -------------------------------------------------------- |
| `backend/celery_app.py`                 | Celery config; update `include` list for new tasks       |
| `backend/feeder/runner.py`              | Data pipeline orchestrator; change here for new ingestion modes |
| `backend/api/database.py`               | MongoDB singleton; don't create multiple connections     |
| `backend/api/routes/flights.py`         | Largest route file; flight queries + caching patterns    |
| `backend/api/routes/ingestion.py`       | Sabre trigger endpoints + job status polling             |
| `frontend/lib/api.ts`                   | Frontend API client; mirrors backend routes              |
| `frontend/lib/types.ts`                 | Type definitions; keep in sync with backend Pydantic     |
| `knowledge_base/architecture_recommendations.md` | Planned migrations (Zeep, structlog adoption, etc.) |

## Environment & Configuration

- `.env` file (not in git):
  - `MONGODB_URI=mongodb+srv://...`
  - `SABRE_*` (credentials for SOAP client)
  - `REDIS_URL=redis://127.0.0.1:6379/0`
  - `DATABASE_URL=postgresql://...` (audit logs)
  - `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` (frontend sees this)

- Secrets: Use environment variables; never hardcode credentials in code.

## Debugging Tips

- **Celery not picking up tasks?** Check `include` list in `celery_app.py` — task module must be imported.
- **MongoDB DNS timeout?** Ensure API shares DB connection with feeder via `feeder_storage.init_db(db)`.
- **Frontend API calls failing?** Check `NEXT_PUBLIC_API_URL` environment variable.
- **Job stuck in "running"?** Redis key didn't expire; manually delete `falconeye:job:{id}` from Redis.
- **Structlog output not appearing?** Check log level — `make_filtering_bound_logger("INFO")` filters below INFO.

