# Dashboard

## Architecture

The frontend is a Next.js 16 App Router application with React 19, TypeScript, Tailwind CSS, and shadcn/ui.

**Entry:** `/flights/[flightNumber]?origin=X&date=Y` → `FlightWorkbench` component (~900+ lines)

**State management:** React Query v5 (`@tanstack/react-query`) — no Redux or Zustand.

**Charting:** Apache ECharts (primary, 12 builders) + Recharts (secondary, composition charts only).

## Tab Structure

`FlightWorkbench` renders 13 tabs:

| Tab ID         | Label         | Component                                                           |
| -------------- | ------------- | ------------------------------------------------------------------- |
| `overview`     | Live Ops      | `StatePanels` + `PhaseTimeline` + `PhaseAlertBanner`                |
| `commercial`   | Commercial    | `CommercialInsightsTab`                                             |
| `readiness`    | Readiness     | `ReadinessInsightsTab`                                              |
| `exceptions`   | Exceptions    | `ExceptionsInsightsTab`                                             |
| `passengers`   | Passengers    | `PassengerTable` with cabin/status/type/loyalty/nationality filters |
| `groups`       | Groups        | Group booking details                                               |
| `standby`      | Standby       | `StandbyPanel`                                                      |
| `changes`      | Changes       | `ChangeTimeline`                                                    |
| `history`      | History       | `HistoryOverview` (stacked area + hotspots + snapshot compare)      |
| `reservations` | Reservations  | `ReservationView`                                                   |
| `activity`     | Activity      | `FlightTimeline` (activity feed)                                    |
| `audit`        | Audit         | `AuditPanel`                                                        |
| `journey`      | Phase Journey | `PhaseJourney` → `PhaseTransitionChart`                             |

## Layout

```
┌─────────────────────────────────────────────────┐
│  Header: Logo | Search | Today | Data-Audit |   │
│          Live indicator | Theme | Refresh | ⚡   │
├────────┬────────────────────────────────────────┤
│        │  Tab Bar                               │
│ Flight │  ┌──────────────────────────────────┐  │
│  List  │  │                                  │  │
│ (280px)│  │  Active Tab Content              │  │
│        │  │                                  │  │
│ Filters│  │                                  │  │
│Calendar│  └──────────────────────────────────┘  │
│        │  Bottom Detail Panel (optional)        │
└────────┴────────────────────────────────────────┘
```

## React Query Configuration

**Provider** (`components/providers.tsx`):

- `staleTime: 30_000` (30s)
- `retry: 2` with exponential backoff (1s → 10s max)
- `refetchOnWindowFocus: false`

**Polling intervals:**
| Query | Interval |
|-------|----------|
| Flight list | 60s |
| Dashboard | 60s (disabled when viewing historical snapshot) |
| Flight tree | 60s |
| Boarding progress | 15s |
| Job status | 3s (stops on terminal state) |

## API Client (`frontend/lib/api.ts`)

31 typed fetch functions. Constants:

- `API_BASE` = `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`)
- `REQUEST_TIMEOUT_MS` = 30,000ms
- `INGEST_POLL_INTERVAL_MS` = 3,000ms
- `INGEST_JOB_TIMEOUT_MS` = 10 minutes

Key functions: `fetchFlights`, `fetchDashboard`, `fetchFlightTree`, `ingestFlight` (polls via batch+status), `fetchPassengers`, `fetchStandbyList`, `fetchPassengerDetail`, `fetchChanges`, `fetchSnapshots`, `compareSnapshot`, `fetchReservations`, `fetchAudit`, `fetchBoardingProgress`, `fetchPhaseJourney`, `fetchActivityFeed`, plus 15 more.

All snapshot-aware queries accept optional `snapshotSequence` param for historical views.

## ECharts Builders (`echarts-option-builders.ts`)

12 pure functions producing `EChartsOption`:

