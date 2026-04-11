"use client";

import { cn } from "@/lib/utils";
import type { PhaseSnapshot } from "@/lib/types";
import { Calendar, CheckCircle2, DoorClosed, Plane, ScanLine, Users } from "lucide-react";

const PHASE_CONFIG: Record<string, { color: string; borderColor: string; icon: typeof Calendar }> = {
  SCHEDULED: { color: "text-slate-400", borderColor: "border-slate-500/40", icon: Calendar },
  CHECK_IN: { color: "text-blue-400", borderColor: "border-blue-500/40", icon: CheckCircle2 },
  BOARDING: { color: "text-amber-400", borderColor: "border-amber-500/40", icon: ScanLine },
  CLOSED: { color: "text-red-400", borderColor: "border-red-500/40", icon: DoorClosed },
  DEPARTED: { color: "text-emerald-400", borderColor: "border-emerald-500/40", icon: Plane },
};

function formatTime(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "—";
  }
}

interface PhaseSnapshotCardsProps {
  phases: PhaseSnapshot[];
  selectedPhase?: string | null;
  onPhaseClick?: (phase: PhaseSnapshot) => void;
}

export function PhaseSnapshotCards({ phases, selectedPhase, onPhaseClick }: PhaseSnapshotCardsProps) {
  if (!phases || phases.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        No phase snapshots available — ingest more data to track the flight journey.
      </div>
    );
  }

  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {phases.map((ps) => {
        const config = PHASE_CONFIG[ps.phase] ?? PHASE_CONFIG.SCHEDULED;
        const Icon = config.icon;
        const s = ps.summary;
        const isSelected = selectedPhase === ps.phase;

        return (
          <button
            key={ps.phase}
            onClick={() => onPhaseClick?.(ps)}
            className={cn(
              "flex-shrink-0 w-56 rounded-lg border bg-card p-3 text-left transition-all hover:shadow-md",
              config.borderColor,
              isSelected && "ring-2 ring-primary shadow-lg",
            )}
          >
            {/* Phase Header */}
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <Icon className={cn("h-3.5 w-3.5", config.color)} />
                <span className={cn("text-xs font-bold uppercase tracking-wide", config.color)}>
                  {ps.label}
                </span>
              </div>
              <span className="text-[10px] text-muted-foreground font-mono">
                {formatTime(ps.capturedAt)}
              </span>
            </div>

            {/* Total */}
            <div className="flex items-baseline gap-1 mb-2">
              <Users className="h-3 w-3 text-muted-foreground" />
              <span className="text-lg font-bold">{s.totalPassengers}</span>
              <span className="text-[10px] text-muted-foreground">
                ({s.totalSouls} souls)
              </span>
            </div>

            {/* Status Breakdown */}
            <div className="grid grid-cols-3 gap-1 mb-2">
              <div className="text-center rounded bg-amber-500/10 py-0.5">
                <p className="text-[9px] text-amber-400 font-medium">Booked</p>
                <p className="text-xs font-bold text-amber-400">
                  {s.booked.totalPassengers}
                </p>
              </div>
              <div className="text-center rounded bg-blue-500/10 py-0.5">
                <p className="text-[9px] text-blue-400 font-medium">ChkIn</p>
                <p className="text-xs font-bold text-blue-400">
                  {s.checkedIn.totalPassengers}
                </p>
              </div>
              <div className="text-center rounded bg-emerald-500/10 py-0.5">
                <p className="text-[9px] text-emerald-400 font-medium">Board</p>
                <p className="text-xs font-bold text-emerald-400">
                  {s.boarded.totalPassengers}
                </p>
              </div>
            </div>

            {/* Cabin Split */}
            <div className="flex gap-2 mb-1.5">
              <div className="flex-1 text-center rounded bg-emerald-500/8 py-0.5">
                <p className="text-[9px] text-emerald-400">Economy</p>
                <p className="text-xs font-semibold">{s.cabinTotals?.economy?.passengers ?? s.economy?.total ?? 0}</p>
              </div>
              <div className="flex-1 text-center rounded bg-amber-500/8 py-0.5">
                <p className="text-[9px] text-amber-400">Business</p>
                <p className="text-xs font-semibold">{s.cabinTotals?.business?.passengers ?? s.business?.total ?? 0}</p>
              </div>
            </div>

            {/* Demographics Row */}
            <div className="flex gap-1.5 text-[10px]">
              {s.totalMale > 0 && (
                <span className="text-blue-400">
                  M:{s.totalMale}
                </span>
              )}
              {s.totalFemale > 0 && (
                <span className="text-pink-400">
                  F:{s.totalFemale}
                </span>
              )}
              {s.totalChildren > 0 && (
                <span className="text-amber-400">
                  C:{s.totalChildren}
                </span>
              )}
              {s.totalInfants > 0 && (
                <span className="text-teal-400">
                  I:{s.totalInfants}
                </span>
              )}
              {s.nonRevenue > 0 && (
                <span className="text-purple-400">
                  NR:{s.nonRevenue}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
