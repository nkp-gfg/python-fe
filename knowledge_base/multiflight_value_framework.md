# MultiFlight Availability Value Framework (One Page)

## Objective

Turn class-level availability signals into faster commercial and operational decisions for each departure, with measurable impact in 90 days.

## Problem Today (Baseline)

- Decision-makers rely mostly on status, manifest, and reservation snapshots.
- Inventory pressure by class is often inferred manually and late.
- Disruption handling (offload, reaccommodation, protection) lacks a shared class-availability source of truth.
- Teams reconcile decisions post-fact with fragmented evidence.

## What This Capability Adds

- Sabre MultiFlight availability integrated into backend ingestion and on-demand lookup.
- Normalized segment/class seats in API and frontend Availability tab.
- Historical snapshots for trend and auditability.
- Unified route/segment view for commercial + ops users.

## Baseline Metrics (Day 0)

Use last 30 days for baseline collection before rollout expansion.

- Decision latency:
  - Time from trigger (inventory pressure/disruption) to action
  - Baseline: capture median and p90 minutes per station
- Manual effort:
  - Analyst/operator time spent producing availability insight
  - Baseline: hours per day/week
- Forecast/action quality:
  - % flights where premium/economy seat actions happened after late threshold
  - Baseline: percentage by route family
- Disruption effectiveness:
  - Time to publish reaccommodation strategy for impacted flights
  - Baseline: median and p90 minutes
- Data confidence:
  - % operational decisions with auditable source snapshot attached
  - Baseline: percentage

## 90-Day Target Metrics

Targets should be set per route cluster; suggested initial goals:

- Decision latency:
  - Reduce median by 30%
  - Reduce p90 by 25%
- Manual effort:
  - Reduce analyst manual effort by 40%
- Forecast/action quality:
  - Reduce late inventory interventions by 20%
- Disruption effectiveness:
  - Reduce reaccommodation decision time by 20%
- Data confidence:
  - Achieve >= 90% decisions with linked snapshot evidence
- Technical reliability:
  - Availability API success rate >= 98%
  - p95 endpoint latency < 1200 ms (stored read), < 5000 ms (live lookup)

## Pilot Scope (Phase 1)

Duration: 6 weeks active + 2 weeks baseline prep

- Flight scope:
  - 2 high-volume routes + 1 disruption-prone route
  - Single carrier partition first (GF)
- User scope:
  - Commercial control, OCC, airport duty control, revenue desk
- Data scope:
  - Stored availability for selected flights
  - On-demand lookup from dashboard during active ops windows
- Governance:
  - Daily standup for adoption blockers
  - Weekly KPI review with leadership sponsor

## Rollout Plan

- Week 0-2: baseline measurement and instrumentation validation
- Week 3-4: pilot launch (selected routes, supervised usage)
- Week 5-6: optimize thresholds and alerting
- Week 7-8: KPI readout and scale decision

## Decision Cadence and Ownership

- Product owner: Ops Analytics Lead
- Technical owner: FalconEye Backend Lead
- Commercial owner: Revenue Control Lead
- Executive review: weekly KPI snapshot + risk log

## Risks and Mitigations

- Risk: API/endpoint variability in live ops windows
  - Mitigation: keep lookup non-blocking; fallback to last good snapshot
- Risk: low user adoption
  - Mitigation: embed into existing dashboard workflow and daily ritual
- Risk: noisy signals
  - Mitigation: route-specific thresholds and staged alerting

## Definition of Success

Pilot is successful if at least 4 of 6 target areas meet threshold and no critical operational regression is introduced.
