# FalconEye Operational Scenarios & Edge Cases

This document catalogs operational scenarios and edge cases handled by the FalconEye system, based on actual code implementation.

## 1. Passenger Classification & Special Cases

### 1.1 Group Bookings

**Trigger:** Passengers share a `GroupCode` in Sabre passenger list.

**System Handling:**
- Extracted in `backend/feeder/converter.py`
- Creates a `groupBookings` summary array with:
  - `groupCode`: unique identifier
  - `totalMembers`, `namedMembers`, `unnamedMembers`: count breakdown
  - `checkedIn`, `boarded`: progress tracking per group
  - `cabin`, `bookingClass`: cabin assignment

**Unnamed Group Members:**
- Detected when `GroupCode` exists AND `LastName == "PAX"` AND no `FirstName`
- Indicates group members whose names haven't been assigned yet (placeholder from Sabre)
- Tracked separately from named members for visibility into incomplete group manifests
- `nameAssociationId` attribute preserved for later resolution

**Known Limitations:**
- If names are assigned after initial ingestion, no historical tracking of the name change
- Group re-assignments (moving passengers between group codes) not tracked as a discrete change

---

### 1.2 Infant & Child Passengers

**Trigger:** Edit codes `INF` (infant) or `CHD` (child) present in passenger record.

**System Handling:**
- Stored as `hasInfant`, `isChild` boolean flags
- Counted separately: `infantCount`, `childCount`, `adultCount` in manifest
- `totalSouls` calculation: `totalPassengers + infantCount` (infants not counted in pax count)
- Both flags extracted from `EditCodeList → EditCode` array in Sabre response

**Relationship:**
- Infant parent: usually an adult passenger marked with `INF` edit code
- Child: passenger with `CHD` edit code (seated pax)
- Infants listed as separate passengers in GetPassengerListRS with parent PNR link

**Edge Case — No PNR for Infants:**
- Infants may not have a distinct PNR if traveling with parent
- Fallback key for deduplication uses `lineNumber` instead
- Prevents duplicates when same infant appears in multiple API calls

---

### 1.3 Staff Travel & Non-Revenue Passengers

**Trigger:** `IsRevenue` indicator in passenger data, or `PassengerType == "E"`.

**System Handling:**
- Stored as `isRevenue` boolean flag (presence of "Revenue" in Indicators list)
- Non-revenue passengers flagged separately for capacity/manifest reporting
- In flight analysis: staff = employee type (`PassengerType == "E"`) OR non-revenue
- Jump seat occupancy tracked separately: `jumpSeat.cockpit`, `jumpSeat.cabin` counts

**Data Structures:**
- `passengerCounts` per cabin: includes `revenue` and `nonRevenue` subtotals
- Gender/cabin breakdown analysis splits staff from regular passengers

**Known Limitation:**
- Staff travel on standby positions not consistently identified across all APIs
- Employee discount passengers may appear as revenue in some Sabre contexts

---

## 2. Passenger State Transitions & No-Shows

### 2.1 Multi-Call Passenger List Merge

**Trigger:** Ingestion pipeline requires comprehensive manifest covering all passenger states.

**Four Sequential API Calls** (runner.py):
1. **Booked (RV, XRV):** Passengers with Booked status (reserved but not checked in)
2. **CheckedIn (BP, BT):** Checked-in and boarded passengers
3. **NoShowOFL (NS, OFL):** No-show and offloaded passengers
4. **AllEdit (AE):** Catch-all for any edit codes not in above categories

**Merge Logic:**
- First successful call becomes base
- Subsequent calls append new passengers via de-duplication key: `PNRLocator | LastName | PassengerID`
- No passengers duplicated across multiple responses
- If all four calls fail: entire passenger list ingestion marked as error

**Partial Failure Handling:**
- If Call 1 succeeds, Call 2 fails, Call 3 succeeds: merge includes calls 1+3
- Each call status logged separately in `passengerList.calls` array
- Failures logged at WARNING level; pipeline continues unless merged_raw is None

**Known Limitation:**
- Deduplication happens in memory; no database lookup
- If passenger appears in two calls with conflicting data (e.g., different cabins), first occurrence wins

---

### 2.2 No-Show & Offload Detection

**Trigger:** Trip report APIs (MLX/MLC) called after passenger list.

**Trip Report Types:**
- **MLX (cancelled passengers):** Passengers booked but removed before flight
- **MLC (ever-booked passengers):** All passengers ever on any version of the flight

