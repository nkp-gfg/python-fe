# Flight Status Lifecycle

## Observed Statuses

| Status   | Meaning                 | When                                                 |
| -------- | ----------------------- | ---------------------------------------------------- |
| `OPENCI` | Open Check-In           | Check-in counters are open; passengers can check in  |
| `FINAL`  | Final                   | Boarding is complete; doors closed or about to close |
| `PDC`    | Post-Departure Checkout | Flight has departed; final reconciliation phase      |

## Lifecycle Flow

```
OPENCI → FINAL → PDC
```

### OPENCI (Open Check-In)

- Check-in counters are open
- Passengers are checking in (isCheckedIn transitions false→true)
- Seats are being assigned
- Boarding passes are being issued
- Bag counts are updating
- Passenger counts (Booked/Local) are still changing
- Gate and terminal are assigned
- New passengers can still be added (late bookings)
- Passengers can be removed (cancellations)

### FINAL (Final Boarding)

- Boarding is complete
- TotalOnBoard = actual boarded count
- TotalBoardingPassIssued = final count
- Passenger list reflects final state
- No-shows can be identified (CheckedIn=true but Boarded=false, or not on list at all)

### PDC (Post-Departure Checkout)

- Flight has departed
- Final reconciliation of all passenger data
- OnBoard counts are finalized
- This is the "ground truth" for who actually flew
- Offloaded passengers can be detected (were checked in but not in final PDC list)
- Late additions (staff, standbys) are finalized

## Key Observations from Live Data

1. **GF2006 LHR 2026-03-18**: Sample was PDC, but live data shows OPENCI for next day
   - The API returns data for the _current_ flight on that route, not historical
   - Gate changed 408→B41, Terminal changed 4→2 (different day = different gate assignment)

2. **GF2057 BOM 2026-03-19**: FINAL → PDC transition observed
   - Booked count dropped 56→55 (offloaded passenger?)
   - OnBoard stayed at 56 (all boarded passengers remained)

3. **GF2274 DMM 2026-03-19**: FINAL → PDC
   - Full flight: 180/180 Y class, 12/12 J class
   - No changes in counts between FINAL and PDC

## Important Notes

- **Flight Status API returns TODAY's data** — it does not return historical flights
- **Multiple calls same day will show progression**: OPENCI → FINAL → PDC
- **Passenger counts change at each stage** — must snapshot and compare
- **Gate/Terminal may change** even during OPENCI (operational reassignment)
