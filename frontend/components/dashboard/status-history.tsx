"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  Loader2,
  Clock,
  Plane,
  DoorOpen,
  Hash,
  Users,
  ArrowRight,
  Timer,
  MessageSquare,
  TowerControl,
  PlaneTakeoff,
  PlaneLanding,
} from "lucide-react";
import { format } from "date-fns";
import { compareSnapshot, fetchStatusHistory, fetchSnapshots, restoreSnapshotVersion } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HistoryOverview } from "@/components/dashboard/history-overview";
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
import type { FlightStatusRecord, ClassCounts, SnapshotMeta } from "@/lib/types";

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
  Scheduled: "bg-blue-500/15 text-blue-600 border-blue-300",
  OnTime: "bg-emerald-500/15 text-emerald-600 border-emerald-300",
  Boarding: "bg-amber-500/15 text-amber-600 border-amber-300",
  Departed: "bg-purple-500/15 text-purple-600 border-purple-300",
  Arrived: "bg-emerald-500/15 text-emerald-600 border-emerald-300",
  Delayed: "bg-red-500/15 text-red-600 border-red-300",
  Cancelled: "bg-red-500/15 text-red-600 border-red-300",
  Diverted: "bg-orange-500/15 text-orange-600 border-orange-300",
};

const STATUS_DOT_COLORS: Record<string, string> = {
  Scheduled: "bg-blue-500",
  OnTime: "bg-emerald-500",
  Boarding: "bg-amber-500",
  Departed: "bg-purple-500",
  Arrived: "bg-emerald-500",
  Delayed: "bg-red-500",
  Cancelled: "bg-red-500",
  Diverted: "bg-orange-500",
};

/* ---------- helpers ---------- */

function formatTs(ts: string): string {
  try {
    return format(new Date(ts), "HH:mm:ss · dd MMM");
  } catch {
    return ts;
  }
}

/** Extract time portion (HH:mm) from "YYYY-MM-DDTHH:mm" or "HH:mm:ss" */
function fmtTime(v: string | undefined): string {
  if (!v) return "—";
  // Full ISO with T
  const tIdx = v.indexOf("T");
  if (tIdx >= 0) {
    const timePart = v.slice(tIdx + 1); // "HH:mm:ss" or "HH:mm"
    return timePart ? timePart.slice(0, 5) : "—";
  }
  // Already a time string
  if (v.includes(":")) return v.slice(0, 5);
  return v || "—";
}

function totalPax(counts: Record<string, ClassCounts> | undefined): { booked: number; onBoard: number; bp: number } {
  if (!counts) return { booked: 0, onBoard: 0, bp: 0 };
  let booked = 0, onBoard = 0, bp = 0;
  for (const c of Object.values(counts)) {
    booked += c.booked ?? 0;
    onBoard += c.onBoard ?? 0;
    bp += c.boardingPasses ?? 0;
  }
  return { booked, onBoard, bp };
}

/** Compute which fields changed between current and the *previous* (older) record. */
interface ChangedFields {
  status?: boolean;
  gate?: boolean;
  terminal?: boolean;
  aircraft?: boolean;
  std?: boolean;
  etd?: boolean;
  sta?: boolean;
  eta?: boolean;
  boarding?: boolean;
  paxBooked?: boolean;
  paxOnBoard?: boolean;
}

