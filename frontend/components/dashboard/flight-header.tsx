import type { FlightListItem } from "@/lib/types";
import { Plane } from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface Props {
  flightStatus: FlightListItem | null;
  flightNumber: string;
}

export function FlightHeader({ flightStatus: fs, flightNumber }: Props) {
  if (!fs) return null;

  const origin = fs.origin ?? "---";
  const sched = fs.schedule;
  const depDate = fs.departureDate ?? "";

  // Compute delay from schedule
  let delayBadge = null;
  if (sched?.scheduledDeparture && sched?.estimatedDeparture) {
    const schedTime = new Date(sched.scheduledDeparture).getTime();
    const estTime = new Date(sched.estimatedDeparture).getTime();
    const diffMin = Math.round((estTime - schedTime) / 60000);
    if (diffMin > 0) {
      delayBadge = `+${diffMin}m`;
    }
  }

  return (
    <div className="flex items-center gap-4 px-6 py-4 border-b border-border">
      {/* Icon */}
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-amber-500/20">
        <Plane className="h-5 w-5 text-amber-600 dark:text-amber-400" />
      </div>

      {/* Info */}
      <div>
        <h1 className="text-xl font-bold tracking-tight">
          {fs.airline}
          {flightNumber}
        </h1>
        <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
          <span className="text-foreground font-semibold">{origin}</span>
          <span>→</span>
          <span className="text-foreground font-semibold">—</span>
          <span className="mx-0.5">·</span>
          <span>DEP: {depDate}</span>
          {delayBadge && (
            <Badge className="ml-1 bg-destructive/90 text-destructive-foreground text-[10px] px-1.5 py-0 inline-flex items-center gap-0.5">
              <Plane className="h-3 w-3" /> {delayBadge}
            </Badge>
          )}
        </div>
      </div>

      {/* Aircraft box */}
      <div className="ml-auto flex items-center gap-2 border border-border rounded-lg px-4 py-2 text-sm bg-card">
        <Plane className="h-4 w-4 text-cyan-600 dark:text-cyan-400" />
        <span className="font-bold">{fs.aircraft?.type}</span>
        {fs.aircraft?.registration && (
          <span className="text-muted-foreground">
            · {fs.aircraft.registration}
          </span>
        )}
      </div>

      {/* Status */}
      <Badge
        variant="outline"
        className="bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
      >
        {fs.status}
      </Badge>

      {/* Gate */}
      {fs.gate && (
        <div className="text-center pl-2">
          <div className="text-[11px] text-muted-foreground uppercase tracking-wide">
            Gate
          </div>
          <div className="text-sm font-bold">{fs.gate}</div>
        </div>
      )}
    </div>
  );
}
