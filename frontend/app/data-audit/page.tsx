"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { fetchDataAuditCompare, fetchDataAuditPassengers } from "@/lib/api";
import type { ComparisonResult, ComparisonRow, PassengerComparisonResult } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ArrowLeft,
  Search,
  Loader2,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Database,
  ArrowRightLeft,
  Plane,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";

type AuditView = "flight" | "passengers";
type AuditResult = ComparisonResult | PassengerComparisonResult;

/* ─────────── Match status styling ─────────── */

function matchBadge(match: ComparisonRow["match"]) {
  switch (match) {
    case "match":
      return (
        <Badge className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 gap-1">
          <CheckCircle2 className="h-3 w-3" />
          Match
        </Badge>
      );
    case "mismatch":
      return (
        <Badge className="bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20 gap-1">
          <XCircle className="h-3 w-3" />
          Mismatch
        </Badge>
      );
    case "pg_only":
      return (
        <Badge className="bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20 gap-1">
          <Database className="h-3 w-3" />
          PG Only
        </Badge>
      );
    case "mongo_only":
      return (
        <Badge className="bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20 gap-1">
          <Database className="h-3 w-3" />
          Mongo Only
        </Badge>
      );
  }
}

function rowBg(match: ComparisonRow["match"]) {
  switch (match) {
    case "match":
      return "";
    case "mismatch":
      return "bg-red-500/5 dark:bg-red-500/10";
    case "pg_only":
      return "bg-amber-500/5 dark:bg-amber-500/10";
    case "mongo_only":
      return "bg-blue-500/5 dark:bg-blue-500/10";
  }
}

function EmptyPane({
  icon: Icon,
  title,
  message,
}: {
  icon: LucideIcon;
  title: string;
  message: string;
}) {
  return (
    <div className="flex h-full min-h-[280px] flex-col items-center justify-center rounded-3xl border border-dashed border-border/70 bg-background/40 px-6 text-center">
      <Icon className="h-10 w-10 text-muted-foreground/40" />
      <h3 className="mt-4 text-sm font-semibold text-foreground">{title}</h3>
      <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">{message}</p>
    </div>
  );
}

/* ─────────── Summary cards ─────────── */

function SummaryCards({ summary }: { summary: { match: number; mismatch: number; pg_only: number; mongo_only: number } }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <Card>
        <CardContent className="flex flex-col items-center py-4 px-3">
          <span className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{summary.match}</span>
          <span className="text-xs text-muted-foreground">Matched</span>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="flex flex-col items-center py-4 px-3">
          <span className="text-2xl font-bold text-red-600 dark:text-red-400">{summary.mismatch}</span>
          <span className="text-xs text-muted-foreground">Mismatched</span>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="flex flex-col items-center py-4 px-3">
          <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">{summary.pg_only}</span>
          <span className="text-xs text-muted-foreground">PG Only</span>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="flex flex-col items-center py-4 px-3">
          <span className="text-2xl font-bold text-blue-600 dark:text-blue-400">{summary.mongo_only}</span>
          <span className="text-xs text-muted-foreground">Mongo Only</span>
        </CardContent>
      </Card>
    </div>
  );
}

/* ─────────── Comparison Table (reused for both tabs) ─────────── */

