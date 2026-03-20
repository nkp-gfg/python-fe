"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  ArrowRightLeft,
  UserPlus,
  UserMinus,
  RefreshCw,
  Clock,
} from "lucide-react";
import { format } from "date-fns";
import { fetchChanges, fetchChangeSummary } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface ChangeTimelineProps {
  flightNumber: string;
  origin: string;
  date: string;
}

const CHANGE_ICONS: Record<string, typeof ArrowRightLeft> = {
  seat_change: ArrowRightLeft,
  passenger_added: UserPlus,
  passenger_removed: UserMinus,
  status_change: RefreshCw,
};

const CHANGE_COLORS: Record<string, string> = {
  seat_change: "bg-blue-500/15 text-blue-600 border-transparent",
  passenger_added: "bg-emerald-500/15 text-emerald-600 border-transparent",
  passenger_removed: "bg-red-500/15 text-red-600 border-transparent",
  status_change: "bg-amber-500/15 text-amber-600 border-transparent",
  check_in: "bg-purple-500/15 text-purple-600 border-transparent",
  boarding: "bg-indigo-500/15 text-indigo-600 border-transparent",
  bag_count_change: "bg-orange-500/15 text-orange-600 border-transparent",
};

function formatChangeType(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
}

function formatTimestamp(ts: string): string {
  try {
    return format(new Date(ts), "HH:mm:ss · dd MMM");
  } catch {
    return ts;
  }
}

export function ChangeTimeline({ flightNumber, origin, date }: ChangeTimelineProps) {
  const [typeFilter, setTypeFilter] = useState<string>("all");

  const { data: changes, isLoading } = useQuery({
    queryKey: ["changes", flightNumber, origin, date, typeFilter === "all" ? undefined : typeFilter],
    queryFn: () =>
      fetchChanges(
        flightNumber,
        origin,
        date,
        typeFilter === "all" ? undefined : typeFilter
      ),
  });

  const { data: summary } = useQuery({
    queryKey: ["changeSummary", flightNumber, origin, date],
    queryFn: () => fetchChangeSummary(flightNumber, origin, date),
  });

  const changeTypes = useMemo(() => {
    if (!summary?.changeTypes) return [];
    return Object.entries(summary.changeTypes)
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => ({ type, count }));
  }, [summary]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading changes...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary Chips */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setTypeFilter("all")}
          className={cn(
            "rounded-full px-3 py-1 text-xs border transition-colors",
            typeFilter === "all"
              ? "bg-primary text-primary-foreground border-primary"
              : "bg-muted/50 text-muted-foreground border-transparent hover:border-muted-foreground/20"
          )}
        >
          All {summary?.totalChanges ?? 0}
        </button>
        {changeTypes.map(({ type, count }) => (
          <button
            key={type}
            onClick={() => setTypeFilter(type === typeFilter ? "all" : type)}
            className={cn(
              "rounded-full px-3 py-1 text-xs border transition-colors",
              typeFilter === type
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-muted/50 text-muted-foreground border-transparent hover:border-muted-foreground/20"
            )}
          >
            {formatChangeType(type)} {count}
          </button>
        ))}
      </div>

      {/* Timeline */}
      {(!changes || changes.length === 0) ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          No changes recorded yet.
        </div>
      ) : (
        <div className="relative space-y-0">
          {/* Vertical line */}
          <div className="absolute left-5 top-2 bottom-2 w-px bg-border" />

          {changes.map((c, i) => {
            const Icon = CHANGE_ICONS[c.changeType] ?? RefreshCw;
            const colorClass = CHANGE_COLORS[c.changeType] ?? "bg-muted text-muted-foreground border-transparent";
            return (
              <div key={i} className="relative flex gap-4 py-3 pl-1">
                {/* Dot */}
                <div className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border bg-background">
                  <Icon className="h-4 w-4 text-muted-foreground" />
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge className={cn("text-[10px]", colorClass)}>
                      {formatChangeType(c.changeType)}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      <Clock className="inline h-3 w-3 mr-0.5" />
                      {formatTimestamp(c.detectedAt)}
                    </span>
                    {c.sequenceNumber != null && (
                      <span className="text-[10px] text-muted-foreground">#{c.sequenceNumber}</span>
                    )}
                  </div>

                  {c.passenger && (
                    <p className="text-sm mt-1">
                      <span className="font-medium">{c.passenger.lastName}, {c.passenger.firstName}</span>
                      <span className="text-muted-foreground ml-1.5 font-mono text-xs">{c.passenger.pnr}</span>
                    </p>
                  )}

                  {c.field && (
                    <div className="flex items-center gap-2 mt-1 text-xs">
                      <span className="text-muted-foreground">{c.field}:</span>
                      {c.oldValue != null && (
                        <Badge variant="outline" className="text-[10px] px-1 line-through opacity-60">
                          {String(c.oldValue)}
                        </Badge>
                      )}
                      {c.oldValue != null && c.newValue != null && (
                        <span className="text-muted-foreground">→</span>
                      )}
                      {c.newValue != null && (
                        <Badge variant="outline" className="text-[10px] px-1 border-emerald-300 text-emerald-600">
                          {String(c.newValue)}
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
