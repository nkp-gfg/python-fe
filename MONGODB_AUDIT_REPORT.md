# FalconEye MongoDB Performance Audit Report

**Date:** 2025-07-14  
**Database:** `falconeye` on Docker `falconeye_mongodb`  
**MongoDB Version:** 8.2.6 Community  
**Total Documents:** 4,219 | **Total Size:** ~476 MB (data) / ~102 MB (storage+compressed)

---

## Executive Summary

The audit revealed **4 critical COLLSCAN issues** where application queries bypass existing indexes due to a field prefix mismatch (`airline` leading compound indexes, but queries never include `airline`). This affects every page load and dashboard request. Additionally, the connection pool is unconfigured, there are no TTL indexes for cleanup, and some indexes have low selectivity. At current data volumes (~4K docs), the impact is milliseconds — but at 10x–100x scale these will become serious bottlenecks.

---

## Database Inventory

| Collection         | Docs      | Storage Size | Avg Doc Size    | Indexes |
| ------------------ | --------- | ------------ | --------------- | ------- |
| `sabre_requests`   | 821       | 178.88 MB    | ~218 KB         | 3       |
| `snapshots`        | 467       | 145.17 MB    | ~311 KB         | 4       |
| `reservations`     | 154       | 121.93 MB    | ~792 KB         | 3       |
| `passenger_list`   | 161       | 23.71 MB     | ~147 KB         | 3       |
| `changes`          | 2,196     | 874 KB       | ~400 B          | 5       |
| `flight_status`    | 162       | 249 KB       | ~1.5 KB         | 2       |
| `flight_schedules` | 87        | 137 KB       | ~1.6 KB         | 2       |
| `flights`          | 46        | 24.6 KB      | ~535 B          | 2       |
| `trip_reports`     | 125       | 28.2 KB      | ~226 B          | 2       |
| `app_settings`     | 1         | 115 B        | 115 B           | 1       |
| **Total**          | **4,219** | **~471 MB**  | **~118 KB avg** | **27**  |

> ⚠️ `reservations` avg doc = **792 KB** — approaching MongoDB's 16 MB limit at current growth rate.

---

## Findings

### 🔴 CRITICAL — Index Prefix Mismatch Causing Full Collection Scans

**Impact:** Every API call to `/flights`, `/flights/{id}/dashboard`, `/flights/{id}/status` does a COLLSCAN.

**Root Cause:** Indexes on `flight_status`, `passenger_list`, `reservations`, and `trip_reports` all start with `airline:1` as the prefix field:

```
flight_status.flight_lookup:    {airline:1, flightNumber:1, origin:1, departureDate:1, fetchedAt:-1}
passenger_list.pax_lookup:      {airline:1, flightNumber:1, origin:1, departureDate:1, fetchedAt:-1}
reservations.res_lookup:        {airline:1, flightNumber:1, departureAirport:1, departureDate:1, fetchedAt:-1}
trip_reports.trip_report_lookup: {airline:1, flightNumber:1, origin:1, departureDate:1, fetchedAt:-1}
```

But **no application query includes `airline` in the filter**. The code builds queries like:

```python
query = {"flightNumber": flight_number}
if origin: query["origin"] = origin
if date: query["departureDate"] = date
# ← airline is NEVER added
```

For list aggregations, `$match: {departureDate}` also can't use indexes prefixed with `airline`.

**Evidence (explain plans):**

| Query                | Collection     | Plan         | Docs Examined | Keys Examined |
| -------------------- | -------------- | ------------ | ------------- | ------------- |
| `list_flights` agg   | flight_status  | **COLLSCAN** | 162 (ALL)     | 0             |
| `list_flights` agg   | passenger_list | **COLLSCAN** | 161 (ALL)     | 0             |
| `dashboard` find_one | flight_status  | **COLLSCAN** | 162 (ALL)     | 0             |
| `dashboard` find_one | reservations   | **COLLSCAN** | 154 (ALL)     | 0             |

**Contrast** — collections WITHOUT `airline` prefix work correctly:

| Query                 | Collection | Plan          | Docs Examined | Keys Examined |
| --------------------- | ---------- | ------------- | ------------- | ------------- |
| `dashboard` find      | changes    | **IXSCAN** ✅ | 0             | 0             |
| `versioning` find_one | snapshots  | **IXSCAN** ✅ | 0             | 0             |

#### Fix — Create New Indexes (Keep Old as Commented in Code)

