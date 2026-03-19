# Sabre Ingestion API

FalconEye now exposes a FastAPI write endpoint that triggers the existing Sabre SOAP feeder pipeline for a single flight.

## Endpoints

| Method | Path                           | Mode        | Description                                     |
| ------ | ------------------------------ | ----------- | ----------------------------------------------- |
| `POST` | `/flights/ingest`              | Synchronous | Ingest one flight, wait for result              |
| `POST` | `/flights/ingest/batch`        | Background  | Submit 1-50 flights, returns job ID immediately |
| `GET`  | `/flights/ingest/jobs/{jobId}` | Query       | Poll status / results of a background job       |

## Single Flight Request Body

```json
{
  "airline": "GF",
  "flightNumber": "2006",
  "origin": "LHR",
  "departureDate": "2026-03-19",
  "departureDateTime": "2026-03-19T08:00:00"
}
```

## Batch Request Body

```json
{
  "flights": [
    {
      "airline": "GF",
      "flightNumber": "2006",
      "origin": "LHR",
      "departureDate": "2026-03-19",
      "departureDateTime": "2026-03-19T08:00:00"
    },
    {
      "airline": "GF",
      "flightNumber": "2057",
      "origin": "BOM",
      "departureDate": "2026-03-19",
      "departureDateTime": "2026-03-19T03:10:00"
    }
  ]
}
```

The batch endpoint returns HTTP 202 with a `jobId` and `pollUrl`. Poll the job status endpoint to check progress.

## Required Inputs

| Field               | Required | Notes                                           |
| ------------------- | -------- | ----------------------------------------------- |
| `flightNumber`      | Yes      | Used by all Sabre business APIs                 |
| `origin`            | Yes      | Departure airport / station                     |
| `departureDate`     | Yes      | Used by passenger list and MongoDB storage keys |
| `departureDateTime` | Yes      | Used by reservations search                     |
| `airline`           | No       | Defaults to `GF`                                |

## Internal Sequence

When this endpoint is called, FalconEye runs the existing feeder pipeline in this order:

1. `SessionCreateRQ`
2. `ACS_FlightDetailRQ`
3. `GetPassengerListRQ`
4. `Trip_SearchRQ`
5. `SessionCloseRQ`

For each business API response, FalconEye:

1. Stores raw XML request/response in `sabre_requests`
2. Converts Sabre payloads into normalized JSON
3. Stores immutable snapshots in `snapshots`
4. Detects and stores changes in `changes`
5. Updates the current-state document in `flights`
6. Maintains legacy compatibility collections: `flight_status`, `passenger_list`, `reservations`

## Response Shape

The endpoint returns a per-flight summary showing whether each Sabre business API succeeded, along with stored snapshot metadata such as request IDs, snapshot IDs, checksums, duplicate detection, and change counts.

## Execution Modes

### Synchronous (`POST /flights/ingest`)

- Client waits for the full pipeline to finish.
- Best for single-flight, manual triggering or curl/Bruno testing.
- Returns the full per-API result inline.

### Background (`POST /flights/ingest/batch`)

- Accepts 1-50 flights in one request.
- Returns HTTP 202 immediately with a `jobId`.
- The feeder runs asynchronously on the event loop's threadpool.
- Poll `GET /flights/ingest/jobs/{jobId}` for `status` (accepted → running → completed / failed).
- Once completed, the job response contains per-flight results.

### Job Status (`GET /flights/ingest/jobs/{jobId}`)

- Returns the current state of a background job.
- `status` values: `accepted`, `running`, `completed`, `failed`.
- `results` is populated once the job completes.

## Design Notes

- All routes are `async`; blocking Sabre/Mongo work is offloaded to `run_in_threadpool`.
- The job store is an in-process dict — suitable for single-worker deployments. For multi-worker, swap to Redis or a Mongo collection.
- Partial success is possible per flight. For example, flight status may succeed even if reservations fail.
- Sabre credentials and MongoDB connection settings come from the backend `.env`.