function diffRecords(
  current: FlightStatusRecord,
  prev: FlightStatusRecord | undefined,
): ChangedFields {
  if (!prev) return {}; // first (oldest) record — nothing to diff
  const ch: ChangedFields = {};
  if (current.status !== prev.status) ch.status = true;
  if (current.gate !== prev.gate) ch.gate = true;
  if (current.terminal !== prev.terminal) ch.terminal = true;
  if (current.aircraft?.type !== prev.aircraft?.type || current.aircraft?.registration !== prev.aircraft?.registration) ch.aircraft = true;
  if (current.schedule?.scheduledDeparture !== prev.schedule?.scheduledDeparture) ch.std = true;
  if (current.schedule?.estimatedDeparture !== prev.schedule?.estimatedDeparture) ch.etd = true;
  if (current.schedule?.scheduledArrival !== prev.schedule?.scheduledArrival) ch.sta = true;
  if (current.schedule?.estimatedArrival !== prev.schedule?.estimatedArrival) ch.eta = true;
  if (current.boarding?.time !== prev.boarding?.time) ch.boarding = true;
  const curPax = totalPax(current.passengerCounts);
  const prevPax = totalPax(prev.passengerCounts);
  if (curPax.booked !== prevPax.booked) ch.paxBooked = true;
  if (curPax.onBoard !== prevPax.onBoard) ch.paxOnBoard = true;
  return ch;
}

const changedCls = "text-amber-600 dark:text-amber-400 font-medium";

function StatusHistoryLoadingState() {
  return (
    <div className="flex items-center justify-center py-20 text-muted-foreground">
      <Loader2 className="mr-2 h-5 w-5 animate-spin" />
      Loading flight history...
    </div>
  );
}