**Option A (Recommended): Add `airline` to all queries** — minimal index change, matches existing data model:

```python
# In _fetch_dashboard_data_parallel() and all find_one queries:
query = {"airline": "GF", "flightNumber": flight_number}
```

**Option B: Replace indexes** — new indexes without `airline` prefix, matching query patterns:

```javascript
// flight_status — dashboard queries (flightNumber + origin + date)
db.flight_status.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "fs_flight_lookup_v2" },
);
// flight_status — list queries (date filter only)
db.flight_status.createIndex(
  { departureDate: 1, fetchedAt: -1 },
  { name: "fs_date_lookup" },
);

// passenger_list — dashboard queries
db.passenger_list.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "pax_flight_lookup_v2" },
);
// passenger_list — list queries
db.passenger_list.createIndex(
  { departureDate: 1, fetchedAt: -1 },
  { name: "pax_date_lookup" },
);

// reservations — uses departureAirport not origin
db.reservations.createIndex(
  { flightNumber: 1, departureAirport: 1, departureDate: 1, fetchedAt: -1 },
  { name: "res_flight_lookup_v2" },
);

// trip_reports — dashboard queries
db.trip_reports.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "trip_flight_lookup_v2" },
);

// flight_schedules — same issue, no airline in queries
db.flight_schedules.createIndex(
  { flightNumber: 1, departureDate: 1, fetchedAt: -1 },
  { name: "sched_flight_lookup_v2" },
);
```

**Expected improvement:** COLLSCAN → IXSCAN for ALL hot queries. At 10K+ docs, this means 100ms → <1ms per query.

---

### 🔴 CRITICAL — Aggregation Pipelines Missing $match Before $sort

The `_latest_per_flight_agg()` and `_agg_passenger_list()` pipelines do:

```
$match → $sort → $group
```

When `$match: {departureDate}` can't use an index (due to prefix mismatch above), the `$sort: {fetchedAt: -1}` becomes an **in-memory blocking sort** on the entire collection. The entire pipeline materializes ALL documents in RAM before grouping.

**Fix:** After creating the `{departureDate:1, fetchedAt:-1}` indexes (Option B above), MongoDB will use the index to satisfy both `$match` and `$sort`, eliminating the in-memory sort entirely.

---

### 🔴 CRITICAL — Connection Pool Not Configured

In `backend/api/database.py`:

```python
_client = MongoClient(
    uri,
    serverSelectionTimeoutMS=10_000,
    connectTimeoutMS=10_000,
    # ← NO maxPoolSize, NO minPoolSize
)
```

Default `maxPoolSize=100`, but with `ThreadPoolExecutor(max_workers=6)` doing parallel queries, you need at least 6 connections available. Under load with multiple concurrent HTTP requests, the default may work, but you should explicitly set:

**Fix:**

```python
_client = MongoClient(
    uri,
    serverSelectionTimeoutMS=10_000,
    connectTimeoutMS=10_000,
    maxPoolSize=20,          # Enough for 6 parallel queries × ~3 concurrent users
    minPoolSize=2,           # Keep 2 warm connections
    maxIdleTimeMS=60_000,    # Close idle connections after 60s
    socketTimeoutMS=30_000,  # Don't hang forever on slow queries
)
```

---

### 🟡 WARNING — Low-Selectivity Index: `changes.chg_type`

```
changes.chg_type: {changeType: 1}
```

`changeType` has very few distinct values (`status_change`, `checkin`, `boarding`, `standby_change`, etc. — likely <10 values across 2,196 documents). This index has very low selectivity — scanning the index produces nearly as many results as a COLLSCAN.

**Fix:** Drop this index unless there's a query that filters ONLY on `changeType`:

```javascript
db.changes.dropIndex("chg_type");
```

If you need `changeType` filtering, combine it with the flight filter:

```javascript
db.changes.createIndex(
  {
    flightNumber: 1,
    origin: 1,
    departureDate: 1,
    changeType: 1,
    detectedAt: -1,
  },
  { name: "chg_flight_type_lookup" },
);
```

---

### 🟡 WARNING — Field Naming Inconsistency: `departureAirport` vs `origin`

- `flight_status`, `passenger_list`, `snapshots`, `changes`, `trip_reports` use `origin`
- `reservations` uses `departureAirport`

This forces different query construction in `_fetch_dashboard_data_parallel()`:

```python
res_query = {"flightNumber": flight_number}
if origin: res_query["departureAirport"] = origin   # ← Different field name!
```

