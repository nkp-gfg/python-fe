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
| S    | Standby — can be revenue or non-revenue (determined by Indicators array) |

### Cabin

| Code | Meaning                            |
| ---- | ---------------------------------- |
| Y    | Economy                            |
| J    | Business (Falcon Gold on Gulf Air) |

### DisplayCode (Request Filter)

Controls which subset of passengers is returned by GetPassengerListRQ.

| Code | Meaning                                                     |
| ---- | ----------------------------------------------------------- |
| RV   | Revenue passengers                                          |
| XRV  | Non-Revenue passengers (staff, deadhead, etc.)              |
| BP   | Passengers with Boarding Pass issued                        |
| PALL | Pending ALL — full upgrade/standby list including checked-in |
| P    | Pending only — upgrade/standby candidates NOT yet checked-in |

Our current request uses `RV + XRV + BP` (condition="OR") to get the complete manifest.
`P` and `PALL` are used for dedicated standby/upgrade list views.

### PriorityCode

| Code | Meaning                                        |
| ---- | ---------------------------------------------- |
| UPG  | Upgrade list — revenue passenger awaiting cabin upgrade |
| B01  | Standby priority 1 — highest non-revenue standby |
| B02  | Standby priority 2                             |
| B03+ | Lower standby priority levels                  |

Used in the standby/upgrade queue ordering. UPG passengers are sorted by LineNumber.
Standby (B01, B02, ...) passengers are sorted by SeniorityDate then LineNumber.

### DesiredBookingClass

Target cabin class for upgrade candidates. Present only when `PriorityCode` = UPG.

| Example | Meaning                                   |
| ------- | ----------------------------------------- |
| F       | Desires First Class / Business full fare  |
| J       | Desires Business class                    |

### CorpID

Corporate traveler indicator. Present on passengers with a corporate travel profile.

| Value | Meaning            |
| ----- | ------------------ |
| T     | Corporate traveler |

### SeniorityDate

Seniority date for non-revenue/staff passengers (format: `YYYY-MM-DD`).
Determines priority ordering in the standby queue — earlier date = higher priority.
Example: `1977-06-21` (employee hire date or seniority effective date).

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

### CabinInfo (from ItineraryInfo > CabinInfoList > CabinInfo[])

Per-cabin seat capacity and availability for each destination on the flight.

| Field       | Type | Description                                 |
| ----------- | ---- | ------------------------------------------- |
| Cabin       | str  | Cabin code (J = Business, Y = Economy)      |
| Destination | str  | Destination airport code for this cabin leg  |
| Authorized  | int  | Total authorized seats in the cabin          |
| Available   | int  | Remaining empty seats available for sale     |
| Count       | int  | Number of passengers currently in the cabin  |

Example from sample: `J cabin AUH→BLR: 16 authorized, 16 available` (empty business cabin).

### Edit Codes (from EditCodeList > EditCode[])

Critical codes for passenger classification:

- **CHD**: Child — passenger is a child (has own seat, counted in passenger list)
- **INF**: Infant — parent of a lap infant (infant does NOT appear as a separate passenger record)
- **M/F**: Meal/Fare codes — **NOT gender indicators** (see Gender Sources below)

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

### Gender — Authoritative Source

Gender is **NOT available** from GetPassengerListRS (the M/F edit codes are meal/fare codes).

Gender IS available from Trip_SearchRS via the APIS `DOCSEntry` (passport data):

| Field | Source | Example |
| ----- | ------ | ------- |
| `Gender` | `Passenger.SpecialRequests.APISRequest.DOCSEntry.Gender` | `M`, `F`, `MI` (Male Infant), `FI` (Female Infant) |
| `DateOfBirth` | `Passenger.SpecialRequests.APISRequest.DOCSEntry.DateOfBirth` | `1994-01-20` |
| `DocumentNationalityCountry` | `DOCSEntry.DocumentNationalityCountry` | `IN`, `BH`, `GB` |

Coverage: ~98% of passengers have DOCS entries with gender (passport data required for international flights).

The converter enriches each reservation passenger with `gender`, `dateOfBirth`, and `nationality` from DOCSEntry.
The dashboard cross-references reservation gender data with passenger list records (matched by PNR + lastName).

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
| GetPassengerDataRQ | `ns3:GetPassengerDataRS`               | `http://services.sabre.com/checkin/getPassengerData/v4` |
| Trip_SearchRQ      | `Trip_SearchRS`                        | `http://webservices.sabre.com/triprecord`               |
| SessionCreateRQ    | (in Header) `wsse:BinarySecurityToken` |                                                         |
| SessionCloseRQ     | `SessionCloseRS`                       |                                                         |

## Reservation Inner Namespaces

- `stl19:` — STL v19 namespace for reservation details
- `or114:` — Sabre reservation format
- `raw:` — Raw itinerary data

## Trip_ReportsRS Fields

### Report Types

| Code | Purpose | Sabre Name |
| ---- | ------- | ---------- |
| MLX  | List of CANCELLED passengers (offloaded, removed from flight) | Cancelled Passenger List |
| MLC  | List of ALL passengers ever booked on the flight (including cancelled) | Complete Historical Passenger List |

### Cancelled Passenger Entry (MLX)

| Field | Description |
| ----- | ----------- |
| `lastName` | Passenger last name |
| `firstName` | Passenger first name |
| `pnr` | PNR locator |
| `cancelReason` | Reason text if available |

### Offloaded & No-Show Detection

| Metric | Source | Logic |
| ------ | ------ | ----- |
| **Offloaded** | MLX report | Count of passengers in the cancelled list for this flight |
| **No-Show** | MLC vs passenger_list | Passengers in MLC (ever-booked) that are NOT in the current passenger_list AND flight status is FINAL or PDC |

