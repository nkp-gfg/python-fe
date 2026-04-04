"use client";

import { useMemo, type ElementType, type ReactNode } from "react";
import {
  AlertTriangle,
  BarChart3,
  Briefcase,
  ClipboardCheck,
  Clock3,
  CreditCard,
  FileCheck2,
  Info,
  Luggage,
  ShieldAlert,
  Ticket,
  Timer,
  TrendingUp,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts";

import type { FlightInsights } from "@/lib/types";
import type { InsightInfoKey } from "@/components/dashboard/insight-info-panel";
import { buildCabinStackedBarOption, buildCheckInTimelineAreaOption, buildDonutChartOption, buildHorizontalBarOption, buildPassengerProgressFunnelOption, buildVerticalBarOption } from "@/components/dashboard/echarts-option-builders";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EChart } from "@/components/ui/echarts";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { cn } from "@/lib/utils";

type InsightsTabProps = {
  insights: FlightInsights;
  totalPassengers: number;
  onOpenInfo?: (key: InsightInfoKey) => void;
};

type MetricRow = {
  label: string;
  value: number;
  total: number;
  color: string;
  valueClassName?: string;
};

type ChartDatum = {
  label: string;
  shortLabel: string;
  value: number;
  fill?: string;
};

type FunnelStageDatum = {
  label: string;
  value: number;
  fill?: string;
  helper?: string;
};

type CabinStackDatum = {
  cabin: "Y" | "J";
  total: number;
  segments: Array<{ bookingClass: string; value: number; fill: string }>;
};

const palette = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "oklch(0.78 0.11 165)",
  "oklch(0.72 0.15 80)",
  "oklch(0.7 0.17 20)",
];

function infoButton(onClick?: () => void, title?: string) {
  if (!onClick) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
      aria-label={title ?? "More info"}
      title={title ?? "More info"}
    >
      <Info className="h-3.5 w-3.5" />
    </button>
  );
}

