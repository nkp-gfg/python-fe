# Flight Statuses

## Sabre Status Values

| Status | Meaning | Description |
|--------|---------|-------------|
| `OPENCI` | Open Check-In | Flight is open for check-in. Active passenger processing. |
| `FINAL` | Finalized | Boarding complete, manifest locked. No further check-ins. |
| `PDC` | Post-Departure Close | Flight has departed. Final passenger counts frozen. |

## Lifecycle

```
OPENCI → FINAL → PDC
```

Each transition is detected as a `STATUS_CHANGE` in the differ and stored in the `changes` collection.

## Flight Phases (Derived)

The backend derives a more granular phase from Sabre status + passenger data in `_derive_flight_phase()` (flights.py):

| Phase | Code | Derivation |
|-------|------|-----------|
| `SCHEDULED` | `SCHEDULED` | Before check-in opens |
| `CHECK_IN` | `CHECK_IN` | OPENCI status, passengers checking in |
| `BOARDING` | `BOARDING` | OPENCI status, passengers boarding |
| `CLOSED` | `CLOSED` | FINAL status |
| `DEPARTED` | `DEPARTED` | PDC status |

Each phase includes: `label`, `focusCard`, `alertColor`, `alertIcon`, `description`.

## Tracked Field Changes

Beyond the status itself, the differ tracks these flight-level fields:

| Field | Change Type |
|-------|------------|
| `status` | `STATUS_CHANGE` |
| `gate` | `GATE_CHANGE` |
| `terminal` | `TERMINAL_CHANGE` |
| `boardingTime` | `BOARDING_TIME_CHANGE` |
| `jumpSeat.cockpit`, `jumpSeat.cabin` | `JUMPSEAT_CHANGE` |
| Per-class booked/onBoard/boardingPasses | `COUNT_CHANGE` |

## Phase Journey

The frontend tracks phase progression over time via `GET /flights/{fn}/phase-journey`, showing:
- Phase snapshots at each capture point
- Passenger state buckets per phase (booked, checkedIn, boarded, new, removed)
- Transitions between phases with flow counts
- Demographic and cabin breakdowns per phase

Visualized as Sankey diagrams, stacked bars, demographic bars, and cabin bars in the Phase Journey tab.

## OTP Integration

PostgreSQL `otp.flight_xml_current` table provides external flight data:

| Field | Description |
|-------|-------------|
| `flightSequenceNumber` | Used for Sabre API calls |
| `scheduled_departure_utc/local` | Official schedule |
| `estimated_departure_utc/local` | Updated estimate |
| `actual_departure_utc/local` | Actual times |
| `flightStatus` | OTP system status |
| `isCancelled` | Cancellation flag |
| `aircraftType`, `aircraftRegistration` | Aircraft info |
| `totalPax` | Passenger count from OTP |
| `delayDetails` | Parsed delay codes/reasons |
| `sabreDepartureDate` | Resolved via priority chain (scheduled → local → leg → flight date) |

Endpoint: `GET /otp/flights?date=YYYY-MM-DD`
