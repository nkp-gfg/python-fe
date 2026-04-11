import { cn } from "@/lib/utils";

/** Shared flight selection shape used across workbench sub-components */
export type FlightSelection = {
  flightNumber: string;
  origin: string;
  date: string;
};

/** Tab keys used in the vertical navigation rail and tab content */
export type WorkbenchTab =
  | "overview"
  | "commercial"
  | "readiness"
  | "exceptions"
  | "passengers"
  | "groups"
  | "standby"
  | "changes"
  | "history"
  | "reservations"
  | "activity"
  | "audit"
  | "journey";

/** Map flight status / phase codes to tailwind badge colour classes */
export function getStatusColor(status: string) {
  switch (status.toUpperCase()) {
    case "PDC":
    case "DEPARTED":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
    case "OPENCI":
    case "CHECK_IN":
      return "bg-blue-500/15 text-blue-600 dark:text-blue-400";
    case "FINAL":
    case "CLOSED":
      return "bg-red-500/15 text-red-600 dark:text-red-400";
    case "BOARDING":
      return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
    case "SCHEDULED":
      return "bg-slate-500/15 text-slate-600 dark:text-slate-400";
    default:
      return "bg-secondary text-secondary-foreground";
  }
}

/** Compact schedule / delay indicator for the flight header KPI strip */
export function ScheduleDelay({ schedule }: { schedule?: { scheduledDeparture?: string; estimatedDeparture?: string; scheduledArrival?: string; estimatedArrival?: string } }) {
  if (!schedule?.scheduledDeparture) return <span>STD {"\u2014"}</span>;

  const std = schedule.scheduledDeparture;
  const etd = schedule.estimatedDeparture;

  const stdTime = std.slice(-8, -3);

  if (!etd || etd === std) {
    return <span>STD {stdTime}</span>;
  }

  const parseMinutes = (t: string) => {
    const hhmm = t.slice(-8, -3);
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + m;
  };
  const delayMin = parseMinutes(etd) - parseMinutes(std);

  if (delayMin === 0) return <span>STD {stdTime}</span>;

  const etdTime = etd.slice(-8, -3);
  const sign = delayMin > 0 ? "+" : "";

  return (
    <span className="flex items-center gap-1">
      <span>STD {stdTime}</span>
      <span className={cn(
        "text-[10px] font-semibold px-1 rounded",
        delayMin > 0 ? "text-rose-500 bg-rose-50 dark:bg-rose-950/40" : "text-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
      )}>
        ETD {etdTime} ({sign}{delayMin}m)
      </span>
    </span>
  );
}
