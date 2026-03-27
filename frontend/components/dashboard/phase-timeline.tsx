"use client";

import type { FlightPhaseCode } from "@/lib/types";
import {
  CalendarDays,
  UserCheck,
  ScanLine,
  DoorClosed,
  PlaneTakeoff,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  phase: FlightPhaseCode;
  label: string;
}

const PHASES: {
  code: FlightPhaseCode;
  label: string;
  icon: React.ElementType;
  color: string;        // text color for current label
  bg: string;           // bg for current dot
  ring: string;         // glow ring for current dot
  line: string;         // connector color for completed segment
  pastBg: string;       // bg for completed dot
}[] = [
  {
    code: "SCHEDULED",
    label: "Scheduled",
    icon: CalendarDays,
    color: "text-slate-600 dark:text-slate-300",
    bg: "bg-slate-600 dark:bg-slate-500",
    ring: "ring-slate-600/25 dark:ring-slate-400/30",
    line: "bg-slate-500",
    pastBg: "bg-slate-500/80 dark:bg-slate-500/60",
  },
  {
    code: "CHECK_IN",
    label: "Check-In",
    icon: UserCheck,
    color: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-600 dark:bg-blue-500",
    ring: "ring-blue-600/25 dark:ring-blue-400/30",
    line: "bg-blue-500",
    pastBg: "bg-blue-500/80 dark:bg-blue-500/60",
  },
  {
    code: "BOARDING",
    label: "Boarding",
    icon: ScanLine,
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500 dark:bg-amber-500",
    ring: "ring-amber-500/25 dark:ring-amber-400/30",
    line: "bg-amber-500",
    pastBg: "bg-amber-500/80 dark:bg-amber-500/60",
  },
  {
    code: "CLOSED",
    label: "Closed",
    icon: DoorClosed,
    color: "text-red-600 dark:text-red-400",
    bg: "bg-red-600 dark:bg-red-500",
    ring: "ring-red-600/25 dark:ring-red-400/30",
    line: "bg-red-500",
    pastBg: "bg-red-500/80 dark:bg-red-500/60",
  },
  {
    code: "DEPARTED",
    label: "Departed",
    icon: PlaneTakeoff,
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-600 dark:bg-emerald-500",
    ring: "ring-emerald-600/25 dark:ring-emerald-400/30",
    line: "bg-emerald-500",
    pastBg: "bg-emerald-500/80 dark:bg-emerald-500/60",
  },
];

function phaseIndex(code: FlightPhaseCode): number {
  const idx = PHASES.findIndex((p) => p.code === code);
  return idx >= 0 ? idx : 0;
}

export function PhaseTimeline({ phase, label }: Props) {
  const currentIdx = phaseIndex(phase);

  return (
    <div className="flex items-center gap-0 w-full px-2 py-1">
      {PHASES.map((p, i) => {
        const isPast = i < currentIdx;
        const isCurrent = i === currentIdx;
        const isFuture = i > currentIdx;
        const Icon = p.icon;

        return (
          <div key={p.code} className="flex items-center flex-1 last:flex-none">
            {/* Step node + label */}
            <div className={cn(
              "flex flex-col items-center gap-1 relative",
              isCurrent ? "min-w-[76px]" : "min-w-[60px]",
            )}>
              {/* Dot / icon circle */}
              <div
                className={cn(
                  "relative flex items-center justify-center rounded-full transition-all duration-300",
                  isCurrent && `h-9 w-9 ${p.bg} ring-[5px] ${p.ring} shadow-lg`,
                  isPast && `h-7 w-7 ${p.pastBg}`,
                  isFuture && "h-7 w-7 bg-muted dark:bg-muted/50 border-2 border-muted-foreground/20",
                )}
              >
                {isPast ? (
                  <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
                ) : (
                  <Icon
                    className={cn(
                      "transition-all",
                      isCurrent && "h-4.5 w-4.5 text-white",
                      isFuture && "h-3.5 w-3.5 text-muted-foreground/40",
                    )}
                  />
                )}
                {/* Pulse ring on current */}
                {isCurrent && (
                  <span className={cn(
                    "absolute inset-0 rounded-full animate-ping opacity-20",
                    p.bg,
                  )} />
                )}
              </div>

              {/* Label */}
              <span
                className={cn(
                  "leading-tight text-center whitespace-nowrap transition-all",
                  isCurrent && `text-xs font-bold ${p.color}`,
                  isPast && "text-[10px] text-muted-foreground font-medium",
                  isFuture && "text-[10px] text-muted-foreground/40",
                )}
              >
                {isCurrent ? label : p.label}
              </span>
            </div>

            {/* Connector line (not after last) */}
            {i < PHASES.length - 1 && (
              <div className="flex-1 mx-1.5 relative">
                {/* Track */}
                <div className="h-[3px] rounded-full bg-muted-foreground/10 dark:bg-muted-foreground/15" />
                {/* Fill */}
                {i < currentIdx && (
                  <div
                    className={cn(
                      "absolute inset-0 h-[3px] rounded-full transition-all duration-500",
                      PHASES[Math.min(i + 1, currentIdx)].line,
                    )}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
