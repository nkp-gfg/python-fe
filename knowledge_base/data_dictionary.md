# Data Dictionary — All Known Field Values

## Flight Status Fields

### FlightStatus

| Value  | Meaning                                   |
| ------ | ----------------------------------------- |
| OPENCI | Open Check-In — counters active           |
| FINAL  | Final — boarding complete                 |
| PDC    | Post-Departure Checkout — flight departed |

## Passenger List Fields

### PassengerType

| Code | Meaning                             |
| ---- | ----------------------------------- |
| F    | Fare-paying (revenue) passenger     |
| P    | Prepaid / Group / Package passenger |
| E    | Employee / Staff (non-revenue)      |
| S    | Staff booking (crew/positioning)    |

### Cabin

| Code | Meaning                            |
| ---- | ---------------------------------- |
| Y    | Economy                            |
| J    | Business (Falcon Gold on Gulf Air) |

### BookingClass (Fare Buckets — observed)

| Class | Cabin | Typical Fare Level                   |
| ----- | ----- | ------------------------------------ |
| J     | J     | Business full fare                   |
| C     | J     | Business discounted                  |
| D     | J     | Business deeply discounted / upgrade |
| Z     | J     | Business lowest / codeshare          |
| Y     | Y     | Economy full fare                    |
| B     | Y     | Economy high                         |
| H     | Y     | Economy mid                          |
| K     | Y     | Economy mid                          |
| W     | Y     | Economy mid-low                      |
| V     | Y     | Economy low                          |
| S     | Y     | Economy low                          |
| Q     | Y     | Economy discounted                   |
| N     | Y     | Economy deeply discounted            |
| O     | Y     | Economy special/group                |
| X     | Y     | Economy lowest                       |
| G     | Y     | Economy group                        |
| I     | Y     | Economy special                      |

### Indicators (from Indicators > Indicator[])

| Value      | Meaning                            |
| ---------- | ---------------------------------- |
| Revenue    | Paying passenger                   |
| NonRevenue | Non-paying (staff, deadhead, etc.) |
| CheckedIn  | Has completed check-in             |
| NotBoarded | Has not boarded yet                |
| Boarded    | Has boarded the aircraft           |

### Edit Codes (from EditCodeList > EditCode[])

Critical codes for passenger classification:

- **CHD**: Child — passenger is a child (has own seat, counted in passenger list)
- **INF**: Infant — parent of a lap infant (infant does NOT appear as a separate passenger record)
- **M/F**: Gender codes (Male/Female) — present on adults/teens, not on children

| Code | Category   | Meaning                            |
| ---- | ---------- | ---------------------------------- |
| M    | Meal       | Standard meal requested            |
| AVML | Meal       | Asian Vegetarian Meal              |
| VJML | Meal       | Vegetarian Jain Meal               |
| CHML | Meal       | Child Meal                         |
| SM   | Meal       | Special Meal (generic)             |
| FF   | Loyalty    | Frequent Flyer                     |
| GLD  | Loyalty    | Gold tier member                   |
| SLV  | Loyalty    | Silver tier member                 |
| BLU  | Loyalty    | Blue tier member                   |
| DOCS | Document   | Travel document (passport) on file |
| DOCA | Document   | Address document on file           |
| DOCV | Document   | Visa document on file              |
| DCVI | Document   | Document verification incomplete   |
| ET   | Ticket     | Electronic ticket                  |
| ETI  | Ticket     | Electronic ticket issued           |
| TKT  | Ticket     | Ticket number                      |
| AE   | Ticket     | Additional e-ticket                |
| CKIN | Check-In   | Checked in                         |
| PRCH | Check-In   | Pre-checked in                     |
| PSTC | Check-In   | Post check-in                      |
| CHD  | Passenger  | Child passenger                    |
| INF  | Passenger  | Infant                             |
| WCHC | Assistance | Wheelchair (cabin seat)            |
| WCHR | Assistance | Wheelchair (ramp)                  |
| WCHS | Assistance | Wheelchair (steps)                 |
| APP  | Status     | Approved                           |
| OFL  | Status     | Offloaded                          |
| IFET | Service    | In-flight entertainment/tech       |
| IB   | Bag        | Interline bag                      |
| OB   | Bag        | Oversized bag                      |
| WB   | Bag        | Weight bag                         |
| EIB  | Bag        | Electronic interline bag           |
| LC   | Status     | Last connection                    |
| CM   | Status     | Crew member                        |
| MPR  | Status     | Missing passenger record           |
| F    | Fare       | Fare related                       |
| JF   | Service    | Jump seat forward                  |
| JS   | Service    | Jump seat                          |
| YF   | Service    | Economy fare                       |
| YL   | Service    | Economy low                        |
| YS   | Service    | Economy standard                   |

### VCR Types

Note: VCR_Info/VCR_Data is NOT present in GetPassengerListRS responses from Sabre.
The VCR type field in our converter returns empty string ("") for all passengers.
Passenger classification (adult/child/infant) is derived from **Edit Codes** instead.

### Passenger Counting — Critical Business Rule

| Count Field     | Description                                                   |
| --------------- | ------------------------------------------------------------- |
| totalPassengers | Number of passenger records from Sabre (excludes lap infants) |
| adultCount      | Passengers without CHD edit code (includes teens)             |
| childCount      | Passengers with CHD edit code (have own seat)                 |
| infantCount     | Count of INF edit codes (lap infants, no seat, no own record) |
| totalSouls      | totalPassengers + infantCount (all humans on aircraft)        |

**Why external dashboards show a higher total**: They count `totalSouls` (including infants).
Sabre's GetPassengerList returns only seated passengers as individual records.
Infants are associated with parent records via the INF edit code.

## Trip_SearchRS (Reservations) Fields

### ActionCode (Segment Status)

| Code | Meaning                      |
| ---- | ---------------------------- |
| HK   | Confirmed                    |
| HL   | Confirmed (waitlist cleared) |
| GK   | Passive (ghost) segment      |
| SS   | Sold                         |
| TK   | Ticketed                     |
| XX   | Cancelled                    |

### numberInParty

- Ranges from 1 to 94 (observed)
- Large values (94) indicate group bookings

### numberOfInfants

- 0-6 observed per reservation
- Infants travel on parent's lap (no separate seat)

## Sabre API Response Keys (with namespaces)

| API                | Response Key                           | Namespace                                               |
| ------------------ | -------------------------------------- | ------------------------------------------------------- |
| ACS_FlightDetailRQ | `ns3:ACS_FlightDetailRS`               | `http://services.sabre.com/ACS/BSO/flightDetail/v3`     |
| GetPassengerListRQ | `GetPassengerListRS`                   | `http://services.sabre.com/checkin/getPassengerList/v4` |
| Trip_SearchRQ      | `Trip_SearchRS`                        | `http://webservices.sabre.com/triprecord`               |
| SessionCreateRQ    | (in Header) `wsse:BinarySecurityToken` |                                                         |
| SessionCloseRQ     | `SessionCloseRS`                       |                                                         |

## Reservation Inner Namespaces

- `stl19:` — STL v19 namespace for reservation details
- `or114:` — Sabre reservation format
- `raw:` — Raw itinerary data
