# Data Preservation

## 4-Layer Storage Architecture

All data flows through `backend/feeder/storage.py` in append-only fashion.

### Layer 1: Raw Archive (`sabre_requests`)

Immutable record of every Sabre API call. `store_raw_request()` inserts:

| Field | Description |
|-------|------------|
| `requestId` | UUID (via `_new_id()`) |
| `apiType` | e.g. `flight_status`, `passenger_list` |
| `airline`, `flightNumber`, `origin`, `departureDate` | Flight key |
| `requestedAt` | ISO timestamp |
| `rawXml` | Original XML response |
| `parsedData` | `xmltodict`-parsed dict |
| `httpStatus`, `durationMs` | Response metadata |

**Indexes:** `req_id` (unique), `req_flight_lookup` (fn + date + apiType + requestedAt)

### Layer 2: Snapshots (`snapshots`)

Normalized JSON with checksum-based deduplication. `store_snapshot()`:

1. Takes converter output (normalized JSON)
2. Computes SHA-256 of `json.dumps(data, sort_keys=True, default=str)`
3. Fetches previous snapshot for same flight + type
4. Always stores the snapshot (append-only)
5. Returns `(snapshotId, checksum, is_duplicate)` — `is_duplicate=True` if checksum matches previous

**Auto-incrementing `sequenceNumber`:** `_next_sequence()` queries max sequence for the flight+type combination and increments.

| Field | Description |
|-------|------------|
| `snapshotId` | UUID |
| `requestId` | Links to Layer 1 |
| `snapshotType` | `flight_status`, `passenger_list`, `reservations`, `trip_reports`, `flight_schedule` |
| `airline`, `flightNumber`, `origin`, `departureDate` | Flight key |
| `sequenceNumber` | Auto-increment per flight+type |
| `checksum` | SHA-256 of normalized data |
| `capturedAt` | ISO timestamp |
| `data` | Normalized JSON document |

**Indexes:** `snap_id` (unique), `snap_flight_lookup` (fn + origin + date + type + seq desc), `snap_request`

### Layer 3: Changes (`changes`)

Diffs between consecutive snapshots. `store_changes()` bulk inserts change documents produced by `differ.detect_changes()`.

| Field | Description |
|-------|------------|
| `flightNumber`, `origin`, `departureDate` | Flight key |
| `changeType` | One of 22 types (see `change_tracking.md`) |
| `beforeSnapshotId`, `afterSnapshotId` | Snapshot pair |
| `detectedAt` | ISO timestamp |
| `passenger` | `{pnr, lastName, firstName}` (if applicable) |
| `field` | Changed field name |
| `oldValue`, `newValue` | Before/after values |
| `metadata` | Rich context (upgrade direction, codes, tier, etc.) |

**Indexes:** `chg_flight_lookup` (fn + origin + date + detectedAt), `chg_snapshot`, `chg_pnr`, `chg_type`

### Layer 4: Current State (`flights`)

Materialized view updated via `update_flight_state()` after each API call. Uses `$set`, `$inc`, `$setOnInsert` upsert.

**Unique key:** `(airline, flightNumber, origin, departureDate)`

Contains latest dashboard data (status, passenger counts, last ingested timestamp, snapshot count, change count).

**Index:** `flight_key` (unique)

### Legacy Collections (backward compatibility)

In addition to the 4 layers, raw normalized data is written to standalone collections for direct querying:

| Collection | Function | Indexes |
|------------|----------|---------|
| `flight_status` | `store_flight_status()` | `flight_lookup` (airline+fn+origin+date+fetchedAt desc) |
| `passenger_list` | `store_passenger_list()` | `pax_lookup`, `pax_pnr_lookup` |
| `reservations` | `store_reservations()` | `res_lookup`, `res_pnr_lookup` |
| `trip_reports` | `store_trip_reports()` | `trip_report_lookup` |
| `flight_schedules` | `store_flight_schedule()` | `schedule_lookup` |

## Key Guarantees

- **Append-only raw storage**: `sabre_requests` is never modified after insert
- **Snapshot always stored first**: Checksum comparison happens after write — "don't skip storing even if it seems the same"
- **Namespace stripping**: XML namespaces stripped via regex in `converter._strip_ns()`, original preserved in `_raw`
- **`_raw` preservation**: Every converter output includes `_raw: raw_data` for full traceability

## Snapshot Versioning (`backend/api/snapshot_versioning.py`)

`get_snapshot_data_as_of(db, flight_number, snapshot_type, snapshot_sequence, origin, departure_date)` enables time-travel queries by returning the snapshot data for sequence ≤ requested. Injects `snapshotSequenceNumber` and `snapshotCapturedAt` into the returned data.

## Connection Management

`storage.init_db(db)` receives the FastAPI layer's `MongoClient` instance — avoids creating a second connection that could cause DNS timeouts. The `_owns_connection` flag prevents `close()` from shutting down the shared connection.