function ComparisonTable({ rows }: { rows: ComparisonRow[] }) {
  return (
    <Card className="overflow-hidden border-border/70 bg-card/90">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[220px]">Field</TableHead>
              <TableHead>PostgreSQL (OTP)</TableHead>
              <TableHead>MongoDB (Sabre)</TableHead>
              <TableHead className="w-[120px]">Status</TableHead>
              <TableHead>Remark</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.field} className={rowBg(row.match)}>
                <TableCell className="font-medium text-xs">{row.field}</TableCell>
                <TableCell className="text-xs font-mono">
                  {row.pgValue ?? <span className="text-muted-foreground italic">—</span>}
                </TableCell>
                <TableCell className="text-xs font-mono">
                  {row.mongoValue ?? <span className="text-muted-foreground italic">—</span>}
                </TableCell>
                <TableCell>{matchBadge(row.match)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {row.remark ?? ""}
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  No data available
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}

/* ─────────── DB Status Badges ─────────── */

function DbBadges({
  pgFound,
  mongoFound,
  className,
}: {
  pgFound: boolean;
  mongoFound: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <Badge
        className={cn(
          "gap-1",
          pgFound
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
            : "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
        )}
      >
        <Database className="h-3 w-3" />
        PG: {pgFound ? "Found" : "Not Found"}
      </Badge>
      <Badge
        className={cn(
          "gap-1",
          mongoFound
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
            : "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
        )}
      >
        <Database className="h-3 w-3" />
        Mongo: {mongoFound ? "Found" : "Not Found"}
      </Badge>
    </div>
  );
}

function SectionRailButton({
  icon: Icon,
  title,
  description,
  selected,
  result,
  loading,
  error,
  onClick,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  selected: boolean;
  result?: AuditResult;
  loading: boolean;
  error: Error | null;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        "flex w-full flex-col gap-2 rounded-md px-3 py-3 text-left transition-all",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        selected
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0" />
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold">{title}</div>
          <div className={cn("truncate text-[10px]", selected ? "text-primary-foreground/70" : "text-muted-foreground")}>
            {description}
          </div>
        </div>
      </div>

      <div className={cn("text-[10px]", selected ? "text-primary-foreground/80" : "text-muted-foreground")}>
        {loading ? (
          <span className="inline-flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Loading
          </span>
        ) : error ? (
          "Load failed"
        ) : result ? (
          `${result.rows.length} fields compared`
        ) : (
          "No data yet"
        )}
      </div>

      {result ? (
        <div className="grid grid-cols-2 gap-1 text-[10px]">
          <span className={cn("rounded px-1.5 py-1", selected ? "bg-primary-foreground/10 text-primary-foreground" : "bg-background text-foreground")}>
            M {result.summary.match}
          </span>
          <span className={cn("rounded px-1.5 py-1", selected ? "bg-primary-foreground/10 text-primary-foreground" : "bg-background text-foreground")}>
            X {result.summary.mismatch}
          </span>
        </div>
      ) : null}
    </button>
  );
}

function AuditWorkspace({
  title,
  description,
  result,
  loading,
  error,
  isRefreshing,
  sequenceNumber,
  emptyMessage,
}: {
  title: string;
  description: string;
  result?: AuditResult;
  loading: boolean;
  error: Error | null;
  isRefreshing: boolean;
  sequenceNumber?: number | null;
  emptyMessage: string;
}) {
  return (
    <main className="h-full w-full overflow-y-auto px-3 py-3 md:px-4 md:py-4">
      <div className="space-y-3">
        <div className="rounded-lg border bg-card px-4 py-3 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {sequenceNumber ? (
                <Badge variant="outline" className="border-orange-500/30 text-orange-500">
                  Seq #{sequenceNumber}
                </Badge>
              ) : null}
              {isRefreshing ? (
                <Badge variant="outline" className="gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Refreshing
                </Badge>
              ) : null}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="flex min-h-[420px] items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Querying PostgreSQL and MongoDB...
          </div>
        ) : error ? (
          <Card className="border-red-500/30 bg-red-500/5">
            <CardContent className="flex items-start gap-3 py-4">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
              <div>
                <div className="text-sm font-medium text-red-500">Unable to load this section</div>
                <p className="mt-1 text-sm text-red-500/90">{error.message}</p>
              </div>
            </CardContent>
          </Card>
        ) : result ? (
          <>
            <div className="rounded-lg border bg-card px-4 py-3 shadow-sm">
              <div className="space-y-3">
                <DbBadges pgFound={result.pgFound} mongoFound={result.mongoFound} />
                <SummaryCards summary={result.summary} />
              </div>
            </div>
            <ComparisonTable rows={result.rows} />
          </>
        ) : (
          <div className="rounded-lg border bg-card px-4 py-10 shadow-sm">
            <EmptyPane icon={ArrowRightLeft} title="No comparison loaded" message={emptyMessage} />
          </div>
        )}
      </div>
    </main>
  );
}

