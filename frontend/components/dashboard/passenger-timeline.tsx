"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  UserPlus,
  LogIn,
  Plane,
  ArrowUp,
  ArrowDown,
  Armchair,
  Briefcase,
  Clock,
  AlertCircle,
} from "lucide-react";
import { fetchPassengerTimeline } from "@/lib/api";
import type { TimelineEvent, TimelineEventCategory, PassengerTimelineResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface PassengerTimelineProps {
  flightNumber: string;
  pnr: string;
  origin: string;
  date: string;
}

const categoryConfig: Record<TimelineEventCategory, { 
  icon: typeof UserPlus; 
  color: string; 
  bgColor: string;
  label: string;
}> = {
  booking: { 
    icon: UserPlus, 
    color: "text-blue-600", 
    bgColor: "bg-blue-100 dark:bg-blue-900/30",
    label: "Booking",
  },
  checkin: { 
    icon: LogIn, 
    color: "text-emerald-600", 
    bgColor: "bg-emerald-100 dark:bg-emerald-900/30",
    label: "Check-In",
  },
  boarding: { 
    icon: Plane, 
    color: "text-purple-600", 
    bgColor: "bg-purple-100 dark:bg-purple-900/30",
    label: "Boarding",
  },
  upgrade: { 
    icon: ArrowUp, 
    color: "text-amber-600", 
    bgColor: "bg-amber-100 dark:bg-amber-900/30",
    label: "Upgrade",
  },
  seat: { 
    icon: Armchair, 
    color: "text-cyan-600", 
    bgColor: "bg-cyan-100 dark:bg-cyan-900/30",
    label: "Seat",
  },
  baggage: { 
    icon: Briefcase, 
    color: "text-orange-600", 
    bgColor: "bg-orange-100 dark:bg-orange-900/30",
    label: "Baggage",
  },
  other: { 
    icon: Clock, 
    color: "text-gray-600", 
    bgColor: "bg-gray-100 dark:bg-gray-900/30",
    label: "Other",
  },
};

const upgradeTypeBadge: Record<string, { variant: "default" | "secondary" | "outline"; label: string }> = {
  LMU: { variant: "default", label: "Last Minute Upgrade" },
  PAID: { variant: "secondary", label: "Paid Upgrade" },
  COMPLIMENTARY: { variant: "outline", label: "Complimentary" },
  OPERATIONAL: { variant: "outline", label: "Operational" },
};

function formatTimestamp(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function TimelineEventItem({ event, isFirst, isLast }: { 
  event: TimelineEvent; 
  isFirst: boolean;
  isLast: boolean;
}) {
  const config = categoryConfig[event.category] || categoryConfig.other;
  const Icon = event.category === "upgrade" && event.upgradeInfo?.direction === "DOWNGRADE" 
    ? ArrowDown 
    : config.icon;

  return (
    <div className="relative flex gap-4 pb-6">
      {/* Vertical line */}
      {!isLast && (
        <div className="absolute left-[19px] top-10 bottom-0 w-0.5 bg-border" />
      )}
      
      {/* Icon */}
      <div className={cn(
        "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 border-background shadow-sm",
        config.bgColor,
      )}>
        <Icon className={cn("h-4 w-4", config.color)} />
      </div>

      {/* Content */}
      <div className="flex-1 space-y-1.5 pt-0.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{event.description}</span>
          {event.upgradeInfo?.upgradeType && (
            <Badge 
              variant={upgradeTypeBadge[event.upgradeInfo.upgradeType]?.variant || "outline"}
              className="text-[10px] px-1.5"
            >
              {upgradeTypeBadge[event.upgradeInfo.upgradeType]?.label || event.upgradeInfo.upgradeType}
            </Badge>
          )}
        </div>
        
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {formatTimestamp(event.timestamp)}
          </span>
          <Badge variant="outline" className="text-[9px] px-1">
            {config.label}
          </Badge>
        </div>

        {/* Original booking info for first event */}
        {isFirst && event.originalBooking?.cabin && (
          <div className="mt-2 p-2 rounded-md bg-muted/50 text-xs">
            <span className="text-muted-foreground">Original booking: </span>
            <span className="font-medium">
              Cabin {event.originalBooking.cabin} / Class {event.originalBooking.bookingClass}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function CurrentStateCard({ data }: { data: PassengerTimelineResponse }) {
  const { currentState, originalBooking } = data;
  
  if (!currentState) return null;

  const hasUpgrade = originalBooking.cabin && currentState.cabin && 
    originalBooking.cabin !== currentState.cabin;
  
  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          Current Status
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <span className="text-xs text-muted-foreground">Cabin</span>
            <div className="flex items-center gap-2">
              <span className="text-lg font-semibold">{currentState.cabin || "—"}</span>
              {hasUpgrade && (
                <Badge variant="secondary" className="text-[9px]">
                  <ArrowUp className="h-2.5 w-2.5 mr-0.5" />
                  from {originalBooking.cabin}
                </Badge>
              )}
            </div>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Class</span>
            <div className="text-lg font-semibold">{currentState.bookingClass || "—"}</div>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Seat</span>
            <div className="text-lg font-semibold font-mono">{currentState.seat || "—"}</div>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Status</span>
            <div className="flex items-center gap-1.5 mt-1">
              {currentState.isBoarded ? (
                <Badge className="bg-purple-600 text-[10px]">Boarded</Badge>
              ) : currentState.isCheckedIn ? (
                <Badge className="bg-emerald-600 text-[10px]">Checked In</Badge>
              ) : (
                <Badge variant="outline" className="text-[10px]">Not Checked In</Badge>
              )}
            </div>
          </div>
        </div>
        {currentState.bagCount > 0 && (
          <div className="mt-3 pt-3 border-t text-xs text-muted-foreground">
            <Briefcase className="h-3 w-3 inline mr-1" />
            {currentState.bagCount} bag{currentState.bagCount > 1 ? "s" : ""} checked
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function PassengerTimeline({ 
  flightNumber, 
  pnr, 
  origin, 
  date 
}: PassengerTimelineProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["passengerTimeline", flightNumber, pnr, origin, date],
    queryFn: () => fetchPassengerTimeline(flightNumber, pnr, origin, date),
    enabled: !!pnr && !!flightNumber,
    retry: false, // Don't retry on 404
  });

  // Sort events chronologically (oldest first)
  const events = data?.events;
  const sortedEvents = useMemo(() => {
    if (!events) return [];
    return [...events].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );
  }, [events]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading timeline...
      </div>
    );
  }

  if (error) {
    const is404 = error instanceof Error && error.message.includes("404");
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <AlertCircle className="h-8 w-8 mb-2 text-muted-foreground/50" />
        <p className="text-sm">
          {is404 
            ? "No history available for this passenger yet."
            : "Failed to load timeline."}
        </p>
        <p className="text-xs mt-1 text-muted-foreground/70">
          History is recorded when data changes between snapshots.
        </p>
      </div>
    );
  }

  if (!data || sortedEvents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Clock className="h-8 w-8 mb-2 text-muted-foreground/50" />
        <p className="text-sm">No events recorded yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <CurrentStateCard data={data} />
      
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center justify-between">
            <span>Event History</span>
            <Badge variant="secondary" className="text-[10px]">
              {data.eventCount} event{data.eventCount > 1 ? "s" : ""}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="pt-2">
            {sortedEvents.map((event, index) => (
              <TimelineEventItem
                key={`${event.changeType}-${event.timestamp}-${index}`}
                event={event}
                isFirst={index === 0}
                isLast={index === sortedEvents.length - 1}
              />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