**Impact:** Increases code complexity, easy to introduce bugs, prevents generic query helpers.

**Fix (future):** When storing new reservation documents, normalize to `origin`. Add a migration script that adds an `origin` field mirroring `departureAirport` for existing docs.

---

### 🟡 WARNING — No TTL Indexes for Data Lifecycle

Append-only collections grow without bound:

- `sabre_requests`: 821 docs, 178 MB (raw XML payloads)
- `snapshots`: 467 docs, 145 MB (full document snapshots)

At current ingestion rate (~100 snapshots/day), `snapshots` will reach 1 GB in ~30 days.

**Fix:** Add TTL indexes on `requestedAt`/`capturedAt` for data older than retention period:

```javascript
// Keep raw Sabre XML for 90 days
db.sabre_requests.createIndex(
  { requestedAt: 1 },
  { name: "ttl_sabre_requests", expireAfterSeconds: 7776000 },
);

// Keep snapshots for 60 days
db.snapshots.createIndex(
  { capturedAt: 1 },
  { name: "ttl_snapshots", expireAfterSeconds: 5184000 },
);
```

⚠️ **Note:** TTL only works if the field contains a `Date` type. Currently `requestedAt`/`capturedAt` are stored as **strings** (`"2025-06-20T10:30:00Z"`). You would need to either:

1. Store as ISODate going forward + backfill existing docs
2. Or use a scheduled cleanup script instead of TTL

---

### 🟡 WARNING — Large `_raw` Field Stored in Snapshots

`snapshots.data._raw` contains the original Sabre XML parsed into JSON. This bloats snapshot documents significantly (~311 KB avg). When `_fetch_dashboard_data_parallel()` calls `find_one()` for snapshots, it transfers the entire `_raw` field even though the app only uses the parsed fields.

**Fix:** The code already strips `_raw` via `_strip_id()`, but the data is still transferred over the wire. Use projection:

```python
doc = db["snapshots"].find_one(query, sort=[...], projection={"data._raw": 0})
```

---

### 🟡 WARNING — `_build_dashboard_payload` Recomputed on `/tree` Endpoint

The `/tree` endpoint calls `_build_dashboard_payload()` which computes the FULL dashboard + tree, then returns only the `tree` portion. This redundantly processes all passenger analysis.

The code tries to use the dashboard cache first, but on cache miss it duplicates all work.

**Current:** Acceptable at small scale. Flag for optimization if tree requests increase.

---

### 🟢 SUGGESTION — In-Memory Dashboard Cache Improvements

The current Python dict cache (`_dashboard_cache`) has issues:

1. **TTL=30s is very short** — dashboard data changes only on ingestion (minutes apart)
2. **Max 100 entries** with LRU eviction by oldest timestamp
3. **Not thread-safe** — `dict` operations aren't atomic under ThreadPoolExecutor
4. **Process-local** — won't share across uvicorn workers

**Fix:**

```python
import threading

_cache_lock = threading.Lock()
_cache_ttl = 120  # 2 minutes — matches typical ingestion interval

def _get_cached(key):
    with _cache_lock:
        if key in _dashboard_cache:
            data, ts = _dashboard_cache[key]
            if time.time() - ts < _cache_ttl:
                return data
            del _dashboard_cache[key]
    return None

def _set_cache(key, data):
    with _cache_lock:
        if len(_dashboard_cache) > 100:
            oldest_key = min(_dashboard_cache, key=lambda k: _dashboard_cache[k][1])
            del _dashboard_cache[oldest_key]
        _dashboard_cache[key] = (data, time.time())
```

---

### 🟢 SUGGESTION — `flights` Collection Under-utilized

The `flights` collection (46 docs, 24 KB) stores the materialized current state per flight with `summary.totalPax`, `summary.boarded`, etc. But `list_flights()` queries `flight_status` + `passenger_list` instead of reading from `flights`.

**Fix (future):** Use `flights` as the primary source for `list_flights()`:

```python
@router.get("")
def list_flights(date=None):
    match = {"departureDate": date} if date else {}
    return list(db["flights"].find(match, {"_id": 0}).sort([("departureDate", 1), ("flightNumber", 1)]))
```

This would reduce 3 aggregation pipelines to 1 simple indexed find. Requires enriching the `flights` doc with all fields needed by the frontend list.

---

### 🟢 SUGGESTION — `passenger_list` and `reservations` Have Very Large Documents

