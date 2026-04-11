"use client";

import { useMemo } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  Crosshair,
  Gauge,
  Plane,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  Users,
  Zap,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

// ─── Types ────────────────────────────────────────────────────────

type Severity = "critical" | "warning" | "info" | "success";

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
  docs: number;
  tickets: number;
  bags: number;
}

interface DecisionBriefingProps {
  dashboard: FlightDashboard;
  className?: string;
}

// ─── Score Computation ────────────────────────────────────────────

function computeReadinessScore(insights: FlightInsights): ReadinessScore {
  const seats = insights.seatOccupancy.seatPct;
  const boarding = insights.boardingPasses.issuedPct;
  const docs = insights.documentCompliance.DOCS.pct;
  const tickets = insights.ticketStatus.ticketPct;
  const bags = insights.baggage.dataAvailablePct;

  // Weighted composite: seats and boarding are most critical for departure
  const overall = Math.round(
    seats * 0.30 +
    boarding * 0.25 +
    tickets * 0.20 +
    docs * 0.15 +
    bags * 0.10
  );

  return { overall, seats, boarding, docs, tickets, bags };
}

// ─── Decision Engine ──────────────────────────────────────────────

function generateDecisions(
  dashboard: FlightDashboard,
  insights: FlightInsights,
  score: ReadinessScore,
): Decision[] {
  const decisions: Decision[] = [];
  const total = dashboard.passengerSummary.totalPassengers;

  // 1. Connection Risk → Gate-side intervention
  if (insights.connectionRisk.atRiskCount > 0) {
    decisions.push({
      id: "connection-risk",
      severity: insights.connectionRisk.atRiskCount >= 5 ? "critical" : "warning",
      title: "At-Risk Connecting Passengers",
      metric: `${insights.connectionRisk.atRiskCount} pax (${insights.connectionRisk.riskPct}%)`,
      description: `${insights.connectionRisk.atRiskCount} connecting passengers may miss their onward flight. They haven't checked in or boarded yet.`,
      action: "Coordinate gate-side escort or priority boarding for these passengers. Check minimum connection times.",
      category: "operational",
    });
  }

  // 2. Not checked in (post check-in phase)
  const notCheckedIn = insights.operationalReadiness.notCheckedIn;
  const phase = dashboard.flightPhase?.phase ?? "SCHEDULED";
  if (notCheckedIn > 0 && (phase === "CHECK_IN" || phase === "BOARDING" || phase === "CLOSED")) {
    const pct = total > 0 ? Math.round((notCheckedIn / total) * 100) : 0;
    decisions.push({
      id: "not-checked-in",
      severity: notCheckedIn > 10 ? "critical" : notCheckedIn > 3 ? "warning" : "info",
      title: "Passengers Not Checked In",
      metric: `${notCheckedIn} pax (${pct}%)`,
      description: `${notCheckedIn} passengers still haven't checked in during ${phase.replace("_", " ").toLowerCase()} phase.`,
      action: phase === "BOARDING" || phase === "CLOSED"
        ? "Consider offload decision. Check if any are connecting or have reservation issues."
        : "Send check-in reminders or verify PNR validity. May indicate no-shows.",
      category: "operational",
    });
  }

  // 3. No seat assigned
  if (insights.operationalReadiness.noSeat > 0) {
    decisions.push({
      id: "no-seat",
      severity: insights.operationalReadiness.noSeat >= 5 ? "warning" : "info",
      title: "Missing Seat Assignment",
      metric: `${insights.operationalReadiness.noSeat} pax`,
      description: `${insights.operationalReadiness.noSeat} passengers have no seat. May need manual intervention at the gate.`,
      action: "Auto-assign seats or flag for gate agent. Prioritize families with children and mobility-restricted passengers.",
      category: "operational",
    });
  }

  // 4. No ticket / VCR
  if (insights.ticketStatus.withoutTicket > 0) {
    decisions.push({
      id: "no-ticket",
      severity: insights.ticketStatus.withoutTicket >= 3 ? "critical" : "warning",
      title: "Passengers Without Ticket",
      metric: `${insights.ticketStatus.withoutTicket} pax`,
      description: "These passengers lack ticket or VCR coverage — they cannot legally fly.",
      action: "Verify ticket issuance, contact booking agency, or escalate to Revenue Integrity.",
      category: "revenue",
    });
  }

  // 5. Standby pressure vs availability
  if (insights.standbyUpgrade.standbyTotal > 0) {
    const cabins = Object.entries(insights.standbyUpgrade.standbyCabins);
    const cabinList = cabins.map(([c, n]) => `${c}: ${n}`).join(", ");
    decisions.push({
      id: "standby-pressure",
      severity: insights.standbyUpgrade.standbyTotal >= 5 ? "warning" : "info",
      title: "Standby Queue Active",
      metric: `${insights.standbyUpgrade.standbyTotal} standby, ${insights.standbyUpgrade.upgradeTotal} upgrades`,
      description: `Standby breakdown by cabin: ${cabinList}. Upgrade candidates: ${insights.standbyUpgrade.upgradeTotal}.`,
      action: "Review standby list priority. Clear upgrades if cabin has availability after check-in cutoff.",
      category: "revenue",
    });
  }

  // 6. Class mismatch opportunities
  if (insights.classMismatch.upgrades > 0 || insights.classMismatch.downgrades > 0) {
    decisions.push({
      id: "class-mismatch",
      severity: "info",
      title: "Class Mismatch Detected",
      metric: `${insights.classMismatch.upgrades} up, ${insights.classMismatch.downgrades} down`,
      description: `${insights.classMismatch.upgrades} passengers seated above their booked class, ${insights.classMismatch.downgrades} below.`,
      action: insights.classMismatch.downgrades > 0
        ? "Prioritize re-seating downgraded passengers. Consider complimentary upgrade offers for goodwill."
        : "Verify upgrade authorization. Revenue Integrity may need to audit these.",
      category: "revenue",
    });
  }

  // 7. Low boarding rate (during boarding phase)
  if ((phase === "BOARDING" || phase === "CLOSED") && insights.boardingRate.boardedPct < 90) {
    decisions.push({
      id: "boarding-rate",
      severity: insights.boardingRate.boardedPct < 70 ? "critical" : "warning",
      title: "Boarding Rate Below Target",
      metric: `${insights.boardingRate.boardedPct}% boarded`,
      description: `Only ${insights.boardingRate.boarded} of ${total} passengers have boarded. Target: 90%+ before closure.`,
      action: "Make final boarding call. Identify checked-in but not boarded passengers for gate paging.",
      category: "operational",
    });
  }

  // 8. Document compliance gaps
  if (insights.documentCompliance.DOCS.pct < 80) {
    decisions.push({
      id: "docs-gap",
      severity: insights.documentCompliance.DOCS.pct < 50 ? "warning" : "info",
      title: "Low Document Compliance",
      metric: `${insights.documentCompliance.DOCS.pct}% DOCS`,
      description: `Only ${insights.documentCompliance.DOCS.count} of ${total} passengers have APIS documents on file.`,
      action: "Flag non-compliant passengers for document check at counter. APIS required for international flights.",
      category: "safety",
    });
  }

  // 9. High no-show rate indicator (booked but not appearing)
  const noShows = dashboard.stateSummary.others.noShow ?? 0;
  if (noShows > 0 && total > 0) {
    const pct = Math.round((noShows / total) * 100);
    decisions.push({
      id: "no-shows",
      severity: pct >= 10 ? "warning" : "info",
      title: "No-Show Passengers",
      metric: `${noShows} pax (${pct}%)`,
      description: `${noShows} booked passengers did not show up. This frees ${noShows} seats for standby.`,
      action: noShows > 0 && insights.standbyUpgrade.standbyTotal > 0
        ? `Clear standby queue — ${Math.min(noShows, insights.standbyUpgrade.standbyTotal)} standby passengers can be accommodated.`
        : "Revenue opportunity: these seats can be released for last-minute sales or standby clearance.",
      category: "revenue",
    });
  }

  // 10. Corporate travel concentration
  if (insights.corporateTravel.corporatePct >= 50) {
    decisions.push({
      id: "corporate-heavy",
      severity: "info",
      title: "High Corporate Concentration",
      metric: `${insights.corporateTravel.corporatePct}% corporate`,
      description: `${insights.corporateTravel.totalCorporate} of ${total} passengers are corporate travelers. High-touch service expected.`,
      action: "Ensure premium service delivery. Any disruption impacts corporate accounts directly.",
      category: "commercial",
    });
  }

  // 11. Infant safety check
  if (insights.infantTracking.total > 0) {
    decisions.push({
      id: "infant-tracking",
      severity: insights.infantTracking.total > 5 ? "info" : "info",
      title: "Infants On Board",
      metric: `${insights.infantTracking.total} infants`,
      description: "Verify infant life vests, bassinets, and parent seating adjacency.",
      action: "Confirm infant equipment available. Check seat assignments for parent-infant pairs.",
      category: "safety",
    });
  }

  // Sort: critical first, then warning, then info
  const order: Record<Severity, number> = { critical: 0, warning: 1, info: 2, success: 3 };
  decisions.sort((a, b) => order[a.severity] - order[b.severity]);

  return decisions;
}

