"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  AlertCircle,
  Clock,
  Users,
  LogIn,
  Plane,
  TrendingUp,
} from "lucide-react";
import { fetchBoardingProgress } from "@/lib/api";
import type { BoardingProgressResponse, ProgressDataPoint, FlightMilestone } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

interface BoardingProgressProps {
  flightNumber: string;
  origin: string;
  date: string;
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  return date.toLocaleString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function MilestoneItem({ milestone }: { milestone: FlightMilestone }) {
  return (
    <div className="flex items-center gap-2">
      <div className={cn(
        "h-2 w-2 rounded-full",
        milestone.type === "departure" ? "bg-purple-500" :
        milestone.type === "boarding_start" ? "bg-indigo-500" :
        milestone.type === "boarding_complete" ? "bg-emerald-500" :
        "bg-slate-500"
      )} />
      <span className="text-xs text-muted-foreground">{milestone.label}</span>
      <span className="text-xs font-mono">{formatTime(milestone.timestamp)}</span>
    </div>
  );
}

function ProgressChart({ 
  checkinSeries, 
  boardingSeries,
  totalPassengers,
}: { 
  checkinSeries: ProgressDataPoint[];
  boardingSeries: ProgressDataPoint[];
  totalPassengers: number;
}) {
  // Simple timeline visualization
  const checkinPoints = checkinSeries.length;
  const boardingPoints = boardingSeries.length;

  if (checkinPoints === 0 && boardingPoints === 0) {
    return (
      <div className="h-32 flex items-center justify-center text-muted-foreground text-sm">
        No progress data available
      </div>
    );
  }

  // Calculate current counts
  const currentCheckins = checkinSeries[checkinSeries.length - 1]?.cumulativeCount || 0;
  const currentBoarded = boardingSeries[boardingSeries.length - 1]?.cumulativeCount || 0;

  const checkinPct = totalPassengers > 0 ? (currentCheckins / totalPassengers) * 100 : 0;
  const boardingPct = totalPassengers > 0 ? (currentBoarded / totalPassengers) * 100 : 0;

  return (
    <div className="space-y-4">
      {/* Check-in progress */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <LogIn className="h-4 w-4 text-emerald-600" />
            <span>Checked In</span>
          </div>
          <span className="font-mono font-medium">
            {currentCheckins} / {totalPassengers}
          </span>
        </div>
        <Progress value={checkinPct} className="h-2" />
        {checkinSeries.length > 0 && (
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Started: {formatTime(checkinSeries[0].timestamp)}</span>
            {checkinSeries.length > 1 && (
              <span>Latest: {formatTime(checkinSeries[checkinSeries.length - 1].timestamp)}</span>
            )}
          </div>
        )}
      </div>

      {/* Boarding progress */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <Plane className="h-4 w-4 text-purple-600" />
            <span>Boarded</span>
          </div>
          <span className="font-mono font-medium">
            {currentBoarded} / {totalPassengers}
          </span>
        </div>
        <Progress value={boardingPct} className="h-2 [&>div]:bg-purple-600" />
        {boardingSeries.length > 0 && (
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Started: {formatTime(boardingSeries[0].timestamp)}</span>
            {boardingSeries.length > 1 && (
              <span>Latest: {formatTime(boardingSeries[boardingSeries.length - 1].timestamp)}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function QuickStats({ data }: { data: BoardingProgressResponse }) {
  const checkins = data.checkinProgress?.data || [];
  const boardings = data.boardingProgress?.data || [];
  
  const totalCheckedIn = checkins.length > 0 ? (checkins[checkins.length - 1]?.cumulativeCount || 0) : 0;
  const totalBoarded = boardings.length > 0 ? (boardings[boardings.length - 1]?.cumulativeCount || 0) : 0;
  const notCheckedIn = data.totalPassengers - totalCheckedIn;

  return (
    <div className="grid grid-cols-4 gap-2 mb-4">
      <div className="text-center p-2 rounded-md bg-muted/50">
        <div className="text-xl font-bold">{data.totalPassengers}</div>
        <div className="text-[10px] text-muted-foreground uppercase">Total</div>
      </div>
      <div className="text-center p-2 rounded-md bg-emerald-50 dark:bg-emerald-900/20">
        <div className="text-xl font-bold text-emerald-600">{totalCheckedIn}</div>
        <div className="text-[10px] text-muted-foreground uppercase">Checked In</div>
      </div>
      <div className="text-center p-2 rounded-md bg-purple-50 dark:bg-purple-900/20">
        <div className="text-xl font-bold text-purple-600">{totalBoarded}</div>
        <div className="text-[10px] text-muted-foreground uppercase">Boarded</div>
      </div>
      <div className="text-center p-2 rounded-md bg-amber-50 dark:bg-amber-900/20">
        <div className="text-xl font-bold text-amber-600">{notCheckedIn}</div>
        <div className="text-[10px] text-muted-foreground uppercase">Remaining</div>
      </div>
    </div>
  );
}

export function BoardingProgress({ 
  flightNumber, 
  origin, 
  date 
}: BoardingProgressProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["boardingProgress", flightNumber, origin, date],
    queryFn: () => fetchBoardingProgress(flightNumber, origin, date),
    enabled: !!flightNumber,
    refetchInterval: 15000, // Auto-refresh every 15 seconds during active boarding
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          <span className="text-muted-foreground">Loading progress...</span>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <AlertCircle className="mr-2 h-4 w-4 text-destructive" />
          <span className="text-destructive">Failed to load progress</span>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-12">
          <Clock className="h-8 w-8 mb-2 text-muted-foreground opacity-50" />
          <span className="text-muted-foreground">No data available</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <TrendingUp className="h-4 w-4" />
              Boarding Progress
            </CardTitle>
            <CardDescription>
              Check-in and boarding rates for {flightNumber}
            </CardDescription>
          </div>
          {data.totalPassengers > 0 && (
            <Badge variant="outline" className="font-mono">
              <Users className="h-3 w-3 mr-1" />
              {data.totalPassengers} PAX
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {/* Quick Stats */}
        <QuickStats data={data} />

        {/* Progress Chart */}
        <ProgressChart 
          checkinSeries={data.checkinProgress.data}
          boardingSeries={data.boardingProgress.data}
          totalPassengers={data.totalPassengers}
        />

        {/* Milestones */}
        {data.milestones.length > 0 && (
          <div className="mt-4 pt-4 border-t space-y-2">
            <span className="text-xs font-medium text-muted-foreground uppercase">Milestones</span>
            {data.milestones.map((m) => (
              <MilestoneItem 
                key={`${m.type}-${m.timestamp}`}
                milestone={m}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