function StatusEvolutionCard({
  history,
  diffs,
}: {
  history: FlightStatusRecord[];
  diffs: ChangedFields[];
}) {
  return (
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
          <p className="text-xs text-muted-foreground text-center py-6">
            No status records found.
          </p>
        ) : (
          <div className="relative space-y-0">
            <div className="absolute left-[18px] top-4 bottom-4 w-px bg-border" />

            {history.map((rec, i) => {
              const ch = diffs[i];
              const colorClass = STATUS_COLORS[rec.status] ?? "bg-muted text-muted-foreground";
              const dotColor = STATUS_DOT_COLORS[rec.status] ?? "bg-muted-foreground";
              const pax = totalPax(rec.passengerCounts);
              const hasChanges = Object.values(ch).some(Boolean);

              return (
                <div key={`${rec.fetchedAt}-${rec.status}-${rec.gate || "gate"}-${rec.terminal || "terminal"}`} className="relative flex gap-3 py-3 pl-0 group">
                  <div className="relative z-10 mt-1 flex h-9 w-9 shrink-0 items-center justify-center">
                    <div className={cn("h-3 w-3 rounded-full ring-4 ring-background", dotColor)} />
                  </div>

                  <div
                    className={cn(
                      "flex-1 min-w-0 rounded-lg border p-3 space-y-2 transition-colors",
                      hasChanges ? "border-amber-300/50 bg-amber-500/5" : "bg-muted/20",
                    )}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge className={cn("text-[10px] border-transparent", colorClass)}>
                        {rec.status || "Unknown"}
                      </Badge>
                      {ch.status && (
                        <span className="text-[9px] text-amber-600 font-medium">CHANGED</span>
                      )}
                      <span className="text-xs text-muted-foreground ml-auto flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {formatTs(rec.fetchedAt)}
                      </span>
                    </div>

                    {rec.schedule && (
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1">
                        <div className="text-[11px]">
                          <span className="text-muted-foreground flex items-center gap-1">
                            <PlaneTakeoff className="h-3 w-3" /> STD
                          </span>
                          <span className={cn("font-mono", ch.std && changedCls)}>
                            {fmtTime(rec.schedule.scheduledDeparture)}
                          </span>
                        </div>
                        <div className="text-[11px]">
                          <span className="text-muted-foreground">ETD</span>
                          <span className={cn("font-mono ml-1", ch.etd && changedCls)}>
                            {fmtTime(rec.schedule.estimatedDeparture)}
                          </span>
                        </div>
                        <div className="text-[11px]">
                          <span className="text-muted-foreground flex items-center gap-1">
                            <PlaneLanding className="h-3 w-3" /> STA
                          </span>
                          <span className={cn("font-mono", ch.sta && changedCls)}>
                            {fmtTime(rec.schedule.scheduledArrival)}
                          </span>
                        </div>
                        <div className="text-[11px]">
                          <span className="text-muted-foreground">ETA</span>
                          <span className={cn("font-mono ml-1", ch.eta && changedCls)}>
                            {fmtTime(rec.schedule.estimatedArrival)}
                          </span>
                        </div>
                      </div>
                    )}

                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                      {rec.gate && (
                        <span className={cn("flex items-center gap-1", ch.gate && changedCls)}>
                          <DoorOpen className="h-3 w-3" />
                          Gate {rec.gate}
                        </span>
                      )}
                      {rec.terminal && (
                        <span className={cn("flex items-center gap-1", ch.terminal && changedCls)}>
                          <TowerControl className="h-3 w-3" />
                          Terminal {rec.terminal}
                        </span>
                      )}
                      {rec.aircraft?.type && (
                        <span className={cn("flex items-center gap-1", ch.aircraft && changedCls)}>
                          <Plane className="h-3 w-3" />
                          {rec.aircraft.type}
                          {rec.aircraft.registration ? ` (${rec.aircraft.registration})` : ""}
                        </span>
                      )}
                      {rec.boarding?.time && (
                        <span className={cn("flex items-center gap-1", ch.boarding && changedCls)}>
                          <Clock className="h-3 w-3" />
                          Boarding {fmtTime(rec.boarding.time)}
                        </span>
                      )}
                      {typeof rec.timeToDeparture === "number" && rec.timeToDeparture > 0 && (
                        <span className="flex items-center gap-1">
                          <Timer className="h-3 w-3" />
                          T-{rec.timeToDeparture}min
                        </span>
                      )}
                      {rec.schedule?.durationMinutes ? (
                        <span className="flex items-center gap-1">
                          <ArrowRight className="h-3 w-3" />
                          {Math.floor(rec.schedule.durationMinutes / 60)}h{String(rec.schedule.durationMinutes % 60).padStart(2, "0")}m
                        </span>
                      ) : null}
                    </div>

                    {rec.passengerCounts && Object.keys(rec.passengerCounts).length > 0 && (
                      <div className="flex flex-wrap gap-2 items-center">
                        <Users className="h-3 w-3 text-muted-foreground" />
                        {Object.entries(rec.passengerCounts).map(([cabin, counts]) => (
                          <Badge key={cabin} variant="outline" className="text-[9px] px-1.5 font-mono">
                            <span className="font-semibold">{cabin}</span>
                            <span className="mx-0.5">:</span>
                            <span className={cn(ch.paxBooked && changedCls)}>{counts.booked}</span>
                            <span className="text-muted-foreground mx-0.5">bkd</span>
                            <span className={cn(ch.paxOnBoard && changedCls)}>{counts.onBoard}</span>
                            <span className="text-muted-foreground mx-0.5">obd</span>
                            <span>{counts.boardingPasses}</span>
                            <span className="text-muted-foreground mx-0.5">bp</span>
                          </Badge>
                        ))}
                        <span className="text-[9px] text-muted-foreground ml-1">
                          Total: {pax.booked} booked / {pax.onBoard} on-board / {pax.bp} BP
                        </span>
                      </div>
                    )}

                    {rec.remarks && rec.remarks.length > 0 && (
                      <div className="flex gap-1 items-start text-[10px] text-muted-foreground">
                        <MessageSquare className="h-3 w-3 mt-0.5 shrink-0" />
                        <span className="italic">
                          {rec.remarks.map((r) => (typeof r === "string" ? r : r.text ?? "")).join(" · ")}
                        </span>
                      </div>
                    )}

                    {rec.codeshareInfo && rec.codeshareInfo.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {rec.codeshareInfo.map((cs) => (
                          <Badge key={cs} variant="secondary" className="text-[9px]">
                            CS: {cs}
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
  );
}

function SnapshotsCard({
  snaps,
  selectedSnapshotSequence,
  onLoadSnapshot,
  onClearSnapshot,
  compareMode,
  onToggleCompare,
  restoring,
  onRestore,
  compareLoading,
  compareData,
}: {
  snaps: SnapshotMeta[];
  selectedSnapshotSequence?: number | null;
  onLoadSnapshot?: (sequenceNumber: number) => void;
  onClearSnapshot?: () => void;
  compareMode: boolean;
  onToggleCompare: () => void;
  restoring: boolean;
  onRestore: () => void;
  compareLoading: boolean;
  compareData?: Awaited<ReturnType<typeof compareSnapshot>>;
}) {
  return (
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
              onClick={onToggleCompare}
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
              onClick={onRestore}
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
              {snaps.map((s) => (
                <TableRow key={`${s.snapshotType}-${s.sequenceNumber}`}>
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
  );
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

  // Records arrive newest-first; compute diffs (each record compared to the NEXT in array = older)
  const history = useMemo(() => records ?? [], [records]);
  const diffs = useMemo(() => {
    return history.map((rec, i) => diffRecords(rec, history[i + 1]));
  }, [history]);

  const snaps = useMemo(() => {
    const seen = new Set<string>();
    const unique: SnapshotMeta[] = [];

    for (const snapshot of snapshots ?? []) {
      const dedupeKey = `${snapshot.snapshotType}-${snapshot.sequenceNumber}`;
      if (seen.has(dedupeKey)) {
        continue;
      }
      seen.add(dedupeKey);
      unique.push(snapshot);
    }

    return unique;
  }, [snapshots]);

  const historySeries = useMemo(() => {
    return [...history]
      .reverse()
      .map((rec, index) => {
        const pax = totalPax(rec.passengerCounts);
        return {
          key: `${rec.fetchedAt}-${index}`,
          label: `S${index + 1}`,
          timestamp: formatTs(rec.fetchedAt),
          booked: pax.booked,
          onBoard: pax.onBoard,
          boardingPasses: pax.bp,
        };
      });
  }, [history]);

  const changeHotspots = useMemo(() => {
    const counters: Record<string, number> = {
      Status: 0,
      Gate: 0,
      Terminal: 0,
      Aircraft: 0,
      STD: 0,
      ETD: 0,
      STA: 0,
      ETA: 0,
      Boarding: 0,
      "Booked Pax": 0,
      "On Board Pax": 0,
    };

    diffs.forEach((diff) => {
      if (diff.status) counters.Status += 1;
      if (diff.gate) counters.Gate += 1;
      if (diff.terminal) counters.Terminal += 1;
      if (diff.aircraft) counters.Aircraft += 1;
      if (diff.std) counters.STD += 1;
      if (diff.etd) counters.ETD += 1;
      if (diff.sta) counters.STA += 1;
      if (diff.eta) counters.ETA += 1;
      if (diff.boarding) counters.Boarding += 1;
      if (diff.paxBooked) counters["Booked Pax"] += 1;
      if (diff.paxOnBoard) counters["On Board Pax"] += 1;
    });

    return Object.entries(counters)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => ({ label, count }));
  }, [diffs]);

  if (isLoading) {
    return <StatusHistoryLoadingState />;
  }

  async function handleRestore() {
    if (!selectedSnapshotSequence || restoring) return;
    const ok = window.confirm(
      `Restore snapshot #${selectedSnapshotSequence} as latest data for this flight? This writes new latest documents.`,
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
      <HistoryOverview
        historySeries={historySeries}
        changeHotspots={changeHotspots}
        snapshots={snaps}
        selectedSnapshotSequence={selectedSnapshotSequence}
        compareMode={compareMode}
        compareLoading={compareLoading}
        compareData={compareData}
      />

      <StatusEvolutionCard history={history} diffs={diffs} />

      <Separator />

      <SnapshotsCard
        snaps={snaps}
        selectedSnapshotSequence={selectedSnapshotSequence}
        onLoadSnapshot={onLoadSnapshot}
        onClearSnapshot={onClearSnapshot}
        compareMode={compareMode}
        onToggleCompare={() => setCompareMode((value) => !value)}
        restoring={restoring}
        onRestore={handleRestore}
        compareLoading={compareLoading}
        compareData={compareData}
      />
    </div>
  );
}