**Storage & Merge** (converter.py):
```python
"cancelledPassengers": mlx_doc.get("passengers", []) if mlx_doc else [],
"cancelledCount": mlx_doc.get("totalPassengers", 0) if mlx_doc else 0,
"everBookedPassengers": mlc_doc.get("passengers", []) if mlc_doc else [],
"everBookedCount": mlc_doc.get("totalPassengers", 0) if mlc_doc else 0,
```

**Edge Case — Partial Trip Report Failure:**
- MLX fails, MLC succeeds: document stored with `cancelledCount=0`, `everBookedCount=n`
- Both fail: `trip_reports` collection not updated

**Known Limitation:**
- Trip reports reflect state at API call time; earlier cancellations not retroactively marked
- No passenger-level tracking of *when* cancellation occurred

---

### 2.3 Check-In & Boarding State Tracking

**Trigger:** GetPassengerListRS includes `CheckIn_Info` and `Boarding_Info` per passenger.

**Data Extraction** (converter.py):
```python
"isCheckedIn": str(checkin_info.get("CheckInStatus", "false")).lower() == "true",
"isBoarded": str(boarding_info.get("BoardStatus", "false")).lower() == "true",
"checkInSequence": _safe_int(checkin_info.get("CheckInNumber")),
"checkInDate": checkin_info.get("CheckInDate", ""),
"checkInTime": checkin_info.get("CheckInTime", ""),
```

**Change Detection** (differ.py):
- Transitions from `false → true` generate `CHECKED_IN` or `BOARDED` change records
- Never detects reversal (checked-in → not checked-in); Sabre doesn't permit this state
- Check-in sequence number preserved for seat assignment ordering

---

## 3. Seat Assignments & Upgrades

### 3.1 Seat Assignment Handling

**Trigger:** `Seat` field in passenger record.

**Data Structures:**
- In **Passenger List**: `seat` string (e.g., "12A", empty if unassigned)
- In **Reservations**: pre-reserved seats via `PreReservedSeats` array
  - `seatNumber`, `seatStatusCode`, `seatTypeCode`
  - `seatBoardPoint`, `seatOffPoint`: routing info

**Change Tracking** (differ.py):
- Detected when `seat` changes between snapshots
- Generates `SEAT_CHANGE` record with old/new values
- No distinction between initial assignment vs. reassignment

**Edge Cases:**
- Unassigned seats: `seat` field is empty string or null
- Seat type codes: premium economy, bulkhead, exit row stored separately

---

### 3.2 Cabin Changes & Upgrades

**Trigger:** `Cabin` field changes between snapshots (e.g., "Y" → "J").

**Upgrade Type Classification** (differ.py):
```python
if upgrade_code == "LMU":                       return "LMU"           # Last Minute Upgrade
elif upgrade_code in ("PU", "PAU", "PUP"):      return "PAID"          # Paid upgrade
elif upgrade_code in ("CU", "CMP", "COMP"):     return "COMPLIMENTARY"
elif upgrade_code in ("OP", "OPS", "OPER"):     return "OPERATIONAL"
elif priority_code == "UPG" and upgrade_code:    return "LMU"           # Gate upgrade
```

**Change Metadata** (differ.py):
```python
"CABIN_CHANGE": {
    "direction": "UPGRADE" if (Y→J/F or J→F) else "DOWNGRADE",
    "upgradeCode": a.get("upgradeCode"),
    "upgradeType": _classify_upgrade_type(a),
}
```

**Booking Class vs. Cabin:**
- `bookingClass`: reserved fare basis (E, Y, J, F, etc.)
- `cabin`: actual cabin assignment (Y, J, F)
- Change in one or both triggers separate change records:
  - `CLASS_CHANGE`: booking class changed
  - `CABIN_CHANGE`: cabin changed (upgrade/downgrade)

**Upgrade Confirmation** (differ.py):
- Detects when `desiredBookingClass` is cleared AND `bookingClass` now matches original desire
- Generates `UPGRADE_CONFIRMED` record

**Edge Case:**
- Booking class → cabin mismatch: passenger booked in Y but seated in J with no upgrade code
- Repeated upgrades: Y→J→Y→J tracked as multiple changes

---

## 4. Passenger Data Enrichment & Travel Documents

### 4.1 APIS Data (Gender, DOB, Nationality)

