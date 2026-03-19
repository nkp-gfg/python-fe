# Airline Operations Scenarios

Real-world patterns observed in Gulf Air Sabre data and provided by domain expertise.

---

## 1. Group Bookings

**Pattern**: A single PNR with many passengers (10-200+), all passenger type "P" (prepaid).

**Observations from GF2274**:

- 148 passengers in first snapshot, 192 in final (44 added later)
- Very few unique PNRs (3 in our data) — massive group bookings
- All passengers had type "P" (prepaid/group)
- Cabin was Y (Economy) for groups

**Behavior**:

- Group names are initially placeholder-style (e.g., generic name patterns)
- Individual names get assigned as passengers are confirmed
- Seat assignments done in bulk, close to departure
- Check-in often done together (entire group transitions at once)

---

## 2. PDC Sync (Post-Departure Close)

**What**: After a flight departs, Sabre marks it PDC. The API still returns data, but it's the final frozen state.

**Observations**:

- GF2056 (DMM, 2026-03-16): PDC — 196 passengers, all boarded
- GF2057 (BOM, 2026-03-18): PDC — 19 passengers, all boarded
- GF2130 (DMM, 2026-03-18): No status (estimated), 284 passengers
- GF2754 (DMM, 2026-03-18): No status (estimated), 146 passengers

**Key Insight**: PDC flights are historical. Their data won't change. But we still store them because:

1. We need the final state for analytics
2. Comparing how data evolved from OPENCI → FINAL → PDC tells the complete story

---

## 3. No-Shows

**Pattern**: A passenger is booked (appears in reservation) and possibly checked-in, but never boards.

**Detection**:

- In PassengerList: `ns3:Indicators` will show `CheckedIn="true"` but `Boarded="false"`
- In the final (PDC) snapshot, if still not boarded → confirmed no-show
- Revenue no-shows have financial implications (ticket revenue collected, no service delivered)
- NonRevenue no-shows (staff/employee) have standy implications

**Our Data**:

- GF2006 (2026-03-18): 48 passengers in manifest, check how many boarded vs checked-in only
- This requires comparing `Boarded` indicator across snapshots

---

## 4. Cabin Upgrades

**Pattern**: A passenger originally booked in Y (Economy) moves to J (Business/Gulf).

**Observations from Step 3 Analysis**:

- **GF2006 (LHR, 2026-03-18)**: 4 passengers had `CABIN_CHANGE` from Y to J
- Same 4 passengers also had `CLASS_CHANGE` (booking class reclassified)
- Upgrades happen close to departure when premium seats are unsold

**Types of Upgrades**:

1. **Complimentary Upgrade**: Airline initiates (loyalty status, oversold economy)
2. **Paid Upgrade**: Passenger purchases (would show in edit codes)
3. **Operational Upgrade**: Overselling/weight balance requires moving passengers up

**Tracking**: The `CABIN_CHANGE` + `CLASS_CHANGE` pair is the signature of an upgrade.

---

## 5. Staff & Employee Travel

**Passenger Types**:

- `E` = Employee (airline staff)
- `S` = Staff (non-revenue, standby basis)
- `F` = Fare-paying passenger (regular)
- `P` = Prepaid/Group

**Observations**:

- Staff passengers (`S`, `E`) appear in passenger list but are `NonRevenue`
- They may not have confirmed seats until close to departure
- They can be offloaded if fare-paying passengers need seats
- The `PASSENGER_REMOVED` changes we detected (3 cases) could be staff offloads

**Detection of Standby Resolution**:

- Staff appears in one snapshot with no seat
- Next snapshot: either assigned a seat (confirmed) or removed (denied boarding)

---

## 6. Nationality & Document Data

**What we see in Edit Codes**:

- Edit codes like `CTCE`, `CTCM` → Contact info (email, mobile)
- `DOCS` → Travel documents (passport)
- `DOCO` → Visa/entry permits
- `DOCA` → Destination/residence address
- These contain nationality, passport number, visa details

**Data Warehouse Value**:

- Nationality distribution per route
- Document compliance rates
- Contact info completeness (important for IROP — irregular operations communication)

---

## 7. Meal & Special Service Requests

**Edit Codes Observed**:

- `MOML` — Muslim meal
- `BBML` — Baby meal
- `CHML` — Child meal
- `HNML` — Hindu meal (non-vegetarian)
- `VGML` — Vegetarian meal
- `VLML` — Vegetarian lacto-ovo
- `KSML`, `ORML`, `SFML`, `AVML`, `GFML`, `DBML` — Other dietary types

**Operational Relevance**:

- Meal counts must match catering orders
- Changes in meal requests between snapshots = catering update needed
- Missing meal codes for passengers who should have them = potential service failure

---

## 8. Baggage Tracking

**Observations**:

- `BagCount` field in passenger list tracks checked bags
- Changes detected: 102 `BAG_COUNT_CHANGE` events
- Most common: 0→1 or 0→2 (bags checked at counter)

**Patterns**:

- Bags go from 0 to N at check-in
- Sometimes bags increase (additional bags at counter)
- Rarely bags decrease (bag pulled for security or weight)