// ─── Visual Components ────────────────────────────────────────────

const severityConfig: Record<Severity, { bg: string; border: string; text: string; icon: string; badge: string }> = {
  critical: {
    bg: "bg-rose-500/8 dark:bg-rose-500/10",
    border: "border-rose-500/30 dark:border-rose-500/40",
    text: "text-rose-600 dark:text-rose-400",
    icon: "text-rose-500",
    badge: "bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30",
  },
  warning: {
    bg: "bg-amber-500/8 dark:bg-amber-500/10",
    border: "border-amber-500/30 dark:border-amber-500/40",
    text: "text-amber-600 dark:text-amber-400",
    icon: "text-amber-500",
    badge: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
  },
  info: {
    bg: "bg-sky-500/8 dark:bg-sky-500/10",
    border: "border-sky-500/30 dark:border-sky-500/40",
    text: "text-sky-600 dark:text-sky-400",
    icon: "text-sky-500",
    badge: "bg-sky-500/15 text-sky-700 dark:text-sky-300 border-sky-500/30",
  },
  success: {
    bg: "bg-emerald-500/8 dark:bg-emerald-500/10",
    border: "border-emerald-500/30 dark:border-emerald-500/40",
    text: "text-emerald-600 dark:text-emerald-400",
    icon: "text-emerald-500",
    badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30",
  },
};