**Trigger:** Reservations API response contains `APISRequest` with `DOCSEntry`.

**Data Extraction** (converter.py):
```python
"gender": gender,                  # M/F/U from DOCSEntry
"dateOfBirth": date_of_birth,     # DDMMMYY format
"nationality": nationality,       # DocumentNationalityCountry
"docaAddress": doca_address,      # Destination address (APIS requirement)
```

**Enrichment Pipeline** (passengers.py):
- Build lookup from latest reservations snapshot: `(PNR, lastName) → {nationality, specialMeal, wheelchairCode, ffTier, ffStatus}`
- Merge into passenger list at query time
- Falls back to empty strings if reservation data missing

**Known Limitation:**
- Gender/DOB only available from reservations; passenger list doesn't include APIS data
- Updates to APIS data not tracked as discrete changes (only current state preserved)

---

### 4.2 SSR (Special Service Requests)

**Trigger:** Reservations `SpecialServices → SpecialService` array.

**Data Extraction** (converter.py):
```python
"ssr_requests": [
    {
        "code": ssr.get("Code", ssr.get("@code", "")),
        "text": ssr.get("Text", ssr.get("FreeText", "")),
        "status": ssr.get("ActionCode", ssr.get("Status", "")),
        "airline": ssr.get("AirlineCode", ""),
        "type": ssr.get("@type", ""),
    }
]
```

