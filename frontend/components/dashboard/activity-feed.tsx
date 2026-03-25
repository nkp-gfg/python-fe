"use client";

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
  DoorOpen,
  Users,
  Award,
  RefreshCw,
  ExternalLink,
  BadgeCheck,
  BarChart3,
  CreditCard,
} from "lucide-react";
import { fetchActivityFeed } from "@/lib/api";
import type { ActivityFeedEvent, FlightEventCategory } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import Link from "next/link";

interface ActivityFeedProps {
  date?: string;
  limit?: number;
  categories?: string;
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
    bgColor: "bg-purple-100 dark:bg-emerald-900/30",
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

function formatTimeAgo(ts: string): string {
  const now = new Date();
  const then = new Date(ts);
  const diffMs = now.getTime() - then.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  
  return then.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function ActivityItem({ event }: { event: ActivityFeedEvent }) {
  const config = categoryConfig[event.category] || categoryConfig.other;
  const Icon = config.icon;

  return (
    <div className="flex gap-3 py-3 border-b last:border-0">
      {/* Icon */}
      <div className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
        config.bgColor,
      )}>
        <Icon className={cn("h-4 w-4", config.color)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <span className="font-medium text-sm line-clamp-1">{event.description}</span>
          <span className="text-xs text-muted-foreground shrink-0">
            {formatTimeAgo(event.timestamp)}
          </span>
        </div>
        
        <div className="flex items-center gap-2 flex-wrap">
          <Link 
            href={`/dashboard/${event.flightNumber}?origin=${event.origin}&date=${event.date}`}
            className="flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <Plane className="h-3 w-3" />
            {event.flightNumber}
            <ExternalLink className="h-2.5 w-2.5" />
          </Link>
          {event.passengerName && (
            <span className="text-xs text-muted-foreground truncate max-w-[150px]">
              {event.passengerName}
            </span>
          )}
          {event.pnr && (
            <Badge variant="outline" className="text-[9px] px-1 font-mono">
              {event.pnr}
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
}

export function ActivityFeed({ 
  date, 
  limit = 50,
  categories,
}: ActivityFeedProps) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["activityFeed", date, limit, categories],
    queryFn: () => fetchActivityFeed(date, limit, categories),
    refetchInterval: 30000, // Auto-refresh every 30 seconds
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          <span className="text-muted-foreground">Loading activity...</span>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <AlertCircle className="mr-2 h-4 w-4 text-destructive" />
          <span className="text-destructive">Failed to load activity</span>
        </CardContent>
      </Card>
    );
  }

  if (!data || data.events.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-12">
          <Clock className="h-8 w-8 mb-2 text-muted-foreground opacity-50" />
          <span className="text-muted-foreground">No recent activity</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Live Activity</CardTitle>
            <CardDescription>
              {data.totalEvents} events across {data.flightsAffected} flights
            </CardDescription>
          </div>
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="max-h-[600px] overflow-y-auto">
        {data.events.map((event, idx) => (
          <ActivityItem 
            key={`${event.timestamp}-${event.flightNumber}-${event.pnr}-${idx}`}
            event={event} 
          />
        ))}
      </CardContent>
    </Card>
  );
}
