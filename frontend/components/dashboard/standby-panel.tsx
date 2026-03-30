"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Users, ArrowUpCircle, Clock } from "lucide-react";
import { fetchStandbyList } from "@/lib/api";
import type { StandbyEntry, CabinAvailability } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface StandbyPanelProps {
  flightNumber: string;
  origin: string;
  date: string;
  snapshotSequence?: number | null;
}

function AvailabilityBar({ cabin }: { cabin: CabinAvailability }) {
  const total = cabin.authorized;
  const used = total - cabin.available;
  const pct = total > 0 ? Math.round((used / total) * 100) : 0;
  const avail = cabin.available;
  return (
    <div className="flex items-center gap-3">
      <span className="w-8 text-xs font-medium">{cabin.cabin}</span>
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            pct > 90 ? "bg-red-500" : pct > 70 ? "bg-amber-500" : "bg-emerald-500"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-muted-foreground w-20 text-right">
        {used}/{total} ({avail} avail)
      </span>
    </div>
  );
}

function QueueTable({
  entries,
  type,
}: {
  entries: StandbyEntry[];
  type: "upgrade" | "standby";
}) {
  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground text-center py-4">
        No {type} requests.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="bg-muted/30">
          <TableHead className="w-10">#</TableHead>
          <TableHead>Passenger</TableHead>
          <TableHead>PNR</TableHead>
          {type === "upgrade" && <TableHead>Priority</TableHead>}
          <TableHead>Class</TableHead>
          <TableHead>Current</TableHead>
          <TableHead>Requested</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Seniority</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map((e, i) => (
          <TableRow key={`${e.pnr}-${e.lastName}-${i}`}>
            <TableCell className="font-mono text-xs text-muted-foreground">
              {e.lineNumber ?? i + 1}
            </TableCell>
            <TableCell className="font-medium text-sm">
              {e.lastName}, {e.firstName}
            </TableCell>
            <TableCell className="font-mono text-xs">{e.pnr}</TableCell>
            {type === "upgrade" && (
              <TableCell>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px]",
                    e.priorityCode === "1"
                      ? "border-emerald-400 text-emerald-600"
                      : "border-muted-foreground"
                  )}
                >
                  P{e.priorityCode}
                </Badge>
              </TableCell>
            )}
            <TableCell className="text-xs">{e.bookingClass || "—"}</TableCell>
            <TableCell>
              <Badge variant="outline" className="text-[10px]">{e.cabin}</Badge>
            </TableCell>
            <TableCell>
              <Badge className="bg-amber-500/15 text-amber-600 border-transparent text-[10px]">
                {e.desiredBookingClass}
              </Badge>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-1">
                {e.isCheckedIn ? (
                  <Badge className="bg-blue-500/15 text-blue-600 border-transparent text-[10px]">CI</Badge>
                ) : (
                  <span className="text-[10px] text-muted-foreground">—</span>
                )}
                {e.boardingPassIssued && (
                  <span className="text-[9px] text-emerald-500" title="Boarding pass issued">BP</span>
                )}
              </div>
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {e.seniorityDate || "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function StandbyPanel({ flightNumber, origin, date, snapshotSequence }: StandbyPanelProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["standby", flightNumber, origin, date, snapshotSequence],
    queryFn: () => fetchStandbyList(flightNumber, origin, date, snapshotSequence),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading standby list...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/20 bg-destructive/10 p-6 text-center text-sm text-destructive">
        Failed to load standby list.
      </div>
    );
  }

  const upgrades = data?.upgrade?.passengers ?? [];
  const standbys = data?.standby?.passengers ?? [];
  const cabins = data?.cabinAvailability ?? [];

  return (
    <div className="space-y-6">
      {/* Cabin Availability */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            Cabin Availability
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {cabins.length === 0 ? (
            <p className="text-xs text-muted-foreground">No cabin data available.</p>
          ) : (
            cabins.map((c) => <AvailabilityBar key={c.cabin} cabin={c} />)
          )}
        </CardContent>
      </Card>

      {/* Upgrade Queue */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <ArrowUpCircle className="h-4 w-4 text-amber-500" />
            Upgrade Queue
            <Badge variant="secondary" className="ml-auto text-[10px]">
              {upgrades.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <QueueTable entries={upgrades} type="upgrade" />
        </CardContent>
      </Card>

      <Separator />

      {/* Standby Queue */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Clock className="h-4 w-4 text-blue-500" />
            Standby Queue
            <Badge variant="secondary" className="ml-auto text-[10px]">
              {standbys.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <QueueTable entries={standbys} type="standby" />
        </CardContent>
      </Card>
    </div>
  );
}
