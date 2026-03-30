"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { format, addDays, subDays } from "date-fns";
import {
  ArrowRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Plane,
  RefreshCw,
  Users,
  ArrowLeft,
  X,
  AlertCircle,
  Download,
  Check,
  AlertTriangle,
} from "lucide-react";

import { fetchOtpFlights, ingestFlight, ingestBatch, fetchJobStatus } from "@/lib/api";
import type { OtpFlight, SabreIngestRequest } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ThemeToggle } from "@/components/theme-toggle";

/* ─────────── STATUS HELPERS ─────────── */

const STATUS_COLORS: Record<string, string> = {
  PUSHBACK: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400",
  DEPARTED: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  AIRBORNE: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  ENROUTE: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
  LANDED: "bg-teal-500/15 text-teal-600 dark:text-teal-400",
  ARRIVED: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  CANCELLED: "bg-red-500/15 text-red-600 dark:text-red-400",
  SCHEDULED: "bg-slate-500/15 text-slate-600 dark:text-slate-400",
};

function getStatusColor(status: string) {
  return STATUS_COLORS[status.toUpperCase()] ?? "bg-secondary text-secondary-foreground";
}

const PHASE_BORDERS: Record<string, string> = {
  PUSHBACK: "border-indigo-400",
  DEPARTED: "border-emerald-400",
  AIRBORNE: "border-sky-400",
  ENROUTE: "border-cyan-400",
  LANDED: "border-teal-400",
  ARRIVED: "border-emerald-400",
  CANCELLED: "border-red-400",
  SCHEDULED: "border-muted-foreground/20",
};

function getPhaseBorder(status: string) {
  return PHASE_BORDERS[status.toUpperCase()] ?? "border-muted-foreground/20";
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  // ISO string like "2026-03-30T09:55:00"
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
}

/* ─────────── helpers ─────────── */

const INGEST_POLL_MS = 3_000;
const INGEST_TIMEOUT_MS = 10 * 60_000;

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

function buildIngestPayload(f: OtpFlight): SabreIngestRequest {
  return {
    airline: "GF",
    flightNumber: f.flightNumber,
    origin: f.origin,
    departureDate: f.flightDate,
    departureDateTime: f.scheduledDepartureUtc ?? `${f.flightDate}T00:00:00`,
    flightSequenceNumber: f.flightSequenceNumber,
  };
}

/* ─────────── TODAY'S FLIGHTS PAGE ─────────── */

