"use client";

import { useMemo } from "react";
import {
  TrendingUp,
  ArrowUpRight,
  Briefcase,
  Users,
  Star,
  Ticket,
  Plane,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { EChart } from "@/components/ui/echarts";
import { buildDonutChartOption } from "@/components/dashboard/echarts-option-builders";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

// ─── Constants ────────────────────────────────────────────────────

const palette = [
  "oklch(0.65 0.15 250)",
  "oklch(0.72 0.18 50)",
  "oklch(0.58 0.14 160)",
  "oklch(0.62 0.20 330)",
  "oklch(0.78 0.11 90)",
  "oklch(0.55 0.12 280)",
  "oklch(0.70 0.08 30)",
  "oklch(0.80 0.06 210)",
];

const J_CABIN_CLASSES = new Set(["J", "C", "D", "Z", "R", "F"]);

// ─── Types ────────────────────────────────────────────────────────

type RevenueTabProps = {
  dashboard: FlightDashboard;
};

// ─── Helpers ──────────────────────────────────────────────────────

function pct(n: number, total: number): number {
  return total > 0 ? Math.round((n / total) * 100) : 0;
}

function sortedEntries(obj: Record<string, number>): [string, number][] {
  return Object.entries(obj).sort((a, b) => b[1] - a[1]);
}

// ─── Component ────────────────────────────────────────────────────

export function RevenueTab({ dashboard }: RevenueTabProps) {
  const insights = dashboard.insights;

  const computed = useMemo(() => {
    if (!insights) return null;

    const classMix = insights.revenueClassMix;
    const totalMix = Object.values(classMix).reduce((s, v) => s + v, 0);
    let jCount = 0;
    let yCount = 0;
    for (const [cls, count] of Object.entries(classMix)) {
      if (J_CABIN_CLASSES.has(cls)) jCount += count;
      else yCount += count;
    }
    const jPct = pct(jCount, totalMix);
    const yPct = totalMix > 0 ? 100 - jPct : 0;

    const noShows = dashboard.stateSummary.others.noShow ?? 0;
    const standbyTotal = insights.standbyUpgrade.standbyTotal;
    const clearable = Math.min(noShows, standbyTotal);

    const businessLoad = pct(jCount, totalMix);

    const channels = sortedEntries(insights.bookingChannels.channels).slice(0, 8);
    const channelTotal = channels.reduce((s, [, v]) => s + v, 0);

    const jClasses = sortedEntries(classMix).filter(([c]) => J_CABIN_CLASSES.has(c));
    const yClasses = sortedEntries(classMix).filter(([c]) => !J_CABIN_CLASSES.has(c));

    const companies = sortedEntries(insights.corporateTravel.companies).slice(0, 3);
    const companyTotal = Object.values(insights.corporateTravel.companies).reduce((s, v) => s + v, 0);

    const totalPax = dashboard.passengerSummary.totalPassengers;

    const loyaltyCounts = dashboard.analysis.loyaltyCounts;
    const totalLoyalty = loyaltyCounts
      ? Object.values(loyaltyCounts).reduce((s, v) => s + v, 0)
      : 0;

    const connecting = insights.connectionRisk.totalConnecting;
    const atRisk = insights.connectionRisk.atRiskCount;

    return {
      jCount,
      yCount,
      jPct,
      yPct,
      totalMix,
      noShows,
      standbyTotal,
      clearable,
      businessLoad,
      channels,
      channelTotal,
      jClasses,
      yClasses,
      companies,
      companyTotal,
      totalPax,
      loyaltyCounts,
      totalLoyalty,
      connecting,
      atRisk,
    };
  }, [dashboard, insights]);

  // ─── Empty state ──────────────────────────────────────────────

  if (!insights || !computed) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32 text-muted-foreground">
        <TrendingUp className="h-10 w-10 opacity-40" />
        <p className="text-sm">Revenue analytics require flight ingestion.</p>
      </div>
    );
  }

  const {
    jPct,
    yPct,
    noShows,
    standbyTotal,
    clearable,
    businessLoad,
    channels,
    channelTotal,
    jClasses,
    yClasses,
    jCount,
    yCount,
    totalMix,
    companies,
    companyTotal,
    totalPax,
    loyaltyCounts,
    totalLoyalty,
    connecting,
    atRisk,
  } = computed;

  // ─── Section 1: KPI Strip ────────────────────────────────────

  const kpis = [
    { label: "Revenue Pax", value: dashboard.analysis.revenue },
    { label: "Yield Mix", value: `J:${jPct}% Y:${yPct}%` },
    { label: "Upgrade Pool", value: insights.standbyUpgrade.upgradeTotal },
    { label: "Standby Queue", value: standbyTotal },
    { label: "Corp Coverage", value: `${insights.corporateTravel.corporatePct}%` },
  ];

  // ─── Section 3: Channel chart ────────────────────────────────

  const channelChartOption = buildDonutChartOption({
    title: "Booking Channels",
    centerLabel: "Channels",
    data: channels.map(([label, value], i) => ({
      label,
      value,
      fill: palette[i % palette.length],
    })),
  });

  return (
    <div className="space-y-6">
      {/* ── Section 1: KPI Strip ─────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {kpis.map((kpi) => (
          <Card key={kpi.label}>
            <CardContent className="px-4 py-3">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {kpi.label}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {kpi.value}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Section 2: Revenue Opportunity Cards ─────────────── */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Card 1: Standby Clearance */}
        <Card>
          <CardContent className="space-y-3 px-4 py-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <TrendingUp className="h-4 w-4 text-emerald-500" />
              Standby Clearance Opportunity
            </div>
            {noShows > 0 && standbyTotal > 0 ? (
              <>
                <p className="text-3xl font-bold tabular-nums text-emerald-500">
                  {clearable}
                </p>
                <p className="text-xs text-muted-foreground">
                  {noShows} no-show seat{noShows !== 1 ? "s" : ""} available.{" "}
                  {standbyTotal} standby passenger{standbyTotal !== 1 ? "s" : ""} waiting.{" "}
                  {clearable} can be cleared immediately.
                </p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No clearance opportunity
              </p>
            )}
          </CardContent>
        </Card>

        {/* Card 2: Upgrade Potential */}
        <Card>
          <CardContent className="space-y-3 px-4 py-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <ArrowUpRight className="h-4 w-4 text-violet-500" />
              Upgrade Potential
            </div>
            <div className="space-y-1 text-sm tabular-nums">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Upgrades</span>
                <span>{insights.classMismatch.upgrades}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Downgrades</span>
                <span>{insights.classMismatch.downgrades}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Business Load</span>
                <span>{businessLoad}%</span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {insights.classMismatch.downgrades > 0
                ? `${insights.classMismatch.downgrades} passenger${insights.classMismatch.downgrades !== 1 ? "s" : ""} downgraded — offer complimentary upgrades for goodwill`
                : businessLoad < 70 && insights.standbyUpgrade.upgradeTotal > 0
                  ? `Business cabin at ${businessLoad}% — ${insights.standbyUpgrade.upgradeTotal} upgrade candidate${insights.standbyUpgrade.upgradeTotal !== 1 ? "s" : ""} available`
                  : "No class mismatches detected"}
            </p>
          </CardContent>
        </Card>

        {/* Card 3: Corporate Account Intelligence */}
        <Card>
          <CardContent className="space-y-3 px-4 py-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Briefcase className="h-4 w-4 text-amber-500" />
              Corporate Account Intelligence
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold tabular-nums">
                {insights.corporateTravel.totalCorporate}
              </span>
              <span className="text-xs text-muted-foreground">
                corporate ({insights.corporateTravel.corporatePct}%)
              </span>
            </div>
            {companies.length > 0 && (
              <div className="space-y-1.5">
                {companies.map(([name, count]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="truncate">{name}</span>
                    <div className="flex items-center gap-2">
                      <span className="tabular-nums">{count}</span>
                      <Badge
                        variant="secondary"
                        className="text-[10px] tabular-nums"
                      >
                        {pct(count, companyTotal)}%
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {insights.corporateTravel.corporatePct >= 30 && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                High corporate concentration — prioritize premium service
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── Section 3: Revenue Analytics ─────────────────────── */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Left: Booking Channel Revenue Split */}
        <Card>
          <CardContent className="px-4 py-4">
            <p className="mb-3 text-sm font-semibold">
              Booking Channel Revenue Split
            </p>
            <div className="flex items-start gap-4">
              <div className="h-[200px] w-[200px] shrink-0">
                <EChart option={channelChartOption} className="h-full w-full" />
              </div>
              <div className="space-y-1.5 pt-2">
                {channels.map(([label, value], i) => (
                  <div key={label} className="flex items-center gap-2 text-xs">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: palette[i % palette.length] }}
                    />
                    <span className="truncate text-muted-foreground">
                      {label}
                    </span>
                    <span className="ml-auto tabular-nums">{value}</span>
                    <span className="w-8 text-right tabular-nums text-muted-foreground">
                      {pct(value, channelTotal)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Right: Revenue Class Distribution */}
        <Card>
          <CardContent className="space-y-4 px-4 py-4">
            <p className="text-sm font-semibold">
              Revenue Class Distribution
            </p>

            {/* J Cabin bar */}
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium">J Cabin</span>
                <span className="tabular-nums text-muted-foreground">
                  {jCount} pax ({pct(jCount, totalMix)}%)
                </span>
              </div>
              <div className="h-5 w-full overflow-hidden rounded-md bg-muted">
                <div
                  className="h-full rounded-md"
                  style={{
                    width: `${pct(jCount, totalMix)}%`,
                    backgroundColor: palette[0],
                    minWidth: jCount > 0 ? "4px" : "0",
                  }}
                />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
                {jClasses.map(([cls, count]) => (
                  <span key={cls} className="text-[11px] tabular-nums text-muted-foreground">
                    {cls}: {count}
                  </span>
                ))}
              </div>
            </div>

            {/* Y Cabin bar */}
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium">Y Cabin</span>
                <span className="tabular-nums text-muted-foreground">
                  {yCount} pax ({pct(yCount, totalMix)}%)
                </span>
              </div>
              <div className="h-5 w-full overflow-hidden rounded-md bg-muted">
                <div
                  className="h-full rounded-md"
                  style={{
                    width: `${pct(yCount, totalMix)}%`,
                    backgroundColor: palette[1],
                    minWidth: yCount > 0 ? "4px" : "0",
                  }}
                />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
                {yClasses.map(([cls, count]) => (
                  <span key={cls} className="text-[11px] tabular-nums text-muted-foreground">
                    {cls}: {count}
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Section 4: Passenger Value Segmentation ──────────── */}
      <Card>
        <CardContent className="px-4 py-4">
          <p className="mb-4 text-sm font-semibold">
            Passenger Value Segmentation
          </p>
          <div className="space-y-3">
            {/* Premium */}
            <SegmentRow
              icon={<Star className="h-4 w-4 text-amber-500" />}
              label="Premium"
              count={jCount}
              total={totalPax}
              recommendation={
                jCount > 0
                  ? `${jCount} business class passengers — ensure premium service levels`
                  : undefined
              }
            />
            {/* Corporate */}
            <SegmentRow
              icon={<Briefcase className="h-4 w-4 text-violet-500" />}
              label="Corporate"
              count={insights.corporateTravel.totalCorporate}
              total={totalPax}
              recommendation={
                insights.corporateTravel.corporatePct >= 30
                  ? "High corporate concentration — monitor service quality"
                  : undefined
              }
            />
            {/* Loyalty */}
            <SegmentRow
              icon={<Users className="h-4 w-4 text-sky-500" />}
              label="Loyalty"
              count={totalLoyalty}
              total={totalPax}
              detail={
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {(["FF", "BLU", "SLV", "GLD", "BLK"] as const).map(
                    (tier) =>
                      (loyaltyCounts?.[tier] ?? 0) > 0 && (
                        <span key={tier}>
                          {tier}: {loyaltyCounts?.[tier]}
                        </span>
                      ),
                  )}
                </div>
              }
            />
            {/* Revenue */}
            <SegmentRow
              icon={<Ticket className="h-4 w-4 text-emerald-500" />}
              label="Revenue"
              count={dashboard.analysis.revenue}
              total={totalPax}
              recommendation={
                dashboard.analysis.nonRevenue > 0
                  ? `${dashboard.analysis.nonRevenue} non-revenue passengers on manifest`
                  : undefined
              }
            />
            {/* Connecting */}
            <SegmentRow
              icon={<Plane className="h-4 w-4 text-rose-500" />}
              label="Connecting"
              count={connecting}
              total={totalPax}
              recommendation={
                atRisk > 0
                  ? `${atRisk} at-risk connecting passengers — prioritize boarding`
                  : undefined
              }
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ─── Segment Row ──────────────────────────────────────────────────

function SegmentRow({
  icon,
  label,
  count,
  total,
  recommendation,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
  total: number;
  recommendation?: string;
  detail?: React.ReactNode;
}) {
  const percentage = pct(count, total);

  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">{label}</span>
          <span className="tabular-nums text-muted-foreground">
            {count} ({percentage}%)
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-foreground/25"
            style={{ width: `${percentage}%`, minWidth: count > 0 ? "2px" : "0" }}
          />
        </div>
        {detail}
        {recommendation && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {recommendation}
          </p>
        )}
      </div>
    </div>
  );
}
