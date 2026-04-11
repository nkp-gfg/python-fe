# Ingestion Pipeline

## Endpoints

| Endpoint                       | Method | Mode           | Handler                         |
| ------------------------------ | ------ | -------------- | ------------------------------- |
| `/flights/ingest`              | POST   | Synchronous    | `run_in_threadpool(run_feeder)` |
| `/flights/ingest/batch`        | POST   | Async (Celery) | `run_batch_ingestion.delay()`   |
| `/flights/ingest/jobs/{jobId}` | GET    | Polling        | Redis job status                |

## Request Model (`SabreIngestRequest`)

| Field                  | Type | Validation                                     |
| ---------------------- | ---- | ---------------------------------------------- |
| `airline`              | str  | 2-char (default "GF")                          |
| `flightNumber`         | str  | 1–5 digits                                     |
| `origin`               | str  | 3-letter IATA uppercase                        |
| `departureDate`        | str  | YYYY-MM-DD                                     |
| `departureDateTime`    | str  | ISO datetime                                   |
| `flightSequenceNumber` | str? | Optional, looked up from PostgreSQL if missing |
| `serviceTypeCode`      | str? | Optional                                       |

Batch: `SabreBatchRequest` wraps 1–50 `SabreIngestRequest` items.

## Job Status Model (`SabreJobStatus`)

Statuses: `accepted` → `running` → `completed` | `partial` | `failed`

- **completed**: all flights succeeded
- **partial**: some flights succeeded, some failed
- **failed**: all flights failed or unrecoverable error

Job stored in Redis with 24h TTL. Progress published via SSE (`falconeye:events` channel).

## Pipeline Sequence (`backend/feeder/runner.py`)

For each flight, `run_feeder` executes 8 API calls through a single `SabreClient` session:

```
1. CreateSession
2. GetFlightStatus (ACS_FlightDetailRQ)
3. GetPassengerList ×4 (merged — see below)
4. GetReservations (Trip_SearchRQ, 60s timeout)
5. GetTripReport MLX (cancelled passengers)
6. GetTripReport MLC (ever-booked passengers)
7. GetSchedule (VerifyFlightDetailsLLSRQ)
8. CloseSession
```

### 4-Call Passenger List Merge

The pipeline makes **4 sequential GetPassengerListRQ calls** with different display codes, then merges results with deduplication:

| Call | Display Codes | Purpose                     |
| ---- | ------------- | --------------------------- |
| 1    | `RV`, `XRV`   | Booked passengers           |
| 2    | `BP`, `BT`    | Checked-in passengers       |
| 3    | `NS`, `OFL`   | No-show and offloaded       |
| 4    | `AE`          | All edit codes (enrichment) |

Merge strategy: dedup by `PNR|lastName|passengerId` key. Call 4 (AE) passengers enrich existing records.

### Per-API Processing (`_process_api_call`)

Each API call goes through a 4-layer pipeline:

```
Raw XML → store_raw_request()        [Layer 1: sabre_requests]
       → convert_*()                 [Normalization]
       → store_snapshot()            [Layer 2: snapshots + checksum]
       → detect_changes()            [Layer 3: changes]
       → update_flight_state()       [Layer 4: flights materialized view]
       → store_*() legacy            [Backward-compat collections]
```

### Payload Validation

Before processing, `_validate_live_flight_payload()` rejects payloads where Sabre returns:

- Error code 2566
- "FLIGHT NOT INITIALIZED" messages

Rejected flights skip all remaining API calls. Raises `IngestionPayloadRejectedError`.

### Flight Sequence Number Fallback

If `flightSequenceNumber` is not provided, `_lookup_flight_sequence_number()` queries PostgreSQL `otp.flight_xml_current` table for the value.

### Error Handling

- Per-API failures (e.g., `SabreError`) don't abort the entire flight — partial results are recorded
- Celery tasks use `bind=True` with `self.retry(exc=exc)` for transient failures
- On batch completion, `set_ingestion_job_finished()` determines status from success/failure counts
- Cache pattern `flights:*` invalidated on successful batch completion

### SSE Events (`backend/api/routes/events.py`)

| Endpoint                    | Purpose            | Timeout |
| --------------------------- | ------------------ | ------- |
| `GET /events/jobs/{job_id}` | Per-job SSE stream | 10 min  |
| `GET /events/jobs`          | Global job events  | 1 hour  |

Event types: `connected`, `progress`, `completed`, `partial`, `failed`, `heartbeat`

Redis pub/sub via `asyncio.to_thread()`. Handles late-connecting clients (checks if job already terminal).

## Sabre Client (`backend/sabre/client.py`)

- Context manager: `__enter__` creates session, `__exit__` closes it
- Rate limiting: `SABRE_API_DELAY_SECONDS` (default 0.5s) between calls
- Retry: `@retry(retry_if_exception_type((ConnectionError, Timeout)), stop=3, wait=exponential(2–15s))`
- XML templates in `backend/sabre/templates.py` with `{placeholder}` variables