export default function TodayPage() {
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const dateStr = format(selectedDate, "yyyy-MM-dd");
  const isToday = dateStr === format(new Date(), "yyyy-MM-dd");

  /* ── selection state ── */
  const [selected, setSelected] = useState<Set<number>>(new Set());
  /* ── per-row ingest state: seq → "idle" | "loading" | "done" | "error" */
  const [rowIngestState, setRowIngestState] = useState<Record<number, "idle" | "loading" | "done" | "error">>({});
  /* ── batch ingest state ── */
  const [batchState, setBatchState] = useState<"idle" | "loading" | "polling" | "done" | "error">("idle");
  const [batchJobId, setBatchJobId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState({ queued: 0, processed: 0 });
  const [batchError, setBatchError] = useState<string | null>(null);
  /* ── cancelled warning modal ── */
  const [showCancelledWarning, setShowCancelledWarning] = useState(false);

  const {
    data: flights,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["otp-flights", dateStr],
    queryFn: () => fetchOtpFlights(dateStr),
    refetchInterval: 60_000,
  });

  const visibleFlights = useMemo(() => {
    if (!flights) return [];
    let list = flights;
    if (statusFilter !== "all") {
      list = list.filter((f) => f.flightStatus.toUpperCase() === statusFilter);
    }
    return list;
  }, [flights, statusFilter]);

  const statusCounts = useMemo(() => {
    if (!flights) return {};
    const counts: Record<string, number> = {};
    for (const f of flights) {
      const s = f.flightStatus;
      counts[s] = (counts[s] || 0) + 1;
    }
    return counts;
  }, [flights]);

  const stats = useMemo(() => {
    if (!flights) return { total: 0, active: 0, cancelled: 0, totalPax: 0 };
    const total = flights.length;
    const cancelled = flights.filter((f) => f.isCancelled).length;
    const active = total - cancelled;
    const totalPax = flights.reduce((s, f) => s + (f.totalPax ?? 0), 0);
    return { total, active, cancelled, totalPax };
  }, [flights]);

  /* ── selection helpers ── */
  const toggleSelect = useCallback(
    (seq: number) => setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq); else next.add(seq);
      return next;
    }),
    [],
  );

  const toggleSelectAll = useCallback(() => {
    if (!visibleFlights.length) return;
    setSelected((prev) => {
      const allSeqs = visibleFlights.map((f) => f.flightSequenceNumber);
      const allSelected = allSeqs.every((s) => prev.has(s));
      if (allSelected) return new Set();
      return new Set(allSeqs);
    });
  }, [visibleFlights]);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  /* ── per-row ingest handler ── */
  const handleRowIngest = useCallback(async (flight: OtpFlight) => {
    const seq = flight.flightSequenceNumber;
    setRowIngestState((prev) => ({ ...prev, [seq]: "loading" }));
    try {
      await ingestFlight(buildIngestPayload(flight));
      setRowIngestState((prev) => ({ ...prev, [seq]: "done" }));
    } catch {
      setRowIngestState((prev) => ({ ...prev, [seq]: "error" }));
    }
  }, []);

  /* ── batch ingest handler ── */
  const selectedFlights = useMemo(() => {
    if (!flights) return [];
    return flights.filter((f) => selected.has(f.flightSequenceNumber));
  }, [flights, selected]);

  const selectedCancelledCount = useMemo(
    () => selectedFlights.filter((f) => f.isCancelled).length,
    [selectedFlights],
  );

  const handleBatchIngest = useCallback(async () => {
    if (!selectedFlights.length) return;

    // Warn if cancelled flights are in selection
    if (selectedCancelledCount > 0 && !showCancelledWarning) {
      setShowCancelledWarning(true);
      return;
    }
    setShowCancelledWarning(false);

    const payloads = selectedFlights.map(buildIngestPayload);
    setBatchState("loading");
    setBatchError(null);
    try {
      const accepted = await ingestBatch({ flights: payloads });
      setBatchJobId(accepted.jobId);
      setBatchProgress({ queued: accepted.flightsQueued, processed: 0 });
      setBatchState("polling");

      // Poll until done
      const deadline = Date.now() + INGEST_TIMEOUT_MS;
      while (Date.now() < deadline) {
        await sleep(INGEST_POLL_MS);
        const job = await fetchJobStatus(accepted.jobId);
        setBatchProgress({ queued: job.flightsQueued, processed: job.flightsProcessed });

        if (job.status === "completed") {
          setBatchState("done");
          clearSelection();
          return;
        }
        if (job.status === "failed") {
          setBatchState("error");
          setBatchError(job.error ?? "Batch job failed");
          return;
        }
      }
      setBatchState("error");
      setBatchError("Batch job timed out");
    } catch (e) {
      setBatchState("error");
      setBatchError(e instanceof Error ? e.message : "Batch ingestion failed");
    }
  }, [selectedFlights, selectedCancelledCount, showCancelledWarning, clearSelection]);

  const confirmBatchWithCancelled = useCallback(() => {
    setShowCancelledWarning(false);
    // Re-trigger, this time without the cancelled check
    const payloads = selectedFlights.map(buildIngestPayload);
    setBatchState("loading");
    setBatchError(null);
    (async () => {
      try {
        const accepted = await ingestBatch({ flights: payloads });
        setBatchJobId(accepted.jobId);
        setBatchProgress({ queued: accepted.flightsQueued, processed: 0 });
        setBatchState("polling");

        const deadline = Date.now() + INGEST_TIMEOUT_MS;
        while (Date.now() < deadline) {
          await sleep(INGEST_POLL_MS);
          const job = await fetchJobStatus(accepted.jobId);
          setBatchProgress({ queued: job.flightsQueued, processed: job.flightsProcessed });

          if (job.status === "completed") {
            setBatchState("done");
            clearSelection();
            return;
          }
          if (job.status === "failed") {
            setBatchState("error");
            setBatchError(job.error ?? "Batch job failed");
            return;
          }
        }
        setBatchState("error");
        setBatchError("Batch job timed out");
      } catch (e) {
        setBatchState("error");
        setBatchError(e instanceof Error ? e.message : "Batch ingestion failed");
      }
    })();
  }, [selectedFlights, clearSelection]);

  return (
    <div className="flex-1 flex flex-col min-h-screen relative">
      {/* Top Bar */}
      <header className="border-b border-border px-4 sm:px-6 py-3 flex items-center gap-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          <span className="hidden sm:inline">Dashboard</span>
        </Link>

        <div className="flex items-center gap-2 ml-2">
          <Plane className="h-4 w-4 text-amber-400" />
          <h1 className="text-sm font-semibold">
            {isToday ? "Today\u2019s Flights" : "Flights"}
          </h1>
        </div>

        {/* Date Navigation */}
        <div className="flex items-center gap-1 ml-2">
          <button
            onClick={() => setSelectedDate((d) => subDays(d, 1))}
            className="rounded p-1 hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
            aria-label="Previous day"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>

          <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
            <PopoverTrigger asChild>
              <button
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                  isToday
                    ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400"
                    : "border-input bg-background text-foreground"
                )}
              >
                <CalendarDays className="h-3.5 w-3.5" />
                {isToday ? "Today" : format(selectedDate, "EEE, MMM d")}
                <span className="text-muted-foreground ml-0.5">{dateStr}</span>
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <Calendar
                mode="single"
                selected={selectedDate}
                onSelect={(day) => {
                  if (day) setSelectedDate(day);
                  setCalendarOpen(false);
                }}
                defaultMonth={selectedDate}
              />
              <div className="border-t px-3 py-2 flex gap-2">
                <button
                  onClick={() => { setSelectedDate(new Date()); setCalendarOpen(false); }}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Today
                </button>
              </div>
            </PopoverContent>
          </Popover>

          <button
            onClick={() => setSelectedDate((d) => addDays(d, 1))}
            className="rounded p-1 hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
            aria-label="Next day"
          >
            <ChevronRight className="h-4 w-4" />
          </button>

          {!isToday && (
            <button
              onClick={() => setSelectedDate(new Date())}
              className="ml-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Reset to today
            </button>
          )}
        </div>

        <div className="flex-1" />

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
          <span className="hidden sm:inline">Refresh</span>
        </button>
        <ThemeToggle />
      </header>

      {/* Summary Strip + Status Filters */}
      {!isLoading && !error && flights && (
        <div className="border-b border-border bg-muted/30 px-4 sm:px-6 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs tabular-nums">
          <span className="font-semibold text-foreground">{stats.total} flights</span>
          <span className="text-muted-foreground">
            {stats.active} active &middot; {stats.cancelled} cancelled
          </span>

          {/* Status filter pills */}
          <div className="flex items-center gap-1.5 ml-2">
            <button
              onClick={() => setStatusFilter("all")}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors",
                statusFilter === "all"
                  ? "border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-400"
                  : "border-input bg-background text-muted-foreground hover:text-foreground"
              )}
            >
              All
            </button>
            {Object.entries(statusCounts)
              .sort(([, a], [, b]) => b - a)
              .map(([status, count]) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(statusFilter === status.toUpperCase() ? "all" : status.toUpperCase())}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors",
                    statusFilter === status.toUpperCase()
                      ? "border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-400"
                      : "border-input bg-background text-muted-foreground hover:text-foreground"
                  )}
                >
                  {status} ({count})
                </button>
              ))}
          </div>

          <span className="ml-auto text-muted-foreground">
            <Users className="inline h-3 w-3 mr-1" />
            {stats.totalPax} pax
          </span>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto pb-20">
        {isLoading ? (
          <div className="flex items-center justify-center py-32 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading flights\u2026
          </div>
        ) : error ? (
          <div className="max-w-md mx-auto mt-16 rounded-lg border border-destructive/50 bg-destructive/10 p-6 text-center">
            <AlertCircle className="h-6 w-6 mx-auto mb-2 text-destructive" />
            <div className="font-semibold text-destructive mb-1">Failed to load flights</div>
            <div className="text-sm text-destructive/80">
              {error instanceof Error ? error.message : "Could not connect to API."}
            </div>
          </div>
        ) : visibleFlights.length === 0 ? (
          <div className="text-center py-32 text-muted-foreground">
            <Plane className="h-10 w-10 mx-auto mb-3 opacity-40" />
            <p className="text-sm">
              {statusFilter !== "all"
                ? `No ${statusFilter.toLowerCase()} flights on ${dateStr}.`
                : `No flights on ${dateStr}.`}
            </p>
          </div>
        ) : (
          <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-4">
            <div className="rounded-lg border border-border overflow-hidden divide-y divide-border">
              {/* Header */}
              <div className="hidden sm:grid grid-cols-[28px_36px_minmax(110px,1fr)_110px_90px_minmax(130px,1fr)_70px_70px_50px_60px_70px] gap-x-2 px-4 py-2 bg-muted/50 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <span>
                  <input
                    type="checkbox"
                    checked={visibleFlights.length > 0 && visibleFlights.every((f) => selected.has(f.flightSequenceNumber))}
                    onChange={toggleSelectAll}
                    className="h-3.5 w-3.5 rounded border-muted-foreground/50 cursor-pointer accent-blue-600"
                    aria-label="Select all flights"
                  />
                </span>
                <span>#</span>
                <span>Flight</span>
                <span>Seq #</span>
                <span>Status</span>
                <span>Route</span>
                <span>STD</span>
                <span>STA</span>
                <span className="text-right">Pax</span>
                <span className="text-right">Delay</span>
                <span className="text-center">Ingest</span>
              </div>

              {visibleFlights.map((flight, idx) => (
                <FlightRow
                  key={flight.flightSequenceNumber}
                  flight={flight}
                  index={idx + 1}
                  isSelected={selected.has(flight.flightSequenceNumber)}
                  onToggleSelect={() => toggleSelect(flight.flightSequenceNumber)}
                  ingestState={rowIngestState[flight.flightSequenceNumber] ?? "idle"}
                  onIngest={() => handleRowIngest(flight)}
                />
              ))}
            </div>

            <div className="text-center text-xs text-muted-foreground mt-3">
              Showing {visibleFlights.length} of {flights?.length ?? 0} flights
            </div>
          </div>
        )}
      </div>

      {/* ── Floating Batch Action Bar ── */}
      {selected.size > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-50 flex justify-center pointer-events-none">
          <div className="pointer-events-auto mb-6 flex items-center gap-3 rounded-xl border border-border bg-background/95 backdrop-blur shadow-lg px-5 py-3">
            <span className="text-sm font-medium">
              {selected.size} flight{selected.size > 1 ? "s" : ""} selected
            </span>

            {selectedCancelledCount > 0 && (
              <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                <AlertTriangle className="h-3 w-3" />
                {selectedCancelledCount} cancelled
              </span>
            )}

            {batchState === "polling" && (
              <span className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400">
                <Loader2 className="h-3 w-3 animate-spin" />
                {batchProgress.processed}/{batchProgress.queued}
              </span>
            )}

            {batchState === "done" && (
              <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                <Check className="h-3 w-3" />
                Done
              </span>
            )}

            {batchState === "error" && (
              <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400" title={batchError ?? undefined}>
                <AlertCircle className="h-3 w-3" />
                Failed
              </span>
            )}

            <button
              onClick={handleBatchIngest}
              disabled={batchState === "loading" || batchState === "polling"}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                "bg-blue-600 text-white hover:bg-blue-700",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {(batchState === "loading" || batchState === "polling") ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              Ingest {selected.size} flight{selected.size > 1 ? "s" : ""}
            </button>

            <button
              onClick={clearSelection}
              className="rounded p-1.5 hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
              aria-label="Clear selection"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* ── Cancelled Warning Dialog ── */}
      {showCancelledWarning && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-background border border-border rounded-lg shadow-xl max-w-sm w-full mx-4 p-5">
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              <h3 className="font-semibold text-sm">Cancelled Flights Selected</h3>
            </div>
            <p className="text-sm text-muted-foreground mb-4">
              {selectedCancelledCount} of {selected.size} selected flight{selected.size > 1 ? "s" : ""} {selectedCancelledCount > 1 ? "are" : "is"} cancelled. Ingesting cancelled flights may produce limited data. Continue anyway?
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCancelledWarning(false)}
                className="rounded-md border border-input px-3 py-1.5 text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmBatchWithCancelled}
                className="rounded-md bg-amber-600 text-white px-3 py-1.5 text-sm hover:bg-amber-700 transition-colors"
              >
                Ingest Anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────── COMPACT FLIGHT ROW ─────────── */

/** Parse ISO duration like "PT0H24M" → minutes */
function parseDurationMin(d: string): number {
  const m = d.match(/PT(\d+)H(\d+)M/);
  if (!m) return 0;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

function FlightRow({
  flight,
  index,
  isSelected,
  onToggleSelect,
  ingestState,
  onIngest,
}: {
  flight: OtpFlight;
  index: number;
  isSelected: boolean;
  onToggleSelect: () => void;
  ingestState: "idle" | "loading" | "done" | "error";
  onIngest: () => void;
}) {
  const status = flight.flightStatus;
  const std = fmtTime(flight.scheduledDepartureUtc);
  const sta = fmtTime(flight.scheduledArrivalUtc);
  const etd = fmtTime(flight.estimatedBlockOffUtc);
  const delayed = etd !== "—" && etd !== std;
  const pax = flight.totalPax ?? 0;

  // Sum delay from delay_details JSONB
  const totalDelayMin = flight.delayDetails
    ? flight.delayDetails.reduce((sum, d) => sum + parseDurationMin(d.DelayDuration), 0)
    : 0;

  return (
    <div
      className={cn(
        "transition-colors hover:bg-accent/50",
        "sm:grid sm:grid-cols-[28px_36px_minmax(110px,1fr)_110px_90px_minmax(130px,1fr)_70px_70px_50px_60px_70px] sm:gap-x-2 sm:items-center",
        "px-4 py-2.5",
        flight.isCancelled && "opacity-50",
        isSelected && "bg-blue-500/5 dark:bg-blue-500/10"
      )}
    >
      {/* Checkbox */}
      <div className="hidden sm:flex items-center">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={onToggleSelect}
          className="h-3.5 w-3.5 rounded border-muted-foreground/50 cursor-pointer accent-blue-600"
          aria-label={`Select flight GF${flight.flightNumber}`}
        />
      </div>

      {/* Row Number */}
      <div className="hidden sm:block text-xs tabular-nums text-muted-foreground">
        {index}
      </div>

      {/* Flight Number + Aircraft */}
      <div className="flex items-center gap-2">
        <div className={cn("h-2 w-2 rounded-full border-2 shrink-0", getPhaseBorder(status))} />
        <span className="font-bold text-sm tracking-tight">
          GF{flight.flightNumber}
        </span>
        {flight.aircraftType && (
          <span className="text-[10px] text-muted-foreground font-mono">{flight.aircraftType}</span>
        )}
        {flight.aircraftRegistration && (
          <span className="text-[10px] text-muted-foreground">{flight.aircraftRegistration}</span>
        )}
      </div>

      {/* Sequence Number */}
      <div className="hidden sm:block text-[10px] tabular-nums text-muted-foreground font-mono">
        {flight.flightSequenceNumber}
      </div>

      {/* Status */}
      <div className="mt-1 sm:mt-0">
        <Badge
          variant="outline"
          className={cn("text-[10px] px-1.5 font-medium border-transparent", getStatusColor(status))}
        >
          {status}
        </Badge>
      </div>

      {/* Route */}
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-0.5 sm:mt-0">
        <span className="font-medium text-foreground">{flight.origin}</span>
        <ArrowRight className="h-3 w-3 shrink-0" />
        <span className="font-medium text-foreground">{flight.destination || "—"}</span>
      </div>

      {/* STD */}
      <div className="text-xs tabular-nums mt-0.5 sm:mt-0">
        <span>{std}</span>
        {delayed && (
          <span className="ml-1 text-[10px] font-semibold text-rose-500">{"\u2192"} {etd}</span>
        )}
      </div>

      {/* STA */}
      <div className="text-xs tabular-nums text-muted-foreground mt-0.5 sm:mt-0">
        {sta}
      </div>

      {/* Pax */}
      <div className="text-xs tabular-nums text-right mt-0.5 sm:mt-0">
        <span className="font-semibold">{pax}</span>
      </div>

      {/* Delay */}
      <div className="text-xs tabular-nums text-right mt-0.5 sm:mt-0">
        {totalDelayMin > 0 ? (
          <span className="font-semibold px-1.5 py-0.5 rounded text-[10px] text-rose-600 bg-rose-50 dark:bg-rose-950/40 dark:text-rose-400">
            +{totalDelayMin}m
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>

      {/* Ingest Button */}
      <div className="hidden sm:flex justify-center mt-0.5 sm:mt-0">
        <button
          onClick={onIngest}
          disabled={ingestState === "loading"}
          className={cn(
            "flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium transition-colors",
            ingestState === "idle" && "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/25",
            ingestState === "loading" && "bg-blue-500/15 text-blue-600 dark:text-blue-400 cursor-wait",
            ingestState === "done" && "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
            ingestState === "error" && "bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/25"
          )}
          aria-label={`Ingest flight GF${flight.flightNumber}`}
        >
          {ingestState === "loading" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : ingestState === "done" ? (
            <Check className="h-3 w-3" />
          ) : ingestState === "error" ? (
            <AlertCircle className="h-3 w-3" />
          ) : (
            <Download className="h-3 w-3" />
          )}
          {ingestState === "idle" && "Ingest"}
          {ingestState === "loading" && "..."}
          {ingestState === "done" && "Done"}
          {ingestState === "error" && "Retry"}
        </button>
      </div>
    </div>
  );
}