function SeverityIcon({ severity, className }: { severity: Severity; className?: string }) {
  const props = { className: cn("shrink-0", className) };
  switch (severity) {
    case "critical": return <ShieldAlert {...props} />;
    case "warning": return <AlertTriangle {...props} />;
    case "info": return <Zap {...props} />;
    case "success": return <CheckCircle2 {...props} />;
  }
}

const categoryLabels: Record<string, string> = {
  operational: "Operations",
  commercial: "Commercial",
  safety: "Safety",
  revenue: "Revenue",
};

// ─── Readiness Gauge ──────────────────────────────────────────────

function ReadinessGauge({ score }: { score: ReadinessScore }) {
  const color = score.overall >= 90 ? "text-emerald-500" : score.overall >= 70 ? "text-amber-500" : "text-rose-500";
  const ringColor = score.overall >= 90 ? "stroke-emerald-500" : score.overall >= 70 ? "stroke-amber-500" : "stroke-rose-500";
  const trackColor = "stroke-muted/30";

  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score.overall / 100) * circumference;

  const metrics = [
    { label: "Seats", value: score.seats, color: "text-sky-500" },
    { label: "Boarding", value: score.boarding, color: "text-violet-500" },
    { label: "Tickets", value: score.tickets, color: "text-amber-500" },
    { label: "DOCS", value: score.docs, color: "text-blue-500" },
    { label: "Bags", value: score.bags, color: "text-emerald-500" },
  ];

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <Gauge className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Departure Readiness</h3>
        </div>
        <div className="flex items-center gap-6">
          {/* Circular Gauge */}
          <div className="relative shrink-0">
            <svg width="128" height="128" viewBox="0 0 128 128" className="-rotate-90" aria-label={`Departure readiness ${score.overall}%`}>
              <circle cx="64" cy="64" r={radius} fill="none" className={trackColor} strokeWidth="8" />
              <circle
                cx="64" cy="64" r={radius}
                fill="none"
                className={cn(ringColor, "transition-all duration-700")}
                strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={offset}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className={cn("text-3xl font-bold tabular-nums", color)}>{score.overall}</span>
              <span className="text-[10px] text-muted-foreground">/ 100</span>
            </div>
          </div>

          {/* Metric Breakdown */}
          <div className="flex-1 space-y-2">
            {metrics.map((m) => (
              <div key={m.label} className="flex items-center gap-2">
                <span className="w-16 text-xs text-muted-foreground">{m.label}</span>
                <div className="flex-1 h-1.5 rounded-full bg-muted/30 overflow-hidden">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all duration-500",
                      m.value >= 90 ? "bg-emerald-500" : m.value >= 70 ? "bg-amber-500" : "bg-rose-500"
                    )}
                    style={{ width: `${m.value}%` }}
                  />
                </div>
                <span className={cn("w-10 text-right text-xs font-medium tabular-nums", m.color)}>{m.value}%</span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Flight Risk Summary ──────────────────────────────────────────

function RiskSummary({ decisions }: { decisions: Decision[] }) {
  const criticals = decisions.filter(d => d.severity === "critical").length;
  const warnings = decisions.filter(d => d.severity === "warning").length;
  const infos = decisions.filter(d => d.severity === "info").length;

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <Crosshair className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Risk Summary</h3>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className={cn("rounded-lg border p-3 text-center", criticals > 0 ? "border-rose-500/30 bg-rose-500/8" : "border-border bg-muted/10")}>
            <div className={cn("text-2xl font-bold tabular-nums", criticals > 0 ? "text-rose-500" : "text-muted-foreground")}>{criticals}</div>
            <div className="text-[11px] text-muted-foreground mt-1">Critical</div>
          </div>
          <div className={cn("rounded-lg border p-3 text-center", warnings > 0 ? "border-amber-500/30 bg-amber-500/8" : "border-border bg-muted/10")}>
            <div className={cn("text-2xl font-bold tabular-nums", warnings > 0 ? "text-amber-500" : "text-muted-foreground")}>{warnings}</div>
            <div className="text-[11px] text-muted-foreground mt-1">Warning</div>
          </div>
          <div className={cn("rounded-lg border p-3 text-center", infos > 0 ? "border-sky-500/30 bg-sky-500/8" : "border-border bg-muted/10")}>
            <div className={cn("text-2xl font-bold tabular-nums", infos > 0 ? "text-sky-500" : "text-muted-foreground")}>{infos}</div>
            <div className="text-[11px] text-muted-foreground mt-1">Advisory</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Quick Stats Strip ────────────────────────────────────────────

function QuickStat({ label, value, trend, accent }: { label: string; value: string; trend?: "up" | "down" | "flat"; accent?: string }) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="text-[11px] text-muted-foreground truncate">{label}</span>
      <div className="flex items-center gap-1">
        <span className={cn("text-lg font-semibold tabular-nums", accent)}>{value}</span>
        {trend === "up" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
        {trend === "down" && <TrendingDown className="h-3 w-3 text-rose-500" />}
      </div>
    </div>
  );
}

// ─── Decision Card ────────────────────────────────────────────────

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

export function DecisionBriefing({ dashboard, className }: DecisionBriefingProps) {
  const insights = dashboard.insights;

  const readinessScore = useMemo(() => {
    if (!insights) return null;
    return computeReadinessScore(insights);
  }, [insights]);

  const decisions = useMemo(() => {
    if (!insights) return [];
    return generateDecisions(dashboard, insights, readinessScore!);
  }, [dashboard, insights, readinessScore]);

  if (!insights) {
    return (
      <div className={cn("rounded-lg border bg-muted/20 p-8 text-center", className)}>
        <Plane className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
        <p className="text-sm text-muted-foreground">No insights data available yet. Ingest the flight to generate analytics.</p>
      </div>
    );
  }

  const total = dashboard.passengerSummary.totalPassengers;
  const phase = dashboard.flightPhase?.label ?? "Unknown";
  const noShows = dashboard.stateSummary.others.noShow ?? 0;
  const boardedPct = insights.boardingRate.boardedPct;

  return (
    <div className={cn("space-y-4", className)}>
      {/* Row 1: Quick Stats + Readiness Gauge + Risk Summary */}
      <div className="grid gap-3 lg:grid-cols-[1fr_1.2fr_0.8fr]">
        {/* Quick Operational KPIs */}
        <Card className="shadow-sm">
          <CardContent className="p-4">
            <div className="flex items-center gap-1.5 mb-3">
              <Plane className="h-4 w-4 text-muted-foreground" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Flight Snapshot</h3>
              <Badge variant="outline" className="ml-auto text-[10px]">{phase}</Badge>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <QuickStat label="Total Passengers" value={String(total)} accent="text-foreground" />
              <QuickStat label="Boarded" value={`${boardedPct}%`} accent={boardedPct >= 90 ? "text-emerald-500" : boardedPct >= 70 ? "text-amber-500" : "text-rose-500"} />
              <QuickStat label="Check-in Rate" value={`${insights.boardingRate.checkedInPct}%`} accent="text-sky-500" />
              <QuickStat label="No-Shows" value={String(noShows)} accent={noShows > 0 ? "text-amber-500" : "text-emerald-500"} />
              <QuickStat label="Connecting" value={`${insights.connectingPassengers.connecting}`} accent="text-violet-500" />
              <QuickStat label="Non-Revenue" value={`${dashboard.analysis.nonRevenue}`} accent="text-purple-500" />
            </div>
          </CardContent>
        </Card>

        <ReadinessGauge score={readinessScore!} />
        <RiskSummary decisions={decisions} />
      </div>

      {/* Row 2: Decision Cards */}
      {decisions.length > 0 ? (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Zap className="h-4 w-4 text-amber-500" />
            <h3 className="text-sm font-semibold">Action Items</h3>
            <Badge variant="secondary" className="text-[10px]">{decisions.length}</Badge>
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
              <h4 className="text-sm font-semibold text-emerald-600 dark:text-emerald-400">All Clear</h4>
              <p className="text-xs text-muted-foreground mt-0.5">No actionable issues detected. Flight is operationally ready.</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Row 3: Revenue Opportunities (compact) */}
      <RevenueOpportunities insights={insights} noShows={noShows} total={total} />
    </div>
  );
}

// ─── Revenue Opportunities Panel ──────────────────────────────────

function RevenueOpportunities({ insights, noShows, total }: { insights: FlightInsights; noShows: number; total: number }) {
  const opportunities = useMemo(() => {
    const ops: Array<{ label: string; value: string; detail: string; accent: string }> = [];

    // Standby clearance opportunity
    if (noShows > 0 && insights.standbyUpgrade.standbyTotal > 0) {
      const clearable = Math.min(noShows, insights.standbyUpgrade.standbyTotal);
      ops.push({
        label: "Standby Clearance",
        value: `${clearable} seats`,
        detail: `${noShows} no-shows → ${clearable} standby passengers can be cleared`,
        accent: "text-emerald-500",
      });
    }

    // Upgrade revenue
    if (insights.classMismatch.downgrades > 0) {
      ops.push({
        label: "Upgrade Recovery",
        value: `${insights.classMismatch.downgrades} pax`,
        detail: "Passengers downgraded from booked class — offer complimentary upgrade if J has availability",
        accent: "text-amber-500",
      });
    }

    // Low load factor in business
    const businessCabinSummary = insights.revenueClassMix ? Object.entries(insights.revenueClassMix)
      .filter(([cls]) => BUSINESS_CLASSES.has(cls.toUpperCase()))
      .reduce((sum, [, count]) => sum + count, 0) : 0;
    if (businessCabinSummary > 0 && businessCabinSummary < 15 && insights.standbyUpgrade.upgradeTotal > 0) {
      ops.push({
        label: "Business Upsell",
        value: `${insights.standbyUpgrade.upgradeTotal} candidates`,
        detail: `Business cabin has ${businessCabinSummary} passengers — ${insights.standbyUpgrade.upgradeTotal} upgrade candidates available`,
        accent: "text-violet-500",
      });
    }

    // Corporate account service
    if (insights.corporateTravel.corporatePct >= 30) {
      const topCompany = Object.entries(insights.corporateTravel.companies).sort((a, b) => b[1] - a[1])[0];
      ops.push({
        label: "VIP Corporate Service",
        value: `${insights.corporateTravel.totalCorporate} corp pax`,
        detail: topCompany
          ? `Top account: ${topCompany[0]} (${topCompany[1]} pax) — ensure premium handling`
          : `${insights.corporateTravel.corporatePct}% corporate travelers on board`,
        accent: "text-blue-500",
      });
    }

    return ops;
  }, [insights, noShows]);

  if (opportunities.length === 0) return null;

  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 mb-3">
          <TrendingUp className="h-4 w-4 text-emerald-500" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Revenue & Service Opportunities</h3>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {opportunities.map((op) => (
            <div key={op.label} className="rounded-lg border bg-muted/10 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium">{op.label}</span>
                <span className={cn("text-sm font-semibold tabular-nums", op.accent)}>{op.value}</span>
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">{op.detail}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

const BUSINESS_CLASSES = new Set(["J", "C", "D", "Z", "R", "F"]);
