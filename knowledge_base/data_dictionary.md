# Data Dictionary

## MongoDB Collections

### `sabre_requests` — Raw API Archive (Layer 1)

| Field | Type | Description |
|-------|------|-------------|
| `requestId` | string (UUID) | Unique request ID |
| `apiType` | string | `flight_status`, `passenger_list`, `reservations`, `trip_reports`, `flight_schedule` |
| `airline` | string | 2-char IATA (e.g. "GF") |
| `flightNumber` | string | 1–5 digits |
| `origin` | string | 3-letter IATA |
| `departureDate` | string | YYYY-MM-DD |
| `requestedAt` | string | ISO timestamp |
| `rawXml` | string | Original XML response |
| `parsedData` | object | `xmltodict`-parsed dict |
| `httpStatus` | int | HTTP response code |
| `durationMs` | float | Response time |

### `snapshots` — Normalized Snapshots (Layer 2)

| Field | Type | Description |
|-------|------|-------------|
| `snapshotId` | string (UUID) | Unique snapshot ID |
| `requestId` | string | Links to `sabre_requests` |
| `snapshotType` | string | Same as `apiType` |
| `airline`, `flightNumber`, `origin`, `departureDate` | string | Flight key |
| `sequenceNumber` | int | Auto-increment per flight+type |
| `checksum` | string | SHA-256 of normalized data |
| `capturedAt` | string | ISO timestamp |
| `data` | object | Normalized document (varies by type) |

### `changes` — Detected Changes (Layer 3)

See `change_tracking.md` for full schema and all 22 change types.

### `flights` — Current State Materialized View (Layer 4)

Unique key: `(airline, flightNumber, origin, departureDate)`

Upserted after each API call via `$set/$inc/$setOnInsert`.

### `flight_status` — Latest Flight Status (Legacy)

Normalized output of `convert_flight_status()`:

| Field | Type | Description |
|-------|------|-------------|
| `airline`, `flightNumber`, `origin`, `departureDate` | string | Flight key |
| `status` | string | `OPENCI`, `FINAL`, `PDC` |
| `aircraft` | object | `{type, registration, owner}` |
| `schedule` | object | Scheduled/estimated departure & arrival datetimes |
| `gate`, `terminal` | string | Gate/terminal assignment |
| `boarding` | object | Boarding time info |
| `legs` | array | Route legs |
| `passengerCounts` | object | Per-class: authorized, booked, available, thru, local, onBoard, boardingPasses, meals, revenue, nonRevenue |
| `jumpSeat` | object | `{cockpit, cabin}` counts |
| `remarks`, `freeTextRemarks` | array | Flight remarks |
| `codeshareInfo` | array | Codeshare details |
| `fetchedAt` | string | ISO timestamp |
| `_raw` | object | Original parsed data |

### `passenger_list` — Latest Passenger Manifest (Legacy)

Normalized output of `convert_passenger_list()`:

#### Passenger Record Fields

| Field | Type | Description |
|-------|------|-------------|
| `lastName`, `firstName` | string | Passenger name |
| `pnr` | string | PNR locator (may be dict with `#text`) |
| `passengerId` | string | Sabre internal ID |
| `lineNumber` | int | Line number in manifest |
| `priorityCode` | string | Standby priority |
| `bookingClass` | string | Current booking class |
| `desiredBookingClass` | string | Upgrade request class |
| `cabin` | string | `Y` (economy) or `J` (business) |
| `seat` | string | Seat assignment |
| `destination` | string | Arrival airport |
| `passengerType` | string | `F` (full), `P` (positive space), `E` (employee), `S` (standby) |
| `isStandby` | bool | On standby queue |
| `corpId` | string | Corporate ID (`T` = staff) |
| `seniorityDate` | string | Staff seniority |
| `bagCount` | int | Checked bags |
| `isCheckedIn` | bool | Check-in status |
| `isBoarded` | bool | Boarding status |
| `boardingPassIssued` | bool | BP issued flag |
| `checkInSequence` | string | Sequence number |
| `checkInDate`, `checkInTime` | string | Check-in timestamp |
| `isRevenue` | bool | Revenue passenger |
| `isThru` | bool | Thru traffic (ThruIndicator) |
| `isChild` | bool | `CHD` edit code present |
| `hasInfant` | bool | `INF` edit code present |
| `vcrType` | string | VCR (ticket) type |
| `ticketNumber` | string | Ticket number |
| `upgradeCode` | string | Upgrade code if present |
| `editCodes` | array | All edit codes from `EditCodeList` |
| `groupCode` | string | Group booking code |
| `isGroup` | bool | Part of group |
| `isUnnamedGroup` | bool | `groupCode` present + lastName=="PAX" + no firstName |
| `nameAssociationId` | string | Name association ID |
| `baggageRoutes` | array | Per-passenger baggage routing |
| `_raw` | object | Original parsed data |

#### Summary Fields (Document Level)

