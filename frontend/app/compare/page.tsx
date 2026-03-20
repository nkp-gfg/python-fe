"use client";

import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { fetchFlights, fetchDashboard, fetchFlightTree } from "@/lib/api";
import type { FlightListItem } from "@/lib/types";
import { PassengerTree } from "@/components/dashboard/passenger-tree";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Plane,
  ArrowLeft,
  Loader2,
  AlertCircle,
  Users,
  ArrowRight,
  Check,
  ChevronDown,
  RefreshCw,
} from "lucide-react";

/* ─────────── COMPARISON PAGE ─────────── */
export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Loading comparison...
        </div>
      }
    >
      <ComparePageContent />
    </Suspense>
  );
}

function ComparePageContent() {
  const searchParams = useSearchParams();
  const preselected = searchParams.get("flights")?.split(",") ?? [];

  const [selected, setSelected] = useState<
    { flightNumber: string; origin: string; date: string }[]
  >(
    preselected.map((s) => {
      const [fn, origin, date] = s.split(":");
      return { flightNumber: fn, origin: origin ?? "", date: date ?? "" };
    }),
  );

  // Fetch flight list for selector
  const { data: flights, refetch: refetchFlights, isFetching: flightsFetching } = useQuery({
    queryKey: ["flights"],
    queryFn: () => fetchFlights(),
  });

  return (
    <div className="flex-1 flex flex-col">
      {/* Nav */}
      <div className="border-b border-border px-6 py-3 flex items-center gap-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Flights
        </Link>
        <h1 className="text-sm font-semibold ml-4">Flight Comparison</h1>
        <div className="flex-1" />
        <button
          onClick={() => refetchFlights()}
          disabled={flightsFetching}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${flightsFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="flex-1 px-6 py-5 max-w-7xl mx-auto w-full space-y-6">
        {/* Flight Selector */}
        <FlightSelector
          flights={flights ?? []}
          selected={selected}
          onToggle={(f) => {
            const key = `${f.flightNumber}:${f.origin}:${f.departureDate}`;
            const exists = selected.find(
              (s) =>
                `${s.flightNumber}:${s.origin}:${s.date}` === key,
            );
            if (exists) {
              setSelected(
                selected.filter(
                  (s) =>
                    `${s.flightNumber}:${s.origin}:${s.date}` !== key,
                ),
              );
            } else {
              setSelected([
                ...selected,
                {
                  flightNumber: f.flightNumber,
                  origin: f.origin,
                  date: f.departureDate,
                },
              ]);
            }
          }}
        />

        {/* Comparison Grid */}
        {selected.length > 0 && (
          <ComparisonGrid selected={selected} />
        )}

        {selected.length === 0 && (
          <div className="text-center py-20 text-muted-foreground">
            <Users className="h-10 w-10 mx-auto mb-3 opacity-40" />
            <p>Select flights above to compare</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────── FLIGHT SELECTOR ─────────── */
function FlightSelector({
  flights,
  selected,
  onToggle,
}: {
  flights: FlightListItem[];
  selected: { flightNumber: string; origin: string; date: string }[];
  onToggle: (f: FlightListItem) => void;
}) {
  const [open, setOpen] = useState(true);

  return (
    <Card>
      <CardContent className="py-4">
        <button
          onClick={() => setOpen(!open)}
          className="w-full flex items-center justify-between"
        >
          <div className="flex items-center gap-2">
            <Plane className="h-4 w-4 text-amber-400" />
            <span className="text-sm font-semibold">
              Select Flights to Compare
            </span>
            <Badge variant="secondary" className="text-xs">
              {selected.length} selected
            </Badge>
          </div>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>

        {open && (
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {flights.map((f) => {
              const key = `${f.flightNumber}:${f.origin}:${f.departureDate}`;
              const isSelected = selected.some(
                (s) =>
                  `${s.flightNumber}:${s.origin}:${s.date}` === key,
              );
              const onBoard = f.operationalSummary?.soulsOnBoard ?? 0;
              return (
                <button
                  key={key}
                  onClick={() => onToggle(f)}
                  className={`flex items-center gap-3 rounded-lg border p-3 text-left transition-colors ${
                    isSelected
                      ? "border-amber-400/50 bg-amber-400/5"
                      : "border-border hover:border-muted-foreground/30"
                  }`}
                >
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border ${
                      isSelected
                        ? "border-amber-400 bg-amber-400 text-background"
                        : "border-muted-foreground/30"
                    }`}
                  >
                    {isSelected && <Check className="h-3.5 w-3.5" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-bold">
                        {f.airline}
                        {f.flightNumber}
                      </span>
                      <Badge
                        variant="outline"
                        className="text-[10px] px-1 py-0"
                      >
                        {f.status}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                      <span>{f.origin}</span>
                      <ArrowRight className="h-2.5 w-2.5" />
                      <span>{f.destination || "—"}</span>
                      <span className="ml-1">{f.departureDate}</span>
                    </div>
                  </div>
                  <div className="text-right text-xs tabular-nums">
                    <div className="font-bold">{onBoard}</div>
                    <div className="text-muted-foreground">on board</div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ─────────── COMPARISON GRID ─────────── */
function ComparisonGrid({
  selected,
}: {
  selected: { flightNumber: string; origin: string; date: string }[];
}) {
  return (
    <div className="space-y-6">
      <div
        className="grid gap-5"
        style={{
          gridTemplateColumns: `repeat(${Math.min(selected.length, 3)}, 1fr)`,
        }}
      >
        {selected.map((s) => (
          <ComparisonColumn
            key={`${s.flightNumber}:${s.origin}:${s.date}`}
            flightNumber={s.flightNumber}
            origin={s.origin}
            date={s.date}
          />
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Plane className="h-4 w-4 text-cyan-400" />
          <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Backend Tree Comparison
          </h2>
        </div>
        <div className="space-y-4">
          {selected.map((s) => (
            <ComparisonTreePanel
              key={`tree:${s.flightNumber}:${s.origin}:${s.date}`}
              flightNumber={s.flightNumber}
              origin={s.origin}
              date={s.date}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ComparisonTreePanel({
  flightNumber,
  origin,
  date,
}: {
  flightNumber: string;
  origin: string;
  date: string;
}) {
  const { data: tree, isLoading, error, refetch: refetchTree, isFetching: treeFetching } = useQuery({
    queryKey: ["tree", flightNumber, origin, date],
    queryFn: () => fetchFlightTree(flightNumber, origin, date),
  });

  return (
    <Card className="overflow-hidden">
      <CardContent className="py-5 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-base font-semibold text-foreground">
              GF{flightNumber}
            </div>
            <div className="text-xs text-muted-foreground">
              {origin} • {date}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => refetchTree()}
              disabled={treeFetching}
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${treeFetching ? "animate-spin" : ""}`} />
            </button>
            <Badge variant="outline" className="border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
              Tree API
            </Badge>
          </div>
        </div>

        {isLoading && (
          <div className="flex min-h-[220px] items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading tree...
          </div>
        )}

        {!isLoading && (error || !tree) && (
          <div className="flex min-h-[220px] items-center justify-center text-destructive">
            <AlertCircle className="mr-2 h-4 w-4" />
            Failed to load tree
          </div>
        )}

        {tree && <PassengerTree tree={tree} />}
      </CardContent>
    </Card>
  );
}

/* ─────────── SINGLE FLIGHT COLUMN ─────────── */
function ComparisonColumn({
  flightNumber,
  origin,
  date,
}: {
  flightNumber: string;
  origin: string;
  date: string;
}) {
  const { data, isLoading, error, refetch: refetchDash, isFetching: dashFetching } = useQuery({
    queryKey: ["dashboard", flightNumber, origin, date],
    queryFn: () => fetchDashboard(flightNumber, origin, date),
  });

  if (isLoading) {
    return (
      <Card className="min-h-[400px] flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card className="min-h-[400px] flex flex-col items-center justify-center gap-3 text-destructive">
        <div className="flex items-center">
          <AlertCircle className="h-5 w-5 mr-2" />
          Failed to load
        </div>
        <button
          onClick={() => refetchDash()}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </button>
      </Card>
    );
  }

  const fs = data.flightStatus;
  const a = data.analysis;
  const ps = data.passengerSummary;
  const overview = data.overview;
  const state = data.stateSummary;
  const route = data.route;
  const ep = a.economy.passengers;
  const bp = a.business.passengers;
  const es = a.economy.staff;

  return (
    <div className="space-y-3">
      {/* Flight header card */}
      <Card>
        <CardContent className="py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500/15">
              <Plane className="h-4 w-4 text-amber-400" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold">
                  GF{flightNumber}
                </span>
                <Badge
                  variant="outline"
                  className="bg-emerald-500/15 text-emerald-400 border-emerald-500/30 text-[10px]"
                >
                  {fs?.status}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground flex items-center gap-1.5 mt-0.5">
                <span className="font-medium text-foreground">{route.origin}</span>
                <ArrowRight className="h-3 w-3" />
                <span>{route.destination || "—"}</span>
                <span className="ml-1">{date}</span>
              </div>
            </div>
            <button
              onClick={() => refetchDash()}
              disabled={dashFetching}
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${dashFetching ? "animate-spin" : ""}`} />
            </button>
          </div>
          {fs?.aircraft && (
            <div className="flex items-center gap-2 mt-3 text-xs border border-border rounded-md px-3 py-1.5 w-fit">
              <Plane className="h-3.5 w-3.5 text-cyan-400" />
              <span className="font-semibold">{fs.aircraft.type}</span>
              {fs.aircraft.registration && (
                <span className="text-muted-foreground">
                  · {fs.aircraft.registration}
                </span>
              )}
              {fs.gate && (
                <span className="text-muted-foreground">
                  · Gate {fs.gate}
                </span>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2">
        <MiniStat value={overview.soulsOnBoard} label="SOB" accent="text-foreground" />
        <MiniStat value={overview.economySouls} label="Economy" accent="text-emerald-400" />
        <MiniStat value={overview.businessSouls} label="Business" accent="text-amber-400" />
      </div>

      {/* State breakdown */}
      <Card>
        <CardContent className="py-4 space-y-1">
          <CompareRow label="Boarded" value={state.boarded.totalPassengers} cls="text-destructive font-bold" />
          <CompareRow label="Checked-In" value={state.checkedIn.totalPassengers} />
          <CompareRow label="Not Checked-In" value={state.booked.totalPassengers} cls={state.booked.totalPassengers > 0 ? "text-amber-400" : ""} />
          <CompareRow label="Revenue" value={a.revenue} />
          <CompareRow label="Non-Revenue" value={state.others.nonRevenue} cls={state.others.nonRevenue > 0 ? "text-purple-400" : ""} />
          <div className="border-t border-border my-2" />
          <CompareRow label="Total Records" value={overview.manifestRecords} cls="font-bold" />
          <CompareRow label="Infants (lap)" value={ps.infantCount} cls="text-amber-400" />
          <CompareRow label="Total Souls" value={overview.totalSouls} cls="font-bold" />
        </CardContent>
      </Card>

      {/* Gender / Age breakdown */}
      <Card>
        <CardContent className="py-4">
          <div className="text-[10px] font-bold tracking-widest uppercase text-muted-foreground mb-3">
            Passenger Breakdown
          </div>

          {/* Economy */}
          <div className="mb-3">
            <div className="text-xs font-semibold text-emerald-400 mb-1.5">
              Economy ({a.economy.total})
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              <BadgeBox type="M" value={ep.male} />
              <BadgeBox type="F" value={ep.female} />
              <BadgeBox type="C" value={ep.children} />
              <BadgeBox type="I" value={ep.infants} />
            </div>
            {es.total > 0 && (
              <div className="mt-1.5 text-xs text-purple-400">
                Staff: {es.total} ({es.male > 0 ? `M:${es.male} ` : ""}{es.female > 0 ? `F:${es.female}` : ""})
              </div>
            )}
          </div>

          {/* Business */}
          <div>
            <div className="text-xs font-semibold text-amber-400 mb-1.5">
              Business ({a.business.total})
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              <BadgeBox type="M" value={bp.male} />
              <BadgeBox type="F" value={bp.female} />
              <BadgeBox type="C" value={bp.children} />
              <BadgeBox type="I" value={bp.infants} />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Totals */}
      <Card>
        <CardContent className="py-4">
          <div className="text-[10px] font-bold tracking-widest uppercase text-muted-foreground mb-3">
            Totals
          </div>
          <div className="grid grid-cols-4 gap-1.5">
            <BadgeBox type="M" value={a.totalMale} />
            <BadgeBox type="F" value={a.totalFemale} />
            <BadgeBox type="C" value={a.totalChildren} />
            <BadgeBox type="I" value={ps.infantCount} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ─────────── HELPERS ─────────── */
function MiniStat({
  value,
  label,
  accent,
}: {
  value: number;
  label: string;
  accent: string;
}) {
  return (
    <Card>
      <CardContent className="py-3 text-center">
        <div className={`text-xl font-bold tabular-nums ${accent}`}>
          {value}
        </div>
        <div className="text-[10px] text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

function CompareRow({
  label,
  value,
  cls,
}: {
  label: string;
  value: number;
  cls?: string;
}) {
  return (
    <div className="flex justify-between py-0.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={`tabular-nums ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

const badgeStyles: Record<string, { bg: string; text: string; label: string }> = {
  M: { bg: "bg-blue-500/15", text: "text-blue-400", label: "Male" },
  F: { bg: "bg-pink-500/15", text: "text-pink-400", label: "Female" },
  C: { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "Children" },
  I: { bg: "bg-amber-500/15", text: "text-amber-400", label: "Infants" },
};

function BadgeBox({ type, value }: { type: string; value: number }) {
  const s = badgeStyles[type];
  return (
    <div
      className={`rounded-md ${s.bg} px-2 py-1.5 text-center`}
    >
      <div className={`text-sm font-bold tabular-nums ${s.text}`}>{value}</div>
      <div className="text-[9px] text-muted-foreground">{s.label}</div>
    </div>
  );
}
