"use client";

import { useMemo } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Gauge,
  Plane,
  ShieldAlert,
  Zap,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

// ─── Types ────────────────────────────────────────────────────────

type BriefingTabProps = {
  dashboard: FlightDashboard;
};

type Severity = "critical" | "warning" | "info";

interface Decision {
  id: string;
  severity: Severity;
  title: string;
  metric: string;
  description: string;
  action: string;
  category: "operational" | "commercial" | "safety" | "revenue";
}

interface ReadinessScore {
  overall: number;
  seats: number;
  boarding: number;
  tickets: number;
  docs: number;
  bags: number;
}

// ─── Score Computation ────────────────────────────────────────────

function computeReadinessScore(insights: FlightInsights): ReadinessScore {
  const seats = insights.seatOccupancy.seatPct;
  const boarding = insights.boardingPasses.issuedPct;
  const tickets = insights.ticketStatus.ticketPct;
  const docs = insights.documentCompliance.DOCS.pct;
  const bags = insights.baggage.dataAvailablePct;

  const overall = Math.round(
    seats * 0.3 +
    boarding * 0.25 +
    tickets * 0.2 +
    docs * 0.15 +
    bags * 0.1,
  );

  return { overall, seats, boarding, tickets, docs, bags };
}

// ─── Decision Engine ──────────────────────────────────────────────