type SubmittedAuditQuery = {
  flightNumber: string;
  origin: string;
  date: string;
  seq?: number;
};

export default function DataAuditPage() {
  const [flightNumber, setFlightNumber] = useState("");
  const [origin, setOrigin] = useState("");
  const [date, setDate] = useState("");
  const [seqNumber, setSeqNumber] = useState("");
  const [activeView, setActiveView] = useState<AuditView>("flight");
  const [submitted, setSubmitted] = useState<SubmittedAuditQuery | null>(null);

  // Flight info comparison
  const flightQuery = useQuery<ComparisonResult>({
    queryKey: ["data-audit-flight", submitted],
    queryFn: () =>
      fetchDataAuditCompare(
        submitted!.flightNumber,
        submitted!.origin || undefined,
        submitted!.date || undefined,
        submitted!.seq,
      ),
    enabled: !!submitted,
    retry: false,
    staleTime: 0,
  });

  // Passenger comparison
  const paxQuery = useQuery<PassengerComparisonResult>({
    queryKey: ["data-audit-pax", submitted],
    queryFn: () =>
      fetchDataAuditPassengers(
        submitted!.flightNumber,
        submitted!.origin || undefined,
        submitted!.date || undefined,
        submitted!.seq,
      ),
    enabled: !!submitted,
    retry: false,
    staleTime: 0,
  });

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!flightNumber.trim()) return;
    setActiveView("flight");
    setSubmitted({
      flightNumber: flightNumber.trim().toUpperCase(),
      origin: origin.trim().toUpperCase(),
      date: date.trim(),
      seq: seqNumber ? Number(seqNumber) : undefined,
    });
  }

  const flightError = flightQuery.error instanceof Error ? flightQuery.error : null;
  const paxError = paxQuery.error instanceof Error ? paxQuery.error : null;
  const activeResult = activeView === "flight" ? flightQuery.data : paxQuery.data;
  const activeError = activeView === "flight" ? flightError : paxError;
  const activeLoading = activeView === "flight" ? flightQuery.isLoading : paxQuery.isLoading;
  const activeRefreshing = activeView === "flight" ? flightQuery.isFetching : paxQuery.isFetching;

  const searchPane = (
    <aside className="flex h-full flex-col overflow-hidden">
      <div className="border-b px-5 py-4">
        <div className="text-[11px] uppercase tracking-[0.16em] text-orange-500">Search</div>
        <h2 className="mt-2 text-lg font-semibold text-foreground">Flight selector</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Enter the flight you want to audit, then load flight and passenger comparisons.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-5">
        <div className="space-y-1.5">
          <label htmlFor="fn" className="text-xs font-medium text-muted-foreground">
            Flight Number *
          </label>
          <input
            id="fn"
            type="text"
            required
            placeholder="GF2152"
            value={flightNumber}
            onChange={(e) => setFlightNumber(e.target.value)}
            className="h-11 w-full rounded-2xl border border-input bg-background/70 px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="origin" className="text-xs font-medium text-muted-foreground">
            Origin
          </label>
          <input
            id="origin"
            type="text"
            placeholder="BAH"
            maxLength={3}
            value={origin}
            onChange={(e) => setOrigin(e.target.value)}
            className="h-11 w-full rounded-2xl border border-input bg-background/70 px-3 text-sm uppercase shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="date" className="text-xs font-medium text-muted-foreground">
            Flight Date
          </label>
          <input
            id="date"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="h-11 w-full rounded-2xl border border-input bg-background/70 px-3 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="seq" className="text-xs font-medium text-muted-foreground">
            Sequence #
          </label>
          <input
            id="seq"
            type="number"
            placeholder="Optional"
            value={seqNumber}
            onChange={(e) => setSeqNumber(e.target.value)}
            className="h-11 w-full rounded-2xl border border-input bg-background/70 px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>

        <button
          type="submit"
          disabled={!flightNumber.trim() || flightQuery.isLoading || paxQuery.isLoading}
          className="mt-2 inline-flex h-11 items-center justify-center gap-2 rounded-2xl bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors disabled:pointer-events-none disabled:opacity-50"
        >
          {flightQuery.isFetching || paxQuery.isFetching ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Search className="h-4 w-4" />
          )}
          Compare
        </button>
      </form>

      <div className="mt-auto border-t bg-muted/30 p-4">
        <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">Current query</div>
        {submitted ? (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{submitted.flightNumber}</Badge>
              <Badge variant="outline">{submitted.origin || "Any origin"}</Badge>
              <Badge variant="outline">{submitted.date}</Badge>
              {submitted.seq ? <Badge variant="outline">Seq #{submitted.seq}</Badge> : null}
            </div>
            <p className="text-sm text-muted-foreground">
              Select a section from the middle pane to inspect field-level differences.
            </p>
          </div>
        ) : (
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            Start with a flight number. The middle pane will show flight and passenger sections, and the right pane will open the selected comparison.
          </p>
        )}
      </div>
    </aside>
  );

  const navigatorPane = (
    <aside className="flex h-full flex-col overflow-hidden">
      <div className="border-b px-3 py-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        Sections
      </div>
      <div className="flex-1 space-y-2 bg-muted/20 px-2 py-3">
        <SectionRailButton
          icon={Plane}
          title="Flight information"
          description="OTP vs Sabre"
          selected={activeView === "flight"}
          result={flightQuery.data}
          loading={flightQuery.isLoading}
          error={flightError}
          onClick={() => setActiveView("flight")}
        />
        <SectionRailButton
          icon={Users}
          title="Passenger information"
          description="Manifest metrics"
          selected={activeView === "passengers"}
          result={paxQuery.data}
          loading={paxQuery.isLoading}
          error={paxError}
          onClick={() => setActiveView("passengers")}
        />
      </div>
      <div className="border-t px-3 py-3 text-[10px] leading-5 text-muted-foreground">
        {submitted
          ? `Active flight ${submitted.flightNumber} ${submitted.origin || "ANY"}`
          : "Run a comparison to unlock section details."}
      </div>
    </aside>
  );

  const detailPane = (
    <AuditWorkspace
      title={activeView === "flight" ? "Flight information" : "Passenger information"}
      description={
        activeView === "flight"
          ? "Field-level comparison for the operational flight record."
          : "Field-level comparison for passenger totals and manifest-derived metrics."
      }
      result={activeResult}
      loading={activeLoading}
      error={activeError}
      isRefreshing={activeRefreshing && !activeLoading}
      sequenceNumber={activeView === "flight" ? flightQuery.data?.sequenceNumber : undefined}
      emptyMessage="Use the search pane to run a compare, then select a section from the middle pane."
    />
  );

  return (
    <div className="min-h-screen bg-background">
      {/* ── Header ── */}
      <header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center justify-between px-4 md:px-6">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </Link>
            <div className="hidden sm:block h-5 w-px bg-border" />
            <div className="flex items-center gap-2">
              <ArrowRightLeft className="h-4 w-4 text-orange-500" />
              <h1 className="text-base font-semibold">Data Audit</h1>
              <span className="text-xs text-muted-foreground">PostgreSQL ↔ MongoDB</span>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-6 md:px-6">
        <div className="space-y-4 xl:hidden">
          <div className="overflow-hidden rounded-lg border bg-card shadow-sm">{searchPane}</div>
          <div className="overflow-hidden rounded-lg border bg-card shadow-sm">{navigatorPane}</div>
          <div className="overflow-hidden rounded-lg border bg-card shadow-sm">{detailPane}</div>
        </div>

        <div className="hidden xl:block">
          <div className="grid min-h-[calc(100vh-7.5rem)] grid-cols-[360px_220px_minmax(0,1fr)] gap-4">
            <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
              {searchPane}
            </div>
            <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
              {navigatorPane}
            </div>
            <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
              {detailPane}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
