"use client";

import { useMemo } from "react";
import {
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Users,
  Plane,
  Clock,
  Gauge,
  Shield,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

// ─── Types ────────────────────────────────────────────────────────

type RiskTabProps = {
  dashboard: FlightDashboard;
};

type RiskLevel = "LOW" | "MODERATE" | "HIGH" | "CRITICAL";

interface RiskFactor {
  key: string;
  label: string;
  raw: number;
  score: number;
  weight: number;
}

// ─── Helpers ──────────────────────────────────────────────────────

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}

function pct(n: number, total: number) {
  return total > 0 ? Math.round((n / total) * 100) : 0;
}

function riskLevel(score: number): RiskLevel {
  if (score <= 20) return "LOW";
  if (score <= 50) return "MODERATE";
  if (score <= 80) return "HIGH";
  return "CRITICAL";
}

function riskColor(level: RiskLevel) {
  switch (level) {
    case "LOW":
      return { text: "text-emerald-500", bg: "bg-emerald-500", ring: "ring-emerald-500/20", badge: "bg-emerald-500/10 text-emerald-600 border-emerald-500/20" };
    case "MODERATE":
      return { text: "text-amber-500", bg: "bg-amber-500", ring: "ring-amber-500/20", badge: "bg-amber-500/10 text-amber-600 border-amber-500/20" };
    case "HIGH":
      return { text: "text-rose-500", bg: "bg-rose-500", ring: "ring-rose-500/20", badge: "bg-rose-500/10 text-rose-600 border-rose-500/20" };
    case "CRITICAL":
      return { text: "text-red-600", bg: "bg-red-600", ring: "ring-red-600/20", badge: "bg-red-600/10 text-red-700 border-red-600/20" };
  }
}

function statusDot(pctValue: number) {
  if (pctValue >= 95) return "bg-emerald-500";
  if (pctValue >= 80) return "bg-amber-500";
  return "bg-rose-500";
}

