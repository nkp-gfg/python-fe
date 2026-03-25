"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Loader2, Clock, Plane, DoorOpen, Hash } from "lucide-react";
import { format } from "date-fns";
import { compareSnapshot, fetchStatusHistory, fetchSnapshots, restoreSnapshotVersion } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

interface StatusHistoryProps {
  flightNumber: string;
  origin: string;
  date: string;
  selectedSnapshotSequence?: number | null;
  onLoadSnapshot?: (sequenceNumber: number) => void;
  onClearSnapshot?: () => void;
  onRestoreComplete?: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  Scheduled: "bg-blue-500/15 text-blue-600",
  OnTime: "bg-emerald-500/15 text-emerald-600",
  Boarding: "bg-amber-500/15 text-amber-600",
  Departed: "bg-purple-500/15 text-purple-600",
  Arrived: "bg-emerald-500/15 text-emerald-600",
  Delayed: "bg-red-500/15 text-red-600",
  Cancelled: "bg-red-500/15 text-red-600",
  Diverted: "bg-orange-500/15 text-orange-600",
};

function formatTs(ts: string): string {
  try {
    return format(new Date(ts), "HH:mm:ss · dd MMM");
  } catch {
    return ts;
  }
}

export function StatusHistory({
  flightNumber,
  origin,
  date,
  selectedSnapshotSequence,
  onLoadSnapshot,
  onClearSnapshot,
  onRestoreComplete,
}: StatusHistoryProps) {
  const [compareMode, setCompareMode] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const { data: records, isLoading: loadingHistory } = useQuery({
    queryKey: ["statusHistory", flightNumber, origin, date],
    queryFn: () => fetchStatusHistory(flightNumber, origin, date),
  });

  const { data: snapshots, isLoading: loadingSnaps } = useQuery({
    queryKey: ["snapshots", flightNumber, origin, date],
    queryFn: () => fetchSnapshots(flightNumber, origin, date, "passenger_list"),
  });

  const { data: compareData, isLoading: compareLoading } = useQuery({
    queryKey: ["snapshotCompare", flightNumber, origin, date, selectedSnapshotSequence],
    queryFn: () => compareSnapshot(flightNumber, selectedSnapshotSequence!, origin, date),
    enabled: Boolean(compareMode && selectedSnapshotSequence),
  });

  const isLoading = loadingHistory || loadingSnaps;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading flight history...
      </div>
    );
  }

  const history = records ?? [];
  const snaps = snapshots ?? [];

  async function handleRestore() {
    if (!selectedSnapshotSequence || restoring) return;
    const ok = window.confirm(
      `Restore snapshot #${selectedSnapshotSequence} as latest data for this flight? This writes new latest documents.`
    );
    if (!ok) return;

    try {
      setRestoring(true);
      await restoreSnapshotVersion(flightNumber, selectedSnapshotSequence, origin, date);
      onRestoreComplete?.();
    } finally {
      setRestoring(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Status Timeline */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Plane className="h-4 w-4 text-muted-foreground" />
            Flight Status Evolution
            <Badge variant="secondary" className="ml-auto text-[10px]">
              {history.length} record{history.length !== 1 ? "s" : ""}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-6">No status records found.</p>
          ) : (
            <div className="relative space-y-0">
              <div className="absolute left-5 top-2 bottom-2 w-px bg-border" />
              {history.map((rec, i) => {
                const colorClass = STATUS_COLORS[rec.status] ?? "bg-muted text-muted-foreground";
                return (
                  <div key={i} className="relative flex gap-4 py-3 pl-1">
                    <div className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border bg-background">
                      <Clock className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge className={cn("text-[10px] border-transparent", colorClass)}>
                          {rec.status}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatTs(rec.fetchedAt)}</span>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        {rec.gate && (
                          <span className="flex items-center gap-1">
                            <DoorOpen className="h-3 w-3" />
                            Gate {rec.gate}
                          </span>
                        )}
                        {rec.terminal && <span>Terminal {rec.terminal}</span>}
                        {rec.aircraft?.type && <span>Aircraft {rec.aircraft.type} ({rec.aircraft.registration})</span>}
                        {rec.boarding?.time && <span>Boarding {rec.boarding.time}</span>}
                      </div>
                      {rec.schedule && (
                        <div className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                          {rec.schedule.scheduledDeparture && <span>STD: {rec.schedule.scheduledDeparture}</span>}
                          {rec.schedule.estimatedDeparture && <span>ETD: {rec.schedule.estimatedDeparture}</span>}
                          {rec.schedule.scheduledArrival && <span>STA: {rec.schedule.scheduledArrival}</span>}
                          {rec.schedule.estimatedArrival && <span>ETA: {rec.schedule.estimatedArrival}</span>}
                        </div>
                      )}
                      {rec.passengerCounts && Object.keys(rec.passengerCounts).length > 0 && (
                        <div className="flex flex-wrap gap-2 mt-1">
                          {Object.entries(rec.passengerCounts).map(([cabin, counts]) => (
                            <Badge key={cabin} variant="outline" className="text-[9px] px-1.5">
                              {cabin}: {counts.booked}bk {counts.onBoard}bd
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Separator />

      {/* Snapshots */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Hash className="h-4 w-4 text-muted-foreground" />
            Data Snapshots
            {selectedSnapshotSequence && (
              <Badge variant="outline" className="text-[10px]">
                Viewing #{selectedSnapshotSequence}
              </Badge>
            )}
            {selectedSnapshotSequence && onClearSnapshot && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-[10px]"
                onClick={onClearSnapshot}
              >
                Back to latest
              </Button>
            )}
            {selectedSnapshotSequence && (
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[10px]"
                onClick={() => setCompareMode((v) => !v)}
              >
                {compareMode ? "Hide Compare" : "Compare vs latest"}
              </Button>
            )}
            {selectedSnapshotSequence && (
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[10px]"
                disabled={restoring}
                onClick={handleRestore}
              >
                {restoring ? "Restoring..." : "Restore this version"}
              </Button>
            )}
            <Badge variant="secondary" className="ml-auto text-[10px]">
              {snaps.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {compareMode && selectedSnapshotSequence && (
            <div className="mb-4 rounded-md border p-3 space-y-2">
              <div className="text-xs font-medium">Delta View: selected snapshot vs latest</div>
              {compareLoading && (
                <div className="text-xs text-muted-foreground flex items-center gap-2">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Calculating deltas...
                </div>
              )}
              {!compareLoading && compareData && (
                <div className="space-y-2">
                  {Object.entries(compareData.types).map(([type, value]) => (
                    <div key={type} className="rounded border p-2">
                      <div className="text-xs font-medium mb-1">{type}</div>
                      {!value.available && (
                        <div className="text-[11px] text-muted-foreground">No comparable snapshots.</div>
                      )}
                      {value.available && value.deltas && (
                        <div className="grid gap-1">
                          {Object.entries(value.deltas)
                            .filter(([, d]) => d.changed)
                            .map(([field, d]) => (
                              <div key={field} className="text-[11px] flex items-center justify-between gap-3">
                                <span className="text-muted-foreground">{field}</span>
                                <span>
                                  {String(d.selected)} <span className="text-muted-foreground">→</span> {String(d.latest)}
                                  {typeof d.diff === "number" && (
                                    <span className="ml-1 text-muted-foreground">({d.diff > 0 ? `+${d.diff}` : d.diff})</span>
                                  )}
                                </span>
                              </div>
                            ))}
                          {Object.values(value.deltas).every((d) => !d.changed) && (
                            <div className="text-[11px] text-muted-foreground">No changes.</div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {snaps.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-6">No snapshots recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/30">
                  <TableHead>#</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Fetched At</TableHead>
                  <TableHead>Checksum</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {snaps.map((s, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs">{s.sequenceNumber}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-[10px]">{s.snapshotType}</Badge>
                    </TableCell>
                    <TableCell className="text-xs">{formatTs(s.fetchedAt)}</TableCell>
                    <TableCell className="font-mono text-[10px] text-muted-foreground truncate max-w-[120px]">
                      {s.checksum ?? "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant={selectedSnapshotSequence === s.sequenceNumber ? "secondary" : "outline"}
                        className="h-7 text-[10px]"
                        onClick={() => onLoadSnapshot?.(s.sequenceNumber)}
                      >
                        {selectedSnapshotSequence === s.sequenceNumber ? "Loaded" : "Load"}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
