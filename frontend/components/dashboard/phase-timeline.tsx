"use client";

import type { FlightPhaseCode } from "@/lib/types";
import { CalendarDays, UserCheck, ScanLine, DoorClosed, PlaneTakeoff } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  phase: FlightPhaseCode;
  label: string;
}

const PHASES: {
  code: FlightPhaseCode;
  label: string;
  icon: React.ElementType;
  activeColor: string;
  dotActive: string;
  lineActive: string;
}[] = [
  {
    code: "SCHEDULED",
    label: "Scheduled",
    icon: CalendarDays,
    activeColor: "text-slate-400",
    dotActive: "bg-slate-500 ring-slate-500/30",
    lineActive: "bg-slate-500",
  },
  {
    code: "CHECK_IN",
    label: "Check-In",
    icon: UserCheck,
    activeColor: "text-blue-400",
    dotActive: "bg-blue-500 ring-blue-500/30",
    lineActive: "bg-blue-500",
  },
  {
    code: "BOARDING",
    label: "Boarding",
    icon: ScanLine,
    activeColor: "text-amber-400",
    dotActive: "bg-amber-500 ring-amber-500/30",
    lineActive: "bg-amber-500",
  },
  {
    code: "CLOSED",
    label: "Closed",
    icon: DoorClosed,
    activeColor: "text-red-400",
    dotActive: "bg-red-500 ring-red-500/30",
    lineActive: "bg-red-500",
  },
  {
    code: "DEPARTED",
    label: "Departed",
    icon: PlaneTakeoff,
    activeColor: "text-gray-400",
    dotActive: "bg-gray-500 ring-gray-500/30",
    lineActive: "bg-gray-500",
  },
];

function phaseIndex(code: FlightPhaseCode): number {
  const idx = PHASES.findIndex((p) => p.code === code);
  return idx >= 0 ? idx : 0;
}

export function PhaseTimeline({ phase, label }: Props) {
  const currentIdx = phaseIndex(phase);

  return (
    <div className="flex items-center gap-0 w-full px-1">
      {PHASES.map((p, i) => {
        const isPast = i < currentIdx;
        const isCurrent = i === currentIdx;
        const isFuture = i > currentIdx;
        const Icon = p.icon;

        return (
          <div key={p.code} className="flex items-center flex-1 last:flex-none">
            {/* Step dot + label */}
            <div className="flex flex-col items-center gap-0.5 min-w-[60px]">
              <div
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full transition-all",
                  isCurrent && `${p.dotActive} ring-4 shadow-lg`,
                  isPast && "bg-muted-foreground/30",
                  isFuture && "bg-muted/40 border border-muted-foreground/20",
                )}
              >
                <Icon
                  className={cn(
                    "h-3.5 w-3.5",
                    isCurrent && "text-white",
                    isPast && "text-muted-foreground/70",
                    isFuture && "text-muted-foreground/40",
                  )}
                />
              </div>
              <span
                className={cn(
                  "text-[10px] leading-tight text-center",
                  isCurrent && `font-bold ${p.activeColor}`,
                  isPast && "text-muted-foreground/60 font-medium",
                  isFuture && "text-muted-foreground/40",
                )}
              >
                {isCurrent ? label : p.label}
              </span>
            </div>

            {/* Connector line (not after last) */}
            {i < PHASES.length - 1 && (
              <div className="flex-1 h-0.5 mx-1 rounded-full">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    i < currentIdx ? PHASES[Math.min(i + 1, currentIdx)].lineActive : "bg-muted-foreground/15",
                  )}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