| Builder                              | Chart Type             | Used For                        |
| ------------------------------------ | ---------------------- | ------------------------------- |
| `buildDonutChartOption`              | Pie/donut              | Gender, channel, nationality    |
| `buildPassengerProgressFunnelOption` | Funnel                 | Sold→ticketed→BP→boarded        |
| `buildCabinStackedBarOption`         | Stacked horizontal bar | Y/J booking class mix           |
| `buildHistoryStackedAreaOption`      | Stacked area           | Booked/BP/OnBoard over time     |
| `buildBoardingProgressAreaOption`    | Dual area              | Check-in + boarding timelines   |
| `buildCheckInTimelineAreaOption`     | Area                   | Hourly check-in volume          |
| `buildHorizontalBarOption`           | Horizontal bar         | Change hotspots, destinations   |
| `buildVerticalBarOption`             | Vertical bar           | Booking window, passenger types |
| `buildPhaseTransitionSankeyOption`   | Sankey                 | Passenger flow between phases   |
| `buildPhaseStackedBarOption`         | Stacked bar            | Status breakdown per phase      |
| `buildPhaseDemographicBarOption`     | Grouped bar            | M/F/CHD/INF per phase           |
| `buildPhaseCabinBarOption`           | Grouped bar            | Economy/business per phase      |

Theme: dark-optimized (`ECHARTS_TEXT_COLOR: #e2e8f0`), 450ms animation, linear gradients for area fills.

## Insight Tabs (`flight-insight-tabs.tsx`)

Three tabs, each ~300 lines with reusable chart card components:

**Commercial:** Booking channels (donut), payment methods (bars), revenue class mix (cabin stacked), booking window (columns), channel composition, corporate accounts, passenger funnel (sold→ticketed→BP→checked-in→boarded)

**Readiness:** Document compliance (DOCS/DOCV/DOCA progress), readiness checklist (seats/BP/ticketed/baggage), baggage analytics, check-in timeline (hourly area), bag routing destinations, wheelchair services, meal requests

**Exceptions:** At-risk connections, operational blockers (no seat/BP/check-in), connection risk composition, class mismatch, action queue, standby pressure by cabin, priority codes, passenger type mix

Data source: `FlightInsights` (30+ sub-interfaces) computed by `_build_insights()` in `flights.py` backend route.

## Key Components

| Component              | Purpose                                                                |
| ---------------------- | ---------------------------------------------------------------------- |
| `BoardingProgress`     | Self-contained card, polls every 15s, ECharts area chart               |
| `HistoryOverview`      | Status history area chart + change hotspots + snapshot compare         |
| `PhaseTransitionChart` | 4 views: stacked, demographics, cabin, sankey. Fullscreen mode         |
| `IngestionPanel`       | Form for single/batch Sabre ingestion with date validation             |
| `PassengerDetailSheet` | Slide-out sheet with profile/itinerary/baggage/documents/timeline tabs |
| `PassengerTimeline`    | Chronological events with category icons and upgrade detection         |
| `PaxMatrix`            | Tabular breakdown from FlightTree data                                 |
| `PaxTree`              | SVG tree diagram with badge system (M/F/A/C/I)                         |
| `ErrorBoundary`        | Class-based with default and compact modes                             |

## TypeScript Types (`frontend/lib/types.ts`)

~1550 lines, 100+ interfaces mirroring backend Pydantic models. Major groups:

- Flight core (`FlightDashboard`, `FlightPhase`, `FlightListItem`)
- Passenger analysis (`PassengerAnalysis`, `CabinBreakdown`, `StateBucket`)
- Tree visualization (`FlightTree`, `FlightTreeNode`, `FlightTreeEdge`)
- Sabre ingestion (`SabreIngestRequest`, `SabreJobStatus`)
- Changes & snapshots (`ChangeRecord`, `SnapshotCompareResponse`)
- Reservations (`Reservation`, `ReservationPassenger`)
- Timelines (`TimelineEvent`, `ActivityFeedEvent`, `PhaseJourneyResponse`)
- Flight insights (30+ sub-interfaces under `FlightInsights`)
- Audit (`AuditAlert`, `AuditResponse`)

## Fonts & Styling

- Fonts: Roboto + Roboto Mono via `next/font/google`
- Theme: dark default via `next-themes`, class-based switching
- Colors: oklch palette in Tailwind config
- UI: shadcn/ui primitives (Badge, Button, Card, Dialog, Sheet, Tabs, etc.)
