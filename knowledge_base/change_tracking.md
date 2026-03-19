# Change Tracking Design

## Problem

Every time we call Sabre APIs for the same flight, the data changes:

- Passengers check in, board, get upgraded, change seats
- Bags are added
- New passengers appear (late bookings, standbys)
- Passengers disappear (cancellations, offloads, no-shows)

We need to:

1. Store every snapshot as immutable history (for data warehouse)
2. Detect what changed between any two snapshots
3. Know the current state of any flight at any point in time

## Architecture

### Storage Model

```
MongoDB Collections:

1. sabre_requests (IMMUTABLE — append-only audit log)
   ├── _id
   ├── requestId (UUID)
   ├── requestedAt (ISO timestamp)
   ├── apiType: "FlightStatus" | "PassengerList" | "Reservations"
   ├── flight: { airline, flightNumber, origin, departureDate }
   ├── requestXml (full SOAP request sent)
   ├── responseXml (full SOAP response received)
   ├── responseJson (parsed JSON — full document)
   ├── httpStatus
   ├── durationMs
   └── metadata: { sessionToken, conversationId, messageId }

2. snapshots (IMMUTABLE — normalized per-flight snapshots)
   ├── _id
   ├── snapshotId (UUID)
   ├── requestId (links to sabre_requests)
   ├── airline, flightNumber, origin, departureDate
   ├── snapshotType: "flight_status" | "passenger_list" | "reservations"
   ├── capturedAt (ISO timestamp)
   ├── sequenceNumber (auto-increment per flight+type, for ordering)
   ├── data: { ... normalized fields ... }
   └── checksum (hash of normalized data — for quick equality check)

3. changes (COMPUTED — diffs between consecutive snapshots)
   ├── _id
   ├── airline, flightNumber, origin, departureDate
   ├── changeType: "CHECKED_IN" | "BOARDED" | "CABIN_CHANGE" | etc.
   ├── beforeSnapshotId
   ├── afterSnapshotId
   ├── detectedAt
   ├── passenger: { pnr, lastName, firstName }
   ├── field
   ├── oldValue
   ├── newValue
   └── notes

4. flights (CURRENT STATE — materialized view, updated on each snapshot)
   ├── _id: { airline, flightNumber, origin, departureDate }
   ├── latestStatus
   ├── latestFlightStatusSnapshotId
   ├── latestPassengerListSnapshotId
   ├── latestReservationsSnapshotId
   ├── lastUpdatedAt
   ├── snapshotCount
   └── summary: { totalPax, checkedIn, boarded, ... }
```

### Change Detection Algorithm

```python
def detect_changes(before_snapshot, after_snapshot):
    """
    Compare two passenger list snapshots.
    Returns a list of Change objects.
    """
    before_pax = index_by_key(before_snapshot.passengers)  # key = PNR+Name
    after_pax = index_by_key(after_snapshot.passengers)

    changes = []

    # New passengers
    for key in after_pax - before_pax:
        changes.append(Change("PASSENGER_ADDED", after_pax[key]))

    # Removed passengers
    for key in before_pax - after_pax:
        changes.append(Change("PASSENGER_REMOVED", before_pax[key]))

    # State changes on existing passengers
    for key in before_pax & after_pax:
        b, a = before_pax[key], after_pax[key]

        if not b.isCheckedIn and a.isCheckedIn:
            changes.append(Change("CHECKED_IN", a))
        if not b.isBoarded and a.isBoarded:
            changes.append(Change("BOARDED", a))
        if b.cabin != a.cabin:
            changes.append(Change("CABIN_CHANGE", a, b.cabin, a.cabin))
        if b.bookingClass != a.bookingClass:
            changes.append(Change("CLASS_CHANGE", a, b.bookingClass, a.bookingClass))
        if b.seat != a.seat:
            changes.append(Change("SEAT_CHANGE", a, b.seat, a.seat))
        if b.bagCount != a.bagCount:
            changes.append(Change("BAG_COUNT_CHANGE", a, b.bagCount, a.bagCount))
        if b.passengerType != a.passengerType:
            changes.append(Change("PAX_TYPE_CHANGE", a, b.passengerType, a.passengerType))

    return changes
```

### Change Types

| Type              | Description                       | Tracked Fields               |
| ----------------- | --------------------------------- | ---------------------------- |
| PASSENGER_ADDED   | New passenger appeared            | pnr, name, cabin, class      |
| PASSENGER_REMOVED | Passenger disappeared             | pnr, name, reason guess      |
| CHECKED_IN        | Check-in status changed to true   | pnr, name                    |
| BOARDED           | Boarding status changed to true   | pnr, name                    |
| CABIN_CHANGE      | Cabin changed (upgrade/downgrade) | cabin: Y→J or J→Y            |
| CLASS_CHANGE      | Booking class changed             | bookingClass: V→D etc.       |
| SEAT_CHANGE       | Seat assignment changed           | seat: ""→"21A" or "21A"→"5C" |
| BAG_COUNT_CHANGE  | Checked bags count changed        | bagCount: 0→2                |
| PAX_TYPE_CHANGE   | Passenger type changed            | passengerType: E→F etc.      |
| STATUS_CHANGE     | Flight status changed             | status: OPENCI→FINAL         |
| GATE_CHANGE       | Gate assignment changed           | gate: B41→B43                |
| COUNT_CHANGE      | Passenger count changed           | class, booked, onBoard       |

### Checksumming for Quick Comparison

Before running the full diff, compute a SHA-256 of the normalized passenger list:

- Sort passengers by PNR+Name
- Serialize key fields to a stable JSON string
- Hash it
- If checksum matches previous snapshot → no changes, skip diff
- This saves CPU on high-frequency polling
