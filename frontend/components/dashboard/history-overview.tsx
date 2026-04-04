"use client";

import { useMemo } from "react";
import {
  Activity,
  BarChart3,
  GitCompareArrows,
  Hash,
  TrendingUp,
} from "lucide-react";

import type { SnapshotCompareResponse, SnapshotMeta } from "@/lib/types";
import { buildHistoryStackedAreaOption, buildHorizontalBarOption } from "@/components/dashboard/echarts-option-builders";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EChart } from "@/components/ui/echarts";
import { cn } from "@/lib/utils";

type HistorySeriesPoint = {
  key: string;
  label: string;
  timestamp: string;
  booked: number;
  onBoard: number;
  boardingPasses: number;
};

type ChangeHotspot = {
  label: string;
  count: number;
};

interface HistoryOverviewProps {
  historySeries: HistorySeriesPoint[];
  changeHotspots: ChangeHotspot[];
  snapshots: SnapshotMeta[];
  selectedSnapshotSequence?: number | null;
  compareMode: boolean;
  compareLoading: boolean;
  compareData?: SnapshotCompareResponse;
}

function SummaryTile({
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
    <div className="rounded-xl border bg-card/70 p-4 ring-1 ring-foreground/8">
      <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className={cn("mt-2 text-2xl font-semibold tabular-nums", accent)}>{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{helper}</div>
    </div>
  );
}

function MetricCard({
  title,
  description,
  icon: Icon,
  children,
}: {
  title: string;
  description: string;
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b pb-3">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-muted/60 p-2 text-muted-foreground">
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <CardTitle className="text-sm">{title}</CardTitle>
            <CardDescription className="mt-1 text-xs">{description}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-4">{children}</CardContent>
    </Card>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">{label}</div>;
}

export function HistoryOverview({
  historySeries,
  changeHotspots,
  snapshots,
  selectedSnapshotSequence,
  compareMode,
  compareLoading,
  compareData,
}: HistoryOverviewProps) {
  const historyAreaOption = useMemo(() => buildHistoryStackedAreaOption({ data: historySeries }), [historySeries]);
  const hotspotOption = useMemo(() => buildHorizontalBarOption({
    data: changeHotspots.map((item, index) => ({
      label: item.label,
      shortLabel: item.label,
      value: item.count,
      fill: `var(--color-chart-${(index % 5) + 1})`,
    })),
    valueLabel: "Changes",
  }), [changeHotspots]);
  const latest = historySeries[historySeries.length - 1];
  const earliest = historySeries[0];
  const bookedDelta = latest && earliest ? latest.booked - earliest.booked : 0;
  const boardedDelta = latest && earliest ? latest.onBoard - earliest.onBoard : 0;
  const bpDelta = latest && earliest ? latest.boardingPasses - earliest.boardingPasses : 0;
  const changedTypeCount = compareData ? Object.values(compareData.types).filter((entry) => entry.available && entry.changed).length : 0;
  const changedDeltaRows = compareData
    ? Object.entries(compareData.types).flatMap(([type, result]) => {
        if (!result.available || !result.deltas) return [];
        return Object.entries(result.deltas)
          .filter(([, delta]) => delta.changed)
          .map(([field, delta]) => ({
            id: `${type}-${field}`,
            type,
            field,
            selected: delta.selected,
            latest: delta.latest,
            diff: delta.diff,
          }));
      })
    : [];
  const numericDiffs = changedDeltaRows.filter((row) => typeof row.diff === "number");

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <SummaryTile label="History Points" value={historySeries.length} helper="Status snapshots captured for this departure." accent="text-foreground" />
        <SummaryTile label="Passenger Delta" value={bookedDelta >= 0 ? `+${bookedDelta}` : bookedDelta} helper={earliest && latest ? `${earliest.booked} to ${latest.booked} booked` : "Not enough points to compare."} accent={bookedDelta === 0 ? "text-foreground" : bookedDelta > 0 ? "text-emerald-500" : "text-rose-500"} />
        <SummaryTile label="Boarded Delta" value={boardedDelta >= 0 ? `+${boardedDelta}` : boardedDelta} helper={earliest && latest ? `${earliest.onBoard} to ${latest.onBoard} on board` : "Not enough points to compare."} accent={boardedDelta === 0 ? "text-foreground" : boardedDelta > 0 ? "text-sky-500" : "text-rose-500"} />
        <SummaryTile label="BP Delta" value={bpDelta >= 0 ? `+${bpDelta}` : bpDelta} helper={earliest && latest ? `${earliest.boardingPasses} to ${latest.boardingPasses} boarding passes` : "Not enough points to compare."} accent={bpDelta === 0 ? "text-foreground" : bpDelta > 0 ? "text-violet-500" : "text-rose-500"} />
        <SummaryTile label="Snapshots" value={snapshots.length} helper={selectedSnapshotSequence ? `Selected snapshot #${selectedSnapshotSequence}` : "Load a snapshot to compare against latest."} accent="text-violet-500" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <MetricCard title="Passenger Progression" description="Trend of booked, boarded, and boarding-pass counts across captured status history." icon={TrendingUp}>
          {historySeries.length === 0 ? (
            <EmptyState label="No history points available." />
          ) : (
            <EChart option={historyAreaOption} className="h-[260px]" ariaLabel="Passenger Progression" />
          )}
        </MetricCard>

        <MetricCard title="Change Hotspots" description="Fields that changed most often across the flight status history." icon={Activity}>
          {changeHotspots.length === 0 ? (
            <EmptyState label="No tracked status changes found." />
          ) : (
            <EChart option={hotspotOption} className="h-[260px]" ariaLabel="Change Hotspots" />
          )}
        </MetricCard>
      </div>

      <MetricCard title="Snapshot Comparison" description="Selected snapshot against the latest data for the same flight." icon={GitCompareArrows}>
        {!selectedSnapshotSequence ? (
          <EmptyState label="Load a snapshot to compare it with the latest state." />
        ) : !compareMode ? (
          <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
            Enable compare mode to inspect snapshot deltas.
          </div>
        ) : compareLoading ? (
          <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">Calculating snapshot deltas...</div>
        ) : !compareData ? (
          <EmptyState label="No comparison data available." />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3">
              <SummaryTile label="Changed Types" value={changedTypeCount} helper="Snapshot types that differ from latest." accent={changedTypeCount > 0 ? "text-amber-500" : "text-emerald-500"} />
              <SummaryTile label="Changed Fields" value={changedDeltaRows.length} helper="Metric deltas across available snapshot types." accent={changedDeltaRows.length > 0 ? "text-blue-500" : "text-emerald-500"} />
              <SummaryTile label="Numeric Deltas" value={numericDiffs.length} helper="Changed fields with numeric difference values." accent="text-violet-500" />
            </div>

            <div className="grid gap-3 xl:grid-cols-[1.2fr_0.8fr]">
              <div className="rounded-xl border bg-muted/15 p-3">
                <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                  <Hash className="h-4 w-4 text-muted-foreground" />
                  Changed Fields
                </div>
                {changedDeltaRows.length === 0 ? (
                  <div className="text-sm text-emerald-500">Selected snapshot matches the latest values.</div>
                ) : (
                  <div className="space-y-2">
                    {changedDeltaRows.slice(0, 12).map((row) => (
                      <div key={row.id} className="flex items-center justify-between gap-3 rounded-lg border bg-background/70 px-3 py-2 text-xs">
                        <div className="min-w-0">
                          <div className="font-medium">{row.field}</div>
                          <div className="text-muted-foreground">{row.type}</div>
                        </div>
                        <div className="text-right tabular-nums">
                          <div>{String(row.selected)} <span className="text-muted-foreground">→</span> {String(row.latest)}</div>
                          {typeof row.diff === "number" ? (
                            <div className={cn("text-[11px]", row.diff > 0 ? "text-amber-500" : row.diff < 0 ? "text-sky-500" : "text-muted-foreground")}>
                              {row.diff > 0 ? `+${row.diff}` : row.diff}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-xl border bg-muted/15 p-3">
                <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                  <BarChart3 className="h-4 w-4 text-muted-foreground" />
                  Snapshot Types
                </div>
                <div className="space-y-2">
                  {Object.entries(compareData.types).map(([type, value]) => (
                    <div key={type} className="flex items-center justify-between gap-3 rounded-lg border bg-background/70 px-3 py-2 text-xs">
                      <div>
                        <div className="font-medium">{type}</div>
                        <div className="text-muted-foreground">
                          {value.available ? `Selected #${value.selectedSequence} vs latest #${value.latestSequence}` : value.reason ?? "Unavailable"}
                        </div>
                      </div>
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-[10px]",
                          !value.available ? "text-muted-foreground" : value.changed ? "border-amber-300 text-amber-600" : "border-emerald-300 text-emerald-600"
                        )}
                      >
                        {!value.available ? "N/A" : value.changed ? "Changed" : "Same"}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </MetricCard>
    </div>
  );
}