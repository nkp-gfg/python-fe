"use client";

import { startTransition, useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  ArrowRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Database,
  Gauge,
  GitCompareArrows,
  Loader2,
  Network,
  Search,
  PanelLeft,
  Plane,
  Radar,
  ScanSearch,
} from "lucide-react";

import { fetchDashboard, fetchFlightTree, fetchFlights } from "@/lib/api";
import type { FlightDashboard, FlightListItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { StatePanels } from "@/components/dashboard/state-panels";
import { PassengerTree } from "@/components/dashboard/passenger-tree";

type FlightSelection = {
  flightNumber: string;
  origin: string;
  date: string;
};

interface FlightWorkbenchProps {
  initialSelection?: FlightSelection;
}

export function FlightWorkbench({ initialSelection }: FlightWorkbenchProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [mobileRailOpen, setMobileRailOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<FlightSelection | null>(initialSelection ?? null);

  const {
    data: flights,
    isLoading: flightsLoading,
    error: flightsError,
  } = useQuery({
    queryKey: ["flights"],
    queryFn: () => fetchFlights(),
    refetchInterval: 30_000,
  });

  const effectiveSelected =
    selected ??
    initialSelection ??
    (flights && flights.length > 0
      ? {
          flightNumber: flights[0].flightNumber,
          origin: flights[0].origin,
          date: flights[0].departureDate,
        }
      : null);

  const deferredSelected = useDeferredValue(effectiveSelected);

  const {
    data: dashboard,
    isLoading: dashboardLoading,
    error: dashboardError,
  } = useQuery({
    queryKey: [
      "dashboard",
      deferredSelected?.flightNumber,
      deferredSelected?.origin,
      deferredSelected?.date,
    ],
    queryFn: () =>
      fetchDashboard(
        deferredSelected!.flightNumber,
        deferredSelected!.origin,
        deferredSelected!.date,
      ),
    enabled: Boolean(deferredSelected),
    refetchInterval: 30_000,
  });

  const {
    data: tree,
    isLoading: treeLoading,
  } = useQuery({
    queryKey: [
      "tree",
      deferredSelected?.flightNumber,
      deferredSelected?.origin,
      deferredSelected?.date,
    ],
    queryFn: () =>
      fetchFlightTree(
        deferredSelected!.flightNumber,
        deferredSelected!.origin,
        deferredSelected!.date,
      ),
    enabled: Boolean(deferredSelected),
    refetchInterval: 30_000,
  });

  const selectedFlight =
    flights?.find(
      (flight) =>
        flight.flightNumber === effectiveSelected?.flightNumber &&
        flight.origin === effectiveSelected?.origin &&
        flight.departureDate === effectiveSelected?.date,
    ) ?? null;

  const filteredFlights = (flights ?? []).filter((flight) => {
    const haystack = [
      flight.airline,
      flight.flightNumber,
      flight.origin,
      flight.destination,
      flight.status,
      flight.aircraft?.registration,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(search.toLowerCase());
  });

  function selectFlight(flight: FlightListItem) {
    const next = {
      flightNumber: flight.flightNumber,
      origin: flight.origin,
      date: flight.departureDate,
    };

    startTransition(() => {
      setSelected(next);
      setMobileRailOpen(false);
    });

    const nextHref = `/flights/${flight.flightNumber}?origin=${flight.origin}&date=${flight.departureDate}`;
    if (pathname !== nextHref) {
      router.replace(nextHref, { scroll: false });
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#050816] text-foreground">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(52,211,153,0.12),transparent_28%),radial-gradient(circle_at_80%_20%,rgba(56,189,248,0.12),transparent_24%),radial-gradient(circle_at_bottom_right,rgba(245,158,11,0.10),transparent_22%)]" />
      <div className="pointer-events-none absolute inset-0 opacity-[0.08] [background-image:linear-gradient(rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.08)_1px,transparent_1px)] [background-size:72px_72px]" />

      <div className="relative flex h-screen overflow-hidden">
        <aside
          className={cn(
            "hidden border-r border-white/8 bg-[#09101d]/92 backdrop-blur-xl md:flex md:flex-col transition-[width] duration-300",
            sidebarCollapsed ? "w-[72px]" : "w-[250px]",
          )}
        >
          <div className="border-b border-white/8 px-3 py-3">
            <div className="flex items-center gap-2">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-300 via-amber-400 to-orange-500 text-[#111827] shadow-[0_12px_30px_rgba(245,158,11,0.28)]">
                <Plane className="h-4 w-4" />
              </div>
              {!sidebarCollapsed && (
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold tracking-tight text-white">
                    FalconEye Ops
                  </div>
                  <div className="text-[11px] text-slate-400">
                    Live manifest workspace
                  </div>
                </div>
              )}
              <Button
                variant="ghost"
                size="icon-sm"
                className="text-slate-300 hover:bg-white/8 hover:text-white"
                onClick={() => setSidebarCollapsed((value) => !value)}
                aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              >
                {sidebarCollapsed ? (
                  <ChevronRight className="h-4 w-4" />
                ) : (
                  <ChevronLeft className="h-4 w-4" />
                )}
              </Button>
            </div>

            {!sidebarCollapsed && (
              <div className="mt-3 space-y-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search flight, route, status"
                    className="h-8 w-full rounded-lg border border-white/8 bg-black/20 pl-9 pr-3 text-xs text-white outline-none placeholder:text-slate-500 focus:border-cyan-400/35"
                  />
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                <RailStat
                  icon={<Radar className="h-3 w-3" />}
                  label="Flights"
                  value={String(flights?.length ?? 0)}
                />
                <RailStat
                  icon={<Activity className="h-3 w-3" />}
                  label="Refresh"
                  value="30s"
                />
                </div>
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-2 py-2">
            {flightsLoading && (
              <div className="flex h-full items-center justify-center text-sm text-slate-400">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading flights...
              </div>
            )}

            {flightsError && (
              <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm text-rose-300">
                Failed to load flights from the database.
              </div>
            )}

            <div className="space-y-2">
              {filteredFlights.map((flight) => {
                const active =
                  flight.flightNumber === effectiveSelected?.flightNumber &&
                  flight.origin === effectiveSelected?.origin &&
                  flight.departureDate === effectiveSelected?.date;
                return (
                  <button
                    key={`${flight.flightNumber}-${flight.origin}-${flight.departureDate}`}
                    type="button"
                    onClick={() => selectFlight(flight)}
                    className={cn(
                      "w-full rounded-xl border px-2.5 py-2 text-left transition-all duration-200",
                      active
                        ? "border-cyan-400/40 bg-gradient-to-br from-cyan-500/16 via-sky-500/10 to-emerald-500/10 shadow-[0_8px_24px_rgba(56,189,248,0.12)]"
                        : "border-white/8 bg-white/[0.03] hover:border-white/14 hover:bg-white/[0.05]",
                    )}
                  >
                    <div className="flex items-start gap-2">
                      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/6 text-amber-300">
                        <Plane className="h-3.5 w-3.5" />
                      </div>
                      {!sidebarCollapsed && (
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-1">
                            <div className="truncate text-xs font-semibold text-white">
                              {flight.airline}
                              {flight.flightNumber}
                            </div>
                            <Badge className={cn("border px-1.5 py-0 text-[9px]", statusTone(flight.status))}>
                              {flight.status || "UNKNOWN"}
                            </Badge>
                          </div>
                          <div className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-400">
                            <span>{flight.origin}</span>
                            <ArrowRight className="h-2.5 w-2.5" />
                            <span>{flight.destination || "—"}</span>
                          </div>
                          <div className="mt-1.5 grid grid-cols-3 gap-1 text-[10px] text-slate-400">
                            <FlightChip label="Date" value={flight.departureDate} />
                            <FlightChip label="SOB" value={String(flight.operationalSummary?.soulsOnBoard ?? 0)} />
                            <FlightChip label="Rec" value={String(flight.passengerSummary?.totalPassengers ?? 0)} />
                          </div>
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="border-b border-white/8 bg-[#070c18]/82 px-4 py-3 backdrop-blur-xl sm:px-6">
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="icon-sm"
                className="md:hidden border-white/10 bg-white/5 text-slate-200 hover:bg-white/10"
                onClick={() => setMobileRailOpen(true)}
              >
                <PanelLeft className="h-4 w-4" />
              </Button>
              <div>
                <div className="text-sm font-medium text-slate-200">Flight Console</div>
                <div className="text-xs text-slate-500">
                  Database-backed live passenger and load view
                </div>
              </div>
              <div className="ml-auto flex items-center gap-2 text-xs text-emerald-300">
                <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_18px_rgba(52,211,153,0.6)]" />
                LIVE · refreshing every 30s
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-3 sm:px-4 sm:py-4">
            {dashboardLoading && (
              <div className="flex h-full min-h-[50vh] items-center justify-center text-slate-400">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Loading selected flight...
              </div>
            )}

            {dashboardError && (
              <div className="flex min-h-[50vh] items-center justify-center">
                <Card className="border-rose-500/20 bg-rose-500/10 text-rose-200">
                  <CardContent className="py-6">
                    Failed to load flight dashboard.
                  </CardContent>
                </Card>
              </div>
            )}

            {!dashboardLoading && !dashboardError && dashboard && effectiveSelected && (
              <FlightCenterPanel
                dashboard={dashboard}
                selected={effectiveSelected}
                selectedFlight={selectedFlight}
                onOpenTree={() => setTreeOpen((v) => !v)}
              />
            )}
          </div>
        </main>

        {/* ── Right tree sidebar (inline, not overlay) ── */}
        {treeOpen && (
          <aside className="hidden w-[420px] shrink-0 border-l border-white/8 bg-[#07101c]/96 md:flex md:flex-col transition-[width] duration-300">
            <div className="border-b border-white/8 px-4 py-3 flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2 text-sm font-medium text-white">
                  <Network className="h-4 w-4 text-cyan-300" />
                  Passenger Tree
                </div>
                <div className="text-[11px] text-slate-400">Live database view</div>
              </div>
              <Button
                variant="ghost"
                size="icon-sm"
                className="text-slate-300 hover:bg-white/8 hover:text-white"
                onClick={() => setTreeOpen(false)}
                aria-label="Close tree panel"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              {treeLoading && (
                <div className="flex items-center justify-center py-20 text-sm text-slate-400">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Loading tree...
                </div>
              )}
              {!treeLoading && tree ? (
                <PassengerTree tree={tree} />
              ) : (
                !treeLoading && <div className="text-sm text-slate-400">No tree data available.</div>
              )}
            </div>
          </aside>
        )}
      </div>

      <Sheet open={mobileRailOpen} onOpenChange={setMobileRailOpen}>
        <SheetContent
          side="left"
          className="w-[min(24rem,92vw)] max-w-none border-r border-white/10 bg-[#09101d]/96 p-0 text-white backdrop-blur-2xl md:hidden"
        >
          <SheetHeader className="border-b border-white/8 px-4 py-4">
            <SheetTitle className="text-white">Flights</SheetTitle>
            <SheetDescription className="text-slate-400">
              Select a flight from the database list.
            </SheetDescription>
          </SheetHeader>

          <div className="max-h-full overflow-y-auto p-3">
            <div className="space-y-2">
              {filteredFlights.map((flight) => {
                const active =
                  flight.flightNumber === effectiveSelected?.flightNumber &&
                  flight.origin === effectiveSelected?.origin &&
                  flight.departureDate === effectiveSelected?.date;
                return (
                  <button
                    key={`mobile-${flight.flightNumber}-${flight.origin}-${flight.departureDate}`}
                    type="button"
                    onClick={() => selectFlight(flight)}
                    className={cn(
                      "w-full rounded-2xl border px-3 py-3 text-left transition-all duration-200",
                      active
                        ? "border-cyan-400/40 bg-gradient-to-br from-cyan-500/16 via-sky-500/10 to-emerald-500/10 shadow-[0_14px_40px_rgba(56,189,248,0.15)]"
                        : "border-white/8 bg-white/[0.03] hover:border-white/14 hover:bg-white/[0.05]",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-white/6 text-amber-300">
                        <Plane className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="truncate text-sm font-semibold text-white">
                            {flight.airline}
                            {flight.flightNumber}
                          </div>
                          <Badge className={cn("border px-2 py-0 text-[10px]", statusTone(flight.status))}>
                            {flight.status || "UNKNOWN"}
                          </Badge>
                        </div>
                        <div className="mt-1 flex items-center gap-1 text-xs text-slate-400">
                          <span>{flight.origin}</span>
                          <ArrowRight className="h-3 w-3" />
                          <span>{flight.destination || "Pending"}</span>
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                          <FlightChip label="SOB" value={String(flight.operationalSummary?.soulsOnBoard ?? 0)} />
                          <FlightChip label="Records" value={String(flight.passengerSummary?.totalPassengers ?? 0)} />
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function FlightCenterPanel({
  dashboard,
  selected,
  selectedFlight,
  onOpenTree,
}: {
  dashboard: FlightDashboard;
  selected: FlightSelection;
  selectedFlight: FlightListItem | null;
  onOpenTree: () => void;
}) {
  const status = dashboard.flightStatus;
  const overview = dashboard.overview;
  const route = dashboard.route;

  return (
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-3">
      <Card className="overflow-hidden border-white/10 bg-[linear-gradient(135deg,rgba(12,20,35,0.96),rgba(8,15,28,0.9))] shadow-[0_20px_80px_rgba(0,0,0,0.35)]">
        <CardContent className="relative px-4 py-4 sm:px-5 sm:py-5">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(56,189,248,0.16),transparent_24%),radial-gradient(circle_at_bottom_left,rgba(245,158,11,0.12),transparent_18%)]" />
          <div className="relative flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
            <div className="flex items-start gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-amber-300 via-amber-400 to-orange-500 text-[#101827] shadow-[0_20px_45px_rgba(245,158,11,0.28)]">
                <Plane className="h-7 w-7" />
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                    GF{selected.flightNumber}
                  </h1>
                  <Badge className={cn("border px-2.5 py-0.5 text-xs", statusTone(status?.status ?? ""))}>
                    {status?.status || "No status"}
                  </Badge>
                  <Badge className="border border-white/10 bg-white/5 px-2.5 py-0.5 text-xs text-slate-300">
                    {selected.date}
                  </Badge>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-400">
                  <span className="font-medium text-slate-100">{route.origin || status?.origin || selected.origin}</span>
                  <ArrowRight className="h-4 w-4 text-slate-500" />
                  <span>{route.destination || selectedFlight?.destination || "Destination pending"}</span>
                  <span className="text-slate-600">•</span>
                  <span>Gate {status?.gate || "TBD"}</span>
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-3">
                  <HeroPill
                    icon={<Gauge className="h-4 w-4" />}
                    label="Souls on board"
                    value={String(overview.soulsOnBoard)}
                  />
                  <HeroPill
                    icon={<Database className="h-4 w-4" />}
                    label="Manifest records"
                    value={String(overview.manifestRecords)}
                  />
                  <HeroPill
                    icon={<GitCompareArrows className="h-4 w-4" />}
                    label="Tracked changes"
                    value={String(overview.trackedChanges)}
                  />
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-3 xl:items-end">
              <div className="grid grid-cols-2 gap-2">
                <MetaCard
                  label="Aircraft"
                  value={status?.aircraft?.type || selectedFlight?.aircraft?.type || "—"}
                />
                <MetaCard
                  label="Registration"
                  value={status?.aircraft?.registration || selectedFlight?.aircraft?.registration || "—"}
                />
                <MetaCard
                  label="Scheduled dep"
                  value={status?.schedule?.scheduledDeparture || "—"}
                />
                <MetaCard
                  label="Estimated arr"
                  value={status?.schedule?.estimatedArrival || "—"}
                />
              </div>
              <Button
                size="icon-lg"
                className="bg-gradient-to-r from-cyan-400 to-sky-500 text-slate-950 hover:from-cyan-300 hover:to-sky-400"
                onClick={onOpenTree}
                aria-label="Open passenger tree"
              >
                <Network className="h-5 w-5" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <StatePanels stateSummary={dashboard.stateSummary} />

      <FlightInsights dashboard={dashboard} />
    </div>
  );
}

function FlightInsights({ dashboard }: { dashboard: FlightDashboard }) {
  const summary = dashboard.passengerSummary;
  const analysis = dashboard.analysis;
  const changes = Object.entries(dashboard.changeSummary ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {/* Demographics */}
      <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
        <CardContent className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2 border-b border-white/8 pb-2">
            <Activity className="h-3.5 w-3.5 text-cyan-300" />
            <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">Demographics</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <InsightTile label="Adults" value={summary.adultCount} tone="text-white" />
            <InsightTile label="Children" value={summary.childCount} tone="text-emerald-300" />
            <InsightTile label="Infants" value={summary.infantCount} tone="text-amber-300" />
            <InsightTile label="Non-rev" value={analysis.nonRevenue} tone="text-fuchsia-300" />
          </div>
        </CardContent>
      </Card>

      {/* Cabin mix */}
      <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
        <CardContent className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2 border-b border-white/8 pb-2">
            <ScanSearch className="h-3.5 w-3.5 text-cyan-300" />
            <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">Cabin Mix</span>
          </div>
          <div className="space-y-3">
            <MixRow label="Economy" value={analysis.economy.total} total={summary.totalPassengers || 1} barClass="from-emerald-400 to-cyan-400" />
            <MixRow label="Business" value={analysis.business.total} total={summary.totalPassengers || 1} barClass="from-amber-300 to-orange-400" />
          </div>
        </CardContent>
      </Card>

      {/* Operational posture */}
      <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
        <CardContent className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2 border-b border-white/8 pb-2">
            <CalendarDays className="h-3.5 w-3.5 text-cyan-300" />
            <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">Operational</span>
          </div>
          <div className="space-y-1.5 text-sm">
            <InsightLine label="Checked in" value={analysis.checkedIn} />
            <InsightLine label="Boarded" value={analysis.boarded} />
            <InsightLine label="Not checked in" value={analysis.notCheckedIn} valueTone={analysis.notCheckedIn > 0 ? "text-amber-300" : "text-white"} />
            <InsightLine label="Revenue" value={analysis.revenue} />
          </div>
        </CardContent>
      </Card>

      {/* Change summary */}
      <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
        <CardContent className="px-5 py-4">
          <div className="mb-3 flex items-center gap-2 border-b border-white/8 pb-2">
            <Database className="h-3.5 w-3.5 text-cyan-300" />
            <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">Changes</span>
          </div>
          <div className="space-y-1.5 text-sm">
            {changes.length > 0 ? (
              changes.slice(0, 5).map(([label, value]) => (
                <div key={label} className="flex items-center justify-between">
                  <span className="text-slate-400">{label}</span>
                  <span className="font-semibold text-white">{value}</span>
                </div>
              ))
            ) : (
              <div className="text-sm text-slate-500">No tracked changes yet.</div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function RailStat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-2">
      <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-0.5 text-xs font-semibold text-white">{value}</div>
    </div>
  );
}

function FlightChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/8 bg-black/20 px-1.5 py-1">
      <div className="text-[8px] uppercase tracking-[0.18em] text-slate-500">{label}</div>
      <div className="mt-0.5 truncate text-[11px] font-medium text-slate-200">{value}</div>
    </div>
  );
}

function HeroPill({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/8 bg-black/20 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.22em] text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-0.5 text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/8 bg-black/20 px-2.5 py-2">
      <div className="text-[9px] uppercase tracking-[0.2em] text-slate-500">{label}</div>
      <div className="mt-0.5 truncate text-xs font-medium text-white">{value}</div>
    </div>
  );
}

function InsightTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="rounded-xl border border-white/8 bg-black/20 px-2.5 py-2">
      <div className="text-[9px] uppercase tracking-[0.18em] text-slate-500">{label}</div>
      <div className={cn("mt-0.5 text-lg font-semibold", tone)}>{value}</div>
    </div>
  );
}

function InsightLine({
  label,
  value,
  valueTone,
}: {
  label: string;
  value: number;
  valueTone?: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-400">{label}</span>
      <span className={cn("font-semibold text-white", valueTone)}>{value}</span>
    </div>
  );
}

function MixRow({
  label,
  value,
  total,
  barClass,
}: {
  label: string;
  value: number;
  total: number;
  barClass: string;
}) {
  const width = Math.max(6, Math.round((value / total) * 100));

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="text-slate-400">{label}</span>
        <span className="font-semibold text-white">{value}</span>
      </div>
      <div className="h-2.5 rounded-full bg-white/6">
        <div
          className={cn("h-full rounded-full bg-gradient-to-r", barClass)}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

function statusTone(status: string) {
  switch (status) {
    case "PDC":
      return "bg-emerald-400/10 text-emerald-300 border-emerald-400/25";
    case "OPENCI":
      return "bg-cyan-400/10 text-cyan-300 border-cyan-400/25";
    case "BOARDING":
      return "bg-amber-400/10 text-amber-200 border-amber-400/25";
    case "DEPARTED":
      return "bg-fuchsia-400/10 text-fuchsia-300 border-fuchsia-400/25";
    default:
      return "bg-white/6 text-slate-300 border-white/10";
  }
}
