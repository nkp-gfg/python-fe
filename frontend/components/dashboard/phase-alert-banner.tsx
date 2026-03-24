"use client";

import type { FlightPhase } from "@/lib/types";
import { CalendarDays, UserCheck, ScanLine, DoorClosed, PlaneTakeoff, AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  flightPhase: FlightPhase;
}

const ICON_MAP: Record<string, React.ElementType> = {
  "calendar": CalendarDays,
  "user-check": UserCheck,
  "scan-line": ScanLine,
  "door-closed": DoorClosed,
  "plane-departure": PlaneTakeoff,
};

const COLOR_MAP: Record<string, { bg: string; border: string; text: string; icon: string }> = {
  slate: {
    bg: "bg-slate-50 dark:bg-slate-950/40",
    border: "border-slate-200 dark:border-slate-800",
    text: "text-slate-700 dark:text-slate-300",
    icon: "text-slate-500",
  },
  blue: {
    bg: "bg-blue-50 dark:bg-blue-950/40",
    border: "border-blue-200 dark:border-blue-800",
    text: "text-blue-700 dark:text-blue-300",
    icon: "text-blue-500",
  },
  amber: {
    bg: "bg-amber-50 dark:bg-amber-950/40",
    border: "border-amber-200 dark:border-amber-800",
    text: "text-amber-700 dark:text-amber-300",
    icon: "text-amber-500",
  },
  red: {
    bg: "bg-red-50 dark:bg-red-950/40",
    border: "border-red-200 dark:border-red-800",
    text: "text-red-700 dark:text-red-300",
    icon: "text-red-500",
  },
  green: {
    bg: "bg-emerald-50 dark:bg-emerald-950/40",
    border: "border-emerald-200 dark:border-emerald-800",
    text: "text-emerald-700 dark:text-emerald-300",
    icon: "text-emerald-500",
  },
  gray: {
    bg: "bg-gray-50 dark:bg-gray-950/40",
    border: "border-gray-200 dark:border-gray-800",
    text: "text-gray-700 dark:text-gray-300",
    icon: "text-gray-500",
  },
};

export function PhaseAlertBanner({ flightPhase }: Props) {
  const colors = COLOR_MAP[flightPhase.alertColor] ?? COLOR_MAP.slate;
  const Icon = ICON_MAP[flightPhase.alertIcon] ?? CalendarDays;

  // Show alert triangle for red, check for green, phase icon otherwise
  let StatusIcon = Icon;
  if (flightPhase.alertColor === "red") StatusIcon = AlertTriangle;
  else if (flightPhase.alertColor === "green") StatusIcon = CheckCircle2;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs",
        colors.bg,
        colors.border,
      )}
    >
      <StatusIcon className={cn("h-3.5 w-3.5 shrink-0", colors.icon)} />
      <span className={cn("font-medium", colors.text)}>
        {flightPhase.label}
      </span>
      <span className="text-muted-foreground">—</span>
      <span className={cn("flex-1", colors.text)}>
        {flightPhase.description}
      </span>
    </div>
  );
}
