# Dashboard Blueprint

## Goal

Turn the current single-flight dashboard into a clearer operational product by separating:

- live operations monitoring
- commercial and booking analysis
- passenger readiness and compliance
- exception handling
- historical comparison

This blueprint is aligned to the current frontend workbench in [frontend/components/dashboard/flight-workbench.tsx](frontend/components/dashboard/flight-workbench.tsx) and the current insights grid in [frontend/components/dashboard/flight-insights.tsx](frontend/components/dashboard/flight-insights.tsx).

## Recommended Dashboard Types

### 1. Flight Operations Dashboard

Primary user:
station operations, gate teams, airport duty managers

Primary questions:

- Is the flight operationally ready?
- How far through check-in and boarding are we?
- What needs intervention in the next 15 to 30 minutes?

Core modules:

- boarding progress
- milestone timeline
- seat assignment completion
- baggage coverage
- gate and published schedule
- readiness alerts

Best chart types:

- progress bars and bullet charts for completion metrics
- milestone timeline for flight events
- line or area sparkline for boarding/check-in pace
- stacked bar for boarded, checked-in, remaining

### 2. Commercial Mix Dashboard

Primary user:
revenue, network, sales, route analysts

Primary questions:

- How was the flight sold?
- What demand pattern does this departure show?
- Which channel, class, and payment mixes dominate?

Core modules:

- booking channels
- revenue class mix
- booking lead time distribution
- payment methods
- corporate travel mix
- ticket and VCR status

Best chart types:

- ranked horizontal bars for channels and payment methods
- treemap or horizontal bars for revenue class mix
- histogram for booking lead time
- grouped bars for comparison against route or prior departures

### 3. Passenger Readiness Dashboard

Primary user:
check-in supervisors, document teams, customer service leads

Primary questions:

- Are passengers document-ready to travel?
- Who still needs seat, bag, or document completion?
- Are special handling passengers fully prepared?

Core modules:

- document compliance
- seat occupancy
- boarding pass issuance
- baggage coverage
- wheelchair and meal code coverage
- passenger type mix

Best chart types:

- bullet charts for DOCS, DOCV, DOCA coverage
- stacked status bars for seated vs unseated
- ranked lists for wheelchair and meal codes
- compact exception tables for incomplete passengers

### 4. Exception and Risk Dashboard

Primary user:
operations control, disruption teams, service recovery teams

Primary questions:

- Which passengers or categories are at risk?
- What is likely to create departure delay or service failure?

Core modules:

- connection risk
- class mismatch
- missing documents
- no-seat passengers
- no-ticket passengers
- standby and upgrade pressure

Best chart types:

- alert tiles with thresholds
- bullet charts with red zones
- ranked exception tables
- funnel for booked to checked-in to boarded to departed

### 5. Historical Comparison Dashboard

Primary user:
analysts, product owners, station management

Primary questions:

- How does this flight compare with prior departures?
- Are current patterns normal or unusual?

Core modules:

- trend versus previous same-flight departures
- boarding completion time comparison
- booking lead time comparison
- class mix comparison
- no-show and offload comparison

Best chart types:

- small multiple sparklines
- grouped bars by departure date
- box plots for lead-time spread
- deviation cards with route baseline deltas

## Chart Mapping For Current Insights Grid

The current insights panel in [frontend/components/dashboard/flight-insights.tsx](frontend/components/dashboard/flight-insights.tsx) uses mostly ranked progress rows. That is a strong fallback, but some metrics would communicate more clearly with different chart forms.

| Current Module       | Current Pattern            | Recommended Primary Chart                       | Why                                             |
| -------------------- | -------------------------- | ----------------------------------------------- | ----------------------------------------------- |
| Connecting vs Local  | Two horizontal bars        | 100% stacked horizontal bar                     | Better for composition at a glance              |
| Booking Channels     | Ranked bars                | Sorted horizontal bar chart                     | Best for long labels and category ranking       |
| Payment Methods      | Ranked bars                | Horizontal bar or lollipop                      | Handles long payment labels cleanly             |
| Document Compliance  | Ranked bars                | Bullet charts                                   | Shows actual, target, and threshold together    |
| Revenue Class Mix    | Ranked bars                | Horizontal bars or treemap                      | Better reflects class concentration             |
| Baggage Analytics    | KPI mini-stats + progress  | KPI cards + histogram                           | Totals plus shape of bag distribution           |
| Booking Lead Time    | KPI cards + key-value rows | Histogram with bucket labels                    | Distribution matters more than average          |
| Check-in Sequence    | KPI cards                  | Line chart or sparkline with markers            | Better if sequence progression is important     |
| Seat Occupancy       | Two horizontal bars        | Bullet chart or 100% stacked bar                | Clear readiness view                            |
| Corporate Travel     | Ranked bars                | Horizontal bars                                 | Correct for category ranking                    |
| Ticket / VCR Status  | Two bars + pills           | Bullet chart + breakdown table                  | Fast status plus detail                         |
| Class Mismatch       | KPI cards                  | Diverging bar or split KPI                      | Shows upgrade vs downgrade balance              |
| Connection Risk      | KPI cards + progress       | Bullet chart + risk table                       | Better actionability                            |
| Priority Passengers  | Key-value rows             | Ranked bars if volume grows                     | Current format is fine for low-cardinality data |
| Passenger Types      | Ranked bars                | Sorted bars or donut if only a few stable types | Depends on category count                       |
| Wheelchair Breakdown | Key-value rows             | Ranked bars                                     | Better scan when types increase                 |
| Meal Codes           | Key-value rows             | Ranked bars                                     | Better for top code comparison                  |

