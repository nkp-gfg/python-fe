# Change Tracking

## Overview

Change detection compares consecutive snapshots of the same flight and type, producing structured change documents stored in the `changes` collection.

**Source:** `backend/feeder/differ.py`

## Dispatch

`detect_changes(before_snap, after_snap, snapshot_type, flight_info)` routes to the correct diff function via:

```python
DIFF_FUNCTIONS = {
    "flight_status": diff_flight_status,
    "passenger_list": diff_passenger_list,
    "reservations": diff_reservations,
}
```

## Passenger Keying

`_pax_key(passenger)` builds a unique identifier for matching passengers across snapshots:

- **Primary:** `{PNR}|{lastName}|{firstName}`
- **Staff fallback** (no PNR): `LINE:{lineNumber}|{lastName}|{firstName}`

## All Change Types (22)

### Flight Status Changes (`diff_flight_status`)

| Type | Trigger | Fields |
|------|---------|--------|
| `STATUS_CHANGE` | `status` field differs | oldValue/newValue = status string |
| `GATE_CHANGE` | `gate` field differs | oldValue/newValue = gate string |
| `TERMINAL_CHANGE` | `terminal` field differs | oldValue/newValue |
| `BOARDING_TIME_CHANGE` | `boardingTime` field differs | oldValue/newValue |
| `JUMPSEAT_CHANGE` | Cockpit or cabin jump seat counts change | metadata: jumpSeatType, field |
| `COUNT_CHANGE` | Per-class booked/onBoard/boardingPasses change | field = `{class}.{metric}` |

### Passenger List Changes (`diff_passenger_list`)

| Type | Trigger | Metadata |
|------|---------|----------|
| `PASSENGER_ADDED` | New passenger key appears | originalCabin, originalClass, upgradeCode |
| `PASSENGER_REMOVED` | Passenger key disappears | last known cabin/class/seat |
| `CHECKED_IN` | `isCheckedIn` False→True | — |
| `BOARDED` | `isBoarded` False→True | — |
| `CABIN_CHANGE` | `cabin` field differs | direction (UPGRADE/DOWNGRADE), upgradeType, previousCabin, newCabin |
| `CLASS_CHANGE` | `bookingClass` field differs | previousClass, newClass |
| `SEAT_CHANGE` | `seat` field differs | — |
| `BAG_COUNT_CHANGE` | `bagCount` field differs | — |
| `PAX_TYPE_CHANGE` | `passengerType` field differs | — |
| `PRIORITY_CHANGE` | `priorityCode` field differs | event: STANDBY_CLEARED or ADDED_TO_QUEUE |
| `UPGRADE_CONFIRMED` | `desiredBookingClass` cleared (goal achieved) | — |
| `LOYALTY_STATUS_ADDED` | New loyalty edit code appears | tier code |
| `DOCUMENT_ADDED` | New document edit code appears | doc code |
| `COUNT_CHANGE` | adultCount/childCount/infantCount/totalSouls change | — |

### Reservation Changes (`diff_reservations`)

| Type | Trigger |
|------|---------|
| `RESERVATION_ADDED` | New PNR appears |
| `RESERVATION_REMOVED` | PNR disappears |
| `RESERVATION_PARTY_CHANGE` | `numberInParty` changes for existing PNR |

## Upgrade Classification (`_classify_upgrade_type`)

When a `CABIN_CHANGE` is an upgrade, the type is classified:

| Type | Condition |
|------|-----------|
| `LMU` | upgradeCode starts with "LMU" |
| `PAID` | upgradeCode starts with "PU" or "UP" |
| `COMPLIMENTARY` | upgradeCode starts with "CU" or "CP" |
| `OPERATIONAL` | upgradeCode starts with "OP" |
| `UNKNOWN` | No matching upgradeCode |

## Loyalty and Document Codes

```python
loyalty_codes = {"FF", "GLD", "SLV", "BLU", "PLT", "DIA"}
doc_codes = {"DOCS", "DOCA", "DOCV", "DCVI"}
```

## Change Document Schema

```python
{
    "flightNumber": str,
    "origin": str,
    "departureDate": str,
    "changeType": str,         # One of 22 types above
    "beforeSnapshotId": str,
    "afterSnapshotId": str,
    "detectedAt": str,         # ISO timestamp
    "passenger": {             # Optional (flight-level changes have None)
        "pnr": str,
        "lastName": str,
        "firstName": str
    },
    "field": str,              # Changed field name
    "oldValue": any,
    "newValue": any,
    "metadata": dict           # Contextual data (upgrade direction, codes, tier, etc.)
}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /flights/{fn}/changes` | GET | Changes filtered by origin, date, change_type. Limit 1000, newest first |
| `GET /flights/{fn}/changes/summary` | GET | Count changes by type |
| `GET /flights/{fn}/snapshots` | GET | List snapshot metadata (without data) |
| `GET /flights/{fn}/snapshots/compare` | GET | Compare snapshot sequence against latest — returns deltas |
| `POST /flights/{fn}/snapshots/{seq}/restore` | POST | Restore historical snapshot version |

## Unit Tests (`backend/tests/test_differ.py`)

7 test cases: passenger_added, passenger_boarded, cabin_upgrade (3 changes: cabin + class + seat), passenger_removed, flight_status_change (status + gate), no_changes, multiple_changes (7 changes in complex scenario).
