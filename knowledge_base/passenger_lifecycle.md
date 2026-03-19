# Passenger Lifecycle — State Transitions

## States (from Indicators field)

A passenger can have these indicator combinations:

| Indicators                            | Meaning                               |
| ------------------------------------- | ------------------------------------- |
| `[Revenue, NotBoarded]`               | Booked, not checked in                |
| `[Revenue, CheckedIn, NotBoarded]`    | Checked in, not yet boarded           |
| `[Revenue, CheckedIn, Boarded]`       | Checked in and boarded                |
| `[NonRevenue, NotBoarded]`            | Non-revenue passenger, not checked in |
| `[NonRevenue, CheckedIn, NotBoarded]` | Non-rev checked in, not boarded       |
| `[NonRevenue, CheckedIn, Boarded]`    | Non-rev checked in and boarded        |

## Lifecycle Flow

```
BOOKED → CHECKED_IN → BOARDED → (PDC confirmed)
   ↓         ↓            ↓
CANCELLED  NO_SHOW    OFFLOADED
```

### State Transitions Observed

From analysis of GF2006 LHR 2026-03-18 (sample=early check-in → live=PDC):

1. **Booked → Checked-In** (39 passengers)
   - `isCheckedIn`: false → true
   - Seat assigned (empty → "21A")
   - BagCount may increase (0 → 2)

2. **Checked-In → Boarded** (42 passengers)
   - `isBoarded`: false → true
   - All previously checked-in passengers boarded

3. **Passenger Added** (3 passengers post-booking)
   - New passengers appear in later snapshot
   - Late bookings, standbys accepted, or staff added

4. **Passenger Removed** (1 passenger — AL JAMEA/MOHAMED MR)
   - Disappeared between snapshots
   - Possible: cancellation, name change, no-show cleanup, offload

5. **Cabin Change / Upgrade** (4 passengers on GF2006)
   - Cabin changed Y → J (upgrade to business)
   - Booking class also changed (e.g., V → D, Q → D)
   - Happens at check-in or during boarding

6. **Seat Change** (36 passengers)
   - Most: empty → assigned seat (during check-in)
   - Some: seat A → seat B (reassignment/upgrade)

7. **Bag Count Change** (36 passengers)
   - 0 → 1, 0 → 2, etc. (bags checked in during check-in)

## GF2274 DMM 2026-03-19 — Full Timeline

This flight had TWO sample snapshots plus a live fetch, showing the complete lifecycle:

### Snapshot 1 (148 pax, all CheckedIn, 0 Boarded)

- Check-in complete, boarding not started
- All 148 passengers had seats and check-in status

### Snapshot 2 (192 pax, all CheckedIn, all Boarded)

- 44 new passengers added (late check-ins, standby, groups)
- All 148 original passengers now Boarded
- All 44 new passengers also Boarded
- Full flight: 180Y + 12J = 192 total (matching 180+12 authorized)

### Changes Detected

- **44 PASSENGER_ADDED**: Late arrivals added between check-in close and boarding
- **148 BOARDED**: All checked-in passengers boarded
- **1 BAG_COUNT_CHANGE**: BANDA/VIJAYA BHASKAR changed 1 → 2 bags

## Group Bookings

Observed PNR patterns:

- `IQOWOH`, `LACREE` — large group PNRs on GF2274 (94 and 12+ passengers)
- Group bookings have `GroupCode` field set (e.g., "AB3")
- Individuals within groups share the same PNR but have unique `PassengerID`
- Party size from Trip_SearchRS: min=1, max=94 on GF2274

## Non-Revenue Passengers

- **Type E**: Staff/employee (observed on GF2057)
- Indicator includes `NonRevenue`
- May not have seat assigned (`noSeat` count)
- Can be standby or confirmed

## Identification Keys

To uniquely track a passenger across snapshots:

- **Primary**: `PNR + LastName + FirstName` (composite key)
- **Secondary**: `PassengerID` (Sabre-assigned, but may change)
- **PNR alone is NOT unique** — group bookings share PNR

## No-Show Detection

Compare FINAL/PDC passenger list against OPENCI passenger list:

- Present in OPENCI but NOT in PDC → No-Show or Offloaded
- Present in OPENCI, CheckedIn=true but Boarded=false in PDC → No-Show (checked in but didn't board)
- Present in OPENCI, CheckedIn=false in PDC → No-Show (never checked in)
- **Important**: Passengers without tickets should NOT be flagged as no-show (they're "Booked" only)