## Recommended Single-Flight Page Structure

The current workbench already has strong tab-level separation in [frontend/components/dashboard/flight-workbench.tsx](frontend/components/dashboard/flight-workbench.tsx). The next refinement should be to reshape the top-level experience around user intent.

### Proposed Top Tabs

- Live Ops
- Commercial
- Readiness
- Exceptions
- Passengers
- Changes
- History
- Reservations
- Audit

### Suggested Mapping From Current Tabs

| Current Tab  | Suggested Destination                                         |
| ------------ | ------------------------------------------------------------- |
| overview     | Live Ops                                                      |
| insights     | split across Commercial, Readiness, and Exceptions            |
| passengers   | Passengers                                                    |
| standby      | Exceptions or Passengers subview                              |
| changes      | Changes                                                       |
| history      | History                                                       |
| reservations | Reservations                                                  |
| audit        | Audit                                                         |
| groups       | Passengers subview or separate commercial group-booking panel |
| activity     | Live Ops support panel                                        |

## Wireframe For Single-Flight Dashboard

```text
+----------------------------------------------------------------------------------+
| Flight Header: GF2016 | BAH -> DMM | T-1h12m | Gate A3 | Snapshot / Live Status |
+----------------------------------------------------------------------------------+
| KPI Strip: Load | Checked-in | Boarded | Seated | Docs Ready | Bags Ready        |
+----------------------------------------------------------------------------------+
| Live Ops                                                                    Tab |
+--------------------------------------+-------------------------------------------+
| Left rail                            | Main canvas                               |
| - flight selector                    | 1. Boarding progress timeline             |
| - date/history mode                  | 2. Milestones and operational alerts      |
| - filter chips                       | 3. Seat and baggage readiness             |
| - quick actions                      | 4. Risk queue and incomplete passengers   |
+--------------------------------------+-------------------------------------------+
| Bottom panel: passenger table | standby | reservations | change log | audit      |
+----------------------------------------------------------------------------------+
```

### Commercial Tab Wireframe

```text
+----------------------------------------------------------------------------------+
| KPI Strip: Total Pax | Economy | Business | Corporate | Ticketed | Avg Lead Time |
+----------------------------------------------------------------------------------+
| Row 1: Booking Channels | Revenue Class Mix | Payment Methods                     |
| Row 2: Booking Lead Time Histogram | Group Bookings | Corporate Accounts           |
| Row 3: Comparison vs Last 7 Same-Flight Departures                               |
+----------------------------------------------------------------------------------+
```

### Readiness Tab Wireframe

```text
+----------------------------------------------------------------------------------+
| KPI Strip: Docs % | Seat % | BP Issued % | Bags % | Wheelchair Pax | Meal Pax     |
+----------------------------------------------------------------------------------+
| Row 1: Document Compliance Bullets | Seat Readiness | Boarding Pass Status         |
| Row 2: Baggage Coverage | Special Services | Incomplete Passenger Exceptions      |
+----------------------------------------------------------------------------------+
```

## Visual Hierarchy Guidance

### Use different visual weights by information type

- KPI cards for single headline numbers only
- bullet or stacked bars for readiness and completion
- ranked bars for categorical comparisons
- timelines for operational progression
- tables for passenger-level action items

### Avoid mixing all metrics in one equal-weight grid

The current insights grid gives similar emphasis to both critical operational data and secondary descriptive data. This makes the page feel dense and flat. The next version should promote:

- intervention metrics first
- explanatory distributions second
- descriptive taxonomy last

### Good separation of metric roles

- status: boarded, checked-in, seated, docs-ready
- mix: channels, classes, meal types, wheelchair types
- risk: no ticket, no seat, connection risk, mismatch
- trend: lead time, check-in pace, historical comparison

## Implementation Priorities

### Phase 1

- split current Insights content into three tab destinations: Commercial, Readiness, Exceptions
- keep existing data contracts
- replace only the highest-value visuals first

### Phase 2

- introduce chart components using the existing chart wrapper in [frontend/components/ui/chart.tsx](frontend/components/ui/chart.tsx)
- add small historical comparisons for same flight number and route
- add threshold states and warnings to readiness charts

### Phase 3

- add portfolio and route dashboards for multi-flight analysis
- add cross-flight comparison and anomaly detection views

## Recommended Immediate UI Refactor

If only one near-term redesign is done, it should be this:

1. Keep `Overview` focused on live operations only.
2. Move booking channels, payment methods, revenue class mix, and booking lead time into a new `Commercial` tab.
3. Move document compliance, seat occupancy, boarding pass issuance, baggage, wheelchair, and meal coverage into a new `Readiness` tab.
4. Move connection risk, class mismatch, no-ticket, no-seat, and standby pressure into a new `Exceptions` tab.

This yields a cleaner mental model without requiring backend contract changes.