---

## 9. Seat Changes

**Observations**:

- 157 `SEAT_CHANGE` events detected
- Common patterns:
  - Empty → assigned (check-in assigns seat)
  - Original → moved (passenger requests change, or auto-reseating)
  - Seat swap between passengers (detected as two changes)

**Significance**:

- Pre-assigned seat → changed at check-in: passenger preference
- Seat change close to departure: operational (balance, upgrade, group seating)
- Empty seat suddenly filled: late check-in or standby cleared

---

## 10. Flight Timeline

Typical flight lifecycle as seen from our data:

```
T-24h to T-1h:  OPENCI     Check-in open, passengers checking in
                            Snapshot shows mix of checked-in and not-checked-in
                            Bags being added, seats being assigned

T-1h to T-0:    OPENCI     Gate opens, boarding begins
                            Boarded indicators start appearing
                            Last-minute upgrades, standby clearances

T-0:            FINAL      All passengers processed
                            Final boarding count known
                            No-shows identified (checked-in but not boarded)

T+0 to T+?:    PDC        Post-departure close
                            Data frozen, no further changes
                            Historical record for analytics
```

**Our 9 flight timeline**:

- Future/Active flights: GF2006 (03-19, OPENCI), GF2057 (03-19, PDC), GF2274 (03-19, PDC)
- Recent flights: GF2006 (03-18, OPENCI), GF2057 (03-18, PDC), GF2130 (03-18), GF2754 (03-18)
- Past flights: GF2056 (03-16, PDC), GF2153 (03-17, PDC)

---

## 11. Infant Counting Discrepancy (Lap Infants)

**The Problem**: External dashboards (e.g., airline DCS/ops dashboards) report a different passenger total than Sabre's GetPassengerListRS API.

**Root Cause**: Lap infants do NOT appear as separate passenger records in Sabre. They are associated with a parent's record via the `INF` edit code. External systems count `totalSouls` (all humans on aircraft), while Sabre counts only seated passengers.

**Real-World Example — GF2274 DMM→HYD 2026-03-19**:

| Source             | Total   | Economy | Business | Adults | Children | Infants         |
| ------------------ | ------- | ------- | -------- | ------ | -------- | --------------- |
| External Dashboard | **198** | **186** | 12       | 184    | 8        | 6               |
| Sabre API          | **192** | **180** | 12       | 184    | 8        | 0 (not counted) |
| Delta              | **+6**  | **+6**  | 0        | 0      | 0        | **+6**          |

**Explanation**:

- Sabre returns 192 individual passenger records (184 adults + 8 children)
- 6 of those adults have the `INF` edit code → they are traveling with lap infants
- External dashboard counts: 184 + 8 + 6 = 198 totalSouls
- The 6 infants sit on parents' laps (no seat, no ticket, no individual record in Sabre)

**How to detect infants in Sabre data**:

- Look for `INF` in the `EditCodeList > EditCode[]` of a passenger
- Count of `INF` edit codes = number of lap infants on the flight
- Parent passenger record has the INF code, NOT the infant

**How to detect children in Sabre data**:

- Look for `CHD` in the `EditCodeList > EditCode[]`
- Children HAVE their own seat and ARE counted in Sabre's passenger total
- Children do NOT have M/F (male/female) gender edit codes

**Our solution**:

- `totalPassengers` = seated passenger records from Sabre (adults + children)
- `infantCount` = count of passengers with INF edit code
- `childCount` = count of passengers with CHD edit code
- `adultCount` = totalPassengers - childCount
- `totalSouls` = totalPassengers + infantCount (matches external dashboards)

---

## 12. Aircraft Type Code Mismatch

**The Problem**: Different systems use different coding schemes for the same aircraft.

**Real-World Example — GF2274**:

| System                 | Aircraft Code   | Meaning                                   |
| ---------------------- | --------------- | ----------------------------------------- |
| Sabre FlightStatus API | `321`           | IATA aircraft type code                   |
| External Dashboard     | `31D`           | IATA code with seat configuration variant |
| Manufacturer           | Airbus A321-200 | Full designation                          |

**Confirmation**: Registration number `A9CXA` matched exactly across both systems, confirming it's the same physical aircraft.

**Naming conventions**:

- **IATA equipment type**: 3-character code (e.g., `321` = Airbus A321, `789` = Boeing 787-9)
- **Variant codes**: Add a suffix for seat configuration (e.g., `32A`, `32B`, `31D` are different A320-family configurations)
- **ICAO code**: 4-character (e.g., `A321` = Airbus A321)
- **Sabre config number**: Found in `AircraftConfigNumber` field alongside the type

**Practical impact**: For matching/reconciliation, always use aircraft registration number as the unique identifier, never the type code alone.

---

## 13. Edit Code as Passenger DNA

**What**: The `EditCodeList > EditCode[]` array on each passenger is the richest data source for classification, far more informative than the `PassengerType` field.

**Observed edit codes and their meaning (from GF2274 sample of 148 pax)**:

| Code | Count | Category   | Meaning                                                   |
| ---- | ----- | ---------- | --------------------------------------------------------- |
| APP  | 148   | Status     | Approved for travel (all checked-in passengers have this) |
| DOCS | 148   | Document   | Passport/travel document on file                          |
| TKT  | 148   | Ticket     | Ticket number associated                                  |
| DOCV | 147   | Document   | Visa document verified (1 missing = potential alert)      |
| F    | 80    | Gender     | Female passenger                                          |
| M    | 62    | Gender     | Male passenger                                            |
| LC   | 12    | Connection | Long-haul connection / business class marker              |
| CHD  | 6     | Passenger  | Child passenger (partial check-in; 8 at final count)      |
| INF  | 2     | Passenger  | Parent with lap infant (partial; 6 at final count)        |
| DCVI | 2     | Document   | Document verification incomplete (alert!)                 |

**Key classification logic**:

- Has `M` or `F` → Adult (gender coded)
- Has `CHD` → Child (no gender code)
- Has `INF` → Parent traveling with infant (associated infant not in passenger list)
- Has `DCVI` → **Alert**: Document verification incomplete, potential boarding issue
- Missing `DOCV` when others have it → **Alert**: Visa not verified
- Has `LC` → Connecting passenger, likely business class

**Critical rule**: `M + F (80 + 62 = 142)` + `CHD (6)` = `148 total passengers`. The gender codes only apply to adults. This provides a cross-check for adult/child classification.

---

## 14. VCR Data Absence

**Expectation**: The `VCR_Info > VCR_Data` element should contain passenger voucher/coupon data with a `@type` attribute (adult/child/infant).

**Reality**: Sabre's GetPassengerListRS does NOT return `VCR_Info` or `VCR_Data` in any of our observed responses. This entire XML node is absent from the response body.

**Impact**: Cannot rely on VCR type for passenger classification. Use edit codes (CHD, INF, M, F) instead (see Scenario 13).

**Our handling**: The `vcrType` field in our converter returns empty string ("") for all passengers. This is correct behavior, not a bug.

---

## 15. Cross-System Data Reconciliation

**Context**: When comparing data from different sources (Sabre API vs DCS dashboard vs boarding gate system), discrepancies are expected and tell a story.

**Reconciliation checklist for passenger counts**:

| Check             | Formula                                 | Expected                                  |
| ----------------- | --------------------------------------- | ----------------------------------------- |
| Sabre seated pax  | adults + children                       | = totalPassengers                         |
| External total    | totalPassengers + infants               | = totalSouls                              |
| Adult cross-check | male(M) + female(F)                     | = adultCount (when all have gender codes) |
| Cabin split       | economy + business                      | = totalPassengers                         |
| Boarding check    | boarded + checkedIn-only + notCheckedIn | = totalPassengers                         |

**Reconciliation checklist for aircraft**:
| Check | Compare | Notes |
|-------|---------|-------|
| Aircraft identity | Registration number | **Most reliable** — unique per physical aircraft |
| Aircraft type | IATA code | Sabre uses base code (321), others may use variant (31D) |
| Seat config | ConfigNumber | Sabre returns this, verify matches expected layout |

**Red flags during reconciliation**:

- Total pax differs by exactly N where N = infant count → Infant counting difference (expected, see Scenario 11)
- Total pax differs by a number NOT equal to infant count → Genuine data discrepancy
- Aircraft type differs but registration matches → Code system difference (expected, see Scenario 12)
- Aircraft type AND registration differ → Wrong flight data or aircraft swap occurred

---

## 16. Passenger State Matrix at PDC

**What the final PDC snapshot reveals** (observed on GF2274):

| State                  | Count | Meaning                       |
| ---------------------- | ----- | ----------------------------- |
| Boarded                | 192   | All seated passengers boarded |
| CheckedIn + NotBoarded | 0     | No-shows (in this case none)  |
| NotCheckedIn           | 0     | Everyone checked in           |
| Jump Seat Cockpit      | 0     | No one in cockpit jump seat   |
| Jump Seat Cabin        | 0     | No one in cabin jump seat     |

**Typical PDC patterns across observed flights**:

| Flight           | Total | Boarded | No-Shows | Notes                                 |
| ---------------- | ----- | ------- | -------- | ------------------------------------- |
| GF2274 DMM 03-19 | 192   | 192     | 0        | Full boarding, 6 infants not in count |
| GF2056 DMM 03-16 | 196   | 196     | 0        | Full boarding                         |
| GF2057 BOM 03-18 | 19    | 19      | 0        | Small flight, full boarding           |
| GF2057 BOM 03-19 | 20    | 20      | 0        | Small flight, full boarding           |

**Insight**: All observed PDC flights had 100% boarding rates (no-shows = 0). This is likely because:

1. These are post-departure snapshots — anyone who didn't board was already removed
2. Gulf Air may clean up no-shows before PDC, removing them from the passenger list
3. Or these specific flights genuinely had zero no-shows

**To properly track no-shows**: Compare the OPENCI/FINAL snapshot (where checked-in-but-not-boarded passengers exist) against the PDC snapshot.