| Field | Type | Description |
|-------|------|-------------|
| `totalPassengers` | int | Total count |
| `adultCount` | int | `totalPassengers - childCount` |
| `childCount` | int | Passengers with `CHD` edit code |
| `infantCount` | int | Passengers with `INF` edit code (parent counted once) |
| `totalSouls` | int | `totalPassengers + infantCount` |
| `cabinSummary` | object | Per-cabin passenger counts |
| `groupBookings` | array | Group code summaries |

### `reservations` — Latest Reservations (Legacy)

Normalized output of `convert_reservations()`:

#### Per-Reservation Fields

| Field | Type |
|-------|------|
| `pnr` | string |
| `numberInParty`, `numberOfInfants` | int |
| `createdAt`, `updatedAt` | string |
| `bookingHeader`, `creationAgent` | string |
| `pnrSequence`, `pointOfSale` | string |
| `formOfPayment` | string |
| `passengers[]` | array of reservation passengers |
| `segments[]` | array (codeshare, marriageGroup, eTicket) |
| `ssrs[]`, `emails[]`, `phones[]` | arrays |
| `remarks[]`, `ancillaryServices[]` | arrays |
| `receivedFrom` | string |

#### Reservation Passenger Fields

| Field | Type | Source |
|-------|------|--------|
| `gender` | string | DOCSEntry (`P/{country}/{number}/{nationality}/{DOB}/{gender}/...`) |
| `dateOfBirth` | string | DOCSEntry |
| `nationality` | string | DOCSEntry |
| `seatNumber` | string | Direct field |
| `frequentFlyer` | object | `{tier, status, supplier}` |
| `specialMeal` | string | SSR meal code |
| `wheelchairCode` | string | SSR wheelchair |
| `hasEmergencyContact` | bool | Contact info present |
| `docaAddress` | string | DOCA entry |

### `trip_reports` — Merged MLX/MLC Reports (Legacy)

| Field | Description |
|-------|-------------|
| `mlxPassengers` | Cancelled passenger list (from MLX report) |
| `mlcPassengers` | Ever-booked passenger list (from MLC report) |

Merged by `converter.merge_trip_reports()`.

### `flight_schedules` — Schedule Data (Legacy)

Output of `convert_schedule()` from `VerifyFlightDetailsLLSRQ`.

## Sabre API Templates (`backend/sabre/templates.py`)

| Template | Sabre API | Version |
|----------|-----------|---------|
| `SESSION_CREATE` | SessionCreateRQ | — |
| `SESSION_CLOSE` | SessionCloseRQ | — |
| `FLIGHT_STATUS` | ACS_FlightDetailRQ | v3.2.0 |
| `PASSENGER_LIST` | GetPassengerListRQ | v4.0.0 |
| `RESERVATION` | Trip_SearchRQ | v4.5.0 (MaxItems=800, 27 SubjectAreas) |
| `PASSENGER_DATA` | GetPassengerDataRQ | v4.0.4 (timatic + checkInRequirements) |
| `TRIP_REPORT` | Trip_ReportsRQ | v1.3.0 |
| `VERIFY_FLIGHT_DETAILS` | VerifyFlightDetailsRQ | v2.0.1 |

## Passenger Display Codes

| Constant | Codes | Purpose |
|----------|-------|---------|
| `DISPLAY_CODES_BOOKED` | `RV`, `XRV` | Booked passengers |
| `DISPLAY_CODES_CHECKEDIN` | `BP`, `BT` | Checked-in with boarding pass |
| `DISPLAY_CODES_NOSHOW_OFL` | `NS`, `OFL` | No-shows and offloaded |
| `DISPLAY_CODES_ALL` | `AE` | All edit codes (enrichment) |

## Edit Codes (Passenger DNA)

Edit codes from `EditCodeList > EditCode[]` on each passenger. Key codes:

| Code | Meaning |
|------|---------|
| `CHD` | Child passenger |
| `INF` | Infant accompanying parent |
| `FF`, `GLD`, `SLV`, `BLU`, `PLT`, `DIA` | Loyalty tiers |
| `DOCS`, `DOCA`, `DOCV`, `DCVI` | Travel document types |
| `M` | Meal: standard meal (NOT gender) |
| `F` | Fare: fare-related (NOT gender) |

**Gender is derived only from reservation DOCSEntry**, never from edit codes.

## Passenger Counting Logic

```
totalPassengers  = count(PassengerInfoList)
childCount       = count(passengers with CHD edit code)
adultCount       = totalPassengers - childCount
infantCount      = count(passengers with INF edit code)  # parent counted once
totalSouls       = totalPassengers + infantCount
```

## Aggregation Pipelines (`backend/feeder/aggregations.py`)

| Function | Purpose |
|----------|---------|
| `find_missing_pnrs(date)` | PNRs in passenger_list but not reservations (and vice versa) via `$lookup` + `$setDifference` |
| `passenger_status_distribution(date)` | Status aggregation (total, checkedIn, boarded, revenue) by cabin class |
| `change_type_summary(date)` | Change type counts + affected flights per type |
