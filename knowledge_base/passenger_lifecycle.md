# Passenger Lifecycle

## Identification

**Primary key:** `PNR|lastName|firstName` (via `_pax_key()` in `differ.py`)

**Staff fallback:** When PNR is missing (common for staff travel), falls back to `LINE:{lineNumber}|lastName|firstName`.

**Cross-enrichment:** Passenger list data is enriched with reservation data via `_build_nationality_lookup()` in `passengers.py`, which builds a `(PNR, lastName) → {nationality, specialMeal, wheelchairCode, ffTier...}` map.

## Passenger Types

| Code | Meaning | `isRevenue` | Notes |
|------|---------|-------------|-------|
| `F` | Full fare | true | Standard revenue passenger |
| `P` | Positive space (non-revenue) | varies | Company travel |
| `E` | Employee | false | Staff (`corpId='T'`) |
| `S` | Standby | varies | May be revenue standby |

## State Machine

```
BOOKED → CHECKED_IN → BOARDED
  │          │
  │          └→ OFFLOADED (detected via MLX trip report)
  │
  └→ NO_SHOW (detected via MLC — booked in MLC but absent from final manifest)
  └→ CANCELLED (appeared in MLX report)
```

### State Fields

| Field | Type | Transition |
|-------|------|-----------|
| `isCheckedIn` | bool | False→True triggers `CHECKED_IN` change |
| `isBoarded` | bool | False→True triggers `BOARDED` change |
| `boardingPassIssued` | bool | BP issuance |
| `isStandby` | bool | On standby queue |

## Cabin and Class

| Field | Values | Description |
|-------|--------|-------------|
| `cabin` | `Y` (economy), `J` (business) | Physical cabin |
| `bookingClass` | A–Z | Fare class |
| `desiredBookingClass` | A–Z or null | Upgrade request target |
| `seat` | e.g. "14A" | Assigned seat |

## Upgrade Tracking

Upgrades are detected when `cabin` changes between snapshots. The differ classifies:

| Upgrade Type | Detection | upgradeCode prefix |
|-------------|-----------|-------------------|
| `LMU` | Last Minute Upgrade | `LMU*` |
| `PAID` | Paid upgrade | `PU*`, `UP*` |
| `COMPLIMENTARY` | Free upgrade | `CU*`, `CP*` |
| `OPERATIONAL` | Operational move | `OP*` |
| `UNKNOWN` | No matching code | — |

**Direction:** `UPGRADE` (Y→J) or `DOWNGRADE` (J→Y), stored in change `metadata.direction`.

**Confirmation:** When `desiredBookingClass` is cleared (passenger got their desired class), an `UPGRADE_CONFIRMED` change is emitted.

## Priority and Standby

`priorityCode` changes trigger `PRIORITY_CHANGE` with metadata:
- `STANDBY_CLEARED`: had priority code, now cleared
- `ADDED_TO_QUEUE`: new priority code assigned

Standby list endpoint (`GET /flights/{fn}/passengers/standby-list`) returns two sorted lists:
- **Upgrade queue:** sorted by `lineNumber`
- **Standby queue:** sorted by `seniorityDate` then `lineNumber`
- Includes cabin availability from flight status

## Group Bookings

| Field | Detection |
|-------|-----------|
| `groupCode` | Present on group passengers |
| `isGroup` | `groupCode` is truthy |
| `isUnnamedGroup` | `groupCode` present + `lastName == "PAX"` + no `firstName` |
| `nameAssociationId` | Links passengers within a group |

Group summary aggregated per group code at document level in `groupBookings` array.

## No-Show and Offload Detection

**Offloaded passengers:** Detected via MLX (cancelled) trip report. If a passenger appears in MLX but not in the current manifest, they were offloaded.

**No-show passengers:** Detected via MLC (ever-booked) trip report cross-referenced against the final manifest. If a passenger was in MLC (ever booked on this flight) but not in the final OPENCI→PDC passenger list, they are a no-show.

Both detected in `runner.py` during the trip report processing phase.

## Document and Loyalty Tracking

The differ tracks when new edit codes appear between snapshots:

- **Loyalty codes** (`FF`, `GLD`, `SLV`, `BLU`, `PLT`, `DIA`): triggers `LOYALTY_STATUS_ADDED`
- **Document codes** (`DOCS`, `DOCA`, `DOCV`, `DCVI`): triggers `DOCUMENT_ADDED`

## Gender Resolution

Gender is resolved from reservation DOCSEntry, **not from edit codes**. The DOCS free text format:
```
P/{country}/{number}/{nationality}/{DOB}/{gender}/{expiry}/{last}/{first}
```

Parsed by `_parse_docs_string()` in `converter.py`. Gender lookup built by `_build_gender_lookup()` in `flights.py` as `(PNR, lastName) → gender` map.

**Important:** Edit codes `M` and `F` are Meal and Fare codes respectively — they are NOT gender indicators.

## Passenger Detail (`GetPassengerDataRQ`)

Per-passenger deep detail via `convert_passenger_data()`:
- Itinerary segments with bag tags
- VCR (ticket) info
- AE (ancillary) details
- Required info (check-in requirements)
- Free text entries: DOCS, DOCA, PCTC, INF, BT, TIM, APP, AE, UK
- Timatic regulatory info

Accessed via `GET /flights/{fn}/passengers/{pnr}/detail`.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /flights/{fn}/passengers` | Latest passenger list with reservation enrichment. Supports `snapshot_sequence` for historical view |
| `GET /flights/{fn}/passengers/summary` | Counts: checked-in, boarded, revenue, non-revenue, per-cabin |
| `GET /flights/{fn}/passengers/standby-list` | Prioritized standby/upgrade queues with cabin availability |
| `GET /flights/{fn}/passengers/groups` | Group booking details |
| `GET /flights/{fn}/passengers/{pnr}/detail` | Full GetPassengerData response |
| `GET /flights/{fn}/passengers/{pnr}/timeline` | Chronological event history for one passenger |