function severityColor(count: number, total: number) {
  const p = total > 0 ? (count / total) * 100 : 0;
  if (p >= 20) return { text: "text-red-600", bg: "bg-red-500" };
  if (p >= 10) return { text: "text-rose-500", bg: "bg-rose-500" };
  if (p >= 5) return { text: "text-amber-500", bg: "bg-amber-500" };
  return { text: "text-slate-500", bg: "bg-slate-400" };
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

// ─── Score computation ────────────────────────────────────────────

function computeRisk(insights: FlightInsights, total: number) {
  const connecting = insights.connectingPassengers.connecting;

  const factors: RiskFactor[] = [
    {
      key: "notCheckedIn",
      label: "Not Checked In",
      raw: insights.operationalReadiness.notCheckedIn,
      score: total > 0 ? (insights.operationalReadiness.notCheckedIn / total) * 100 : 0,
      weight: 25,
    },
    {
      key: "noSeat",
      label: "No Seat Assigned",
      raw: insights.operationalReadiness.noSeat,
      score: total > 0 ? (insights.operationalReadiness.noSeat / total) * 100 : 0,
      weight: 20,
    },
    {
      key: "noTicket",
      label: "No Ticket / VCR",
      raw: insights.ticketStatus.withoutTicket,
      score: total > 0 ? (insights.ticketStatus.withoutTicket / total) * 100 : 0,
      weight: 20,
    },
    {
      key: "connectionRisk",
      label: "Connection Risk",
      raw: insights.connectionRisk.atRiskCount,
      score: (insights.connectionRisk.atRiskCount / Math.max(connecting, 1)) * 100,
      weight: 15,
    },
    {
      key: "noBoardingPass",
      label: "No Boarding Pass",
      raw: insights.operationalReadiness.checkedInNoBP,
      score: total > 0 ? (insights.operationalReadiness.checkedInNoBP / total) * 100 : 0,
      weight: 10,
    },
    {
      key: "classMismatch",
      label: "Class Mismatch",
      raw: insights.classMismatch.total,
      score: total > 0 ? (insights.classMismatch.total / total) * 100 : 0,
      weight: 10,
    },
  ];

  const riskScore = clamp(
    factors.reduce((sum, f) => sum + (f.score * f.weight) / 100, 0),
    0,
    100,
  );

  return { riskScore: Math.round(riskScore * 10) / 10, factors };
}

// ─── Component ────────────────────────────────────────────────────

export function RiskTab({ dashboard }: RiskTabProps) {
  const insights = dashboard.insights;
  const total = dashboard.passengerSummary.totalPassengers;

  const risk = useMemo(() => {
    if (!insights) return null;
    return computeRisk(insights, total);
  }, [insights, total]);

  // ── Empty state ──
  if (!insights || !risk) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-32 text-muted-foreground">
        <ShieldAlert className="h-12 w-12 opacity-40" />
        <p className="max-w-sm text-center text-sm">
          Risk assessment requires flight data. Ingest the flight to begin monitoring.
        </p>
      </div>
    );
  }

  const level = riskLevel(risk.riskScore);
  const colors = riskColor(level);
  const noShows = dashboard.stateSummary.others.noShow ?? 0;

  return (
    <div className="space-y-4">
      {/* ── Section 1: Risk Score Header ── */}
      <Card>
        <CardContent className="flex flex-col gap-6 p-6 md:flex-row md:items-center">
          {/* Score */}
          <div className="flex shrink-0 flex-col items-center gap-2">
            <span className={cn("text-6xl font-bold tabular-nums", colors.text)}>
              {risk.riskScore.toFixed(1)}
            </span>
            <Badge variant="outline" className={cn("text-xs font-semibold", colors.badge)}>
              {level}
            </Badge>
          </div>

          {/* Factor bars */}
          <div className="flex-1 space-y-2">
            {risk.factors.map((f) => {
              const barPct = clamp(f.score, 0, 100);
              return (
                <div key={f.key} className="flex items-center gap-3 text-xs">
                  <span className="w-32 shrink-0 truncate text-muted-foreground">{f.label}</span>
                  <span className="w-10 shrink-0 text-right tabular-nums font-medium">{f.raw}</span>
                  <div className="relative h-2 flex-1 rounded-full bg-muted">
                    <div
                      className={cn("absolute inset-y-0 left-0 rounded-full", colors.bg)}
                      style={{ width: `${barPct}%` }}
                    />
                  </div>
                  <span className="w-10 text-right tabular-nums text-muted-foreground">
                    {barPct.toFixed(0)}%
                  </span>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* ── Section 2: Risk Heatmap ── */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Card 1: Passenger Compliance Matrix */}
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Passenger Compliance</h3>
            <div className="space-y-3">
              {(() => {
                const rows = [
                  { label: "Seat assigned", count: insights.seatOccupancy.seated, pctVal: insights.seatOccupancy.seatPct },
                  { label: "Boarding pass", count: insights.boardingPasses.issued, pctVal: insights.boardingPasses.issuedPct },
                  { label: "Ticket / VCR", count: insights.ticketStatus.withTicket, pctVal: insights.ticketStatus.ticketPct },
                  { label: "DOCS passport", count: insights.documentCompliance.DOCS.count, pctVal: insights.documentCompliance.DOCS.pct },
                ];
                return rows.map((r) => (
                  <div key={r.label} className="flex items-center gap-3 text-xs">
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", statusDot(r.pctVal))} />
                    <span className="w-24 shrink-0 text-muted-foreground">{r.label}</span>
                    <span className="w-14 shrink-0 text-right tabular-nums font-medium">
                      {r.count}/{total}
                    </span>
                    <div className="relative h-1.5 flex-1 rounded-full bg-muted">
                      <div
                        className={cn("absolute inset-y-0 left-0 rounded-full", statusDot(r.pctVal))}
                        style={{ width: `${clamp(r.pctVal, 0, 100)}%` }}
                      />
                    </div>
                    <span className="w-10 text-right tabular-nums text-muted-foreground">
                      {r.pctVal.toFixed(0)}%
                    </span>
                  </div>
                ));
              })()}
            </div>
          </CardContent>
        </Card>

        {/* Card 2: Connection Risk Panel */}
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Connection Risk</h3>
            {(() => {
              const connecting = insights.connectingPassengers.connecting;
              const atRisk = insights.connectionRisk.atRiskCount;
              const healthy = connecting - atRisk;
              const atRiskPct = connecting > 0 ? (atRisk / connecting) * 100 : 0;
              const healthyPct = connecting > 0 ? (healthy / connecting) * 100 : 0;

              return (
                <div className="space-y-3">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      Total connecting: <span className="tabular-nums font-medium text-foreground">{connecting}</span>
                    </span>
                    <span>
                      At-risk: <span className="tabular-nums font-medium text-rose-500">{atRisk}</span>
                    </span>
                  </div>
                  {/* Stacked bar */}
                  <div className="flex h-4 w-full overflow-hidden rounded-full bg-muted">
                    {healthyPct > 0 && (
                      <div className="bg-emerald-500" style={{ width: `${healthyPct}%` }} />
                    )}
                    {atRiskPct > 0 && (
                      <div className="bg-rose-500" style={{ width: `${atRiskPct}%` }} />
                    )}
                  </div>
                  <div className="flex gap-4 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
                      Healthy {healthy}
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2 w-2 rounded-full bg-rose-500" />
                      At-risk {atRisk}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    <span className="tabular-nums font-medium">{insights.connectingPassengers.connectingPct.toFixed(1)}%</span> of manifest are connecting
                  </p>
                  {atRisk > 0 && (
                    <p className="text-xs text-rose-500">
                      <AlertTriangle className="mr-1 inline h-3 w-3" />
                      {atRisk} passenger{atRisk > 1 ? "s" : ""} may miss connections. Review transfer times.
                    </p>
                  )}
                </div>
              );
            })()}
          </CardContent>
        </Card>

        {/* Card 3: Standby & Priority Pressure */}
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Standby &amp; Priority</h3>
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div>
                  <p className="tabular-nums text-lg font-bold">{insights.standbyUpgrade.standbyTotal}</p>
                  <p className="text-muted-foreground">Standby</p>
                </div>
                <div>
                  <p className="tabular-nums text-lg font-bold">{insights.standbyUpgrade.upgradeTotal}</p>
                  <p className="text-muted-foreground">Upgrade</p>
                </div>
                <div>
                  <p className="tabular-nums text-lg font-bold">{insights.priorityPassengers.total}</p>
                  <p className="text-muted-foreground">Priority</p>
                </div>
              </div>

              {/* Standby by cabin */}
              {Object.keys(insights.standbyUpgrade.standbyCabins).length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-[11px] font-medium text-muted-foreground">Standby by cabin</p>
                  {Object.entries(insights.standbyUpgrade.standbyCabins).map(([cabin, count]) => (
                    <div key={cabin} className="flex items-center gap-2 text-xs">
                      <span className="w-8 shrink-0 font-medium">{cabin}</span>
                      <div className="relative h-1.5 flex-1 rounded-full bg-muted">
                        <div
                          className="absolute inset-y-0 left-0 rounded-full bg-amber-500"
                          style={{
                            width: `${clamp(
                              insights.standbyUpgrade.standbyTotal > 0
                                ? (count / insights.standbyUpgrade.standbyTotal) * 100
                                : 0,
                              0,
                              100,
                            )}%`,
                          }}
                        />
                      </div>
                      <span className="w-6 text-right tabular-nums">{count}</span>
                    </div>
                  ))}
                </div>
              )}

              {insights.standbyUpgrade.standbyTotal > 0 && noShows > 0 && (
                <p className="text-xs text-emerald-600">
                  <CheckCircle2 className="mr-1 inline h-3 w-3" />
                  Clearance possible: {noShows} no-show seat{noShows > 1 ? "s" : ""} available
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Section 3: Operational Blockers ── */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Left: Blocker Severity List */}
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Operational Blockers</h3>
            <div className="space-y-3">
              {(() => {
                const blockers = [
                  {
                    label: "Not checked in",
                    count: insights.operationalReadiness.notCheckedIn,
                    recommendation: "Initiate check-in reminders or gate agent outreach.",
                  },
                  {
                    label: "No seat assigned",
                    count: insights.operationalReadiness.noSeat,
                    recommendation: "Auto-assign seats or escalate to gate control.",
                  },
                  {
                    label: "Checked-in without BP",
                    count: insights.operationalReadiness.checkedInNoBP,
                    recommendation: "Re-issue boarding passes at kiosk or gate.",
                  },
                  {
                    label: "No ticket / VCR",
                    count: insights.ticketStatus.withoutTicket,
                    recommendation: "Verify ticketing status and resolve before boarding.",
                  },
                  {
                    label: "Thru without seat",
                    count: insights.operationalReadiness.thruNoSeat,
                    recommendation: "Assign transit passengers to available seats.",
                  },
                ];

                return blockers.map((b, i) => {
                  const sev = severityColor(b.count, total);
                  const barPct = total > 0 ? clamp((b.count / total) * 100, 0, 100) : 0;
                  return (
                    <div key={b.label} className="flex gap-3">
                      <span
                        className={cn(
                          "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white",
                          sev.bg,
                        )}
                      >
                        {i + 1}
                      </span>
                      <div className="flex-1 space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-medium">{b.label}</span>
                          <span className={cn("tabular-nums font-bold", sev.text)}>{b.count}</span>
                        </div>
                        <div className="relative h-1.5 rounded-full bg-muted">
                          <div
                            className={cn("absolute inset-y-0 left-0 rounded-full", sev.bg)}
                            style={{ width: `${barPct}%` }}
                          />
                        </div>
                        {b.count > 0 && (
                          <p className="text-[11px] text-muted-foreground">{b.recommendation}</p>
                        )}
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </CardContent>
        </Card>

        {/* Right: Safety & Compliance Checklist */}
        <Card>
          <CardContent className="p-5">
            <h3 className="mb-4 text-sm font-semibold">Safety &amp; Compliance</h3>
            <div className="space-y-3">
              {(() => {
                const items = [
                  { label: "Emergency contacts", pctVal: insights.emergencyContacts.coveragePct },
                  { label: "Document compliance", pctVal: insights.documentCompliance.DOCS.pct },
                  { label: "Seat assignment", pctVal: insights.seatOccupancy.seatPct },
                  { label: "Boarding passes", pctVal: insights.boardingPasses.issuedPct },
                  { label: "Ticket coverage", pctVal: insights.ticketStatus.ticketPct },
                  { label: "Baggage tracking", pctVal: insights.baggage.dataAvailablePct },
                ];

                return items.map((item) => {
                  const passed = item.pctVal >= 80;
                  return (
                    <div key={item.label} className="flex items-center gap-3 text-xs">
                      {passed ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                      ) : (
                        <XCircle className="h-4 w-4 shrink-0 text-red-500" />
                      )}
                      <span className="w-36 shrink-0">{item.label}</span>
                      <span className="w-10 shrink-0 text-right tabular-nums font-medium">
                        {item.pctVal.toFixed(0)}%
                      </span>
                      <div className="relative h-1.5 flex-1 rounded-full bg-muted">
                        <div
                          className={cn(
                            "absolute inset-y-0 left-0 rounded-full",
                            passed ? "bg-emerald-500" : "bg-red-500",
                          )}
                          style={{ width: `${clamp(item.pctVal, 0, 100)}%` }}
                        />
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Section 4: Risk Timeline Context ── */}
      <Card>
        <CardContent className="p-5">
          <h3 className="mb-4 text-sm font-semibold">Risk Timeline Context</h3>
          <div className="grid gap-x-8 gap-y-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
            <div className="flex items-center gap-2">
              <Gauge className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">Phase:</span>
              <span className="font-medium">{dashboard.flightPhase?.label ?? "—"}</span>
              <Badge variant="outline" className="ml-1 text-[10px]">
                {dashboard.flightPhase?.phase ?? "—"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <Plane className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">Gate:</span>
              <span className="font-medium">{dashboard.departureGate || "—"}</span>
            </div>
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">Fetched at:</span>
              <span className="tabular-nums font-medium">{formatTime(dashboard.fetchedAt)}</span>
            </div>
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">Data integrity:</span>
              {dashboard.dataIntegrity.valid ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              ) : (
                <XCircle className="h-4 w-4 text-red-500" />
              )}
            </div>
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
              <span className="text-muted-foreground">Warnings:</span>
              <span className="tabular-nums font-medium">
                {dashboard.dataIntegrity.warnings?.length ?? 0}
              </span>
            </div>
          </div>

          {(dashboard.dataIntegrity.warnings?.length ?? 0) > 0 && (
            <div className="mt-4 space-y-1 border-t pt-3">
              {dashboard.dataIntegrity.warnings.map((w, i) => (
                <p key={i} className="text-xs text-amber-500">
                  <AlertTriangle className="mr-1 inline h-3 w-3" />
                  {w}
                </p>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