| Collection       | Avg Doc Size | Risk                                          |
| ---------------- | ------------ | --------------------------------------------- |
| `reservations`   | ~792 KB      | ⚠️ Could exceed 16 MB with 3x more passengers |
| `snapshots`      | ~311 KB      | Contains `_raw` XML payload                   |
| `passenger_list` | ~147 KB      | Full passenger arrays per flight              |

For `reservations`, each document contains the entire `reservations[]` array for a flight. A flight with 400+ reservations with full DOCS/SSR data could approach the 16 MB BSON limit.

**Fix:** Monitor max document sizes:

```javascript
db.reservations.aggregate([
  { $project: { size: { $bsonSize: "$$ROOT" } } },
  { $sort: { size: -1 } },
  { $limit: 5 },
]);
```

---

### 🟢 SUGGESTION — `connect.py` Creates Unused Connection

`backend/connect.py` creates a standalone `MongoClient` on import with `ServerApi('1')` (used for Atlas). This is a diagnostic script, not used by the app, but if accidentally imported it would create a second connection pool.

**Fix:** Add a `if __name__ == "__main__":` guard or delete the file.

---

## Prioritized Action Plan

| #   | Severity | Item                                                                        | Effort | Impact                                  |
| --- | -------- | --------------------------------------------------------------------------- | ------ | --------------------------------------- |
| 1   | 🔴       | Fix COLLSCAN: Add `airline` to queries OR create non-airline-prefix indexes | 30 min | **Highest** — eliminates all full scans |
| 2   | 🔴       | Fix aggregation pipelines: ensure `$match` uses index                       | 10 min | Included with #1                        |
| 3   | 🔴       | Configure connection pool (`maxPoolSize`, `socketTimeoutMS`)                | 5 min  | Prevents connection exhaustion          |
| 4   | 🟡       | Drop `changes.chg_type` low-selectivity index                               | 2 min  | Saves write overhead                    |
| 5   | 🟡       | Add projection `{"data._raw": 0}` to snapshot queries                       | 10 min | Reduces wire transfer                   |
| 6   | 🟡       | Plan TTL or cleanup strategy for `sabre_requests`/`snapshots`               | 1 hr   | Prevents unbounded growth               |
| 7   | 🟡       | Add thread safety to dashboard cache                                        | 10 min | Prevents race conditions                |
| 8   | 🟢       | Monitor `reservations` document sizes                                       | 5 min  | Prevent 16 MB limit hit                 |
| 9   | 🟢       | Use `flights` collection for list endpoint                                  | 2 hrs  | Eliminates 3 aggregations               |
| 10  | 🟢       | Normalize `departureAirport` → `origin` in reservations                     | 1 hr   | Code simplification                     |

---

## Index Recommendations Summary

### Create New (7 indexes)

```javascript
db.flight_status.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "fs_flight_lookup_v2" },
);
db.flight_status.createIndex(
  { departureDate: 1, fetchedAt: -1 },
  { name: "fs_date_lookup" },
);
db.passenger_list.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "pax_flight_lookup_v2" },
);
db.passenger_list.createIndex(
  { departureDate: 1, fetchedAt: -1 },
  { name: "pax_date_lookup" },
);
db.reservations.createIndex(
  { flightNumber: 1, departureAirport: 1, departureDate: 1, fetchedAt: -1 },
  { name: "res_flight_lookup_v2" },
);
db.trip_reports.createIndex(
  { flightNumber: 1, origin: 1, departureDate: 1, fetchedAt: -1 },
  { name: "trip_flight_lookup_v2" },
);
db.flight_schedules.createIndex(
  { flightNumber: 1, departureDate: 1, fetchedAt: -1 },
  { name: "sched_flight_lookup_v2" },
);
```

### Drop (1 index)

```javascript
db.changes.dropIndex("chg_type");
```

### Keep As-Is (good indexes)

- `changes.chg_flight_lookup` — matches query pattern perfectly ✅
- `snapshots.snap_flight_lookup` — matches query pattern perfectly ✅
- `snapshots.snap_id` — unique lookup by snapshotId ✅
- `sabre_requests.req_id` — unique lookup by requestId ✅
- `flights.flight_key` — unique compound ✅
- All `_id` indexes — default ✅

### Deprecate After Creating v2 (keep for safety, drop later)

- `flight_status.flight_lookup`
- `passenger_list.pax_lookup`
- `reservations.res_lookup`
- `trip_reports.trip_report_lookup`
- `flight_schedules.schedule_lookup`

---

_End of Audit Report_