**Common SSR Codes:**
- Meals: VGML (vegetarian), LFML (low-fat), CHML (child meal)
- Mobility: WCHR (wheelchair), WCHC (wheelchair + companion req'd)
- Medical: DEAF, BLND
- Baggage: SFOXT (surfboard), PETC (pet in cabin)

**Edge Case — SSR Conflicts:**
- Multiple meal requests for same passenger: all stored as list
- Conflicting statuses (one WCHR confirmed, another cancelled): both preserved

---

### 4.3 Frequent Flyer & Loyalty Tier Mapping

**Trigger:** Reservations `FrequentFlyer` array in passenger section.

**Data Extraction** (converter.py):
```python
"frequentFlyerNumber": frequent_flyer,
"frequentFlyerAirline": ff_airline,     # Carrier code (GF, BA, etc.)
"ffTierLevel": ff_tier_level,           # Numeric tier
"ffTierName": ff_tier_name,             # E.g., "GOLD"
"ffStatus": ff_status,                  # Status code
```

**Dashboard Analysis** (flights.py):
```python
"loyaltyCounts": {"FF": 0, "BLU": 0, "SLV": 0, "GLD": 0, "BLK": 0}
```

**Known Limitation:**
- Only first frequent flyer record extracted per passenger (if multiple loyalty programs)
- Tier name not standardized across airlines

---

### 4.4 Baggage Routing & Baggage Count

**Passenger List Baggage Routing** (converter.py):
```python
"baggageRoutes": [
    {
        "airline": br.get("Airline", ""),
        "flight": br.get("Flight", ""),
        "origin": br.get("Origin", ""),
        "destination": br.get("Destination", ""),
        "segmentStatus": br.get("SegmentStatus", ""),
    }
]
```

**Bag Count** (converter.py):
- Simple integer count, not detailed contents
- Change tracking for bag count variances (differ.py)

**Known Limitation:**
- Baggage routing doesn't show actual handling (e.g., if bag transferred to connecting flight)
- Baggage allowance (FREE vs. PAID) not captured

---

## 5. Edit Codes & Operational Flags

**Trigger:** `EditCodeList → EditCode` array in GetPassengerListRS.

**Common Edit Codes** (converter.py):
- `CHD`: Child passenger
- `INF`: Parent carrying infant
- `NS`: No-show
- `OFL`: Offloaded (removed after checkin)
- Airline-specific codes vary

**Storage:**
- Full array stored as-is: `editCodes: ["CHD", "INF", ...]`
- No attempt to normalize or classify
- Used upstream for passenger type logic

**DCS Integration Edge Case:**
- Edit codes may not sync immediately if passenger added/removed in DCS between manifest calls

---

## 6. Flight Status Changes & Gate/Terminal Updates

### 6.1 Flight Status Monitoring

**Trigger:** ACS_FlightDetailRQ response.

**Tracked Fields** (flights.py):
- `status`: gate status (ON TIME, BOARDING, DEPARTED, etc.)
- `gate`: departure gate (may be unassigned, 0, or "TBD")
- `terminal`: departure terminal
- `boarding.time`: boarding start time

**Change Detection** (differ.py):
```
if before.get("status") != after.get("status"): → STATUS_CHANGE
if before.get("gate") != after.get("gate"): → GATE_CHANGE
if before.get("terminal") != after.get("terminal"): → TERMINAL_CHANGE
```

---

### 6.2 Flight Schedule & Aircraft Assignment

**Trigger:** VerifyFlightDetailsRQ response.

**Data Extraction** (converter.py):
```python
"origin": origin_loc.get("@LocationCode", ""),
"originTerminal": origin_loc.get("@Terminal", ""),
"destination": dest_loc.get("@LocationCode", ""),
"aircraftType": equip.get("AircraftCode", ""),
"departureDateTime": dep_dt,
"arrivalDateTime": arr_dt,
```

**Edge Case — Aircraft Changes:**
- If aircraft type changes post-scheduling, new schedule call reflects it
- Previous snapshots retain old aircraft; no change tracking across schedule docs

---

## 7. API & Data Collection Edge Cases

### 7.1 Rate Limiting & Retry Logic

**Enforced by SabreClient** (sabre/client.py):
```python
API_CALL_DELAY = float(os.environ.get("SABRE_API_DELAY_SECONDS", "0.5"))

def _rate_limit(self):
    now = time.monotonic()
    elapsed = now - self._last_call_time
    if elapsed < self.API_CALL_DELAY:
        time.sleep(self.API_CALL_DELAY - elapsed)
    self._last_call_time = time.monotonic()
```

**Connection Retries:**
- 3 attempts max on `ConnectionError` or `Timeout`
- Exponential backoff: wait 2s, 4s, 8s... max 15s
- Non-transient errors (HTTP 400, 403) not retried

**Request Timeouts:**
- Most API calls: 30 seconds
- Trip_SearchRQ (Reservations): 60 seconds

---

### 7.2 Session Management

**Life Cycle** (sabre/client.py):
```python
def __enter__(self):
    self.create_session()    # Authenticate, get BinarySecurityToken
    return self

def __exit__(self, exc_type, exc_val, exc_tb):
    self.close_session()     # Clean close (safe if no session)
    return False
```

**Token Reuse:**
- Single session per feeder run
- All 5 API calls use same token
- On failure: token stale; session closes and new one created on next run

**Known Limitation:**
- Long-running feeder (>2 hours) may hit session timeouts
- No automatic session refresh mid-run

---

### 7.3 Flight Validation & Payload Rejection

**Trigger:** Sabre business error during FlightStatus call.

**Rejection Markers** (runner.py):
```python
invalid_markers = (
    "FLIGHT NOT INITIALIZED",
    "INVALID DATE OR CITY",
)
```

**Consequences of Rejection:**
- Entire flight skipped
- All remaining API calls marked as "skipped" status
- Error message stored in response

**Known Scenario:**
- User supplies `departureDate=2026-03-19` but flight departs 2026-03-20 02:30
- Sabre rejects with "Invalid date or city"
- Entire ingestion for that flight aborted

---

## 8. Data Preservation & Audit Trail

### 8.1 Four-Layer Storage

| Layer | Collection | Content | Purpose |
|-------|-----------|---------|---------|
| 1 | `sabre_requests` | Raw XML request + response | Complete Sabre preservation |
| 2 | `snapshots` | Normalized JSON + checksum | Temporal ordering, dedup |
| 3 | `changes` | Diffs between snapshots | Change audit trail |
| 4 | `flight_status`/`passenger_list`/`reservations` | Latest document | Query-optimized current state |

**Known Risk:**
- If Layer 2 insert fails, Layer 3 changes are not computed
- Checksum collision (unlikely with MD5) would falsely mark real changes as duplicates

---

### 8.2 Snapshot Versioning & Restore

**Snapshot Comparison** (changes.py):
```python
@router.get("/{flight_number}/snapshots/compare")
def compare_snapshot_against_latest(flight_number, snapshot_sequence):
    # Compare historical snapshot vs. latest snapshot
    # Return deltas: selected.value, latest.value, diff (if numeric)
```

**Restore Operation:**
- Restore a historical snapshot to current state by re-inserting into legacy collections
- Updates `fetchedAt` timestamp to current time
- No recomputation of changes; just copies snapshot data

**Known Limitation:**
- Restore does NOT revert to raw data
- Restore does NOT update changes collection
- Multiple restores can create duplicate documents

---

## 9. Change Categorization & Timeline Events

**Change Event Categories** (changes.py):
```python
"PASSENGER_ADDED": "booking",
"CHECKED_IN": "checkin",
"BOARDED": "boarding",
"CABIN_CHANGE": "upgrade",
"SEAT_CHANGE": "seat",
"BAG_COUNT_CHANGE": "baggage",
"PRIORITY_CHANGE": "standby",
"STATUS_CHANGE": "flight",
"GATE_CHANGE": "flight",
"TERMINAL_CHANGE": "flight",
```

**Per-Passenger Timeline:**
```python
@router.get("/{flight_number}/passenger-timeline")
def get_passenger_timeline(flight_number, pnr, ...):
    # Return all changes affecting this PNR
    # Track original booking, current state, events in sequence
```

**Event Descriptions:**
- Human-readable summaries: "Upgraded (LMU) Y → J"
- Cabin direction indicator: UPGRADE vs. DOWNGRADE
- Upgrade type classification included in description

---

## 10. Known Limitations & Constraints

### Sabre API Constraints
1. **No write-back:** FalconEye is read-only; cannot modify reservations
2. **Latency:** Sabre responses may lag live operations by 5–30 seconds
3. **Session timeout:** Tokens expire after ~2 hours of inactivity
4. **Throttling:** Rate limit 0.5s per call is self-imposed (Sabre allows ~5–10 req/s)

### Data Completeness
1. **Interim data:** If passenger removed between API calls, removal not captured as change
2. **State reversals:** System does NOT track check-in → not checked-in (Sabre doesn't permit)
3. **Cascading changes:** Downgrade → seat reassignment appears as two separate changes, not linked
4. **Infant parents:** If parent carries multiple infants, link between them not stored

### Temporal Issues
1. **Clock skew:** If Sabre server time drifts, timestamps may appear out-of-order
2. **No backfill:** Historical API calls cannot be re-requested; only forward-looking calls work
3. **Duplicate detection:** Only checks current snapshot; doesn't look back across time

### Frontend Limitations
1. **Infinite snapshots:** No pagination on snapshot history (potential performance risk for long-running flights)
2. **Real-time:** No WebSocket support; updates require polling
3. **Conflict resolution:** If user restores old snapshot and new data arrives, last write wins

---

## 11. Summary: Key Scenarios at a Glance

| Scenario | Trigger | Data Source | Tracking Method | Known Issues |
|----------|---------|-------------|-----------------|--------------|
| Group Booking | GroupCode present | PassengerList API | Named/unnamed breakdown | No historical name assignments |
| Infants | INF edit code | PassengerList API | infant count + parent link | May lack distinct PNR |
| No-Show | MLX trip report | Trip_ReportsRQ | cancelledPassengers list | No timestamp of cancellation |
| Upgrade (LMU) | Cabin Y→J, UPG priority | PassengerList API | CABIN_CHANGE + upgradeType | Multiple upgrades not linked |
| Paid Upgrade | Upgrade code PU/PAU | PassengerList API | CABIN_CHANGE metadata | No cost tracking |
| Staff Travel | Non-revenue flag | PassengerList API | isRevenue=false | Standby staff ambiguous |
| Standby Clearance | priorityCode cleared | Passenger diffs | PRIORITY_CHANGE + metadata | No queue position tracking |
| Seat Reassignment | seat field changed | PassengerList API | SEAT_CHANGE record | No swap relationships |
| APIS Docs | APISRequest DOCSEntry | Reservations API | gender, nationality, DOB | Not tracked as changes |
| Special Meals | MealType in SSR | Reservations API | specialMeal field | No per-segment tracking |
| Frequent Flyer | FrequentFlyer array | Reservations API | ffTierLevel, ffStatus | Only first tier stored |
| Gate Change | Gate field changed | Flight Status diffs | GATE_CHANGE record | No gate assignment reason |
| Aircraft Change | AircraftType changed | Schedule verify | New schedule_doc | Previous docs not invalidated |
| Partial Merge Fail | Some pax list calls fail | PassengerList merge | Calls logged, manifest merged | Missing passengers possible |
| Flight Validation Fail | "FLIGHT NOT INITIALIZED" | FlightStatus validation | IngestionPayloadRejectedError | Entire flight skipped |
