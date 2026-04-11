"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Route } from "lucide-react";
import { fetchPhaseJourney } from "@/lib/api";
import type { PhaseSnapshot } from "@/lib/types";
import { PhaseSnapshotCards } from "@/components/dashboard/phase-snapshot-cards";
import { PhaseTransitionChart } from "@/components/dashboard/phase-transition-chart";
import { Card, CardContent } from "@/components/ui/card";

interface PhaseJourneyProps {
  flightNumber: string;
  origin?: string;
  date?: string;
}

export function PhaseJourney({ flightNumber, origin, date }: PhaseJourneyProps) {
  const [selectedPhase, setSelectedPhase] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["phase-journey", flightNumber, origin, date],
    queryFn: () => fetchPhaseJourney(flightNumber, origin, date),
    enabled: Boolean(flightNumber),
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">Loading phase journey...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-destructive">
        Failed to load phase journey data.
      </div>
    );
  }

  if (!data || data.phases.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground gap-2">
        <Route className="h-8 w-8 opacity-40" />
        <p>No phase journey data available yet.</p>
        <p className="text-[10px]">Phase snapshots are captured as the flight progresses through its lifecycle.</p>
      </div>
    );
  }

  const handlePhaseClick = (ps: PhaseSnapshot) => {
    setSelectedPhase(selectedPhase === ps.phase ? null : ps.phase);
  };

  // Find selected phase detail
  const selectedDetail = data.phases.find((p) => p.phase === selectedPhase);

  return (
    <div className="space-y-4">
      {/* Section Header */}
      <div className="flex items-center gap-2">
        <Route className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">Flight Phase Journey</h2>
        <span className="text-[10px] text-muted-foreground ml-auto">
          {data.phases.length} phase{data.phases.length !== 1 ? "s" : ""} captured
          {data.transitions.length > 0 && ` · ${data.transitions.length} transition${data.transitions.length !== 1 ? "s" : ""}`}
        </span>
      </div>

      {/* Phase Snapshot Cards — horizontally scrollable */}
      <PhaseSnapshotCards
        phases={data.phases}
        selectedPhase={selectedPhase}
        onPhaseClick={handlePhaseClick}
      />

      {/* Phase Detail Drill-Down */}
      {selectedDetail && (
        <Card className="shadow-sm border-primary/30">
          <CardContent className="p-4">
            <h3 className="text-xs font-bold uppercase tracking-wide text-muted-foreground mb-3">
              {selectedDetail.label} Phase Detail
              <span className="ml-2 text-[10px] font-normal normal-case">
                (Snapshot #{selectedDetail.sequenceNumber})
              </span>
            </h3>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
              {/* Status Distribution */}
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground font-medium mb-2">Status Distribution</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-amber-400">Booked</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.booked.totalPassengers}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-blue-400">Checked-In</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.checkedIn.totalPassengers}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-emerald-400">Boarded</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.boarded.totalPassengers}</span>
                  </div>
                </div>
              </div>

              {/* Cabin Breakdown */}
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground font-medium mb-2">Cabin Breakdown</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-emerald-400">Economy</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.economy?.total ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-amber-400">Business</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.business?.total ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Revenue / Non-Rev</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.revenue} / {selectedDetail.summary.nonRevenue}</span>
                  </div>
                </div>
              </div>

              {/* Demographics */}
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground font-medium mb-2">Demographics</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-blue-400">Male</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.totalMale}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-pink-400">Female</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.totalFemale}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-amber-400">Children</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.totalChildren}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-teal-400">Infants</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.totalInfants}</span>
                  </div>
                </div>
              </div>

              {/* Economy Detail */}
              <div className="rounded-lg bg-muted/30 p-3">
                <p className="text-[10px] text-muted-foreground font-medium mb-2">Boarded Detail</p>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-emerald-400">Econ Adults</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.boarded.economyDetail?.adults ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-emerald-400">Econ Children</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.boarded.economyDetail?.children ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-amber-400">Biz Adults</span>
                    <span className="font-bold tabular-nums">{selectedDetail.summary.boarded.businessDetail?.adults ?? 0}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-purple-400">Staff</span>
                    <span className="font-bold tabular-nums">
                      {(selectedDetail.summary.boarded.economyDetail?.staff ?? 0) + (selectedDetail.summary.boarded.businessDetail?.staff ?? 0)}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Phase Comparison Charts */}
      {data.phases.length > 1 && (
        <PhaseTransitionChart
          phases={data.phases}
          transitions={data.transitions}
        />
      )}

      {/* Single Phase Info */}
      {data.phases.length === 1 && (
        <div className="text-center py-4 text-xs text-muted-foreground">
          Only one phase snapshot is available. Charts and transitions will appear as the flight progresses through more phases.
        </div>
      )}
    </div>
  );
}