function generateDecisions(
  dashboard: FlightDashboard,
  insights: FlightInsights,
): Decision[] {
  const decisions: Decision[] = [];
  const total = dashboard.passengerSummary.totalPassengers;
  const phase = dashboard.flightPhase?.phase ?? "SCHEDULED";

  // 1. At-Risk Connecting
  if (insights.connectionRisk.atRiskCount > 0) {
    decisions.push({
      id: "connection-risk",
      severity: insights.connectionRisk.atRiskCount >= 5 ? "critical" : "warning",
      title: "At-Risk Connecting Passengers",
      metric: `${insights.connectionRisk.atRiskCount} pax (${insights.connectionRisk.riskPct}%)`,
      description: `${insights.connectionRisk.atRiskCount} connecting passengers may miss onward flights.`,
      action: "Coordinate gate-side escort or priority boarding. Verify minimum connection times.",
      category: "operational",
    });
  }

  // 2. Not Checked In
  const notCheckedIn = insights.operationalReadiness.notCheckedIn;
  if (notCheckedIn > 0 && (phase === "CHECK_IN" || phase === "BOARDING" || phase === "CLOSED")) {
    const pct = total > 0 ? Math.round((notCheckedIn / total) * 100) : 0;
    decisions.push({
      id: "not-checked-in",
      severity: notCheckedIn > 10 ? "critical" : notCheckedIn > 3 ? "warning" : "info",
      title: "Passengers Not Checked In",
      metric: `${notCheckedIn} pax (${pct}%)`,
      description: `${notCheckedIn} passengers still not checked in during ${phase.replace("_", " ").toLowerCase()} phase.`,
      action: phase === "BOARDING" || phase === "CLOSED"
        ? "Consider offload decision. Check connecting/reservation issues."
        : "Send check-in reminders or verify PNR validity.",
      category: "operational",
    });
  }

  // 3. Missing Seat
  if (insights.operationalReadiness.noSeat > 0) {
    decisions.push({
      id: "no-seat",
      severity: insights.operationalReadiness.noSeat >= 5 ? "warning" : "info",
      title: "Missing Seat Assignment",
      metric: `${insights.operationalReadiness.noSeat} pax`,
      description: `${insights.operationalReadiness.noSeat} passengers have no seat assigned.`,
      action: "Auto-assign seats or flag for gate agent. Prioritize families and mobility-restricted passengers.",
      category: "operational",
    });
  }

  // 4. No Ticket
  if (insights.ticketStatus.withoutTicket > 0) {
    decisions.push({
      id: "no-ticket",
      severity: insights.ticketStatus.withoutTicket >= 3 ? "critical" : "warning",
      title: "Passengers Without Ticket",
      metric: `${insights.ticketStatus.withoutTicket} pax`,
      description: "These passengers lack ticket or VCR coverage — cannot legally fly.",
      action: "Verify ticket issuance, contact booking agency, or escalate to Revenue Integrity.",
      category: "revenue",
    });
  }

  // 5. Standby Queue
  if (insights.standbyUpgrade.standbyTotal > 0) {
    const cabins = Object.entries(insights.standbyUpgrade.standbyCabins);
    const cabinList = cabins.map(([c, n]) => `${c}: ${n}`).join(", ");
    decisions.push({
      id: "standby-pressure",
      severity: insights.standbyUpgrade.standbyTotal >= 5 ? "warning" : "info",
      title: "Standby Queue Active",
      metric: `${insights.standbyUpgrade.standbyTotal} standby, ${insights.standbyUpgrade.upgradeTotal} upgrades`,
      description: `Standby breakdown: ${cabinList}. Upgrade candidates: ${insights.standbyUpgrade.upgradeTotal}.`,
      action: "Review standby priority. Clear upgrades if cabin has availability after check-in cutoff.",
      category: "revenue",
    });
  }

  // 6. Class Mismatch
  if (insights.classMismatch.upgrades > 0 || insights.classMismatch.downgrades > 0) {
    decisions.push({
      id: "class-mismatch",
      severity: "info",
      title: "Class Mismatch Detected",
      metric: `${insights.classMismatch.upgrades} up, ${insights.classMismatch.downgrades} down`,
      description: `${insights.classMismatch.upgrades} seated above booked class, ${insights.classMismatch.downgrades} below.`,
      action: insights.classMismatch.downgrades > 0
        ? "Prioritize re-seating downgraded passengers. Consider complimentary upgrades."
        : "Verify upgrade authorization with Revenue Integrity.",
      category: "revenue",
    });
  }

  // 7. Low Boarding Rate
  if ((phase === "BOARDING" || phase === "CLOSED") && insights.boardingRate.boardedPct < 90) {
    decisions.push({
      id: "boarding-rate",
      severity: insights.boardingRate.boardedPct < 70 ? "critical" : "warning",
      title: "Boarding Rate Below Target",
      metric: `${insights.boardingRate.boardedPct}% boarded`,
      description: `Only ${insights.boardingRate.boarded} of ${total} passengers boarded. Target: 90%+.`,
      action: "Make final boarding call. Page checked-in but not-boarded passengers.",
      category: "operational",
    });
  }

  // 8. Low DOCS
  if (insights.documentCompliance.DOCS.pct < 80) {
    decisions.push({
      id: "docs-gap",
      severity: insights.documentCompliance.DOCS.pct < 50 ? "warning" : "info",
      title: "Low Document Compliance",
      metric: `${insights.documentCompliance.DOCS.pct}% DOCS`,
      description: `Only ${insights.documentCompliance.DOCS.count} of ${total} have APIS documents on file.`,
      action: "Flag non-compliant passengers for document check. APIS required for international flights.",
      category: "safety",
    });
  }

  // 9. No-Shows
  const noShows = dashboard.stateSummary.others.noShow ?? 0;
  if (noShows > 0 && total > 0) {
    const pct = Math.round((noShows / total) * 100);
    decisions.push({
      id: "no-shows",
      severity: pct >= 10 ? "warning" : "info",
      title: "No-Show Passengers",
      metric: `${noShows} pax (${pct}%)`,
      description: `${noShows} booked passengers did not show. Frees seats for standby.`,
      action: insights.standbyUpgrade.standbyTotal > 0
        ? `Clear standby — ${Math.min(noShows, insights.standbyUpgrade.standbyTotal)} passengers can be accommodated.`
        : "Seats can be released for last-minute sales or standby clearance.",
      category: "revenue",
    });
  }

  // 10. Corporate Heavy
  if (insights.corporateTravel.corporatePct >= 50) {
    decisions.push({
      id: "corporate-heavy",
      severity: "info",
      title: "High Corporate Concentration",
      metric: `${insights.corporateTravel.corporatePct}% corporate`,
      description: `${insights.corporateTravel.totalCorporate} of ${total} are corporate travelers.`,
      action: "Ensure premium service delivery. Disruptions impact corporate accounts directly.",
      category: "commercial",
    });
  }

  // 11. Infants
  if (insights.infantTracking.total > 0) {
    decisions.push({
      id: "infant-tracking",
      severity: "info",
      title: "Infants On Board",
      metric: `${insights.infantTracking.total} infants`,
      description: "Verify infant life vests, bassinets, and parent seating adjacency.",
      action: "Confirm infant equipment available. Check seat assignments for parent-infant pairs.",
      category: "safety",
    });
  }

  const order: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };
  decisions.sort((a, b) => order[a.severity] - order[b.severity]);
  return decisions;
}

// ─── Severity config ──────────────────────────────────────────────

const severityConfig: Record<Severity, { bg: string; border: string; text: string; icon: string; badge: string }> = {
  critical: {
    bg: "bg-rose-500/8 dark:bg-rose-500/10",
    border: "border-rose-500/30",
    text: "text-rose-600 dark:text-rose-400",
    icon: "text-rose-500",
    badge: "bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30",
  },
  warning: {
    bg: "bg-amber-500/8 dark:bg-amber-500/10",
    border: "border-amber-500/30",
    text: "text-amber-600 dark:text-amber-400",
    icon: "text-amber-500",
    badge: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
  },
  info: {
    bg: "bg-sky-500/8 dark:bg-sky-500/10",
    border: "border-sky-500/30",
    text: "text-sky-600 dark:text-sky-400",
    icon: "text-sky-500",
    badge: "bg-sky-500/15 text-sky-700 dark:text-sky-300 border-sky-500/30",
  },
};

