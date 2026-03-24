"use client";

import { startTransition, useDeferredValue, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient, useIsMutating } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  ArrowRight,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  ChevronsLeft,
  ChevronsRight,
  CloudDownload,
  Database,
  Filter,
  History,
  Info,
  LayoutDashboard,
  Loader2,
  Menu,
  Network,
  Plane,
  Radar,
  RefreshCw,
  Search,
  ShieldAlert,
  Timer,
  UserCheck,
  Users,
  Clock,
  Briefcase,
  Ticket,
  Star,
  Globe,
  X,
} from "lucide-react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { useDebounce } from "@/lib/hooks";

import { fetchDashboard, fetchFlightTree, fetchFlights, ingestFlight } from "@/lib/api";
import type { FlightListItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogFooter,
  AlertDialogTitle, AlertDialogDescription, AlertDialogAction, AlertDialogCancel,
} from "@/components/ui/alert-dialog";

import { StatePanels } from "@/components/dashboard/state-panels";
import type { StateCardKey } from "@/components/dashboard/state-panels";
import { PhaseTimeline } from "@/components/dashboard/phase-timeline";
import { PhaseAlertBanner } from "@/components/dashboard/phase-alert-banner";
import { BottomDetailPanel } from "@/components/dashboard/bottom-detail-panel";
import type { DetailView } from "@/components/dashboard/bottom-detail-panel";
import { PassengerTree } from "@/components/dashboard/passenger-tree";
import { IngestionPanel } from "@/components/dashboard/ingestion-panel";
import { TileInfoPanel, type TileInfoKey } from "@/components/dashboard/tile-info-panel";
import { PassengerTable, type FilterCabin, type FilterStatus, type FilterType, type FilterLoyalty } from "@/components/dashboard/passenger-table";
import { StandbyPanel } from "@/components/dashboard/standby-panel";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import { ChangeTimeline } from "@/components/dashboard/change-timeline";
import { StatusHistory } from "@/components/dashboard/status-history";
import { ReservationView } from "@/components/dashboard/reservation-view";

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
  const queryClient = useQueryClient();
  const [treeOpen, setTreeOpen] = useState(false);
  const [mobileRailOpen, setMobileRailOpen] = useState(false);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [activeInfo, setActiveInfo] = useState<TileInfoKey>("booked");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search);
  const [dateFilter, setDateFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selected, setSelected] = useState<FlightSelection | null>(initialSelection ?? null);
  const [detailPnr, setDetailPnr] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [bottomView, setBottomView] = useState<DetailView | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "passengers" | "standby" | "changes" | "history" | "reservations">("overview");
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [filterCabin, setFilterCabin] = useState<FilterCabin>("all");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [filterType, setFilterType] = useState<FilterType>("all");
  const [filterLoyalty, setFilterLoyalty] = useState<FilterLoyalty>("all");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sidebarPanelRef = useRef<any>(null);

  const { data: flights, isLoading: flightsLoading, error: flightsError, refetch: refetchFlights, isFetching: flightsFetching } = useQuery({
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

  const { data: dashboard, isLoading: dashboardLoading, error: dashboardError, refetch: refetchDashboard, isFetching: dashboardFetching } = useQuery({
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

  const { data: tree, isLoading: treeLoading, refetch: refetchTree, isFetching: treeFetching } = useQuery({
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

  const [ingestingFlight, setIngestingFlight] = useState<string | null>(null);
  const [confirmFlight, setConfirmFlight] = useState<FlightListItem | null>(null);
  const ingestMutation = useMutation({
    mutationKey: ["ingest"],
    mutationFn: ingestFlight,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["flights"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["tree"] });
    },
    onSettled: () => setIngestingFlight(null),
  });

  /** Convert Sabre "2026-03-20T04:00PM" → ISO "2026-03-20T16:00:00" */
  function toIsoDateTime(raw: string | undefined, fallbackDate: string): string {
    if (!raw) return `${fallbackDate}T00:00:00`;
    const m = raw.match(/(\d{4}-\d{2}-\d{2})T(\d{1,2}):(\d{2})(AM|PM)?/i);
    if (!m) return `${fallbackDate}T00:00:00`;
    let [, datePart, hStr, min, ampm] = m;
    let h = parseInt(hStr, 10);
    if (ampm) {
      const up = ampm.toUpperCase();
      if (up === "PM" && h < 12) h += 12;
      if (up === "AM" && h === 12) h = 0;
    }
    return `${datePart}T${String(h).padStart(2, "0")}:${min}:00`;
  }

  function handleQuickIngest(flight: FlightListItem, e: React.MouseEvent) {
    e.stopPropagation();
    setConfirmFlight(flight);
  }

  function executeIngest() {
    if (!confirmFlight) return;
    const flight = confirmFlight;
    setConfirmFlight(null);
    const key = `${flight.flightNumber}-${flight.origin}-${flight.departureDate}`;
    setIngestingFlight(key);
    ingestMutation.mutate({
      airline: flight.airline,
      flightNumber: flight.flightNumber,
      origin: flight.origin,
      departureDate: flight.departureDate,
      departureDateTime: toIsoDateTime(flight.schedule?.scheduledDeparture, flight.departureDate),
    });
  }

  const selectedFlight =
    flights?.find(
      (flight) =>
        flight.flightNumber === effectiveSelected?.flightNumber &&
        flight.origin === effectiveSelected?.origin &&
        flight.departureDate === effectiveSelected?.date,
    ) ?? null;

  const availableDates = useMemo(() => {
    const dates = [...new Set((flights ?? []).map((f) => f.departureDate))].sort().reverse();
    return dates;
  }, [flights]);

  const availableStatuses = useMemo(() => {
    return [...new Set((flights ?? []).map((f) => f.status).filter(Boolean))].sort();
  }, [flights]);

  const filteredFlights = useMemo(() => {
    const list = (flights ?? []).filter((flight) => {
      if (dateFilter !== "all" && flight.departureDate !== dateFilter) return false;
      if (statusFilter !== "all" && flight.status !== statusFilter) return false;
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
      return haystack.includes(debouncedSearch.toLowerCase());
    });
    list.sort((a, b) => {
      const dateCmp = b.departureDate.localeCompare(a.departureDate);
      if (dateCmp !== 0) return dateCmp;
      return a.flightNumber.localeCompare(b.flightNumber);
    });
    return list;
  }, [flights, debouncedSearch, dateFilter, statusFilter]);

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

  function openTree() {
    setIngestOpen(false);
    setInfoOpen(false);
    setTreeOpen(true);
  }

  function openIngest() {
    setTreeOpen(false);
    setInfoOpen(false);
    setIngestOpen(true);
  }

  function openInfo(key: TileInfoKey) {
    setTreeOpen(false);
    setIngestOpen(false);
    setActiveInfo(key);
    setInfoOpen(true);
  }

  const activeIngestions = useIsMutating({ mutationKey: ["ingest"] });

  const isRefreshing = dashboardFetching || treeFetching;

  function refreshAll() {
    refetchFlights();
    if (deferredSelected) {
      refetchDashboard();
      refetchTree();
    }
  }

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-background text-foreground font-sans antialiased text-sm">
      {/* Top Navigation Bar */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b bg-card px-4 lg:px-6">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileRailOpen(true)}
          >
            <Menu className="h-5 w-5" />
          </Button>
          <div className="flex items-center gap-2 font-semibold">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-blue-600 text-white">
              <Plane className="h-4 w-4" />
            </div>
            <span className="hidden sm:inline-flex text-base tracking-tight text-card-foreground">FalconEye Ops</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400 sm:flex">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            Live Data
          </div>
          {activeIngestions > 0 && (
            <div className="flex items-center gap-1.5 rounded-full bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-600 dark:text-amber-400 animate-in fade-in">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>Ingesting{activeIngestions > 1 ? ` (${activeIngestions})` : "…"}</span>
            </div>
          )}
          <Separator orientation="vertical" className="hidden h-5 sm:block" />
          <Button
            variant="ghost"
            size="sm"
            onClick={refreshAll}
            disabled={isRefreshing}
            className="h-8 gap-2"
          >
            <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", isRefreshing && "animate-spin")} />
            <span className="hidden sm:inline">Refresh</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={openIngest}
            className="h-8 gap-2"
          >
            <Database className="h-3.5 w-3.5 text-muted-foreground" />
            Ingest Sabre
          </Button>
        </div>
      </header>

      {/* Mobile Flight Sidebar */}
      <Sheet open={mobileRailOpen} onOpenChange={setMobileRailOpen}>
        <SheetContent side="left" className="w-[300px] p-0">
          <div className="flex flex-col h-full">
            <div className="p-4 border-b space-y-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Flights</span>
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search flights..."
                  aria-label="Search flights"
                  className="w-full rounded-md border border-input bg-background px-9 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <CalendarDays className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
                    <select
                      value={dateFilter}
                      onChange={(e) => setDateFilter(e.target.value)}
                      aria-label="Filter by date"
                      className={cn(
                        "w-full appearance-none rounded-md border py-1.5 pl-7 pr-6 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                        dateFilter !== "all"
                          ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                          : "border-input bg-background"
                      )}
                    >
                      <option value="all">All dates</option>
                      {availableDates.map((d) => (
                        <option key={d} value={d}>{d}</option>
                      ))}
                    </select>
                    {dateFilter !== "all" && (
                      <button
                        onClick={() => setDateFilter("all")}
                        className="absolute right-1 top-1.5 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                        aria-label="Clear date filter"
                      >
                        <X className="h-3 w-3 text-blue-500" />
                      </button>
                    )}
                  </div>
                  <div className="relative flex-1">
                    <Filter className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
                    <select
                      value={statusFilter}
                      onChange={(e) => setStatusFilter(e.target.value)}
                      aria-label="Filter by status"
                      className={cn(
                        "w-full appearance-none rounded-md border py-1.5 pl-7 pr-6 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                        statusFilter !== "all"
                          ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                          : "border-input bg-background"
                      )}
                    >
                      <option value="all">All statuses</option>
                      {availableStatuses.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                    {statusFilter !== "all" && (
                      <button
                        onClick={() => setStatusFilter("all")}
                        className="absolute right-1 top-1.5 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                        aria-label="Clear status filter"
                      >
                        <X className="h-3 w-3 text-blue-500" />
                      </button>
                    )}
                  </div>
                </div>
                {(dateFilter !== "all" || statusFilter !== "all") && (
                  <button
                    onClick={() => { setDateFilter("all"); setStatusFilter("all"); }}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="h-3 w-3" />
                    Clear all filters
                  </button>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              {flightsLoading ? (
                <div className="flex h-full items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : (
                <div className="space-y-1">
                  {filteredFlights.map((flight, idx) => {
                    const showDateHeader =
                      idx === 0 || flight.departureDate !== filteredFlights[idx - 1].departureDate;
                    const isActive =
                      flight.flightNumber === effectiveSelected?.flightNumber &&
                      flight.origin === effectiveSelected?.origin &&
                      flight.departureDate === effectiveSelected?.date;
                    return (
                      <div key={`mob-${flight.flightNumber}-${flight.origin}-${flight.departureDate}`}>
                        {showDateHeader && (
                          <>
                            {idx !== 0 && <div className="my-2 border-t border-border" />}
                            <div className="flex items-center gap-2 px-1 pt-2 pb-1">
                              <CalendarDays className="h-3 w-3 text-muted-foreground" />
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{flight.departureDate}</span>
                            </div>
                          </>
                        )}
                      <button
                        onClick={() => selectFlight(flight)}
                        className={cn(
                          "w-full flex flex-col gap-2 rounded-lg p-3 text-left transition-colors",
                          isActive
                            ? "bg-primary text-primary-foreground shadow-sm"
                            : "hover:bg-accent hover:text-accent-foreground text-foreground"
                        )}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-base">{flight.airline}{flight.flightNumber}</span>
                          <div className="flex items-center gap-1.5">
                            <Badge
                              variant="outline"
                              className={cn(
                                "text-[10px] px-1.5 font-medium border-transparent",
                                isActive ? "bg-primary-foreground/20 text-primary-foreground" : getStatusColor(flight.flightPhase?.phase || flight.status)
                              )}
                            >
                              {flight.flightPhase?.label || flight.status || "UNKNOWN"}
                            </Badge>
                            <span
                              role="button"
                              tabIndex={0}
                              title="Re-ingest from Sabre"
                              onClick={(e) => handleQuickIngest(flight, e)}
                              className={cn("rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10 transition-colors", isActive && "hover:bg-primary-foreground/20")}
                            >
                              {ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <CloudDownload className="h-3.5 w-3.5" />
                              )}
                            </span>
                          </div>
                        </div>
                        <div className={cn("flex items-center gap-1.5 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                          <span>{flight.origin}</span>
                          <ArrowRight className="h-3 w-3" />
                          <span>{flight.destination || "Pending"}</span>
                          <span className="mx-1">&bull;</span>
                          <span>{flight.departureDate}</span>
                        </div>
                      </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* Main Content Area */}
      <PanelGroup orientation="horizontal" className="flex flex-1 overflow-hidden">
        {/* Sidebar Flights List */}
        <Panel defaultSize="14" minSize="10" maxSize="22" collapsible collapsedSize="0%" panelRef={sidebarPanelRef} className="hidden lg:flex flex-col h-full border-r bg-muted/30">
          <div className="p-4 border-b space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex-1">Flights</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => refetchFlights()}
                disabled={flightsFetching}
              >
                <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", flightsFetching && "animate-spin")} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => sidebarPanelRef.current?.collapse()}
                title="Collapse sidebar"
              >
                <ChevronsLeft className="h-3.5 w-3.5 text-muted-foreground" />
              </Button>
            </div>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search flights..."
                aria-label="Search flights"
                className="w-full rounded-md border border-input bg-background px-9 py-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <CalendarDays className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
                  <select
                    value={dateFilter}
                    onChange={(e) => setDateFilter(e.target.value)}
                    aria-label="Filter by date"
                    className={cn(
                      "w-full appearance-none rounded-md border py-1.5 pl-7 pr-6 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      dateFilter !== "all"
                        ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                        : "border-input bg-background"
                    )}
                  >
                    <option value="all">All dates</option>
                    {availableDates.map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                  {dateFilter !== "all" && (
                    <button
                      onClick={() => setDateFilter("all")}
                      className="absolute right-1 top-1.5 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                      aria-label="Clear date filter"
                    >
                      <X className="h-3 w-3 text-blue-500" />
                    </button>
                  )}
                </div>
                <div className="relative flex-1">
                  <Filter className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
                  <select
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    aria-label="Filter by status"
                    className={cn(
                      "w-full appearance-none rounded-md border py-1.5 pl-7 pr-6 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      statusFilter !== "all"
                        ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                        : "border-input bg-background"
                    )}
                  >
                    <option value="all">All statuses</option>
                    {availableStatuses.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  {statusFilter !== "all" && (
                    <button
                      onClick={() => setStatusFilter("all")}
                      className="absolute right-1 top-1.5 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                      aria-label="Clear status filter"
                    >
                      <X className="h-3 w-3 text-blue-500" />
                    </button>
                  )}
                </div>
              </div>
              {(dateFilter !== "all" || statusFilter !== "all") && (
                <button
                  onClick={() => { setDateFilter("all"); setStatusFilter("all"); }}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="h-3 w-3" />
                  Clear all filters
                </button>
              )}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            {flightsLoading ? (
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : flightsError ? (
              <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-center text-sm">
                <div className="font-semibold text-destructive mb-1">Backend Offline</div>
                <div className="text-destructive/80">Could not connect to API at 127.0.0.1:8000.</div>
              </div>
            ) : (
              <div className="space-y-1">
                {filteredFlights.map((flight, idx) => {
                  const showDateHeader =
                    idx === 0 || flight.departureDate !== filteredFlights[idx - 1].departureDate;
                  const isActive =
                    flight.flightNumber === effectiveSelected?.flightNumber &&
                    flight.origin === effectiveSelected?.origin &&
                    flight.departureDate === effectiveSelected?.date;
                  return (
                    <div key={`${flight.flightNumber}-${flight.origin}-${flight.departureDate}`}>
                      {showDateHeader && (
                        <>
                          {idx !== 0 && <div className="my-2 border-t border-border" />}
                          <div className="flex items-center gap-2 px-1 pt-2 pb-1">
                            <CalendarDays className="h-3 w-3 text-muted-foreground" />
                            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{flight.departureDate}</span>
                          </div>
                        </>
                      )}
                    <button
                      onClick={() => selectFlight(flight)}
                      className={cn(
                        "w-full flex flex-col gap-2 rounded-lg p-3 text-left transition-colors",
                        isActive
                          ? "bg-primary text-primary-foreground shadow-sm"
                          : "hover:bg-accent hover:text-accent-foreground text-foreground"
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-base">
                          {flight.airline}{flight.flightNumber}
                        </span>
                        <div className="flex items-center gap-1.5">
                          <Badge 
                            variant="outline" 
                            className={cn(
                              "text-[10px] px-1.5 font-medium border-transparent",
                              isActive 
                                ? "bg-primary-foreground/20 text-primary-foreground" 
                                : getStatusColor(flight.flightPhase?.phase || flight.status)
                            )}
                          >
                            {flight.flightPhase?.label || flight.status || "UNKNOWN"}
                          </Badge>
                          <span
                            role="button"
                            tabIndex={0}
                            title="Re-ingest from Sabre"
                            onClick={(e) => handleQuickIngest(flight, e)}
                            className={cn("rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10 transition-colors", isActive && "hover:bg-primary-foreground/20")}
                          >
                            {ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <CloudDownload className="h-3.5 w-3.5" />
                            )}
                          </span>
                        </div>
                      </div>
                      <div className={cn("flex items-center gap-1.5 text-xs", isActive ? "text-primary-foreground/80" : "text-muted-foreground")}>
                        <span>{flight.origin}</span>
                        <ArrowRight className="h-3 w-3" />
                        <span>{flight.destination || "Pending"}</span>
                        <span className="mx-1">•</span>
                        <span>{flight.departureDate}</span>
                      </div>
                    </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </Panel>

        <PanelResizeHandle className="relative w-1.5 border-r bg-border/50 hover:bg-primary/50 cursor-col-resize transition-all group/handle">
          <button
            onClick={() => sidebarPanelRef.current?.isCollapsed() ? sidebarPanelRef.current?.expand() : sidebarPanelRef.current?.collapse()}
            className="absolute top-1/2 -translate-y-1/2 -left-1.5 z-10 flex h-6 w-3 items-center justify-center rounded-sm bg-border hover:bg-primary text-muted-foreground hover:text-primary-foreground opacity-0 group-hover/handle:opacity-100 transition-opacity"
            title="Toggle sidebar"
          >
            <ChevronsLeft className="h-3 w-3" />
          </button>
        </PanelResizeHandle>

        {/* Vertical Navigation Rail */}
        <div className={cn(
          "hidden lg:flex flex-col border-r bg-muted/20 py-3 gap-0.5 shrink-0 transition-all duration-200",
          navCollapsed ? "w-12 items-center" : "w-36"
        )}>
          {([
            { key: "overview", icon: LayoutDashboard, label: "Overview" },
            { key: "passengers", icon: Users, label: "Passengers" },
            { key: "standby", icon: Timer, label: "Standby" },
            { key: "changes", icon: Activity, label: "Changes" },
            { key: "history", icon: History, label: "History" },
            { key: "reservations", icon: BookOpen, label: "Reservations" },
          ] as const).map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={cn(
                "flex items-center gap-2 w-full px-3 py-2 rounded-md transition-all text-xs",
                navCollapsed && "justify-center px-2",
                activeTab === key
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
              title={label}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!navCollapsed && <span className="truncate">{label}</span>}
            </button>
          ))}
          <div className="mt-auto pt-2">
            <button
              onClick={() => setNavCollapsed(!navCollapsed)}
              className="flex items-center justify-center w-full px-3 py-2 rounded-md text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-all"
              title={navCollapsed ? "Expand navigation" : "Collapse navigation"}
            >
              {navCollapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Console Workspace */}
        <Panel minSize="40">
          <main className="h-full w-full overflow-y-auto bg-muted/10 p-3 lg:p-4">
            <div className="mx-auto max-w-6xl space-y-3">
              {dashboardLoading && (
                <div className="flex min-h-[400px] items-center justify-center text-muted-foreground">
                  <Loader2 className="mr-2 h-6 w-6 animate-spin" />
                  Loading dashboard...
                </div>
              )}

              {dashboardError && (
                <Card className="border-destructive/20 bg-destructive/10">
                  <CardContent className="p-6 text-destructive flex items-center gap-3">
                    <Activity className="h-5 w-5" />
                    Failed to load flight dashboard data.
                  </CardContent>
                </Card>
              )}

              {!effectiveSelected && !flightsLoading && (
                <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted/50 mb-6">
                    <Plane className="h-8 w-8 text-muted-foreground" />
                  </div>
                  {flightsError ? (
                    <>
                      <h2 className="text-2xl font-semibold mb-2 text-foreground">Backend Disconnected</h2>
                      <p className="text-muted-foreground max-w-md">
                        We couldn&apos;t reach the FalconEye backend services. Please ensure the API is running locally and try again.
                      </p>
                    </>
                  ) : (
                    <>
                      <h2 className="text-2xl font-semibold mb-2 text-foreground">No flight selected</h2>
                      <p className="text-muted-foreground max-w-md">
                        Select a flight from the sidebar to view live operations data and passenger manifest.
                      </p>
                    </>
                  )}
                </div>
              )}

              {!dashboardLoading && !dashboardError && dashboard && effectiveSelected && (
                <>
                  {/* Compact Flight Header */}
                  <div className="rounded-lg border bg-card shadow-sm px-4 py-2.5">
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                      {/* Flight identity */}
                      <div className="flex items-center gap-3">
                        <h1 className="text-xl font-bold tracking-tight">
                          GF{effectiveSelected.flightNumber}
                        </h1>
                        <Badge 
                          variant="secondary"
                          className={cn("px-2 py-0.5 text-[11px] font-semibold", getStatusColor(dashboard.flightStatus?.status || dashboard.flightPhase?.phase || ""))}
                        >
                          {dashboard.flightStatus?.status || dashboard.flightPhase?.label || "NO STATUS"}
                        </Badge>
                        {/* Data integrity badge */}
                        {dashboard.dataIntegrity?.valid ? (
                          <span className="flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400" title={`All ${dashboard.dataIntegrity.checks} validation checks passed`}>
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            <span className="hidden sm:inline">Verified</span>
                          </span>
                        ) : dashboard.dataIntegrity && !dashboard.dataIntegrity.valid ? (
                          <span
                            className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400 cursor-help"
                            title={dashboard.dataIntegrity.warnings.join(" | ")}
                          >
                            <ShieldAlert className="h-3.5 w-3.5" />
                            <span className="hidden sm:inline">{dashboard.dataIntegrity.warnings.length} warning{dashboard.dataIntegrity.warnings.length !== 1 ? "s" : ""}</span>
                          </span>
                        ) : null}
                        <span className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground">
                          <span className="font-medium text-foreground">{dashboard.route.origin || effectiveSelected.origin}</span>
                          <ArrowRight className="h-3 w-3" />
                          <span className="font-medium text-foreground">{dashboard.route.destination || selectedFlight?.destination || "—"}</span>
                          <span className="mx-0.5">•</span>
                          {effectiveSelected.date}
                          <span className="mx-0.5">•</span>
                          Gate {dashboard.flightStatus?.gate || "TBD"}
                        </span>
                      </div>

                      {/* KPI strip */}
                      <div className="flex items-center gap-4 text-xs">
                        {/* Prominent souls on board */}
                        <button
                          className="flex items-center gap-1.5 bg-emerald-50 dark:bg-emerald-950/40 px-2 py-0.5 rounded-md border border-emerald-200 dark:border-emerald-800 hover:ring-1 hover:ring-emerald-400 transition-all cursor-pointer"
                          title="Souls on Board — boarded passengers + their lap infants"
                          onClick={() => setBottomView(bottomView === "sob" ? null : "sob")}
                        >
                          <Users className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                          <span className="font-bold text-emerald-700 dark:text-emerald-300">{dashboard.overview.soulsOnBoard}</span>
                          <span className="text-emerald-600 dark:text-emerald-400">Souls on Board</span>
                        </button>
                        <button
                          className="flex items-center gap-1.5 hover:bg-muted/50 rounded px-1 py-0.5 transition-colors cursor-pointer"
                          title="Souls on Manifest — all passengers + infants on the manifest"
                          onClick={() => setBottomView(bottomView === "souls" ? null : "souls")}
                        >
                          <Users className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview.totalSouls}</span>
                          <span className="text-muted-foreground">Souls on Manifest</span>
                        </button>
                        <button
                          className="flex items-center gap-1.5 hover:bg-muted/50 rounded px-1 py-0.5 transition-colors cursor-pointer"
                          title="Manifest Records — seated passengers on Sabre manifest"
                          onClick={() => setBottomView(bottomView === "records" ? null : "records")}
                        >
                          <Briefcase className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview.manifestRecords}</span>
                          <span className="text-muted-foreground">Manifest Records</span>
                        </button>
                        <div className="flex items-center gap-1.5" title="Change updates">
                          <Activity className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview.trackedChanges}</span>
                          <span className="text-muted-foreground">updates</span>
                        </div>
                        <Separator orientation="vertical" className="h-5 hidden md:block" />
                        <div className="hidden md:flex items-center gap-3 text-xs text-muted-foreground">
                          <span><span className="text-foreground font-medium">{dashboard.flightStatus?.aircraft?.type || dashboard.schedule?.aircraftType || "—"}</span> {(dashboard.flightStatus?.aircraft?.registration) ? `/ ${dashboard.flightStatus.aircraft.registration}` : ""}</span>
                          {/* Schedule with delay indicator */}
                          <ScheduleDelay schedule={dashboard.flightStatus?.schedule} />
                        </div>
                        {/* Last-fetched timestamp */}
                        {dashboard.fetchedAt && (
                          <>
                            <Separator orientation="vertical" className="h-5 hidden lg:block" />
                            <span className="hidden lg:flex items-center gap-1 text-[10px] text-muted-foreground" title={`Data fetched at ${dashboard.fetchedAt}`}>
                              <Clock className="h-3 w-3" />
                              {new Date(dashboard.fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                            </span>
                          </>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1.5 text-xs"
                          onClick={openTree}
                          disabled={treeOpen}
                        >
                          <Network className="h-3.5 w-3.5" />
                          <span className="hidden lg:inline">Pax Tree</span>
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* Stats Strip — shared across all tabs, interactive */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2 mt-2">
                    {/* Cabin */}
                    <div className="rounded-lg border bg-card p-2.5 shadow-sm">
                      <p className="text-[10px] font-medium text-muted-foreground mb-1.5">Cabin</p>
                      <div className="flex items-baseline gap-3">
                        <button onClick={() => { setFilterCabin(filterCabin === "Y" ? "all" : "Y"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterCabin === "Y" && "ring-1 ring-emerald-500")}>
                          <p className="text-lg font-bold text-emerald-600">{dashboard.analysis?.economy?.total ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Economy</p>
                        </button>
                        <button onClick={() => { setFilterCabin(filterCabin === "J" ? "all" : "J"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterCabin === "J" && "ring-1 ring-amber-500")}>
                          <p className="text-lg font-bold text-amber-600">{dashboard.analysis?.business?.total ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Business</p>
                        </button>
                      </div>
                    </div>

                    {/* Status */}
                    <div className="rounded-lg border bg-card p-2.5 shadow-sm">
                      <p className="text-[10px] font-medium text-muted-foreground mb-1.5">Status</p>
                      <div className="flex items-baseline gap-3">
                        <button onClick={() => { setFilterStatus(filterStatus === "boarded" ? "all" : "boarded"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterStatus === "boarded" && "ring-1 ring-emerald-500")}>
                          <p className="text-lg font-bold text-emerald-600">{dashboard.analysis?.boarded ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Boarded</p>
                        </button>
                        <button onClick={() => { setFilterStatus(filterStatus === "checkedIn" ? "all" : "checkedIn"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterStatus === "checkedIn" && "ring-1 ring-blue-500")}>
                          <p className="text-lg font-bold text-blue-600">{(dashboard.analysis?.checkedIn ?? 0) - (dashboard.analysis?.boarded ?? 0)}</p>
                          <p className="text-[9px] text-muted-foreground">Checked-In</p>
                        </button>
                        <button onClick={() => { setFilterStatus(filterStatus === "booked" ? "all" : "booked"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterStatus === "booked" && "ring-1 ring-amber-500")}>
                          <p className="text-lg font-bold text-amber-600">{dashboard.analysis?.notCheckedIn ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Booked</p>
                        </button>
                      </div>
                    </div>

                    {/* Type */}
                    <div className="rounded-lg border bg-card p-2.5 shadow-sm">
                      <p className="text-[10px] font-medium text-muted-foreground mb-1.5">Type</p>
                      <div className="flex items-baseline gap-3">
                        <button onClick={() => { setFilterType(filterType === "revenue" ? "all" : "revenue"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterType === "revenue" && "ring-1 ring-blue-500")}>
                          <p className="text-lg font-bold text-blue-600">{dashboard.analysis?.revenue ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Revenue</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "nonRevenue" ? "all" : "nonRevenue"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterType === "nonRevenue" && "ring-1 ring-purple-500")}>
                          <p className="text-lg font-bold text-purple-600">{dashboard.analysis?.nonRevenue ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Non-Rev</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "child" ? "all" : "child"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterType === "child" && "ring-1 ring-amber-500")}>
                          <p className="text-lg font-bold text-amber-500">{dashboard.analysis?.totalChildren ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">Children</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "infant" ? "all" : "infant"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1 -mx-1", filterType === "infant" && "ring-1 ring-teal-500")}>
                          <p className="text-lg font-bold text-teal-500">{dashboard.analysis?.totalInfants ?? 0}</p>
                          <p className="text-[9px] text-muted-foreground">w/ Infant</p>
                        </button>
                      </div>
                    </div>

                    {/* Loyalty */}
                    <div className="rounded-lg border bg-card p-2.5 shadow-sm">
                      <p className="text-[10px] font-medium text-muted-foreground mb-1.5 flex items-center gap-1"><Star className="h-3 w-3 text-amber-500" /> Loyalty</p>
                      <div className="flex items-baseline gap-2.5">
                        {[
                          { key: "FF" as FilterLoyalty, label: "FF", val: dashboard.analysis?.loyaltyCounts?.FF ?? 0, color: "text-amber-500" },
                          { key: "BLU" as FilterLoyalty, label: "Blu", val: dashboard.analysis?.loyaltyCounts?.BLU ?? 0, color: "text-blue-500" },
                          { key: "SLV" as FilterLoyalty, label: "Slv", val: dashboard.analysis?.loyaltyCounts?.SLV ?? 0, color: "text-gray-400" },
                          { key: "GLD" as FilterLoyalty, label: "Gld", val: dashboard.analysis?.loyaltyCounts?.GLD ?? 0, color: "text-yellow-500" },
                          { key: "BLK" as FilterLoyalty, label: "Blk", val: dashboard.analysis?.loyaltyCounts?.BLK ?? 0, color: "text-gray-900 dark:text-gray-100" },
                        ].map((t) => (
                          <button key={t.key} onClick={() => { setFilterLoyalty(filterLoyalty === t.key ? "all" : t.key); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-0.5", filterLoyalty === t.key && "ring-1 ring-current")}>
                            <p className={cn("text-lg font-bold", t.color)}>{t.val}</p>
                            <p className="text-[9px] text-muted-foreground">{t.label}</p>
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Nationality - top 5 */}
                    <div className="rounded-lg border bg-card p-2.5 shadow-sm col-span-2 sm:col-span-2">
                      <p className="text-[10px] font-medium text-muted-foreground mb-1.5">Top Nationalities</p>
                      <div className="flex items-baseline gap-3">
                        {(() => {
                          const nc = dashboard.analysis?.nationalityCounts ?? {};
                          const sorted = Object.entries(nc).sort((a, b) => b[1] - a[1]).slice(0, 5);
                          if (sorted.length === 0) return <p className="text-xs text-muted-foreground">No data</p>;
                          return sorted.map(([nat, count]) => (
                            <div key={nat} className="text-center">
                              <p className="text-lg font-bold">{count}</p>
                              <p className="text-[9px] text-muted-foreground">{nat}</p>
                            </div>
                          ));
                        })()}
                      </div>
                    </div>
                  </div>

                  {/* Tab Navigation — now handled by vertical nav rail */}
                  <div>
                    {/* Overview Tab — compact executive dashboard */}
                    {activeTab === "overview" && (
                      <div className="mt-1 space-y-3">
                      {/* Phase Timeline Stepper */}
                      {dashboard.flightPhase && (
                        <div className="rounded-lg border bg-card shadow-sm px-4 py-3">
                          <PhaseTimeline
                            phase={dashboard.flightPhase.phase}
                            label={dashboard.flightPhase.label}
                          />
                        </div>
                      )}

                      {/* Phase Alert Banner */}
                      {dashboard.flightPhase && (
                        <PhaseAlertBanner flightPhase={dashboard.flightPhase} />
                      )}

                      <StatePanels
                        stateSummary={dashboard.stateSummary}
                        phase={dashboard.flightPhase?.phase ?? "SCHEDULED"}
                        focusCard={dashboard.flightPhase?.focusCard ?? "booked"}
                        onInfoClick={openInfo}
                        onCardClick={(key: StateCardKey) => setBottomView(bottomView === key ? null : key)}
                        activeCard={(bottomView === "booked" || bottomView === "checkedIn" || bottomView === "boarded" || bottomView === "others") ? bottomView : null}
                      />

                      {/* 4-Card Overview Grid */}
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                        {/* Demographics */}
                        <Card className="shadow-sm">
                          <CardContent className="p-3">
                            <div className="flex items-center gap-1.5 mb-2">
                              <Users className="h-3.5 w-3.5 text-blue-500" />
                              <h3 className="text-xs font-semibold">Demographics</h3>
                            </div>
                            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                              <div>
                                <p className="text-[10px] text-muted-foreground">Adults</p>
                                <p className="text-lg font-bold">{dashboard.passengerSummary.adultCount}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-muted-foreground">Children</p>
                                <p className="text-lg font-bold text-amber-500">{dashboard.passengerSummary.childCount}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-muted-foreground">Infants</p>
                                <p className="text-lg font-bold text-emerald-500">{dashboard.passengerSummary.infantCount}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-muted-foreground">Non-Rev</p>
                                <p className="text-lg font-bold text-purple-500">{dashboard.analysis?.nonRevenue ?? 0}</p>
                              </div>
                            </div>
                          </CardContent>
                        </Card>

                        {/* Cabin Mix with Load Factor */}
                        <Card className="shadow-sm">
                          <CardContent className="p-3">
                            <div className="flex items-center gap-1.5 mb-2">
                              <Ticket className="h-3.5 w-3.5 text-emerald-500" />
                              <h3 className="text-xs font-semibold">Cabin Mix</h3>
                            </div>
                            <div className="space-y-2">
                              {(dashboard.passengerSummary.cabinSummary ?? []).map((cabin) => {
                                const authorized = cabin.authorized || 0;
                                const count = cabin.count || 0;
                                const loadFactor = authorized > 0 ? Math.round((count / authorized) * 100) : 0;
                                const isEconomy = cabin.cabin === "Y";
                                return (
                                  <div key={cabin.cabin}>
                                    <div className="flex justify-between text-xs mb-1">
                                      <span className="text-muted-foreground">
                                        {isEconomy ? "Economy" : "Business"}
                                      </span>
                                      <span className="font-medium">
                                        {count}
                                        {authorized > 0 && (
                                          <span className="text-muted-foreground ml-1">/ {authorized}</span>
                                        )}
                                        {authorized > 0 && (
                                          <span className={cn(
                                            "ml-1 text-[10px] font-semibold",
                                            loadFactor >= 95 ? "text-rose-500" : loadFactor >= 80 ? "text-amber-500" : "text-emerald-500"
                                          )}>
                                            {loadFactor}%
                                          </span>
                                        )}
                                      </span>
                                    </div>
                                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                                      <div 
                                        className={cn(
                                          "h-full transition-all",
                                          loadFactor >= 95 ? "bg-rose-500" : loadFactor >= 80 ? "bg-amber-500" : isEconomy ? "bg-emerald-500" : "bg-amber-500"
                                        )}
                                        style={{ width: `${authorized > 0 ? Math.max(2, loadFactor) : Math.max(2, (count / Math.max(1, dashboard.passengerSummary.totalPassengers)) * 100)}%` }} 
                                      />
                                    </div>
                                  </div>
                                );
                              })}
                              {(!dashboard.passengerSummary.cabinSummary || dashboard.passengerSummary.cabinSummary.length === 0) && (
                                <>
                                  <div>
                                    <div className="flex justify-between text-xs mb-1">
                                      <span className="text-muted-foreground">Economy</span>
                                      <span className="font-medium">{dashboard.analysis?.economy?.total ?? 0}</span>
                                    </div>
                                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                                      <div className="h-full bg-emerald-500" style={{ width: `${Math.max(2, ((dashboard.analysis?.economy?.total ?? 0) / Math.max(1, dashboard.passengerSummary.totalPassengers)) * 100)}%` }} />
                                    </div>
                                  </div>
                                  <div>
                                    <div className="flex justify-between text-xs mb-1">
                                      <span className="text-muted-foreground">Business</span>
                                      <span className="font-medium">{dashboard.analysis?.business?.total ?? 0}</span>
                                    </div>
                                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                                      <div className="h-full bg-amber-500" style={{ width: `${Math.max(2, ((dashboard.analysis?.business?.total ?? 0) / Math.max(1, dashboard.passengerSummary.totalPassengers)) * 100)}%` }} />
                                    </div>
                                  </div>
                                </>
                              )}
                            </div>
                          </CardContent>
                        </Card>

                        {/* Operational Status */}
                        <Card className="shadow-sm">
                          <CardContent className="p-3">
                            <div className="flex items-center gap-1.5 mb-2">
                              <Radar className="h-3.5 w-3.5 text-purple-500" />
                              <h3 className="text-xs font-semibold">Operational Phase</h3>
                            </div>
                            <div className="space-y-1.5 text-xs">
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Checked in</span>
                                <span className="font-medium">{dashboard.analysis?.checkedIn ?? 0}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Boarded</span>
                                <span className="font-medium">{dashboard.analysis?.boarded ?? 0}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Not checked in</span>
                                <span className={cn("font-medium", (dashboard.analysis?.notCheckedIn ?? 0) > 0 ? "text-rose-500" : "text-emerald-500")}>
                                  {dashboard.analysis?.notCheckedIn ?? 0}
                                </span>
                              </div>
                              <div className="flex justify-between border-t pt-1">
                                <span className="text-muted-foreground">Revenue tickets</span>
                                <span className="font-medium text-blue-500">{dashboard.analysis?.revenue ?? 0}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Non-revenue</span>
                                <span className="font-medium text-purple-500">{dashboard.analysis?.nonRevenue ?? 0}</span>
                              </div>
                            </div>
                          </CardContent>
                        </Card>

                        {/* Activity Stream */}
                        <Card className="shadow-sm">
                          <CardContent className="p-3">
                            <div className="flex items-center gap-1.5 mb-2">
                              <Clock className="h-3.5 w-3.5 text-amber-500" />
                              <h3 className="text-xs font-semibold">Activity Stream</h3>
                            </div>
                            <div className="space-y-1.5 text-xs">
                              {Object.entries(dashboard.changeSummary ?? {})
                                .sort((a, b) => b[1] - a[1])
                                .slice(0, 4)
                                .map(([label, count]) => (
                                  <div key={label} className="flex justify-between items-center">
                                    <span className="text-muted-foreground capitalize">{label.replace(/([A-Z])/g, ' $1').trim()}</span>
                                    <Badge variant="secondary" className="px-1 text-[10px] h-4">{count}</Badge>
                                  </div>
                              ))}
                              {Object.keys(dashboard.changeSummary ?? {}).length === 0 && (
                                <div className="text-muted-foreground text-center py-2">
                                  No recent activity.
                                </div>
                              )}
                            </div>
                          </CardContent>
                        </Card>
                      </div>

                      {/* Published Schedule (from VerifyFlightDetails) */}
                      {dashboard.schedule && dashboard.schedule.success && (
                        <Card className="shadow-sm">
                          <CardContent className="p-3">
                            <div className="flex items-center gap-1.5 mb-2">
                              <CalendarDays className="h-3.5 w-3.5 text-sky-500" />
                              <h3 className="text-xs font-semibold">Published Schedule</h3>
                              <span className="ml-auto text-[10px] text-muted-foreground">{dashboard.schedule.aircraftType}</span>
                            </div>
                            <div className="flex items-center gap-3">
                              {/* Origin */}
                              <div className="text-center min-w-[60px]">
                                <p className="text-lg font-bold">{dashboard.schedule.origin}</p>
                                {dashboard.schedule.originTerminal && (
                                  <p className="text-[10px] text-muted-foreground">T{dashboard.schedule.originTerminal}</p>
                                )}
                                {dashboard.schedule.originTimeZone && (
                                  <p className="text-[10px] text-muted-foreground">{dashboard.schedule.originTimeZone}</p>
                                )}
                              </div>
                              {/* Departure time */}
                              <div className="text-center">
                                <p className="text-sm font-semibold">{dashboard.schedule.scheduledDeparture?.slice(-8, -3) || "—"}</p>
                                <p className="text-[10px] text-muted-foreground">Depart</p>
                              </div>
                              {/* Arrow + duration */}
                              <div className="flex-1 flex flex-col items-center">
                                <div className="flex items-center gap-1 text-muted-foreground w-full">
                                  <div className="h-px flex-1 bg-border" />
                                  <Plane className="h-3 w-3 -rotate-0" />
                                  <div className="h-px flex-1 bg-border" />
                                </div>
                                <p className="text-[10px] text-muted-foreground mt-0.5">
                                  {dashboard.schedule.elapsedTime || "—"}
                                  {dashboard.schedule.airMilesFlown > 0 && ` · ${dashboard.schedule.airMilesFlown} mi`}
                                </p>
                              </div>
                              {/* Arrival time */}
                              <div className="text-center">
                                <p className="text-sm font-semibold">{dashboard.schedule.scheduledArrival?.slice(-8, -3) || "—"}</p>
                                <p className="text-[10px] text-muted-foreground">Arrive</p>
                              </div>
                              {/* Destination */}
                              <div className="text-center min-w-[60px]">
                                <p className="text-lg font-bold">{dashboard.schedule.destination}</p>
                                {dashboard.schedule.destinationTerminal && (
                                  <p className="text-[10px] text-muted-foreground">T{dashboard.schedule.destinationTerminal}</p>
                                )}
                                {dashboard.schedule.destinationTimeZone && (
                                  <p className="text-[10px] text-muted-foreground">{dashboard.schedule.destinationTimeZone}</p>
                                )}
                              </div>
                            </div>
                            {/* Multi-segment indicator */}
                            {dashboard.schedule.segments && dashboard.schedule.segments.length > 1 && (
                              <div className="mt-2 pt-2 border-t space-y-1">
                                <p className="text-[10px] font-semibold text-muted-foreground">{dashboard.schedule.segments.length} Segments</p>
                                {dashboard.schedule.segments.map((seg, i) => (
                                  <div key={i} className="flex items-center gap-2 text-[10px] text-muted-foreground">
                                    <span className="font-medium text-foreground">{seg.origin}</span>
                                    <ArrowRight className="h-2.5 w-2.5" />
                                    <span className="font-medium text-foreground">{seg.destination}</span>
                                    <span>{seg.departureDateTime?.slice(-8, -3)} → {seg.arrivalDateTime?.slice(-8, -3)}</span>
                                    <span>{seg.aircraftType}</span>
                                    {seg.elapsedTime && <span>({seg.elapsedTime})</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                            {dashboard.schedule.mealCode && (
                              <p className="mt-1.5 text-[10px] text-muted-foreground">Meal: {dashboard.schedule.mealCode}</p>
                            )}
                          </CardContent>
                        </Card>
                      )}

                      {/* Bottom Detail Panel */}
                      {bottomView && effectiveSelected && (
                        <BottomDetailPanel
                          view={bottomView}
                          flightNumber={effectiveSelected.flightNumber}
                          origin={effectiveSelected.origin}
                          date={effectiveSelected.date}
                          dashboard={dashboard}
                          onClose={() => setBottomView(null)}
                          onSelectPassenger={(pnr) => {
                            setDetailPnr(pnr);
                            setDetailOpen(true);
                          }}
                        />
                      )}
                    </div>
                    )}

                    {/* Passengers Tab */}
                    {activeTab === "passengers" && (
                      <div className="mt-1">
                      <PassengerTable
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                        onSelectPassenger={(pnr) => {
                          setDetailPnr(pnr);
                          setDetailOpen(true);
                        }}
                        filterCabin={filterCabin}
                        setFilterCabin={setFilterCabin}
                        filterStatus={filterStatus}
                        setFilterStatus={setFilterStatus}
                        filterType={filterType}
                        setFilterType={setFilterType}
                        filterLoyalty={filterLoyalty}
                        setFilterLoyalty={setFilterLoyalty}
                      />
                      </div>
                    )}

                    {/* Standby Tab */}
                    {activeTab === "standby" && (
                      <div className="mt-1">
                      <StandbyPanel
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                      />
                      </div>
                    )}

                    {/* Changes Tab */}
                    {activeTab === "changes" && (
                      <div className="mt-1">
                      <ChangeTimeline
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                      />
                      </div>
                    )}

                    {/* History Tab */}
                    {activeTab === "history" && (
                      <div className="mt-1">
                      <StatusHistory
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                      />
                      </div>
                    )}

                    {/* Reservations Tab */}
                    {activeTab === "reservations" && (
                      <div className="mt-1">
                      <ReservationView
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                      />
                      </div>
                    )}
                  </div>

                  {/* Passenger Detail Sheet */}
                  <PassengerDetailSheet
                    open={detailOpen}
                    onOpenChange={setDetailOpen}
                    flightNumber={effectiveSelected.flightNumber}
                    origin={effectiveSelected.origin}
                    date={effectiveSelected.date}
                    pnr={detailPnr}
                  />
                </>
              )}
            </div>
          </main>
        </Panel>

        {(treeOpen || ingestOpen || infoOpen) && (
          <>
            <PanelResizeHandle className="relative flex w-px items-center justify-center bg-border focus-visible:outline-none data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full transition-colors hover:bg-primary" />
            
            <Panel defaultSize="35" minSize="25" maxSize="60" collapsible collapsedSize="0%">
              <aside className="flex h-full flex-col bg-background shadow-2xl border-l relative overflow-hidden">
                {treeOpen && (
                  <>
                    <div className="flex items-center justify-between border-b bg-muted/30 p-5 sticky top-0 z-10">
                      <div>
                        <h2 className="text-lg font-semibold tracking-tight">Passenger Tree</h2>
                        <p className="text-sm text-muted-foreground">Hierarchical breakdown of flight passengers.</p>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => refetchTree()} disabled={treeFetching}>
                          <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", treeFetching && "animate-spin")} />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setTreeOpen(false)}>Close</Button>
                      </div>
                    </div>
                    <div className="flex-1 overflow-y-auto p-6 bg-muted/10">
                      {treeLoading ? (
                        <div className="flex items-center justify-center p-20 text-muted-foreground">
                          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                          Processing topology...
                        </div>
                      ) : tree ? (
                        <PassengerTree tree={tree} />
                      ) : (
                        <div className="text-center text-muted-foreground mt-10">No tree data found for this flight.</div>
                      )}
                    </div>
                  </>
                )}
                {ingestOpen && (
                  <>
                    <div className="flex items-center justify-between border-b bg-muted/30 p-5 sticky top-0 z-10">
                      <div>
                        <h2 className="text-lg font-semibold tracking-tight">Sabre Ingestion</h2>
                        <p className="text-sm text-muted-foreground">Import live flight data into the operational database.</p>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => setIngestOpen(false)}>Close</Button>
                    </div>
                    <div className="flex-1 overflow-y-auto">
                      <IngestionPanel />
                    </div>
                  </>
                )}
                {infoOpen && (
                  <>
                    <div className="flex items-center justify-between border-b bg-muted/30 p-5 sticky top-0 z-10">
                      <div className="flex items-center gap-2">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10">
                          <Info className="h-4 w-4 text-blue-500" />
                        </div>
                        <div>
                          <h2 className="text-lg font-semibold tracking-tight">Tile Documentation</h2>
                          <p className="text-sm text-muted-foreground">How the tile is calculated</p>
                        </div>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => setInfoOpen(false)}>Close</Button>
                    </div>
                    <div className="flex-1 overflow-y-auto p-6 bg-muted/10">
                      <TileInfoPanel activeTab={activeInfo} />
                    </div>
                  </>
                )}
              </aside>
            </Panel>
          </>
        )}
      </PanelGroup>

      {/* Ingest Confirmation Dialog */}
      <AlertDialog open={!!confirmFlight} onOpenChange={(open) => { if (!open) setConfirmFlight(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-ingest from Sabre</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmFlight && (
                <>
                  Fetch latest data for{" "}
                  <span className="font-semibold text-foreground">
                    {confirmFlight.airline}{confirmFlight.flightNumber}
                  </span>{" "}
                  ({confirmFlight.origin} → {confirmFlight.destination || "?"}, {confirmFlight.departureDate})?
                  <br /><br />
                  This will pull FlightStatus, PassengerList, and Reservations from Sabre and update the local database.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={executeIngest}>
              <CloudDownload className="h-4 w-4 mr-2" />
              Ingest
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

    </div>
  );
}

function getStatusColor(status: string) {
  switch (status.toUpperCase()) {
    case "PDC":
    case "DEPARTED":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
    case "OPENCI":
    case "CHECK_IN":
      return "bg-blue-500/15 text-blue-600 dark:text-blue-400";
    case "FINAL":
    case "CLOSED":
      return "bg-red-500/15 text-red-600 dark:text-red-400";
    case "BOARDING":
      return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
    case "SCHEDULED":
      return "bg-slate-500/15 text-slate-600 dark:text-slate-400";
    default:
      return "bg-secondary text-secondary-foreground";
  }
}

function ScheduleDelay({ schedule }: { schedule?: { scheduledDeparture?: string; estimatedDeparture?: string; scheduledArrival?: string; estimatedArrival?: string } }) {
  if (!schedule?.scheduledDeparture) return <span>STD —</span>;

  const std = schedule.scheduledDeparture;
  const etd = schedule.estimatedDeparture;

  // Parse HH:MM from the time strings (last 8 chars is HH:MM:SS)
  const stdTime = std.slice(-8, -3); // HH:MM

  if (!etd || etd === std) {
    return <span>STD {stdTime}</span>;
  }

  // Calculate delay in minutes
  const parseMinutes = (t: string) => {
    const hhmm = t.slice(-8, -3);
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + m;
  };
  const delayMin = parseMinutes(etd) - parseMinutes(std);

  if (delayMin === 0) return <span>STD {stdTime}</span>;

  const etdTime = etd.slice(-8, -3);
  const sign = delayMin > 0 ? "+" : "";

  return (
    <span className="flex items-center gap-1">
      <span>STD {stdTime}</span>
      <span className={cn(
        "text-[10px] font-semibold px-1 rounded",
        delayMin > 0 ? "text-rose-500 bg-rose-50 dark:bg-rose-950/40" : "text-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
      )}>
        ETD {etdTime} ({sign}{delayMin}m)
      </span>
    </span>
  );
}
