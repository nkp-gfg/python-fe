# ECharts Plan From MongoDB Data

## Purpose

This document converts the current MongoDB-backed FalconEye data model into concrete ECharts implementation options.

It is based on:

- `passenger_list`, `reservations`, `flight_status`, `trip_reports`, `snapshots`, and `changes`
- dashboard aggregation logic in [backend/api/routes/flights.py](../backend/api/routes/flights.py)
- server-side comparison and analytics in [backend/feeder/aggregations.py](../backend/feeder/aggregations.py)
- snapshot delta endpoints in [backend/api/routes/changes.py](../backend/api/routes/changes.py)

This is intended as an implementation backlog for the frontend after installing `echarts`.

## Collection To Chart Map

### 1. `passenger_list`

Best for:

- passenger state progression
- cabin mix and load
- booking class mix
- seat assignment coverage
- baggage routing
- standby and upgrade queue
- groups

Key fields:

- `passengers[]`
- `cabinSummary[]`
- `totalPassengers`
- `adultCount`, `childCount`, `infantCount`
- `groupBookings[]`

Recommended charts:

1. Stacked bar for `booked`, `checkedIn`, `boarded` by cabin.
2. Funnel for `booked -> checkedIn -> boardingPassIssued -> boarded`.
3. Heatmap for check-in activity by hour and cabin.
4. Sankey for `origin state -> cabin -> boarded state`.
5. Treemap or sunburst for `cabin -> bookingClass -> passengerType`.
6. Scatter or bubble chart for group size vs boarded ratio.

### 2. `reservations`

Best for:

- booking channels
- payment methods
- party size
- special requests
- frequent flyer and tier mix
- multi-segment selling pattern

Key fields:

- `reservations[].pointOfSale.agentSine`
- `reservations[].formOfPayment`
- `reservations[].numberInParty`
- `reservations[].passengers[]`
- `reservations[].segments[]`
- `reservations[].ssrRequests[]`

Recommended charts:

1. Donut for booking channels.
2. Horizontal bar for payment methods.
3. Histogram for party size.
4. Radar for frequent flyer or tier mix.
5. Sankey for `channel -> cabin -> ticketed state`.
6. Treemap for SSR code concentration.

### 3. `flight_status`

Best for:

- operational state progression
- capacity and availability
- gate and terminal changes
- time evolution of schedule fields

Key fields:

- `status`
- `gate`
- `terminal`
- `schedule`
- `boarding`
- `passengerCounts`

Recommended charts:

1. Step line for status evolution over time.
2. Stacked bar for `authorized`, `booked`, `available`, `onBoard`, `boardingPasses` by cabin.
3. Custom milestone timeline for STD, ETD, boarding, gate changes.
4. Gauge for operational readiness percentage.

### 4. `changes`

Best for:

- change frequency
- anomaly density
- passenger movement patterns
- operational volatility

Key fields:

- `changeType`
- `detectedAt`
- `passenger`
- `field`
- `oldValue`, `newValue`
- `metadata`

Recommended charts:

1. Ranked bar for top change types.
2. Treemap for change concentration by type.
3. Line or area chart for change volume over time.
4. Calendar heatmap for changes by day.
5. Sankey for `changeType -> field -> passenger outcome` where meaningful.

### 5. `snapshots`

Best for:

- historical progression
- version drift
- sequence-based comparison
- snapshot density

Key fields:

- `snapshotType`
- `sequenceNumber`
- `fetchedAt`
- `checksum`
- `data`

Recommended charts:

1. Line chart for passenger and boarding metrics over snapshot sequence.
2. Grouped comparison bars for selected snapshot vs latest.
3. Waterfall for delta between historical and latest values.
4. Calendar heatmap for snapshot density.

### 6. `trip_reports`

Best for:

- no-show and offload analysis
- passenger loss by cabin or type
- operational fallout from cancellations

Key fields:

- `reportType`
- `totalPassengers`
- `passengers[]`

Recommended charts:

1. Funnel for `ever booked -> checked in -> boarded -> no show/offloaded`.
2. Grouped stacked bar for no-show and offload by cabin.
3. Treemap for offload concentration by booking class or passenger type.

## Concrete ECharts Option Sets

These are implementation-oriented chart patterns rather than final polished UI configs.

### 1. Booking Channel Donut

Mongo source:

- `reservations[].pointOfSale.agentSine`
- or existing `insights.bookingChannels.channels`

Best ECharts family:

- `pie` / donut

Data shape:

```ts
type DonutDatum = { name: string; value: number };
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "item" },
  legend: { bottom: 0, textStyle: { color: "#94a3b8" } },
  series: [
    {
      name: "Booking Channels",
      type: "pie",
      radius: ["52%", "76%"],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 8, borderColor: "#0b1220", borderWidth: 2 },
      label: { show: false },
      emphasis: { label: { show: true, fontSize: 14, fontWeight: 600 } },
      data,
    },
  ],
};
```

Use when:

- category count is small to medium
- you want share of sales at a glance

### 2. Revenue Class Mix Under Y and J

Mongo source:

- `passengers[].bookingClass`
- or existing `insights.revenueClassMix`

Best ECharts family:

- stacked horizontal `bar`

Suggested grouping:

- `J`: `J`, `C`, `D`, `Z`, `R`, `F`
- `Y`: `Y`, `B`, `H`, `K`, `W`, `V`, `S`, `Q`, `N`, `O`, `X`, `G`, `M`, `L`, `U`, `T`

Data shape:

```ts
type CabinRow = {
  cabin: "Y" | "J";
  J?: number;
  C?: number;
  D?: number;
  Z?: number;
  R?: number;
  F?: number;
  Y?: number;
  B?: number;
  H?: number;
  K?: number;
  W?: number;
  V?: number;
  S?: number;
  Q?: number;
  N?: number;
  O?: number;
  X?: number;
  G?: number;
};
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
  xAxis: { type: "value" },
  yAxis: { type: "category", data: ["Y Class", "J Class"] },
  series: bookingClasses.map((cls) => ({
    name: cls,
    type: "bar",
    stack: cls in businessClasses ? "J" : "Y",
    emphasis: { focus: "series" },
    data: rows.map((r) => r[cls] ?? 0),
  })),
};
```

Use when:

- you want both cabin total and class composition in one chart

### 3. Passenger Progress Funnel

Mongo source:

- `passenger_list.passengers[]`
- `stateSummary`

Best ECharts family:

- `funnel`

Data shape:

```ts
[
  { name: "Booked", value: booked },
  { name: "Checked In", value: checkedIn },
  { name: "Boarding Pass", value: issued },
  { name: "Boarded", value: boarded },
];
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "item" },
  series: [
    {
      type: "funnel",
      top: 16,
      bottom: 16,
      left: "10%",
      width: "80%",
      sort: "descending",
      gap: 4,
      label: { show: true, position: "inside" },
      data,
    },
  ],
};
```

### 4. Check-In Timeline

Mongo source:

- `passengers[].checkInTime`
- `insights.checkInTimeline.hourDistribution`

Best ECharts family:

- `line` or `area`

Data shape:

```ts
type HourPoint = { hour: string; value: number };
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "axis" },
  xAxis: { type: "category", data: hours },
  yAxis: { type: "value" },
  series: [
    {
      type: "line",
      smooth: true,
      areaStyle: {},
      data: values,
    },
  ],
};
```

### 5. Change Type Treemap

Mongo source:

- `changes.changeType`
- or aggregated `changeVelocity.changeTypes`

Best ECharts family:

- `treemap`

Data shape:

```ts
type TreemapNode = { name: string; value: number };
```

Option skeleton:

```ts
const option = {
  tooltip: { formatter: "{b}: {c}" },
  series: [
    {
      type: "treemap",
      roam: false,
      breadcrumb: { show: false },
      label: { show: true },
      data,
    },
  ],
};
```

### 6. Snapshot Delta Comparison

Mongo source:

- `snapshots`
- compare endpoint result from `SnapshotCompareResponse`

Best ECharts family:

- grouped `bar`

Data shape:

```ts
type DeltaRow = {
  field: string;
  selected: number;
  latest: number;
};
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
  legend: { data: ["Selected", "Latest"] },
  xAxis: { type: "value" },
  yAxis: { type: "category", data: fields },
  series: [
    { name: "Selected", type: "bar", data: selectedValues },
    { name: "Latest", type: "bar", data: latestValues },
  ],
};
```

### 7. Baggage Route Flow

Mongo source:

- `passengers[].baggageRoutes[]`

Best ECharts family:

- `sankey`

Data shape:

```ts
type SankeyLink = { source: string; target: string; value: number };
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "item" },
  series: [
    {
      type: "sankey",
      data: nodes,
      links,
      emphasis: { focus: "adjacency" },
      lineStyle: { curveness: 0.45 },
    },
  ],
};
```

### 8. Group Booking Bubble Chart

Mongo source:

- `groupBookings[]`

Best ECharts family:

- `scatter`

Data shape:

```ts
type BubblePoint = {
  value: [totalMembers, boardedPct, unnamedMembers, totalMembers];
  name: string;
};
```