const categoryLabels: Record<string, string> = {
  operational: "Operations",
  commercial: "Commercial",
  safety: "Safety",
  revenue: "Revenue",
};

// ─── Sub-components ───────────────────────────────────────────────

function SeverityIcon({ severity, className }: { severity: Severity; className?: string }) {
  const props = { className: cn("shrink-0", className) };
  switch (severity) {
    case "critical": return <ShieldAlert {...props} />;
    case "warning": return <AlertTriangle {...props} />;
    case "info": return <Zap {...props} />;
  }
}

function FlightSnapshot({
  dashboard,
  insights,
}: {
  dashboard: FlightDashboard;
  insights: FlightInsights;
}) {
  const phaseLabel = dashboard.flightPhase?.label ?? "Unknown";
  const total = dashboard.passengerSummary.totalPassengers;
  const boardedPct = insights.boardingRate.boardedPct;
  const checkedInPct = insights.boardingRate.checkedInPct;
  const noShows = dashboard.stateSummary.others.noShow ?? 0;
  const connecting = insights.connectingPassengers.connecting;
  const nonRevenue = dashboard.analysis.nonRevenue;

  const kpis: { label: string; value: string | number; accent?: string }[] = [
    { label: "Total Passengers", value: total },
    {
      label: "Boarded",
      value: `${boardedPct}%`,
      accent: boardedPct >= 90 ? "text-emerald-500" : boardedPct >= 70 ? "text-amber-500" : "text-rose-500",
    },
    { label: "Check-in Rate", value: `${checkedInPct}%`, accent: "text-sky-500" },
    {
      label: "No-Shows",
      value: noShows,
      accent: noShows > 0 ? "text-amber-500" : "text-emerald-500",
    },
    { label: "Connecting", value: connecting, accent: "text-violet-500" },
    { label: "Non-Revenue", value: nonRevenue, accent: "text-purple-500" },
  ];

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <Plane className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Flight Snapshot
          </h3>
          <Badge variant="outline" className="ml-auto text-[10px]">
            {phaseLabel}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-4">
          {kpis.map((k) => (
            <div key={k.label}>
              <p className="text-[11px] text-muted-foreground">{k.label}</p>
              <p className={cn("text-lg font-semibold tabular-nums", k.accent)}>
                {k.value}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ReadinessGauge({ score }: { score: ReadinessScore }) {
  const color =
    score.overall >= 90 ? "text-emerald-500" : score.overall >= 70 ? "text-amber-500" : "text-rose-500";
  const ringStroke =
    score.overall >= 90 ? "stroke-emerald-500" : score.overall >= 70 ? "stroke-amber-500" : "stroke-rose-500";

  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score.overall / 100) * circumference;

  const bars: { label: string; value: number; barColor: string; textColor: string }[] = [
    { label: "Seats", value: score.seats, barColor: "bg-sky-500", textColor: "text-sky-500" },
    { label: "Boarding", value: score.boarding, barColor: "bg-violet-500", textColor: "text-violet-500" },
    { label: "Tickets", value: score.tickets, barColor: "bg-amber-500", textColor: "text-amber-500" },
    { label: "DOCS", value: score.docs, barColor: "bg-blue-500", textColor: "text-blue-500" },
    { label: "Bags", value: score.bags, barColor: "bg-emerald-500", textColor: "text-emerald-500" },
  ];

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <Gauge className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Departure Readiness
          </h3>
        </div>
        <div className="flex items-center gap-6">
          {/* Circular gauge */}
          <div className="relative shrink-0">
            <svg
              width="128"
              height="128"
              viewBox="0 0 128 128"
              className="-rotate-90"
              aria-label={`Departure readiness ${score.overall}%`}
            >
              <circle
                cx="64"
                cy="64"
                r={radius}
                fill="none"
                className="stroke-muted/30"
                strokeWidth="8"
              />
              <circle
                cx="64"
                cy="64"
                r={radius}
                fill="none"
                className={cn(ringStroke, "transition-all duration-700")}
                strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={offset}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className={cn("text-3xl font-bold tabular-nums", color)}>
                {score.overall}
              </span>
              <span className="text-[10px] text-muted-foreground">/ 100</span>
            </div>
          </div>

          {/* Metric breakdown bars */}
          <div className="flex-1 space-y-2">
            {bars.map((b) => (
              <div key={b.label} className="flex items-center gap-2">
                <span className="w-16 text-xs text-muted-foreground">{b.label}</span>
                <div className="flex-1 h-1.5 rounded-full bg-muted/30 overflow-hidden">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all duration-500",
                      b.value >= 90 ? "bg-emerald-500" : b.value >= 70 ? "bg-amber-500" : "bg-rose-500",
                    )}
                    style={{ width: `${b.value}%` }}
                  />
                </div>
                <span className={cn("w-10 text-right text-xs font-medium tabular-nums", b.textColor)}>
                  {b.value}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function RiskSummary({ decisions }: { decisions: Decision[] }) {
  const criticals = decisions.filter((d) => d.severity === "critical").length;
  const warnings = decisions.filter((d) => d.severity === "warning").length;
  const advisories = decisions.filter((d) => d.severity === "info").length;

  const boxes: { label: string; count: number; active: string; border: string; text: string }[] = [
    {
      label: "Critical",
      count: criticals,
      active: "bg-rose-500/8",
      border: "border-rose-500/30",
      text: "text-rose-500",
    },
    {
      label: "Warning",
      count: warnings,
      active: "bg-amber-500/8",
      border: "border-amber-500/30",
      text: "text-amber-500",
    },
    {
      label: "Advisory",
      count: advisories,
      active: "bg-sky-500/8",
      border: "border-sky-500/30",
      text: "text-sky-500",
    },
  ];

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <ShieldAlert className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Risk Summary
          </h3>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {boxes.map((b) => (
            <div
              key={b.label}
              className={cn(
                "rounded-lg border p-3 text-center",
                b.count > 0 ? cn(b.border, b.active) : "border-border bg-muted/10",
              )}
            >
              <div
                className={cn(
                  "text-2xl font-bold tabular-nums",
                  b.count > 0 ? b.text : "text-muted-foreground",
                )}
              >
                {b.count}
              </div>
              <div className="text-[11px] text-muted-foreground mt-1">{b.label}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function DecisionCard({ decision }: { decision: Decision }) {
  const config = severityConfig[decision.severity];

  return (
    <div className={cn("rounded-lg border p-3", config.bg, config.border)}>
      <div className="flex items-start gap-3">
        <SeverityIcon severity={decision.severity} className={cn("h-4 w-4 mt-0.5", config.icon)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-sm font-semibold">{decision.title}</h4>
            <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0", config.badge)}>
              {categoryLabels[decision.category]}
            </Badge>
          </div>
          <div className={cn("text-sm font-medium tabular-nums mt-0.5", config.text)}>
            {decision.metric}
          </div>
          <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">
            {decision.description}
          </p>
          <div className="mt-2 flex items-start gap-1.5 rounded-md bg-background/50 px-2.5 py-2 text-xs">
            <ArrowUpRight className="h-3 w-3 mt-0.5 shrink-0 text-muted-foreground" />
            <span className="font-medium leading-relaxed">{decision.action}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main Export ──────────────────────────────────────────────────

export function BriefingTab({ dashboard }: BriefingTabProps) {
  const insights = dashboard.insights;

  const readinessScore = useMemo(() => {
    if (!insights) return null;
    return computeReadinessScore(insights);
  }, [insights]);

  const decisions = useMemo(() => {
    if (!insights) return [];
    return generateDecisions(dashboard, insights);
  }, [dashboard, insights]);

  // Empty state — no insights yet
  if (!insights) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <Plane className="h-10 w-10 text-muted-foreground mb-3" />
        <p className="text-sm text-muted-foreground max-w-sm">
          No insights data available yet. Ingest the flight to generate analytics.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Section 1 — Departure Readiness Score */}
      <div className="grid gap-3 lg:grid-cols-[1fr_1.2fr_0.8fr]">
        <FlightSnapshot dashboard={dashboard} insights={insights} />
        <ReadinessGauge score={readinessScore!} />
        <RiskSummary decisions={decisions} />
      </div>

      {/* Section 2 — Action Items */}
      {decisions.length > 0 ? (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Zap className="h-4 w-4 text-amber-500" />
            <h3 className="text-sm font-semibold">Action Items</h3>
            <Badge variant="secondary" className="text-[10px]">
              {decisions.length}
            </Badge>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {decisions.map((d) => (
              <DecisionCard key={d.id} decision={d} />
            ))}
          </div>
        </div>
      ) : (
        <Card className="shadow-sm border-emerald-500/30 bg-emerald-500/5">
          <CardContent className="p-4 flex items-center gap-3">
            <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0" />
            <div>
              <h4 className="text-sm font-semibold text-emerald-600 dark:text-emerald-400">
                All Clear
              </h4>
              <p className="text-xs text-muted-foreground mt-0.5">
                No actionable issues detected. Flight is operationally ready.
              </p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
