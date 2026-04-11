"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import type { PhaseSnapshot, PhaseTransition } from "@/lib/types";
import { EChart } from "@/components/ui/echarts";
import {
  buildPhaseTransitionSankeyOption,
  buildPhaseStackedBarOption,
  buildPhaseDemographicBarOption,
  buildPhaseCabinBarOption,
} from "@/components/dashboard/echarts-option-builders";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ArrowRightLeft, BarChart3, GitBranch, Users, Plane, Maximize2 } from "lucide-react";

const STATE_COLORS: Record<string, string> = {
  booked: "#f59e0b",
  checkedIn: "#38bdf8",
  boarded: "#34d399",
  new: "#a78bfa",
  removed: "#fb7185",
};

const STATE_LABELS: Record<string, string> = {
  booked: "Booked",
  checkedIn: "Checked-In",
  boarded: "Boarded",
  new: "Added",
  removed: "Removed",
};

type ChartView = "stacked" | "sankey" | "demographics" | "cabin";

interface PhaseTransitionChartProps {
  phases: PhaseSnapshot[];
  transitions: PhaseTransition[];
}

export function PhaseTransitionChart({ phases, transitions }: PhaseTransitionChartProps) {
  const [view, setView] = useState<ChartView>("stacked");
  const [fullscreen, setFullscreen] = useState(false);

  const stackedData = useMemo(() => {
    return phases.map((ps) => ({
      phase: ps.label,
      booked: ps.summary.booked.totalPassengers,
      checkedIn: ps.summary.checkedIn.totalPassengers,
      boarded: ps.summary.boarded.totalPassengers,
    }));
  }, [phases]);

  const demographicData = useMemo(() => {
    return phases.map((ps) => ({
      phase: ps.label,
      male: ps.summary.totalMale,
      female: ps.summary.totalFemale,
      children: ps.summary.totalChildren,
      infants: ps.summary.totalInfants,
    }));
  }, [phases]);

  const cabinData = useMemo(() => {
    return phases.map((ps) => ({
      phase: ps.label,
      economy: ps.summary.cabinTotals?.economy?.passengers ?? ps.summary.economy?.total ?? 0,
      business: ps.summary.cabinTotals?.business?.passengers ?? ps.summary.business?.total ?? 0,
    }));
  }, [phases]);

  const sankeyData = useMemo(() => {
    if (transitions.length === 0) return null;

    const nodes: Array<{ name: string; itemStyle: { color: string } }> = [];
    const links: Array<{ source: string; target: string; value: number }> = [];
    const nodeSet = new Set<string>();

    for (const t of transitions) {
      for (const flow of t.flows) {
        const sourceName = `${t.fromLabel}: ${STATE_LABELS[flow.fromState] ?? flow.fromState}`;
        const targetName = `${t.toLabel}: ${STATE_LABELS[flow.toState] ?? flow.toState}`;

        if (!nodeSet.has(sourceName)) {
          nodeSet.add(sourceName);
          nodes.push({
            name: sourceName,
            itemStyle: { color: STATE_COLORS[flow.fromState] ?? "#64748b" },
          });
        }
        if (!nodeSet.has(targetName)) {
          nodeSet.add(targetName);
          nodes.push({
            name: targetName,
            itemStyle: { color: STATE_COLORS[flow.toState] ?? "#64748b" },
          });
        }

        if (flow.count > 0) {
          links.push({
            source: sourceName,
            target: targetName,
            value: flow.count,
          });
        }
      }
    }

    return { nodes, links };
  }, [transitions]);

  const stackedOption = useMemo(() => buildPhaseStackedBarOption({ data: stackedData }), [stackedData]);
  const demographicOption = useMemo(() => buildPhaseDemographicBarOption({ data: demographicData }), [demographicData]);
  const cabinOption = useMemo(() => buildPhaseCabinBarOption({ data: cabinData }), [cabinData]);
  const sankeyOption = useMemo(
    () => sankeyData ? buildPhaseTransitionSankeyOption(sankeyData) : null,
    [sankeyData],
  );

  const viewButtons: Array<{ key: ChartView; label: string; icon: typeof BarChart3 }> = [
    { key: "stacked", label: "Status", icon: BarChart3 },
    { key: "demographics", label: "Demographics", icon: Users },
    { key: "cabin", label: "Cabin", icon: Plane },
    { key: "sankey", label: "Flow", icon: GitBranch },
  ];

  const VIEW_LABELS: Record<ChartView, string> = {
    stacked: "Status Breakdown",
    demographics: "Demographics",
    cabin: "Cabin Breakdown",
    sankey: "Passenger Flow",
  };

  const currentOption =
    view === "stacked" ? stackedOption :
    view === "demographics" ? demographicOption :
    view === "cabin" ? cabinOption :
    view === "sankey" ? sankeyOption : null;

  return (
    <div className="space-y-3">
      {/* View Toggle */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          Phase Comparison
        </h3>
        <div className="flex items-center gap-2">
          <div className="flex gap-1 rounded-lg bg-muted/50 p-0.5">
            {viewButtons.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setView(key)}
                disabled={key === "sankey" && !sankeyOption}
                className={cn(
                  "flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] font-medium transition-all",
                  view === key
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent",
                  key === "sankey" && !sankeyOption && "opacity-40 cursor-not-allowed",
                )}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setFullscreen(true)}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-all"
            title="Expand chart"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Inline Chart Area */}
      <div className="rounded-lg border bg-card shadow-sm overflow-hidden">
        {view === "stacked" && (
          <EChart option={stackedOption} className="h-64 w-full" ariaLabel="Phase stacked bar comparison" />
        )}
        {view === "demographics" && (
          <EChart option={demographicOption} className="h-64 w-full" ariaLabel="Passenger demographics breakdown" />
        )}
        {view === "cabin" && (
          <EChart option={cabinOption} className="h-64 w-full" ariaLabel="Cabin distribution chart" />
        )}
        {view === "sankey" && sankeyOption && (
          <EChart option={sankeyOption} className="h-80 w-full" ariaLabel="Phase transition Sankey flow" />
        )}
        {view === "sankey" && !sankeyOption && (
          <div className="flex items-center justify-center h-64 text-sm text-muted-foreground">
            Sankey flow requires at least 2 phase snapshots.
          </div>
        )}
      </div>

      {/* Fullscreen Dialog */}
      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="max-w-[95vw] w-[95vw] h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              Phase Comparison — {VIEW_LABELS[view]}
            </DialogTitle>
            <div className="flex gap-1 rounded-lg bg-muted/50 p-0.5 w-fit">
              {viewButtons.map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  onClick={() => setView(key)}
                  disabled={key === "sankey" && !sankeyOption}
                  className={cn(
                    "flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
                    view === key
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent",
                    key === "sankey" && !sankeyOption && "opacity-40 cursor-not-allowed",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </DialogHeader>
          <div className="flex-1 min-h-0 rounded-lg border bg-card overflow-hidden">
            {view === "sankey" && !sankeyOption ? (
              <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                Sankey flow requires at least 2 phase snapshots.
              </div>
            ) : currentOption ? (
              <EChart option={currentOption} className="h-full w-full" ariaLabel={`Phase comparison — ${VIEW_LABELS[view]} (fullscreen)`} />
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      {/* Transition Summary Cards */}
      {transitions.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            <ArrowRightLeft className="h-3 w-3 inline mr-1" />
            Transition Details
          </h3>
          <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {transitions.map((t) => (
              <div
                key={`${t.fromPhase}-${t.toPhase}`}
                className="rounded-lg border bg-card p-3 shadow-sm"
              >
                <div className="flex items-center gap-1 mb-2">
                  <span className="text-xs font-bold">{t.fromLabel}</span>
                  <ArrowRightLeft className="h-3 w-3 text-muted-foreground" />
                  <span className="text-xs font-bold">{t.toLabel}</span>
                </div>
                <div className="space-y-0.5">
                  {t.flows
                    .filter((f) => f.fromState !== "new" && f.toState !== "removed")
                    .slice(0, 6)
                    .map((f, i) => (
                      <div key={i} className="flex items-center justify-between text-[10px]">
                        <span className="flex items-center gap-1">
                          <span
                            className="inline-block w-2 h-2 rounded-full"
                            style={{ backgroundColor: STATE_COLORS[f.fromState] }}
                          />
                          {STATE_LABELS[f.fromState] ?? f.fromState}
                          <span className="text-muted-foreground">→</span>
                          <span
                            className="inline-block w-2 h-2 rounded-full"
                            style={{ backgroundColor: STATE_COLORS[f.toState] }}
                          />
                          {STATE_LABELS[f.toState] ?? f.toState}
                        </span>
                        <span className="font-bold tabular-nums">{f.count}</span>
                      </div>
                    ))}
                  {t.addedCount > 0 && (
                    <div className="flex items-center justify-between text-[10px] text-purple-400">
                      <span>+ Added</span>
                      <span className="font-bold">{t.addedCount}</span>
                    </div>
                  )}
                  {t.removedCount > 0 && (
                    <div className="flex items-center justify-between text-[10px] text-pink-400">
                      <span>- Removed</span>
                      <span className="font-bold">{t.removedCount}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
