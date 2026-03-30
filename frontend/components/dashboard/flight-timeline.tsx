"use client";

import { useMemo, useState } from "react";
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
  Shield,
  CreditCard,
  Award,
  BadgeCheck,
  Filter,
  BarChart3,
  DoorOpen,
  Users,
} from "lucide-react";
import { fetchFlightTimeline } from "@/lib/api";
import type { FlightTimelineEvent, FlightEventCategory, FlightTimelineResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface FlightTimelineProps {
  flightNumber: string;
  origin: string;
  date: string;
}

const categoryConfig: Record<FlightEventCategory, { 
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
  downgrade: { 
    icon: ArrowDown, 
    color: "text-red-600", 
    bgColor: "bg-red-100 dark:bg-red-900/30",
    label: "Downgrade",
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
  security: { 
    icon: Shield, 
    color: "text-red-600", 
    bgColor: "bg-red-100 dark:bg-red-900/30",
    label: "Security",
  },
  gate: { 
    icon: DoorOpen, 
    color: "text-indigo-600", 
    bgColor: "bg-indigo-100 dark:bg-indigo-900/30",
    label: "Gate",
  },
  flight_ops: { 
    icon: Plane, 
    color: "text-slate-600", 
    bgColor: "bg-slate-100 dark:bg-slate-900/30",
    label: "Flight Ops",
  },
  standby: { 
    icon: Users, 
    color: "text-yellow-600", 
    bgColor: "bg-yellow-100 dark:bg-yellow-900/30",
    label: "Standby",
  },
  loyalty: { 
    icon: Award, 
    color: "text-pink-600", 
    bgColor: "bg-pink-100 dark:bg-pink-900/30",
    label: "Loyalty",
  },
  document: { 
    icon: BadgeCheck, 
    color: "text-teal-600", 
    bgColor: "bg-teal-100 dark:bg-teal-900/30",
    label: "Document",
  },
  capacity: { 
    icon: BarChart3, 
    color: "text-violet-600", 
    bgColor: "bg-violet-100 dark:bg-violet-900/30",
    label: "Capacity",
  },
  reservation: { 
    icon: CreditCard, 
    color: "text-lime-600", 
    bgColor: "bg-lime-100 dark:bg-lime-900/30",
    label: "Reservation",
  },
  snapshot: { 
    icon: Clock, 
    color: "text-sky-600", 
    bgColor: "bg-sky-100 dark:bg-sky-900/30",
    label: "Snapshot",
  },
  other: { 
    icon: Clock, 
    color: "text-gray-600", 
    bgColor: "bg-gray-100 dark:bg-gray-900/30",
    label: "Other",
  },
};

function formatTimestamp(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatTimestampFull(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function TimelineEventItem({ event, isLast }: { 
  event: FlightTimelineEvent; 
  isLast: boolean;
}) {
  const config = categoryConfig[event.category] || categoryConfig.other;
  const Icon = config.icon;

  return (
    <div className="relative flex gap-3 pb-4">
      {/* Vertical line */}
      {!isLast && (
        <div className="absolute left-[15px] top-8 bottom-0 w-0.5 bg-border" />
      )}
      
      {/* Icon */}
      <div className={cn(
        "relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-background shadow-sm",
        config.bgColor,
      )}>
        <Icon className={cn("h-3.5 w-3.5", config.color)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-0.5 pt-0.5">
        <div className="flex items-start gap-2">
          <span className="font-medium text-sm truncate flex-1">{event.description}</span>
          <span className="text-xs text-muted-foreground shrink-0">
            {formatTimestamp(event.timestamp)}
          </span>
        </div>
        
        <div className="flex items-center gap-2 text-xs">
          {event.passengerName && (
            <span className="text-muted-foreground truncate">
              {event.passengerName}
            </span>
          )}
          {event.pnr && (
            <Badge variant="outline" className="text-[9px] px-1 font-mono shrink-0">
              {event.pnr}
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
}

function StatsCard({ stats }: { stats: FlightTimelineResponse["stats"] }) {
  if (!stats) return null;
  return (
    <Card className="mb-4">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <BarChart3 className="h-4 w-4" />
          Activity Summary
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-4 gap-3 text-center">
          <div>
            <div className="text-2xl font-bold text-emerald-600">{stats.totalCheckins ?? 0}</div>
            <div className="text-xs text-muted-foreground">Check-ins</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-purple-600">{stats.totalBoardings ?? 0}</div>
            <div className="text-xs text-muted-foreground">Boarded</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-amber-600">{stats.totalUpgrades ?? 0}</div>
            <div className="text-xs text-muted-foreground">Upgrades</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-cyan-600">{stats.totalSeatChanges ?? 0}</div>
            <div className="text-xs text-muted-foreground">Seat Changes</div>
          </div>
        </div>
        <div className="mt-3 pt-3 border-t flex items-center justify-between text-xs text-muted-foreground">
          <span>Total Events: {stats.totalEvents ?? 0}</span>
          {stats.timeRange?.first && stats.timeRange?.last && (
            <span>
              {formatTimestampFull(stats.timeRange.first)} - {formatTimestampFull(stats.timeRange.last)}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function FlightTimeline({ 
  flightNumber, 
  origin, 
  date 
}: FlightTimelineProps) {
  const [selectedCategories, setSelectedCategories] = useState<Set<FlightEventCategory>>(new Set());

  const { data, isLoading, error } = useQuery({
    queryKey: ["flightTimeline", flightNumber, origin, date],
    queryFn: () => fetchFlightTimeline(flightNumber, origin, date),
    enabled: !!flightNumber,
    retry: false,
  });

  // Sort events chronologically (most recent first)
  const sortedEvents = useMemo(() => {
    let filteredEvents = [...(data?.events ?? [])];
    
    // Filter by category if any selected
    if (selectedCategories.size > 0) {
      filteredEvents = filteredEvents.filter((e) => selectedCategories.has(e.category));
    }
    
    return filteredEvents.sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    );
  }, [data?.events, selectedCategories]);

  // Get unique categories from events
  const availableCategories = useMemo(() => {
    const cats = new Set<FlightEventCategory>();
    (data?.events ?? []).forEach((e) => cats.add(e.category));
    return Array.from(cats);
  }, [data?.events]);

  const toggleCategory = (category: FlightEventCategory) => {
    setSelectedCategories(prev => {
      const newSet = new Set(prev);
      if (newSet.has(category)) {
        newSet.delete(category);
      } else {
        newSet.add(category);
      }
      return newSet;
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading timeline...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-destructive">
        <AlertCircle className="mr-2 h-4 w-4" />
        Failed to load timeline
      </div>
    );
  }

  if (!data || data.events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Clock className="h-8 w-8 mb-2 opacity-50" />
        <span>No activity recorded yet</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Stats Card */}
      <StatsCard stats={data.stats} />
      
      {/* Filter */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          {sortedEvents.length} events
          {selectedCategories.size > 0 && ` (filtered)`}
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              <Filter className="h-3 w-3" />
              Filter
              {selectedCategories.size > 0 && (
                <Badge variant="secondary" className="ml-1 text-[10px] px-1">
                  {selectedCategories.size}
                </Badge>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {availableCategories.map(cat => {
              const config = categoryConfig[cat];
              return (
                <DropdownMenuCheckboxItem
                  key={cat}
                  checked={selectedCategories.has(cat)}
                  onCheckedChange={() => toggleCategory(cat)}
                >
                  <span className={cn("mr-2", config.color)}>●</span>
                  {config.label}
                </DropdownMenuCheckboxItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Timeline */}
      <div className="max-h-[500px] overflow-y-auto pr-2">
        {sortedEvents.map((event, idx) => (
          <TimelineEventItem 
            key={`${event.timestamp}-${event.pnr}-${idx}`}
            event={event} 
            isLast={idx === sortedEvents.length - 1}
          />
        ))}
      </div>
    </div>
  );
}