## GetPassengerDataRQ Fields

### Overview

GetPassengerDataRQ is a **per-passenger detail API** — unlike GetPassengerListRQ which returns the full flight manifest, this API provides deep detail for one or more specific passengers identified by LastName + optional PNR within a flight itinerary.

**Namespace**: `http://services.sabre.com/checkin/getPassengerData/v4`
**Current version**: 4.0.4
**SOAPAction**: `GetPassengerDataRQ`

### Lookup Methods

| Method | Required Fields | Notes |
| ------ | -------------- | ----- |
| LastName only | Itinerary + PassengerList > LastName | May return `similarNameFound="true"` if ambiguous |
| LastName + PNR | Itinerary + PassengerList > LastName + PNRLocator | Most precise lookup |
| BagTagNumber | BagTagNumber (no itinerary needed) | v4.0.3+ only |
| GroupCode | Itinerary + GroupCode | Group booking lookup |

### Request Attributes

| Attribute | Values | Description |
| --------- | ------ | ----------- |
| `validateCheckInRequirements` | `true`/`false` | Include check-in requirement validation |
| `includeTimaticInfo` | `true`/`false` | Include Timatic travel document validation (v4.0.2+) |
| `version` | `4.0.0` – `4.0.4` | API version |
| `Client` | `KIOSK`, `WEB` | Client type indicator (v4.0.2+) |

### Response Status

| Status | CompletionStatus | Meaning |
| ------ | ---------------- | ------- |
| (empty) | Complete | Success — full data returned |
| BusinessLogicError | Incomplete | Partial failure (data may still be returned) |

### Error Codes

| Code | Message | Notes |
| ---- | ------- | ----- |
| 1211 | One or More Passengers Data Not Found | Passenger not on flight |
| 41007 | Did not find any ancillary data | Non-fatal — passenger data still returned |

### RequiredInfoSumList Codes (Check-In Requirements)

| Code | Meaning | Notes |
| ---- | ------- | ----- |
| GENDER | Gender validation required | Passenger gender not on file |
| DOCV | Visa document verification needed | Travel visa not validated |
| TIM | Timatic check required | Travel document validation (adult) |
| TIM/INF | Timatic check for infant | Travel docs for infant |
| DOCS_VAL | DOCS validation | Passport data validation |
| ESTA | ESTA authorization required | US Electronic Travel Authorization |
| DHS | DHS clearance required | US Dept. of Homeland Security |
| DOCA/R | Address documentation required | Residential address |
| BTP | Bag tag payment required | Excess baggage payment needed |

### DetailStatus Values

| Value | Meaning |
| ----- | ------- |
| ValidationRequired | Requirement pending — agent action needed |
| ValidationFailed | Validation attempted but failed |

### FreeTextInfo Edit Codes

| EditCode | Category | Content |
| -------- | -------- | ------- |
| DOCS | Passport | DOCS string: `P/{country}/{docNum}/{nationality}/{DOB}/{gender}/{expiry}/{lastName}/{firstName}` |
| DOCO | Visa | Visa document details |
| PCTC | Contact | Passenger contact information |
| APP | Approval | Clearance/approval status |
| INF | Infant | Infant details (e.g., `BABY BOY,6MO`) |
| BT | Bag Tag | Bag tag number(s) |
| TIM | Timatic | Timatic result (e.g., `OK TO BOARD`) |
| AE | Ancillary | Ancillary/EMD details |
| UK | Clearance | UK/immigration clearance |

### DOCS String Format

Format: `P/{docCountry}/{docNumber}/{nationality}/{DOB_ddMMMYY}/{gender}/{expiry_ddMMMYY}/{lastName}/{firstName}`

Example: `P/US/123456789/US/01JAN70/F/01JAN20/ALPHA/PAX`

Gender codes in DOCS string: `M` (male), `F` (female), `MI` (male infant), `FI` (female infant)

### AEDetails (Ancillary/EMD)

| Field | Description |
| ----- | ----------- |
| ATPCOGroupCode | `BG` = baggage, `SA` = seat assignment, `0B5` = seat (alternative) |
| StatusCode | EMD status |
| UsedEMD | Whether EMD has been used |
| Quantity | Number of items |
| PriceDetails | Amount + Currency |

### PassengerEditList (Edit Attributes)

The `PassengerEditList > Edit` elements contain detailed attributes for specific edit codes:

**TIM (Timatic) edit attributes:**
- `StayType`: VACATION, BUSINESS, etc.
- `DocumentType`: PASSPORT NORMAL, PASSPORT DIPLOMATIC, etc.
- `ResidencyDocumentType`: Document type for residency
- `VisaVerified`: Whether visa has been verified

### BaggageRoute Fields

| Field | Description |
| ----- | ----------- |
| LateCheckin | `true`/`false` — passenger checked in after cutoff |
| HomePrintedBagTag | Home-printed bag tag restriction |
| BagEmbargo | `true`/`false` — baggage embargo on this route |
| SegmentStatus | Status of the baggage routing segment |

### Codeshare Fields

On codeshare flights, the itinerary shows distinct operating vs marketing info:

| Field | Example | Description |
| ----- | ------- | ----------- |
| Airline / Flight | EY / 6418 | Ticketing airline/flight |
| OperatingAirline / OperatingFlight | VA / 880 | Actual operating carrier |
| MarketingAirline / MarketingFlight | EY / 6418 | Marketing carrier |
| BookingClass | I | Ticketed class |
| OperatingBookingClass | S | Class on operating carrier |
| MarketingBookingClass | I | Class on marketing carrier |
| ThirdPartyGroundHandled | true/false | Ground handling by third party |
