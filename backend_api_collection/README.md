# Backend API Collection

Bruno API collection for testing the FalconEye FastAPI endpoints.

## Base URL

- Local: `http://localhost:8000`

## Endpoints

### Health

- `GET /` — Service status
- `GET /health/app` — Application health check
- `GET /health/db` — Database health check

### Flights

- `POST /flights/ingest` — Trigger Sabre SOAP ingestion for one flight (synchronous)
- `POST /flights/ingest/batch` — Batch ingestion for multiple flights (background job)
- `GET /flights/ingest/jobs/{jobId}` — Poll status of a background ingestion job
- `GET /flights` — List flights with latest status and summary
- `GET /flights/{flight_number}/dashboard` — Combined dashboard payload
- `GET /flights/{flight_number}/tree` — Tree payload for the dashboard
- `GET /flights/{flight_number}/status` — Latest flight status
- `GET /flights/{flight_number}/status/history` — Historical status snapshots

### Passengers

- `GET /flights/{flight_number}/passengers` — Latest passenger list
- `GET /flights/{flight_number}/passengers/summary` — Passenger count summary
- `GET /flights/{flight_number}/passengers/{pnr}` — Passenger by PNR

### Reservations

- `GET /flights/{flight_number}/reservations` — Latest reservations
- `GET /flights/{flight_number}/reservations/{pnr}` — Reservation by PNR

### Changes

- `GET /flights/{flight_number}/changes` — Detected changes for a flight
- `GET /flights/{flight_number}/changes/summary` — Change counts grouped by type
- `GET /flights/{flight_number}/snapshots` — Snapshot metadata for a flight
- `GET /flights/{flight_number}/changes/passenger/{pnr}` — Changes for a specific passenger

## Request Files

- `Trigger Sabre Ingestion.bru`
- `Trigger Batch Ingestion.bru`
- `Get Ingestion Job Status.bru`
- `Health - Root.bru`
- `Health - App.bru`
- `Health - DB.bru`
- `List Flights.bru`
- `Get Flight Dashboard.bru`
- `Get Flight Tree.bru`
- `Get Flight Status.bru`
- `Get Flight Status History.bru`
- `Get Passengers.bru`
- `Get Passenger Summary.bru`
- `Get Passenger by PNR.bru`
- `Get Reservations.bru`
- `Get Reservation by PNR.bru`
- `Get Changes.bru`
- `Get Changes Summary.bru`
- `Get Snapshots.bru`
- `Get Passenger Changes.bru`
