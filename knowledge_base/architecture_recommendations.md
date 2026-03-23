# Architecture & Migration Recommendations

## Current State (as of 2026-03-21)

| Layer       | Current                                  | Recommended                                 |
| ----------- | ---------------------------------------- | ------------------------------------------- |
| HTTP Client | `requests` (sync)                        | `httpx.AsyncClient` (async)                 |
| XML Parsing | `xmltodict` + hand-crafted XML templates | Zeep `AsyncClient` for SOAP                 |
| Config      | `python-dotenv` + `os.environ`           | `pydantic-settings` (`BaseSettings`)        |
| Models      | Pydantic only in `ingestion.py`          | Pydantic v2 everywhere                      |
| JSON        | default FastAPI encoder                  | `orjson` via `ORJSONResponse`               |
| Logging     | stdlib `logging`                         | `structlog` (structured)                    |
| Linting     | none configured                          | `ruff` + `black` + `mypy`                   |
| Retries     | none                                     | `tenacity` with exponential backoff         |
| Type Hints  | partial                                  | full (`from __future__ import annotations`) |

## Migration Priority

### Phase 1 — Foundation (no Sabre changes)

1. Switch to `pydantic-settings` for config management
2. Add `structlog` for structured logging
3. Add `orjson` for faster JSON responses
4. Add `from __future__ import annotations` everywhere
5. Add Pydantic response models to all API endpoints
6. Pin exact dependency versions

### Phase 2 — Async HTTP + Zeep

1. Replace `requests` with `httpx.AsyncClient`
2. Install `zeep[async]` with `AsyncTransport`
3. Create Zeep client in FastAPI lifespan (single instance)
4. Migrate `SabreClient` methods one-by-one:
   - `create_session()` / `close_session()`
   - `get_flight_status()` → ACS_FlightDetailRQ
   - `get_passenger_list()` → GetPassengerListRQ
   - `get_reservations()` → Trip_SearchRQ
   - `get_passenger_data()` → GetPassengerDataRQ
   - `get_trip_report()` → Trip_ReportsRQ
5. Remove `templates.py` (Zeep builds XML from WSDL)
6. Use `serialize_object()` instead of `xmltodict.parse()`

### Phase 3 — Production Hardening

1. Add `tenacity` retries with backoff + jitter
2. Add rate limiting (`slowapi`)
3. Add Prometheus metrics for Sabre call latency
4. Replace in-memory job store with Redis
5. Add `ruff` + `black` + `mypy` in pre-commit
6. Integration tests against Sabre cert environment

### Phase 4 — REST API Migration (where available)

1. Evaluate Sabre REST APIs at https://developer.sabre.com
2. Use `httpx.AsyncClient` + Pydantic directly (no Zeep)
3. OAuth 2.0 authentication flow
4. Keep Zeep only for SOAP-only services

## Zeep Client Pattern

```python
from zeep import AsyncClient, Settings
from zeep.transports import AsyncTransport
from zeep.cache import SqliteCache
import httpx

settings = Settings(strict=False, xml_huge_tree=True)

transport = AsyncTransport(
    client=httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=20)),
    cache=SqliteCache(path="zeep_cache.db", timeout=3600)
)

client = AsyncClient(
    wsdl="path/to/wsdl",
    transport=transport,
    settings=settings
)
```

## FastAPI Lifespan Pattern

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sabre_client = await create_sabre_client()
    yield
    await app.state.sabre_client.transport.client.aclose()
```

## Error Handling Pattern

```python
from zeep.exceptions import Fault, TransportError

try:
    result = await client.service.Method(...)
except Fault as e:
    raise SabreAPIError(f"SOAP Fault: {e.message}") from e
except TransportError as e:
    raise SabreAPIError(f"Network error: {e}") from e
```

## Response Transformation Pattern

```python
from zeep.helpers import serialize_object

raw_dict = serialize_object(response)
cleaned = SabreFlightResponse.model_validate(raw_dict)
```

## Sabre REST API (Flight Refresh example)

The Flight Refresh API (`/v1/offers/flightRefresh`) is a REST endpoint using OAuth 2.0.
OpenAPI spec available at: spec.yml (local copy in Downloads).
Use `httpx.AsyncClient` + Pydantic models directly — no Zeep needed.

## Key Principles

1. **Never create a Zeep client per request** — create once in lifespan
2. **Cache WSDLs** with `SqliteCache` (they don't change often)
3. **Never log raw SOAP XML with credentials** — sanitize first
4. **Use `serialize_object()`** to convert Zeep responses to dicts
5. **Pydantic models on every endpoint** for automatic validation + OpenAPI docs
6. **Retries with jitter** — Sabre is flaky, use tenacity
7. **Never commit credentials** — use pydantic-settings with .env
