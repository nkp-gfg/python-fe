# Trip_ReportsRQ v1.3.0 — Flight Reports API

## Overview

Sabre Trip_ReportsRQ provides flight-level reports that are NOT available from GetPassengerListRQ or Trip_SearchRQ. These reports provide historical/aggregate views of passenger activity on a flight.

## Report Types Used

### MLX — Cancelled Passenger List

Returns passengers who **were previously booked** on the flight but whose reservation was **cancelled or removed**.

**Use case**: Populates the **Offloaded** count in the dashboard "Others" tile.

Reasons a passenger appears in MLX:

- Voluntary cancellation by passenger
- Involuntary offload (oversold flight, security, operational)
- Duplicate booking cleanup
- Schedule change removal
- No-show cleanup (post-departure)

### MLC — Complete Historical Passenger List

Returns **ALL passengers ever booked** on the flight, including those who:

- Are currently on the flight (active)
- Were cancelled (also in MLX)
- Were moved to another flight (reaccommodated)

**Use case**: Cross-referenced with current `passenger_list` to compute **No-Show** count.

**No-Show** = passengers in MLC who are NOT in the current manifest AND NOT in MLX (cancelled).

## SOAP Request

```xml
<Trip_ReportsRQ Version="1.3.0">
    <FlightInfo>
        <Airline>GF</Airline>
        <FlightNumber>2006</FlightNumber>
        <DepartureDate>2026-03-19</DepartureDate>
        <DepartureAirport>LHR</DepartureAirport>
    </FlightInfo>
    <ReportType>MLX</ReportType>  <!-- or MLC -->
</Trip_ReportsRQ>
```

## Report Types NOT Currently Used

| Code | Name                      | Why Not Used                                 |
| ---- | ------------------------- | -------------------------------------------- |
| MLR  | Reaccommodated Passengers | Could be added later for connection tracking |
| MLB  | Bag Tag List              | Not needed for current dashboard             |
| BSF  | Booking Summary (Forward) | Inventory analysis, not passenger-level      |
| BSR  | Booking Summary (Reverse) | Inventory analysis                           |
| MLBO | Outbound Connections      | Connection tracking (future)                 |
| MLBI | Inbound Connections       | Connection tracking (future)                 |

## Integration Architecture

### Ingestion Pipeline

The runner calls Trip_ReportsRQ as **Phase 4** (after reservations):

```
1. SessionCreateRQ
2. ACS_FlightDetailRQ     → flight_status collection
3. GetPassengerListRQ     → passenger_list collection
4. Trip_SearchRQ          → reservations collection
5. Trip_ReportsRQ (MLX)   ┐→ trip_reports collection (merged)
6. Trip_ReportsRQ (MLC)   ┘
7. SessionCloseRQ
```

### Storage

Both MLX and MLC reports are merged into a single `trip_reports` document:

```json
{
  "airline": "GF",
  "flightNumber": "2006",
  "origin": "LHR",
  "departureDate": "2026-03-19",
  "fetchedAt": "2026-03-19T10:00:00Z",
  "cancelledPassengers": [...],  // from MLX
  "cancelledCount": 3,
  "everBookedPassengers": [...], // from MLC
  "everBookedCount": 48
}
```

### Dashboard Query

The dashboard endpoint queries `trip_reports` collection and computes:

- **Offloaded** = `len(cancelledPassengers)` — always available if trip report data exists
- **No-Show** = passengers in `everBookedPassengers` NOT in current `passenger_list` manifest — only computed when flight status is FINAL or PDC

### Error Handling

Trip Reports failure is **non-fatal**. If the API call fails:

- Core pipeline (flight_status, passenger_list, reservations) is unaffected
- `offloadedAvailable` and `noShowAvailable` remain `false`
- Dashboard shows "N/A" for these fields
- Warning is logged but `flight.success` is not set to `false`

## MongoDB Collection

**Collection**: `trip_reports`

**Index**: `(airline, flightNumber, origin, departureDate)`

**Schema**:
| Field | Type | Description |
|-------|------|-------------|
| airline | string | Airline code (e.g., "GF") |
| flightNumber | string | Flight number |
| origin | string | Departure airport |
| departureDate | string | YYYY-MM-DD |
| fetchedAt | string | ISO timestamp |
| cancelledPassengers | array | Passengers from MLX report |
| cancelledCount | number | Count of cancelled passengers |
| everBookedPassengers | array | Passengers from MLC report |
| everBookedCount | number | Count of ever-booked passengers |
