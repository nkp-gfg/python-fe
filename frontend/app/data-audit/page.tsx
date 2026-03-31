"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { fetchDataAuditCompare } from "@/lib/api";
import type { ComparisonResult, ComparisonRow } from "@/lib/types";
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
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";

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

/* ─────────── Summary cards ─────────── */

function SummaryCards({ result }: { result: ComparisonResult }) {
  const { summary } = result;
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

/* ─────────── Main Page ─────────── */

export default function DataAuditPage() {
  const [flightNumber, setFlightNumber] = useState("");
  const [origin, setOrigin] = useState("");
  const [date, setDate] = useState("");
  const [seqNumber, setSeqNumber] = useState("");

  // Track submitted values separately so query only runs on submit
  const [submitted, setSubmitted] = useState<{
    flightNumber: string;
    origin: string;
    date: string;
    seq: number | undefined;
  } | null>(null);

  const { data, isLoading, error, isFetching } = useQuery<ComparisonResult>({
    queryKey: ["data-audit", submitted],
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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!flightNumber.trim()) return;
    setSubmitted({
      flightNumber: flightNumber.trim().toUpperCase(),
      origin: origin.trim().toUpperCase(),
      date: date.trim(),
      seq: seqNumber ? Number(seqNumber) : undefined,
    });
  }

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

      <main className="mx-auto max-w-6xl px-4 py-6 md:px-6 space-y-6">
        {/* ── Search Form ── */}
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1.5 min-w-[140px]">
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
                  className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </div>
              <div className="flex flex-col gap-1.5 min-w-[100px]">
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
                  className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring uppercase"
                />
              </div>
              <div className="flex flex-col gap-1.5 min-w-[160px]">
                <label htmlFor="date" className="text-xs font-medium text-muted-foreground">
                  Flight Date
                </label>
                <input
                  id="date"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </div>
              <div className="flex flex-col gap-1.5 min-w-[120px]">
                <label htmlFor="seq" className="text-xs font-medium text-muted-foreground">
                  Sequence #
                </label>
                <input
                  id="seq"
                  type="number"
                  placeholder="Optional"
                  value={seqNumber}
                  onChange={(e) => setSeqNumber(e.target.value)}
                  className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </div>
              <button
                type="submit"
                disabled={!flightNumber.trim() || isLoading}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors disabled:pointer-events-none disabled:opacity-50"
              >
                {isFetching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Search className="h-4 w-4" />
                )}
                Compare
              </button>
            </form>
          </CardContent>
        </Card>

        {/* ── Error ── */}
        {error && (
          <Card className="border-red-500/30">
            <CardContent className="flex items-center gap-3 py-4">
              <AlertCircle className="h-5 w-5 text-red-500 shrink-0" />
              <p className="text-sm text-red-600 dark:text-red-400">
                {error instanceof Error ? error.message : "Comparison failed"}
              </p>
            </CardContent>
          </Card>
        )}

        {/* ── Loading ── */}
        {isLoading && (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Querying PostgreSQL & MongoDB…
          </div>
        )}

        {/* ── Results ── */}
        {data && !isLoading && (
          <>
            {/* DB status badges */}
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">{data.flightNumber}</span>
                {data.sequenceNumber && (
                  <Badge variant="outline" className="text-orange-500 border-orange-500/30">
                    Seq #{data.sequenceNumber}
                  </Badge>
                )}
              </div>
              <Badge
                className={cn(
                  "gap-1",
                  data.pgFound
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                    : "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
                )}
              >
                <Database className="h-3 w-3" />
                PostgreSQL: {data.pgFound ? "Found" : "Not Found"}
              </Badge>
              <Badge
                className={cn(
                  "gap-1",
                  data.mongoFound
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                    : "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
                )}
              >
                <Database className="h-3 w-3" />
                MongoDB: {data.mongoFound ? "Found" : "Not Found"}
              </Badge>
            </div>

            {/* Summary */}
            <SummaryCards result={data} />

            {/* Comparison table */}
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">Field</TableHead>
                    <TableHead>PostgreSQL (OTP)</TableHead>
                    <TableHead>MongoDB (Sabre)</TableHead>
                    <TableHead className="w-[120px]">Status</TableHead>
                    <TableHead>Remark</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.rows.map((row) => (
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
                </TableBody>
              </Table>
            </Card>
          </>
        )}

        {/* ── Empty state ── */}
        {!submitted && !isLoading && (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground space-y-2">
            <ArrowRightLeft className="h-10 w-10 opacity-30" />
            <p className="text-sm">Enter a flight number and click Compare</p>
            <p className="text-xs">
              Compares field-level data between PostgreSQL OTP and MongoDB Sabre
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