function prettyLabel(value: string) {
  return value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/(\d+)/g, " $1")
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function shortLabel(value: string, max = 14) {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1)}…`;
}

function toChartData(entries: Array<[string, number]>, limit = 8): ChartDatum[] {
  return entries.slice(0, limit).map(([label, value], index) => ({
    label,
    shortLabel: shortLabel(label),
    value,
    fill: palette[index % palette.length],
  }));
}

const ECONOMY_CLASSES = new Set(["Y", "B", "H", "K", "W", "V", "S", "Q", "N", "O", "X", "G", "M", "L", "U", "T"]);
const BUSINESS_CLASSES = new Set(["J", "C", "D", "Z", "R", "F"]);

function classifyRevenueCabin(bookingClass: string): "Y" | "J" | null {
  const normalized = bookingClass.trim().toUpperCase();
  if (!normalized) return null;
  if (BUSINESS_CLASSES.has(normalized)) return "J";
  if (ECONOMY_CLASSES.has(normalized)) return "Y";
  return null;
}

function buildCabinStackData(revenueClassMix: Record<string, number>): CabinStackDatum[] {
  const grouped: Record<"Y" | "J", CabinStackDatum> = {
    Y: { cabin: "Y", total: 0, segments: [] },
    J: { cabin: "J", total: 0, segments: [] },
  };

  Object.entries(revenueClassMix)
    .sort((a, b) => b[1] - a[1])
    .forEach(([bookingClass, value], index) => {
      const cabin = classifyRevenueCabin(bookingClass);
      if (!cabin) return;

      grouped[cabin].total += value;
      grouped[cabin].segments.push({
        bookingClass,
        value,
        fill: palette[index % palette.length],
      });
    });

  return [grouped.Y, grouped.J];
}

function SummaryCard({
  label,
  value,
  helper,
  accent,
}: {
  label: string;
  value: string | number;
  helper: string;
  accent?: string;
}) {
  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
        <div className={cn("mt-2 text-2xl font-semibold tabular-nums", accent)}>{value}</div>
        <div className="mt-1 text-xs text-muted-foreground">{helper}</div>
      </CardContent>
    </Card>
  );
}

function PanelCard({
  icon: Icon,
  title,
  description,
  children,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  children: ReactNode;
  onInfo?: () => void;
}) {
  return (
    <Card className="h-full shadow-sm">
      <CardHeader className="border-b pb-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-lg bg-muted/60 p-2 text-muted-foreground">
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-sm">{title}</CardTitle>
              {infoButton(onInfo, `${title} info`)}
            </div>
            {description ? <CardDescription className="mt-1 text-xs">{description}</CardDescription> : null}
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-4">{children}</CardContent>
    </Card>
  );
}

function EmptyState({ label = "No data available." }: { label?: string }) {
  return <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">{label}</div>;
}

function RankedBarsCard({
  icon,
  title,
  description,
  data,
  onInfo,
  formatter,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  data: ChartDatum[];
  onInfo?: () => void;
  formatter?: (value: number) => string;
}) {
  const option = useMemo(() => buildHorizontalBarOption({ data, valueLabel: "Count", valueFormatter: formatter }), [data, formatter]);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <EChart option={option} className="h-[240px]" ariaLabel={title} />
      )}
    </PanelCard>
  );
}

function DonutChartCard({
  icon,
  title,
  description,
  data,
  centerLabel,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  data: ChartDatum[];
  centerLabel: string;
  onInfo?: () => void;
}) {
  const total = data.reduce((sum, item) => sum + item.value, 0);
  const option = useMemo(() => buildDonutChartOption({ title, centerLabel, data }), [centerLabel, data, title]);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr] lg:items-center">
          <EChart option={option} className="h-[220px]" ariaLabel={title} />

          <div className="space-y-2.5">
            {data.map((item) => {
              const pct = total > 0 ? Math.round((item.value / total) * 100) : 0;
              return (
                <div key={item.label} className="flex items-center justify-between gap-3 rounded-lg border bg-muted/15 px-3 py-2 text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: item.fill }} />
                    <span className="truncate text-muted-foreground">{item.label}</span>
                  </div>
                  <div className="text-right font-medium tabular-nums shrink-0">
                    {item.value} <span className="text-xs text-muted-foreground">({pct}%)</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </PanelCard>
  );
}

function CabinStackedBarsCard({
  icon,
  title,
  description,
  data,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  data: CabinStackDatum[];
  onInfo?: () => void;
}) {
  const hasData = data.some((item) => item.total > 0);
  const legendItems = data.flatMap((item) => item.segments.map((segment) => ({
    cabin: item.cabin,
    bookingClass: segment.bookingClass,
    value: segment.value,
    fill: segment.fill,
  })));
  const option = useMemo(() => buildCabinStackedBarOption({ data, businessClasses: BUSINESS_CLASSES }), [data]);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {!hasData ? (
        <EmptyState />
      ) : (
        <div className="space-y-4">
          <EChart option={option} className="h-[220px]" ariaLabel={title} />

          <div className="grid gap-3 md:grid-cols-2">
            {data.map((item) => (
              <div key={item.cabin} className="rounded-lg border bg-muted/15 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="text-sm font-medium">{item.cabin} Class</div>
                  <div className="text-sm font-semibold tabular-nums">{item.total}</div>
                </div>
                <div className="space-y-2">
                  {item.segments.length === 0 ? (
                    <div className="text-xs text-muted-foreground">No mapped booking classes.</div>
                  ) : item.segments.map((segment) => (
                    <div key={`${item.cabin}-${segment.bookingClass}`} className="flex items-center justify-between gap-3 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: segment.fill }} />
                        <span className="text-muted-foreground">Class {segment.bookingClass}</span>
                      </div>
                      <span className="font-medium tabular-nums">{segment.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </PanelCard>
  );
}

function ColumnChartCard({
  icon,
  title,
  description,
  data,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  data: ChartDatum[];
  onInfo?: () => void;
}) {
  const option = useMemo(() => buildVerticalBarOption({ data, valueLabel: "Passengers" }), [data]);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <EChart option={option} className="h-[240px]" ariaLabel={title} />
      )}
    </PanelCard>
  );
}

function AreaTimelineCard({
  icon,
  title,
  description,
  data,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  data: Array<{ hour: string; value: number }>;
  onInfo?: () => void;
}) {
  const option = useMemo(() => buildCheckInTimelineAreaOption({ data }), [data]);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {data.length === 0 ? (
        <EmptyState />
      ) : (
        <EChart option={option} className="h-[240px]" ariaLabel={title} />
      )}
    </PanelCard>
  );
}

function CompletionRowsCard({
  icon,
  title,
  description,
  rows,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  rows: MetricRow[];
  onInfo?: () => void;
}) {
  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      <div className="space-y-3">
        {rows.map((row) => {
          const pct = row.total > 0 ? Math.round((row.value / row.total) * 100) : 0;
          return (
            <div key={row.label} className="space-y-1.5">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="text-muted-foreground">{row.label}</span>
                <span className={cn("font-medium tabular-nums", row.valueClassName)}>
                  {row.value.toLocaleString()} / {row.total.toLocaleString()} <span className="text-muted-foreground">({pct}%)</span>
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${Math.max(row.value > 0 ? 4 : 0, pct)}%`, backgroundColor: row.color }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </PanelCard>
  );
}