Option skeleton:

```ts
const option = {
  tooltip: { trigger: "item" },
  xAxis: { name: "Group Size" },
  yAxis: { name: "Boarded %" },
  series: [
    {
      type: "scatter",
      symbolSize: (val) => Math.max(12, val[3] * 2),
      data,
    },
  ],
};
```

### 9. Passenger Mix Sunburst

Mongo source:

- `passengers[].cabin`
- `passengers[].bookingClass`
- `passengers[].passengerType`

Best ECharts family:

- `sunburst`

Data shape:

```ts
type SunburstNode = { name: string; value?: number; children?: SunburstNode[] };
```

Option skeleton:

```ts
const option = {
  series: [
    {
      type: "sunburst",
      radius: [0, "88%"],
      data,
      label: { rotate: "radial" },
    },
  ],
};
```

### 10. Operational Milestone Timeline

Mongo source:

- `flight_status.schedule`
- `flight_status.boarding`
- `changes` for gate/status changes

Best ECharts family:

- `custom`

Data shape:

```ts
type Milestone = { name: string; start: number; end?: number; type: string };
```

Option skeleton:

```ts
const option = {
  xAxis: { type: "time" },
  yAxis: { type: "category", data: ["Flight"] },
  series: [
    {
      type: "custom",
      renderItem: customMilestoneRenderer,
      data,
    },
  ],
};
```

## Top 10 Charts Ranked By Business Value

This ranking assumes the current single-flight dashboard and current Mongo-backed collections.

### 1. Passenger Progress Funnel

Why first:

- best operational overview for departure readiness
- maps directly to station workflow
- simplest story for ops teams

Source:

- `passenger_list`
- `stateSummary`

ECharts family:

- `funnel`

### 2. Check-In Timeline

Why second:

- exposes flow and operational pacing, not just static counts
- useful for spotting late check-in clustering

Source:

- `passenger_list.checkInTime`
- `insights.checkInTimeline`

ECharts family:

- `line` / `area`

### 3. Booking Channel Donut

Why third:

- immediate commercial insight
- low implementation effort
- good executive summary chart

Source:

- `reservations`
- `insights.bookingChannels`

ECharts family:

- `pie` donut

### 4. Revenue Class Mix Under Y and J

Why fourth:

- directly relevant to revenue and cabin performance
- better than raw fare-bucket bars for managers

Source:

- `passengers[].bookingClass`
- `insights.revenueClassMix`

ECharts family:

- stacked `bar`

### 5. Snapshot Delta Comparison

Why fifth:

- strongest historical diagnostics value
- already supported by backend compare endpoint

Source:

- `snapshots`
- `SnapshotCompareResponse`

ECharts family:

- grouped `bar`

### 6. Change Type Treemap

Why sixth:

- shows operational volatility quickly
- helps audit and support teams focus on dominant issues

Source:

- `changes`

ECharts family:

- `treemap`

### 7. Baggage Route Flow Sankey

Why seventh:

- unique airline-specific insight
- visually strong for transfer and handling flows

Source:

- `passengers[].baggageRoutes[]`

ECharts family:

- `sankey`

### 8. Group Booking Bubble Chart

Why eighth:

- helps detect large parties lagging in check-in or boarding
- operationally useful on group-heavy flights

Source:

- `groupBookings[]`

ECharts family:

- `scatter`

### 9. Passenger Mix Sunburst

Why ninth:

- good for exploratory analytics and management views
- useful when cabin, fare, and type relationships matter

Source:

- `passenger_list`

ECharts family:

- `sunburst`

### 10. Operational Milestone Timeline

Why tenth:

- visually strong and domain-specific
- useful for history and disruption analysis
- slightly higher implementation effort due to custom rendering

Source:

- `flight_status`
- `changes`

ECharts family:

- `custom`

## Recommended Build Order

If only a few ECharts panels are implemented first, use this order:

1. Booking Channel Donut
2. Revenue Class Mix Under Y and J
3. Passenger Progress Funnel
4. Check-In Timeline
5. Snapshot Delta Comparison
6. Change Type Treemap

This sequence gives a mix of commercial, operational, and historical value while staying close to the data already exposed in the frontend.

## Notes For Frontend Integration

1. Prefer `echarts` option builders in dedicated chart files rather than inline option objects inside large page components.
2. Reuse current backend `insights` payloads where possible before adding new API endpoints.
3. Use direct collection-backed endpoints only when a chart truly needs raw or historical shape not already exposed by `/dashboard`.
4. For timeline, sankey, and sunburst charts, define narrow typed view models in the frontend before building options.