function FunnelProgressCard({
  icon,
  title,
  description,
  stages,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  stages: FunnelStageDatum[];
  onInfo?: () => void;
}) {
  const activeStages = stages.filter((stage) => stage.value > 0);
  const option = useMemo(() => buildPassengerProgressFunnelOption({ title, data: activeStages }), [activeStages, title]);
  const baseline = activeStages[0]?.value ?? 0;

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {activeStages.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
          <EChart option={option} className="h-[240px]" ariaLabel={title} />

          <div className="space-y-2.5">
            {activeStages.map((stage, index) => {
              const pct = baseline > 0 ? Math.round((stage.value / baseline) * 100) : 0;
              const previous = activeStages[index - 1]?.value ?? baseline;
              const stagePct = previous > 0 ? Math.round((stage.value / previous) * 100) : 0;

              return (
                <div key={stage.label} className="rounded-lg border bg-muted/15 px-3 py-2.5">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: stage.fill }} />
                      <span className="truncate font-medium">{stage.label}</span>
                    </div>
                    <span className="font-semibold tabular-nums">{stage.value.toLocaleString()}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {pct}% of sold inventory{index > 0 ? ` · ${stagePct}% of previous stage` : ""}
                  </div>
                  {stage.helper ? <div className="mt-1 text-xs text-muted-foreground">{stage.helper}</div> : null}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </PanelCard>
  );
}

function ActionQueueCard({
  rows,
}: {
  rows: Array<{ label: string; value: number; tone?: string }>;
}) {
  const sorted = [...rows].sort((a, b) => b.value - a.value).filter((item) => item.value > 0);

  return (
    <PanelCard
      icon={ShieldAlert}
      title="Action Queue"
      description="Passengers or conditions most likely to require intervention before departure."
    >
      {sorted.length === 0 ? (
        <div className="flex h-[220px] items-center justify-center text-sm text-emerald-500">No open exceptions on current metrics.</div>
      ) : (
        <div className="space-y-2.5">
          {sorted.map((item, index) => (
            <div key={item.label} className="flex items-center justify-between gap-3 rounded-lg border bg-muted/20 px-3 py-2">
              <div className="flex items-center gap-3">
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-muted text-[11px] font-medium text-muted-foreground">
                  {index + 1}
                </div>
                <div>
                  <div className="text-sm font-medium">{item.label}</div>
                  <div className="text-xs text-muted-foreground">Requires review or downstream follow-up.</div>
                </div>
              </div>
              <div className={cn("text-lg font-semibold tabular-nums", item.tone)}>{item.value}</div>
            </div>
          ))}
        </div>
      )}
    </PanelCard>
  );
}

function TagListCard({
  icon,
  title,
  description,
  entries,
  tone,
  onInfo,
  emptyLabel,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  entries: Array<[string, number]>;
  tone?: string;
  onInfo?: () => void;
  emptyLabel: string;
}) {
  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {entries.length === 0 ? (
        <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">{emptyLabel}</div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {entries.map(([label, value]) => (
            <Badge key={label} variant="outline" className={cn("rounded-full px-2.5 py-1 text-xs", tone)}>
              {label}: {value}
            </Badge>
          ))}
        </div>
      )}
    </PanelCard>
  );
}

function CompositionCard({
  icon,
  title,
  description,
  segments,
  onInfo,
}: {
  icon: ElementType;
  title: string;
  description?: string;
  segments: Array<{ label: string; value: number; fill: string }>;
  onInfo?: () => void;
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);

  return (
    <PanelCard icon={icon} title={title} description={description} onInfo={onInfo}>
      {total === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-4">
          <ChartContainer
            config={{ value: { label: "Passengers", color: "var(--color-chart-4)" } }}
            className="h-[120px] w-full aspect-auto"
          >
            <BarChart
              data={[segments.reduce<Record<string, number>>((acc, segment) => {
                acc[segment.label] = segment.value;
                return acc;
              }, {})]}
              layout="vertical"
              margin={{ top: 0, right: 10, left: 0, bottom: 0 }}
            >
              <XAxis type="number" hide domain={[0, total]} />
              <YAxis type="category" dataKey={() => "Current flight"} hide />
              <ChartTooltip
                cursor={false}
                content={<ChartTooltipContent indicator="line" hideLabel />}
              />
              {segments.map((segment) => (
                <Bar key={segment.label} dataKey={segment.label} stackId="composition" fill={segment.fill} radius={6} />
              ))}
            </BarChart>
          </ChartContainer>
          <div className="space-y-2">
            {segments.map((segment) => {
              const pct = Math.round((segment.value / total) * 100);
              return (
                <div key={segment.label} className="flex items-center justify-between gap-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: segment.fill }} />
                    <span className="text-muted-foreground">{segment.label}</span>
                  </div>
                  <div className="font-medium tabular-nums">
                    {segment.value} <span className="text-xs text-muted-foreground">({pct}%)</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </PanelCard>
  );
}

export function CommercialInsightsTab({ insights, totalPassengers, onOpenInfo }: InsightsTabProps) {
  const channelData = useMemo(() => toChartData(Object.entries(insights.bookingChannels.channels).sort((a, b) => b[1] - a[1]), 8), [insights.bookingChannels.channels]);
  const paymentData = useMemo(() => toChartData(Object.entries(insights.paymentMethods).sort((a, b) => b[1] - a[1]), 8), [insights.paymentMethods]);
  const classData = useMemo(() => toChartData(Object.entries(insights.revenueClassMix).sort((a, b) => b[1] - a[1]), 8).map((item) => ({ ...item, label: `Class ${item.label}`, shortLabel: `Class ${item.shortLabel}` })), [insights.revenueClassMix]);
  const cabinStackData = useMemo(() => buildCabinStackData(insights.revenueClassMix), [insights.revenueClassMix]);
  const leadTimeData = useMemo(() => {
    if (!insights.bookingLeadTime) return [];
    const distribution = insights.bookingLeadTime.distribution;
    return [
      { label: "Same Day", shortLabel: "0d", value: distribution.sameDay, fill: palette[0] },
      { label: "Within 7d", shortLabel: "7d", value: distribution.within7d, fill: palette[1] },
      { label: "Within 30d", shortLabel: "30d", value: distribution.within30d, fill: palette[2] },
      { label: "Within 90d", shortLabel: "90d", value: distribution.within90d, fill: palette[3] },
      { label: "Over 90d", shortLabel: "90d+", value: distribution.over90d, fill: palette[4] },
    ];
  }, [insights.bookingLeadTime]);
  const companyData = useMemo(() => toChartData(Object.entries(insights.corporateTravel.companies).sort((a, b) => b[1] - a[1]), 6), [insights.corporateTravel.companies]);
  const progressStages = useMemo(() => [
    { label: "Sold", value: totalPassengers, fill: palette[0], helper: "Current passenger base on this departure." },
    { label: "Ticketed", value: insights.ticketStatus.withTicket, fill: palette[2], helper: `${insights.ticketStatus.ticketPct}% with ticket records.` },
    { label: "Boarding Pass", value: insights.boardingPasses.issued, fill: palette[1], helper: `${insights.boardingPasses.issuedPct}% issued.` },
    { label: "Checked In", value: insights.boardingRate.checkedIn, fill: palette[3], helper: `${insights.boardingRate.checkedInPct}% checked in.` },
    { label: "Boarded", value: insights.boardingRate.boarded, fill: palette[4], helper: `${insights.boardingRate.boardedPct}% boarded.` },
  ], [insights.boardingPasses.issued, insights.boardingPasses.issuedPct, insights.boardingRate.boarded, insights.boardingRate.boardedPct, insights.boardingRate.checkedIn, insights.boardingRate.checkedInPct, insights.ticketStatus.ticketPct, insights.ticketStatus.withTicket, totalPassengers]);
  const channelFamilySegments = useMemo(() => [
    { label: "Online", value: insights.bookingChannels.categories.online, fill: palette[0] },
    { label: "Agent", value: insights.bookingChannels.categories.agent, fill: palette[1] },
    { label: "Corporate", value: insights.bookingChannels.categories.corporate, fill: palette[2] },
    { label: "Other", value: insights.bookingChannels.categories.other, fill: palette[4] },
  ], [insights.bookingChannels.categories]);

  const topChannel = channelData[0];
  const topClass = classData[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <SummaryCard label="Passengers" value={totalPassengers} helper="Commercial view uses passenger and reservation-derived mix." accent="text-foreground" />
        <SummaryCard label="Avg Lead Time" value={insights.bookingLeadTime ? `${insights.bookingLeadTime.avgDays}d` : "—"} helper={insights.bookingLeadTime ? `Median ${insights.bookingLeadTime.medianDays}d` : "Booking window not available."} accent="text-sky-500" />
        <SummaryCard label="Top Channel" value={topChannel?.label ?? "—"} helper={topChannel ? `${topChannel.value} passengers` : "No booking-channel data."} accent="text-violet-500" />
        <SummaryCard label="Top Class" value={topClass?.label ?? "—"} helper={topClass ? `${topClass.value} passengers` : "No class mix available."} accent="text-emerald-500" />
        <SummaryCard label="Corporate Mix" value={`${insights.corporateTravel.corporatePct}%`} helper={`${insights.corporateTravel.totalCorporate} passengers with corporate IDs`} accent="text-amber-500" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <DonutChartCard icon={BarChart3} title="Booking Channels" description="How this departure was sold across source channels." data={channelData} centerLabel="Channels" onInfo={onOpenInfo ? () => onOpenInfo("bookingChannels") : undefined} />
        <RankedBarsCard icon={CreditCard} title="Payment Methods" description="Form-of-payment mix across reservations." data={paymentData} onInfo={onOpenInfo ? () => onOpenInfo("paymentMethods") : undefined} />
        <CabinStackedBarsCard icon={Ticket} title="Revenue Class Mix" description="Fare buckets grouped into Y and J cabin bars. Current mapping treats C, Z, and R as J; Q, S, W, and G as Y." data={cabinStackData} onInfo={onOpenInfo ? () => onOpenInfo("revenueClassMix") : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ColumnChartCard icon={Clock3} title="Booking Window Distribution" description="Distribution matters more than the average for departure demand shape." data={leadTimeData} onInfo={onOpenInfo ? () => onOpenInfo("bookingLeadTime") : undefined} />
        <CompositionCard icon={Briefcase} title="Channel Family Mix" description="Aggregate sales mix by online, agency, corporate, and other sources." segments={channelFamilySegments} onInfo={onOpenInfo ? () => onOpenInfo("bookingChannels") : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <RankedBarsCard icon={Briefcase} title="Corporate Accounts" description="Largest corporate identifiers on this flight." data={companyData} onInfo={onOpenInfo ? () => onOpenInfo("corporateTravel") : undefined} />
        <FunnelProgressCard
          icon={TrendingUp}
          title="Passenger Progress Funnel"
          description="Progression from sold inventory to boarded passengers for this departure."
          stages={progressStages}
          onInfo={onOpenInfo ? () => onOpenInfo("boardingRate") : undefined}
        />
      </div>
    </div>
  );
}

export function ReadinessInsightsTab({ insights, totalPassengers, onOpenInfo }: InsightsTabProps) {
  const docRows: MetricRow[] = [
    { label: "DOCS passport", value: insights.documentCompliance.DOCS.count, total: totalPassengers, color: palette[2], valueClassName: "text-emerald-500" },
    { label: "DOCV visa", value: insights.documentCompliance.DOCV.count, total: totalPassengers, color: palette[0], valueClassName: "text-blue-500" },
    { label: "DOCA address", value: insights.documentCompliance.DOCA.count, total: totalPassengers, color: palette[1], valueClassName: "text-violet-500" },
  ];
  const readinessRows: MetricRow[] = [
    { label: "Seats assigned", value: insights.seatOccupancy.seated, total: totalPassengers, color: palette[2], valueClassName: "text-emerald-500" },
    { label: "Boarding passes", value: insights.boardingPasses.issued, total: totalPassengers, color: palette[0], valueClassName: "text-blue-500" },
    { label: "Ticketed", value: insights.ticketStatus.withTicket, total: totalPassengers, color: palette[4], valueClassName: "text-amber-500" },
    { label: "Baggage records", value: insights.baggage.withBags, total: totalPassengers, color: palette[1], valueClassName: "text-sky-500" },
  ];
  const bagDestinationData = useMemo(() => toChartData(Object.entries(insights.baggageRouting.destinations).sort((a, b) => b[1] - a[1]), 6), [insights.baggageRouting.destinations]);
  const checkInHourData = useMemo(() => Object.entries(insights.checkInTimeline.hourDistribution)
    .sort((a, b) => Number(a[0]) - Number(b[0]))
    .map(([hour, value]) => ({ hour: `${hour.padStart(2, "0")}:00`, value })), [insights.checkInTimeline.hourDistribution]);
  const wheelchairEntries = useMemo(() => Object.entries(insights.wheelchairTypes).sort((a, b) => b[1] - a[1]), [insights.wheelchairTypes]);
  const mealEntries = useMemo(() => Object.entries(insights.mealCodes).sort((a, b) => b[1] - a[1]).slice(0, 10), [insights.mealCodes]);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <SummaryCard label="Operational Ready" value={`${insights.operationalReadiness.readinessPct}%`} helper="Composite view of seat, check-in, and boarding-pass readiness." accent="text-emerald-500" />
        <SummaryCard label="DOCS" value={`${insights.documentCompliance.DOCS.pct}%`} helper={`${insights.documentCompliance.DOCS.count} passengers with passport docs`} accent="text-blue-500" />
        <SummaryCard label="Seats Assigned" value={`${insights.seatOccupancy.seatPct}%`} helper={`${insights.seatOccupancy.seated} seated, ${insights.seatOccupancy.unseated} missing`} accent="text-sky-500" />
        <SummaryCard label="Boarding Passes" value={`${insights.boardingPasses.issuedPct}%`} helper={`${insights.boardingPasses.issued} issued, ${insights.boardingPasses.notIssued} pending`} accent="text-violet-500" />
        <SummaryCard label="Bag Coverage" value={`${insights.baggage.dataAvailablePct}%`} helper={`${insights.baggage.totalBags} bags across ${insights.baggage.withBags} passengers`} accent="text-amber-500" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <CompletionRowsCard icon={FileCheck2} title="Document Compliance" description="APIS document submission coverage across the passenger list." rows={docRows} onInfo={onOpenInfo ? () => onOpenInfo("documentCompliance") : undefined} />
        <CompletionRowsCard icon={ClipboardCheck} title="Readiness Checklist" description="Core passenger readiness milestones before departure." rows={readinessRows} onInfo={onOpenInfo ? () => onOpenInfo("seatOccupancy") : undefined} />
        <PanelCard icon={Luggage} title="Baggage Analytics" description="Bag coverage and routing detail." onInfo={onOpenInfo ? () => onOpenInfo("baggage") : undefined}>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="text-xs text-muted-foreground">With bags</div>
              <div className="mt-1 text-2xl font-semibold text-sky-500">{insights.baggage.withBags}</div>
            </div>
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="text-xs text-muted-foreground">No bags</div>
              <div className="mt-1 text-2xl font-semibold">{insights.baggage.withoutBags}</div>
            </div>
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="text-xs text-muted-foreground">Total bags</div>
              <div className="mt-1 text-2xl font-semibold text-emerald-500">{insights.baggage.totalBags}</div>
            </div>
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="text-xs text-muted-foreground">Avg per pax</div>
              <div className="mt-1 text-2xl font-semibold text-amber-500">{insights.baggage.avgBags}</div>
            </div>
          </div>
          <div className="mt-4 h-px bg-border" />
          <div className="mt-4 space-y-2 text-xs">
            <div className="flex items-center justify-between"><span className="text-muted-foreground">Coverage</span><span className="font-medium">{insights.baggage.dataAvailablePct}%</span></div>
            <div className="flex items-center justify-between"><span className="text-muted-foreground">Routes with bag tags</span><span className="font-medium">{insights.baggage.withBagRoutes}</span></div>
          </div>
        </PanelCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <AreaTimelineCard icon={Timer} title="Check-in Timeline" description={`Check-ins captured by hour. Coverage ${insights.checkInTimeline.coveragePct}%.`} data={checkInHourData} onInfo={onOpenInfo ? () => onOpenInfo("checkInSequence") : undefined} />
        <RankedBarsCard icon={Luggage} title="Bag Routing Destinations" description="Top onward or bag-tag destinations found in baggage routing data." data={bagDestinationData} onInfo={onOpenInfo ? () => onOpenInfo("baggage") : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <TagListCard icon={Users} title="Wheelchair Services" description="Breakdown of wheelchair assistance codes on the flight." entries={wheelchairEntries} tone="border-blue-200 text-blue-600 dark:border-blue-500/30 dark:text-blue-300" onInfo={onOpenInfo ? () => onOpenInfo("wheelchairTypes") : undefined} emptyLabel="No wheelchair assistance codes present." />
        <TagListCard icon={Ticket} title="Meal Requests" description="Top special meal codes requested by passengers." entries={mealEntries} tone="border-orange-200 text-orange-600 dark:border-orange-500/30 dark:text-orange-300" onInfo={onOpenInfo ? () => onOpenInfo("mealCodes") : undefined} emptyLabel="No meal requests present." />
      </div>
    </div>
  );
}

export function ExceptionsInsightsTab({ insights, totalPassengers, onOpenInfo }: InsightsTabProps) {
  const blockers: MetricRow[] = [
    { label: "No seat assigned", value: insights.operationalReadiness.noSeat, total: totalPassengers, color: "oklch(0.72 0.17 20)", valueClassName: "text-rose-500" },
    { label: "Checked-in without BP", value: insights.operationalReadiness.checkedInNoBP, total: totalPassengers, color: palette[1], valueClassName: "text-amber-500" },
    { label: "Not checked in", value: insights.operationalReadiness.notCheckedIn, total: totalPassengers, color: palette[0], valueClassName: "text-blue-500" },
    { label: "Thru without seat", value: insights.operationalReadiness.thruNoSeat, total: Math.max(1, insights.connectingPassengers.connecting), color: palette[4], valueClassName: "text-violet-500" },
  ];
  const standbyData = useMemo(() => toChartData(Object.entries(insights.standbyUpgrade.standbyCabins).sort((a, b) => b[1] - a[1]), 6), [insights.standbyUpgrade.standbyCabins]);
  const priorityEntries = useMemo(() => Object.entries(insights.priorityPassengers.codes).sort((a, b) => b[1] - a[1]), [insights.priorityPassengers.codes]);
  const passengerTypeData = useMemo(() => toChartData(Object.entries(insights.passengerTypes).sort((a, b) => b[1] - a[1]), 6).map((item) => ({ ...item, label: prettyLabel(item.label), shortLabel: shortLabel(prettyLabel(item.label)) })), [insights.passengerTypes]);
  const queueRows = [
    { label: "Not checked in", value: insights.operationalReadiness.notCheckedIn, tone: "text-blue-500" },
    { label: "No seat assigned", value: insights.operationalReadiness.noSeat, tone: "text-rose-500" },
    { label: "Checked-in without boarding pass", value: insights.operationalReadiness.checkedInNoBP, tone: "text-amber-500" },
    { label: "No ticket", value: insights.ticketStatus.withoutTicket, tone: "text-rose-500" },
    { label: "At-risk connections", value: insights.connectionRisk.atRiskCount, tone: "text-rose-500" },
    { label: "Standby passengers", value: insights.standbyUpgrade.standbyTotal, tone: "text-violet-500" },
    { label: "Class mismatches", value: insights.classMismatch.total, tone: "text-amber-500" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
        <SummaryCard label="At-Risk Connections" value={insights.connectionRisk.atRiskCount} helper={`${insights.connectionRisk.riskPct}% of connecting passengers`} accent="text-rose-500" />
        <SummaryCard label="No Seat" value={insights.operationalReadiness.noSeat} helper="Passengers still missing seat assignment." accent="text-amber-500" />
        <SummaryCard label="No Boarding Pass" value={insights.operationalReadiness.checkedInNoBP} helper="Checked in but still lacking a boarding pass." accent="text-blue-500" />
        <SummaryCard label="No Ticket" value={insights.ticketStatus.withoutTicket} helper="Passengers without ticket or VCR coverage." accent="text-rose-500" />
        <SummaryCard label="Standby" value={insights.standbyUpgrade.standbyTotal} helper={`${insights.standbyUpgrade.upgradeTotal} upgrade candidates`} accent="text-violet-500" />
        <SummaryCard label="Class Mismatch" value={insights.classMismatch.total} helper={`${insights.classMismatch.upgrades} upgrades, ${insights.classMismatch.downgrades} downgrades`} accent="text-amber-500" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <CompletionRowsCard icon={AlertTriangle} title="Operational Blockers" description="Counts that directly affect day-of-departure readiness." rows={blockers} />
        <CompositionCard icon={TrendingUp} title="Connection Risk Mix" description="How the connecting population splits between at-risk and healthy transfers." segments={[
          { label: "At risk", value: insights.connectionRisk.atRiskCount, fill: "oklch(0.72 0.17 20)" },
          { label: "Healthy", value: Math.max(insights.connectionRisk.totalConnecting - insights.connectionRisk.atRiskCount, 0), fill: palette[2] },
        ]} onInfo={onOpenInfo ? () => onOpenInfo("connectionRisk") : undefined} />
        <CompositionCard icon={Ticket} title="Class Mismatch Split" description="Upgrades and downgrades against the total mismatch count." segments={[
          { label: "Upgrades", value: insights.classMismatch.upgrades, fill: palette[2] },
          { label: "Downgrades", value: insights.classMismatch.downgrades, fill: "oklch(0.72 0.17 20)" },
          { label: "Matched", value: Math.max(totalPassengers - insights.classMismatch.total, 0), fill: "color-mix(in srgb, var(--color-chart-1) 22%, transparent)" },
        ]} onInfo={onOpenInfo ? () => onOpenInfo("classMismatch") : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ActionQueueCard rows={queueRows} />
        <RankedBarsCard icon={Users} title="Standby Pressure by Cabin" description="Standby demand clustered by requested cabin." data={standbyData.map((item) => ({ ...item, label: `Cabin ${item.label}`, shortLabel: `Cabin ${item.shortLabel}` }))} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <TagListCard icon={ShieldAlert} title="Priority Passenger Codes" description="High-touch or prioritized passengers visible on the flight manifest." entries={priorityEntries} tone="border-amber-200 text-amber-700 dark:border-amber-500/30 dark:text-amber-300" onInfo={onOpenInfo ? () => onOpenInfo("priorityPassengers") : undefined} emptyLabel="No priority passenger codes present." />
        <RankedBarsCard icon={Users} title="Passenger Type Mix" description="Passenger categories most likely to drive service or handling differences." data={passengerTypeData} onInfo={onOpenInfo ? () => onOpenInfo("passengerTypes") : undefined} />
      </div>
    </div>
  );
}