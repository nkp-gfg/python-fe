"use client";

import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient, useIsMutating } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  ArrowRightLeft,
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
  Users,
  Clock,
  Briefcase,
  Ticket,
  Table2,
  Star,
  ChevronRight,
  X,
} from "lucide-react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { useDebounce } from "@/lib/hooks";

import { fetchDashboard, fetchFlightTree, fetchFlights, ingestFlight } from "@/lib/api";
import type { FlightListItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent } from "@/components/ui/card";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { format, parse } from "date-fns";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogFooter,
  AlertDialogTitle, AlertDialogDescription, AlertDialogAction, AlertDialogCancel,
} from "@/components/ui/alert-dialog";

import { StatePanels } from "@/components/dashboard/state-panels";
import type { StateCardKey, PanelFilter } from "@/components/dashboard/state-panels";
import { PhaseTimeline } from "@/components/dashboard/phase-timeline";
import { PhaseAlertBanner } from "@/components/dashboard/phase-alert-banner";
import { BottomDetailPanel } from "@/components/dashboard/bottom-detail-panel";
import type { DetailView } from "@/components/dashboard/bottom-detail-panel";
import { PassengerTree } from "@/components/dashboard/passenger-tree";
import { IngestionPanel } from "@/components/dashboard/ingestion-panel";
import { useToast } from "@/components/ui/toast";
import { TileInfoPanel, type TileInfoKey } from "@/components/dashboard/tile-info-panel";
import { InsightInfoPanel, type InsightInfoKey } from "@/components/dashboard/insight-info-panel";
import { PassengerTable, type FilterCabin, type FilterStatus, type FilterType, type FilterLoyalty, type FilterNationality } from "@/components/dashboard/passenger-table";
import { StandbyPanel } from "@/components/dashboard/standby-panel";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import { ChangeTimeline } from "@/components/dashboard/change-timeline";
import { ErrorBoundary } from "@/components/error-boundary";
import { StatusHistory } from "@/components/dashboard/status-history";
import { ReservationView } from "@/components/dashboard/reservation-view";
import { FlightTimeline } from "@/components/dashboard/flight-timeline";
import { BoardingProgress } from "@/components/dashboard/boarding-progress";
import {
  CommercialInsightsTab,
  ExceptionsInsightsTab,
  ReadinessInsightsTab,
} from "@/components/dashboard/flight-insight-tabs";
import { AuditPanel } from "@/components/dashboard/audit-panel";

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
  const { pushToast } = useToast();
  const [mobileRailOpen, setMobileRailOpen] = useState(false);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [insightInfoOpen, setInsightInfoOpen] = useState(false);
  const [activeInsightInfo, setActiveInsightInfo] = useState<InsightInfoKey>("connectingPassengers");
  const [treeDialogOpen, setTreeDialogOpen] = useState(false);
  const [matrixDialogOpen, setMatrixDialogOpen] = useState(false);
  const [activeInfo, setActiveInfo] = useState<TileInfoKey>("booked");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search);
  const [dateFilter, setDateFilter] = useState<string>("all");
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [mobileCalendarOpen, setMobileCalendarOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [selected, setSelected] = useState<FlightSelection | null>(initialSelection ?? null);
  const [detailPnr, setDetailPnr] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [bottomView, setBottomView] = useState<DetailView | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "commercial" | "readiness" | "exceptions" | "passengers" | "groups" | "standby" | "changes" | "history" | "reservations" | "activity" | "audit">("overview");
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [snapshotSequence, setSnapshotSequence] = useState<number | null>(null);
  const [selectedGroupCode, setSelectedGroupCode] = useState<string | null>(null);
  const [filterCabin, setFilterCabin] = useState<FilterCabin>("all");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [filterType, setFilterType] = useState<FilterType>("all");
  const [filterLoyalty, setFilterLoyalty] = useState<FilterLoyalty>("all");
  const [filterNationality, setFilterNationality] = useState<FilterNationality>("all");

  /** Handle clicks on individual numbers inside state-panel cards */
  const handlePanelFilter = (f: PanelFilter) => {
    setFilterStatus(f.status ?? "all");
    setFilterCabin((f.cabin ?? "all") as FilterCabin);
    setFilterType((f.type ?? "all") as FilterType);
    setFilterLoyalty("all");
    setFilterNationality("all");
    setActiveTab("passengers");
  };

  const { data: flights, isLoading: flightsLoading, error: flightsError, refetch: refetchFlights, isFetching: flightsFetching } = useQuery({
    queryKey: ["flights"],
    queryFn: () => fetchFlights(),
    refetchInterval: 60_000,
  });

  // Only consider a flight "selected" if the user (or caller) explicitly chose one.
  // Do NOT auto-select the first flight on load — that triggers expensive dashboard
  // + tree queries before the user has even seen the flight list.
  const effectiveSelected =
    selected ??
    initialSelection ??
    null;

  const deferredSelected = useDeferredValue(effectiveSelected);

  // Auto-select the first flight once the list arrives, but only if the user
  // hasn't selected anything yet. This delays dashboard/tree queries until
  // flights are present without reading refs during render.
  useEffect(() => {
    if (selected || initialSelection || !flights || flights.length === 0) {
      return;
    }

    startTransition(() => {
      setSelected({
        flightNumber: flights[0].flightNumber,
        origin: flights[0].origin,
        date: flights[0].departureDate,
      });
    });
  }, [selected, initialSelection, flights]);

  const { data: dashboard, isLoading: dashboardLoading, error: dashboardError, refetch: refetchDashboard, isFetching: dashboardFetching } = useQuery({
    queryKey: [
      "dashboard",
      deferredSelected?.flightNumber,
      deferredSelected?.origin,
      deferredSelected?.date,
      snapshotSequence,
    ],
    queryFn: () =>
      fetchDashboard(
        deferredSelected!.flightNumber,
        deferredSelected!.origin,
        deferredSelected!.date,
        snapshotSequence,
      ),
    enabled: Boolean(deferredSelected),
    refetchInterval: snapshotSequence ? false : 60_000,
  });

  const { data: tree, isLoading: treeLoading, refetch: refetchTree, isFetching: treeFetching } = useQuery({
    queryKey: [
      "tree",
      deferredSelected?.flightNumber,
      deferredSelected?.origin,
      deferredSelected?.date,
      snapshotSequence,
    ],
    queryFn: () =>
      fetchFlightTree(
        deferredSelected!.flightNumber,
        deferredSelected!.origin,
        deferredSelected!.date,
        snapshotSequence,
      ),
    enabled: Boolean(deferredSelected),
    refetchInterval: snapshotSequence ? false : 60_000,
  });

  const [ingestingFlight, setIngestingFlight] = useState<string | null>(null);
  const [confirmFlight, setConfirmFlight] = useState<FlightListItem | null>(null);
  const ingestMutation = useMutation({
    mutationKey: ["ingest"],
    mutationFn: ingestFlight,
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["flights"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["tree"] });
      pushToast({
        variant: "success",
        title: `${variables.airline ?? "GF"}${variables.flightNumber} ingest complete`,
        description: "Background ingestion job completed successfully.",
      });
    },
    onError: (error) => {
      pushToast({ variant: "error", title: "Background ingest failed", description: error.message });
    },
    onSettled: () => setIngestingFlight(null),
  });

  /** Convert Sabre "2026-03-20T04:00PM" → ISO "2026-03-20T16:00:00" */
  function toIsoDateTime(raw: string | undefined, fallbackDate: string): string {
    if (!raw) return `${fallbackDate}T00:00:00`;
    const m = raw.match(/(\d{4}-\d{2}-\d{2})T(\d{1,2}):(\d{2})(AM|PM)?/i);
    if (!m) return `${fallbackDate}T00:00:00`;
    const [, datePart, hStr, min, ampm] = m;
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

  const tabLabels: Record<"overview" | "commercial" | "readiness" | "exceptions" | "passengers" | "groups" | "standby" | "changes" | "history" | "reservations" | "activity" | "audit", string> = {
    overview: "Live Ops",
    commercial: "Commercial",
    readiness: "Readiness",
    exceptions: "Exceptions",
    passengers: "Passengers",
    groups: "Group Bookings",
    standby: "Standby",
    changes: "Changes",
    history: "History",
    reservations: "Reservations",
    activity: "Activity",
    audit: "Audit",
  };

  const availableDates = useMemo(() => {
    const dates = [...new Set((flights ?? []).map((f) => f.departureDate))].sort().reverse();
    return dates;
  }, [flights]);

  const availableDateSet = useMemo(() => {
    return new Set(availableDates);
  }, [availableDates]);

  const availableStatuses = useMemo(() => {
    return [...new Set((flights ?? []).map((f) => f.status).filter(Boolean))].sort();
  }, [flights]);

  const relativeDateMap = useMemo(() => {
    const toIsoDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    const now = new Date();
    now.setHours(0, 0, 0, 0);

    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);

    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);

    return {
      yesterday: toIsoDate(yesterday),
      today: toIsoDate(now),
      tomorrow: toIsoDate(tomorrow),
    };
  }, []);

  const calendarSelectedDate = useMemo(() => {
    if (dateFilter === "all") return undefined;
    const resolved = (relativeDateMap as Record<string, string>)[dateFilter] ?? dateFilter;
    return parse(resolved, "yyyy-MM-dd", new Date());
  }, [dateFilter, relativeDateMap]);

  const quickDateFilterItems = useMemo(() => {
    const presets = [
      { key: "yesterday", label: "Yesterday" },
      { key: "today", label: "Today" },
      { key: "tomorrow", label: "Tomorrow" },
    ] as const;

    return presets
      .map((item) => {
        const date = relativeDateMap[item.key];
        const count = (flights ?? []).filter((flight) => flight.departureDate === date).length;
        return { ...item, count };
      })
      .filter((item) => item.count > 0);
  }, [flights, relativeDateMap]);

  const filteredFlights = useMemo(() => {
    const list = (flights ?? []).filter((flight) => {
      if (dateFilter !== "all") {
        const relativeDate = relativeDateMap[dateFilter as keyof typeof relativeDateMap];
        if (relativeDate) {
          if (flight.departureDate !== relativeDate) return false;
        } else if (flight.departureDate !== dateFilter) {
          return false;
        }
      }
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
  }, [flights, debouncedSearch, dateFilter, statusFilter, relativeDateMap]);

  function selectFlight(flight: FlightListItem) {
    const next = {
      flightNumber: flight.flightNumber,
      origin: flight.origin,
      date: flight.departureDate,
    };

    startTransition(() => {
      setSelected(next);
      setSnapshotSequence(null);
      setMobileRailOpen(false);
    });

    const nextHref = `/flights/${flight.flightNumber}?origin=${flight.origin}&date=${flight.departureDate}`;
    if (pathname !== nextHref) {
      router.replace(nextHref, { scroll: false });
    }
  }

  function openIngest() {
    setTreeDialogOpen(false);
    setMatrixDialogOpen(false);
    setInfoOpen(false);
    setInsightInfoOpen(false);
    setIngestOpen(true);
  }

  function openInfo(key: TileInfoKey) {
    setTreeDialogOpen(false);
    setMatrixDialogOpen(false);
    setIngestOpen(false);
    setInsightInfoOpen(false);
    setActiveInfo(key);
    setInfoOpen(true);
  }

  function openInsightInfo(key: InsightInfoKey) {
    setTreeDialogOpen(false);
    setMatrixDialogOpen(false);
    setIngestOpen(false);
    setInfoOpen(false);
    setActiveInsightInfo(key);
    setInsightInfoOpen(true);
  }

  function openTreeDialog() {
    setMatrixDialogOpen(false);
    setTreeDialogOpen(true);
  }

  function openMatrixDialog() {
    setTreeDialogOpen(false);
    setMatrixDialogOpen(true);
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

  function clearSearch() {
    setSearch("");
  }

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-background text-foreground font-sans antialiased text-sm">
      {/* Top Navigation Bar */}
      <header className="flex h-14 shrink-0 items-center border-b bg-card px-4 md:px-6">
        <div className="flex items-center gap-4 min-w-0 shrink-0">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={() => setMobileRailOpen(true)}
          >
            <Menu className="h-5 w-5" />
          </Button>
          <div
            className="flex items-center gap-2 font-semibold cursor-pointer"
            onClick={() => {
              setSelected(null);
              setActiveTab("overview");
              setBottomView(null);
              setSnapshotSequence(null);
              router.push("/");
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { setSelected(null); router.push("/"); } }}
          >
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-blue-600 text-white">
              <Plane className="h-4 w-4" />
            </div>
            <span className="hidden sm:inline-flex text-base tracking-tight text-card-foreground">FalconEye Ops</span>
          </div>
        </div>

        {/* Centered Search */}
        <div className="flex-1 flex justify-center px-4">
          <div className="relative w-full max-w-md">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && clearSearch()}
              placeholder="Search flights..."
              aria-label="Search flights"
              className="w-full rounded-md border border-input bg-background py-2 pl-9 pr-9 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            {search && (
              <button
                type="button"
                onClick={clearSearch}
                className="absolute right-2 top-2 rounded-sm p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Clear flight search"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <Link
            href="/today"
            className="hidden sm:flex items-center gap-1.5 rounded-full bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:bg-blue-500/20 transition-colors"
          >
            <CalendarDays className="h-3 w-3" />
            Today
          </Link>
          <Link
            href="/data-audit"
            className="hidden sm:flex items-center gap-1.5 rounded-full bg-orange-500/10 px-3 py-1 text-xs font-medium text-orange-600 dark:text-orange-400 hover:bg-orange-500/20 transition-colors"
          >
            <ArrowRightLeft className="h-3 w-3" />
            Data Audit
          </Link>
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
              <span>Background ingest running{activeIngestions > 1 ? ` (${activeIngestions})` : "…"}</span>
            </div>
          )}
          <Separator orientation="vertical" className="hidden h-5 sm:block" />
          <ThemeToggle />
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
          <SheetTitle className="sr-only">Flight Sidebar</SheetTitle>
          <SheetDescription className="sr-only">Select a flight from the sidebar list.</SheetDescription>
          <div className="flex flex-col h-full">
            <div className="p-4 border-b space-y-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Flights</span>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <Popover open={mobileCalendarOpen} onOpenChange={setMobileCalendarOpen}>
                    <PopoverTrigger asChild>
                      <button
                        className={cn(
                          "flex-1 flex items-center gap-1.5 rounded-md border py-1.5 px-2 text-xs shadow-sm transition-colors",
                          dateFilter !== "all"
                            ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                            : "border-input bg-background text-muted-foreground"
                        )}
                      >
                        <CalendarDays className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{dateFilter === "all" ? "All dates" : dateFilter}</span>
                        {dateFilter !== "all" && (
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); setDateFilter("all"); }}
                            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); setDateFilter("all"); } }}
                            className="ml-auto rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                            aria-label="Clear date filter"
                          >
                            <X className="h-3 w-3" />
                          </span>
                        )}
                      </button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-0" align="start">
                      <Calendar
                        mode="single"
                        selected={calendarSelectedDate}
                        onSelect={(day) => {
                          if (day) {
                            setDateFilter(format(day, "yyyy-MM-dd"));
                          } else {
                            setDateFilter("all");
                          }
                          setMobileCalendarOpen(false);
                        }}
                        disabled={(day) => !availableDateSet.has(format(day, "yyyy-MM-dd"))}
                        defaultMonth={calendarSelectedDate ?? new Date()}
                      />
                      {dateFilter !== "all" && (
                        <div className="border-t px-3 py-2">
                          <button
                            onClick={() => { setDateFilter("all"); setMobileCalendarOpen(false); }}
                            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                          >
                            <X className="h-3 w-3" />
                            Clear date filter
                          </button>
                        </div>
                      )}
                    </PopoverContent>
                  </Popover>
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
                {quickDateFilterItems.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {quickDateFilterItems.map((item) => {
                      const isActive = dateFilter === item.key;
                      return (
                        <button
                          key={item.key}
                          type="button"
                          onClick={() => setDateFilter(item.key)}
                          className={cn(
                            "rounded-full border px-2 py-1 text-[11px] font-medium transition-colors",
                            isActive
                              ? "border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-400"
                              : "border-input bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                          )}
                          aria-label={`${item.label} quick filter (${item.count} flights)`}
                        >
                          {item.label} ({item.count})
                        </button>
                      );
                    })}
                  </div>
                )}
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

            {/* Currently Selected Flight Card — Mobile */}
            {effectiveSelected && selectedFlight && (
              <div className="border-t px-4 py-3 bg-primary/5">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Currently Selected</div>
                <div className="rounded-lg border-2 border-primary bg-primary/10 p-3 text-left">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-bold text-sm text-primary">
                      {selectedFlight.airline}{selectedFlight.flightNumber}
                    </span>
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[9px] px-1.5 font-medium border-transparent",
                        getStatusColor(selectedFlight.flightPhase?.phase || selectedFlight.status)
                      )}
                    >
                      {selectedFlight.flightPhase?.label || selectedFlight.status || "UNKNOWN"}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-foreground mb-1.5">
                    <span className="font-medium">{selectedFlight.origin}</span>
                    <ArrowRight className="h-3 w-3 text-muted-foreground" />
                    <span className="font-medium">{selectedFlight.destination || "Pending"}</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {selectedFlight.departureDate}
                  </div>
                </div>
              </div>
            )}

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
                            ? "bg-emerald-600 text-white shadow-md ring-1 ring-emerald-400/50"
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
                                isActive ? "bg-white/20 text-white" : getStatusColor(flight.flightPhase?.phase || flight.status)
                              )}
                            >
                              {flight.flightPhase?.label || flight.status || "UNKNOWN"}
                            </Badge>
                            <span
                              role="button"
                              tabIndex={0}
                              title={ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? "Background ingest running" : "Re-ingest from Sabre"}
                              onClick={(e) => handleQuickIngest(flight, e)}
                              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleQuickIngest(flight, e as unknown as React.MouseEvent); } }}
                              className={cn("rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10 transition-colors cursor-pointer", isActive && "hover:bg-white/20")}
                            >
                              {ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? (
                                <span className="inline-flex items-center gap-1 text-[10px] font-medium">
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  <span>Running</span>
                                </span>
                              ) : (
                                <CloudDownload className="h-3.5 w-3.5" />
                              )}
                            </span>
                          </div>
                        </div>
                        <div className={cn("flex items-center gap-1.5 text-xs", isActive ? "text-white/80" : "text-muted-foreground")}>
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
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar Flights List */}
        <ErrorBoundary compact label="Flight list">
        <div className="hidden md:flex w-[280px] shrink-0 flex-col h-full border-r bg-muted/30">
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

            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
                  <PopoverTrigger asChild>
                    <button
                      className={cn(
                        "flex-1 flex items-center gap-1.5 rounded-md border py-1.5 px-2 text-xs shadow-sm transition-colors",
                        dateFilter !== "all"
                          ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                          : "border-input bg-background text-muted-foreground"
                      )}
                    >
                      <CalendarDays className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{dateFilter === "all" ? "All dates" : dateFilter}</span>
                      {dateFilter !== "all" && (
                        <span
                          role="button"
                          tabIndex={0}
                          onClick={(e) => { e.stopPropagation(); setDateFilter("all"); }}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); setDateFilter("all"); } }}
                          className="ml-auto rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                          aria-label="Clear date filter"
                        >
                          <X className="h-3 w-3" />
                        </span>
                      )}
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={calendarSelectedDate}
                      onSelect={(day) => {
                        if (day) {
                          setDateFilter(format(day, "yyyy-MM-dd"));
                        } else {
                          setDateFilter("all");
                        }
                        setCalendarOpen(false);
                      }}
                      disabled={(day) => !availableDateSet.has(format(day, "yyyy-MM-dd"))}
                      defaultMonth={calendarSelectedDate ?? new Date()}
                    />
                    {dateFilter !== "all" && (
                      <div className="border-t px-3 py-2">
                        <button
                          onClick={() => { setDateFilter("all"); setCalendarOpen(false); }}
                          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                        >
                          <X className="h-3 w-3" />
                          Clear date filter
                        </button>
                      </div>
                    )}
                  </PopoverContent>
                </Popover>
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
              {quickDateFilterItems.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {quickDateFilterItems.map((item) => {
                    const isActive = dateFilter === item.key;
                    return (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => setDateFilter(item.key)}
                        className={cn(
                          "rounded-full border px-2 py-1 text-[11px] font-medium transition-colors",
                          isActive
                            ? "border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-400"
                            : "border-input bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                        )}
                        aria-label={`${item.label} quick filter (${item.count} flights)`}
                      >
                        {item.label} ({item.count})
                      </button>
                    );
                  })}
                </div>
              )}
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

          {/* Currently Selected Flight Card */}
          {effectiveSelected && selectedFlight && (
            <div className="border-t px-3 py-3 bg-primary/5">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Currently Selected</div>
              <div className="rounded-lg border-2 border-primary bg-primary/10 p-3 text-left">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-bold text-sm text-primary">
                    {selectedFlight.airline}{selectedFlight.flightNumber}
                    {selectedFlight.flightSequenceNumber != null && (
                      <span className="ml-1.5 text-orange-500">#{selectedFlight.flightSequenceNumber}</span>
                    )}
                  </span>
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-[9px] px-1.5 font-medium border-transparent",
                      getStatusColor(selectedFlight.flightPhase?.phase || selectedFlight.status)
                    )}
                  >
                    {selectedFlight.flightPhase?.label || selectedFlight.status || "UNKNOWN"}
                  </Badge>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-foreground mb-1.5">
                  <span className="font-medium">{selectedFlight.origin}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground" />
                  <span className="font-medium">{selectedFlight.destination || "Pending"}</span>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {selectedFlight.departureDate}
                </div>
              </div>
            </div>
          )}

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
                        "w-full flex flex-col gap-1.5 rounded-lg p-3 text-left transition-colors",
                        isActive
                          ? "bg-emerald-600 text-white shadow-md ring-1 ring-emerald-400/50"
                          : "hover:bg-accent hover:text-accent-foreground text-foreground"
                      )}
                    >
                      {/* Row 1: Flight number + ingest button */}
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-base">
                          {flight.airline}{flight.flightNumber}
                        </span>
                        <div className="flex items-center gap-1.5">
                          <Badge 
                            variant="outline" 
                            className={cn(
                              "text-[10px] px-1.5 font-medium border-transparent max-w-[140px] truncate",
                              isActive 
                                ? "bg-white/20 text-white" 
                                : getStatusColor(flight.flightPhase?.phase || flight.status)
                            )}
                          >
                            {flight.flightPhase?.label || flight.status || "UNKNOWN"}
                          </Badge>
                          <span
                            role="button"
                            tabIndex={0}
                            title={ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? "Background ingest running" : "Re-ingest from Sabre"}
                            onClick={(e) => handleQuickIngest(flight, e)}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleQuickIngest(flight, e as unknown as React.MouseEvent); } }}
                            className={cn("shrink-0 rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10 transition-colors cursor-pointer", isActive && "hover:bg-white/20")}
                          >
                            {ingestingFlight === `${flight.flightNumber}-${flight.origin}-${flight.departureDate}` ? (
                              <span className="inline-flex items-center gap-1 text-[10px] font-medium">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                <span>Running</span>
                              </span>
                            ) : (
                              <CloudDownload className="h-3.5 w-3.5" />
                            )}
                          </span>
                        </div>
                      </div>
                      {/* Row 2: Route + date */}
                      <div className={cn("flex items-center gap-1.5 text-xs", isActive ? "text-white/80" : "text-muted-foreground")}>
                        <span>{flight.origin}</span>
                        <ArrowRight className="h-3 w-3" />
                        <span>{flight.destination || "Pending"}</span>
                        <span className="mx-0.5">·</span>
                        <span>{flight.departureDate}</span>
                        {/* Sequence number */}
                        {flight.flightSequenceNumber != null && (
                          <>
                            <span className="mx-0.5">·</span>
                            <span className={cn("font-medium tabular-nums", isActive ? "text-orange-300" : "text-orange-500 dark:text-orange-400")} title="Seq #">
                              {flight.flightSequenceNumber}
                            </span>
                          </>
                        )}
                      </div>
                    </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
        </ErrorBoundary>

        {/* Vertical Navigation Rail */}
        <div className={cn(
          "hidden md:flex flex-col border-r bg-muted/20 py-3 gap-0.5 shrink-0 transition-all duration-200",
          navCollapsed ? "w-12 items-center" : "w-36"
        )}>
          {([
            { key: "overview", icon: LayoutDashboard, label: "Live Ops" },
            { key: "commercial", icon: Briefcase, label: "Commercial" },
            { key: "readiness", icon: CheckCircle2, label: "Readiness" },
            { key: "exceptions", icon: ShieldAlert, label: "Exceptions" },
            { key: "passengers", icon: Users, label: "Passengers" },
            { key: "groups", icon: Users, label: "Groups" },
            { key: "standby", icon: Timer, label: "Standby" },
            { key: "changes", icon: Activity, label: "Changes" },
            { key: "history", icon: History, label: "History" },
            { key: "reservations", icon: BookOpen, label: "Reservations" },
            { key: "activity", icon: Clock, label: "Activity" },
            { key: "audit", icon: ShieldAlert, label: "Audit" },
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
          <div className="mt-2 border-t border-border pt-2">
            <button
              onClick={openTreeDialog}
              className={cn(
                "flex items-center gap-2 w-full px-3 py-2 rounded-md transition-all text-xs",
                navCollapsed && "justify-center px-2",
                "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
              title="Pax Tree"
            >
              <Network className="h-4 w-4 shrink-0" />
              {!navCollapsed && <span className="truncate">Pax Tree</span>}
            </button>
            <button
              onClick={openMatrixDialog}
              className={cn(
                "flex items-center gap-2 w-full px-3 py-2 rounded-md transition-all text-xs",
                navCollapsed && "justify-center px-2",
                "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
              title="Matrix"
            >
              <Table2 className="h-4 w-4 shrink-0" />
              {!navCollapsed && <span className="truncate">Matrix</span>}
            </button>
          </div>
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

        <PanelGroup orientation="horizontal" className="flex-1 min-w-0">
        {/* Console Workspace */}
        <Panel minSize="40">
          <ErrorBoundary label="Dashboard" key={effectiveSelected ? `${effectiveSelected.flightNumber}-${effectiveSelected.origin}-${effectiveSelected.date}` : "none"}>
          <main className="h-full w-full overflow-y-auto bg-muted/10 py-3 px-2 md:py-4 md:px-3">
            <div className="space-y-3">
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
                  <ErrorBoundary compact label="Flight Header">
                  <div className="rounded-lg border bg-card shadow-sm px-4 py-2.5">
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                      {/* Flight identity */}
                      <div className="flex items-center gap-3">
                        <h1 className="text-xl font-bold tracking-tight">
                          GF{effectiveSelected.flightNumber}
                          <span
                            className={cn("ml-2", dashboard?.flightSequenceNumber != null ? "text-orange-600 dark:text-orange-400" : "text-muted-foreground/50")}
                            title={dashboard?.flightSequenceNumber != null ? `Flight Sequence Number: ${dashboard.flightSequenceNumber}` : "Flight sequence number not available"}
                          >
                            {dashboard?.flightSequenceNumber ?? "N/A"}
                          </span>
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
                            title={(dashboard.dataIntegrity?.warnings ?? []).join(" | ")}
                          >
                            <ShieldAlert className="h-3.5 w-3.5" />
                            <span className="hidden sm:inline">{(dashboard.dataIntegrity?.warnings ?? []).length} warning{(dashboard.dataIntegrity?.warnings ?? []).length !== 1 ? "s" : ""}</span>
                          </span>
                        ) : null}
                        <span className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground">
                          <span className="font-medium text-foreground">{dashboard.route?.origin || effectiveSelected.origin}</span>
                          <ArrowRight className="h-3 w-3" />
                          <span className="font-medium text-foreground">{dashboard.route?.destination || selectedFlight?.destination || "—"}</span>
                          <span className="mx-0.5">•</span>
                          {effectiveSelected.date}
                          <span className="mx-0.5">•</span>
                          Gate {dashboard.flightStatus?.gate || "TBD"}
                        </span>
                      </div>

                      {/* KPI strip */}
                      <div className="flex items-center gap-4 text-xs overflow-x-auto scrollbar-none">
                        {/* Prominent souls on board */}
                        <button
                          className="flex items-center gap-1.5 bg-emerald-50 dark:bg-emerald-950/40 px-2 py-0.5 rounded-md border border-emerald-200 dark:border-emerald-800 hover:ring-1 hover:ring-emerald-400 transition-all cursor-pointer"
                          title="Passengers on Board — boarded passengers + their lap infants"
                          onClick={() => setBottomView(bottomView === "sob" ? null : "sob")}
                        >
                          <Users className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                          <span className="font-bold text-emerald-700 dark:text-emerald-300">{dashboard.overview?.soulsOnBoard ?? 0}</span>
                          <span className="text-emerald-600 dark:text-emerald-400">Passengers on Board</span>
                        </button>
                        <button
                          className="flex items-center gap-1.5 hover:bg-muted/50 rounded px-1 py-0.5 transition-colors cursor-pointer"
                          title="Souls on Manifest — all passengers + infants on the manifest"
                          onClick={() => setBottomView(bottomView === "souls" ? null : "souls")}
                        >
                          <Users className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview?.totalSouls ?? 0}</span>
                          <span className="text-muted-foreground">Souls on Manifest</span>
                        </button>
                        <button
                          className="flex items-center gap-1.5 hover:bg-muted/50 rounded px-1 py-0.5 transition-colors cursor-pointer"
                          title="Manifest Records — seated passengers on Sabre manifest"
                          onClick={() => setBottomView(bottomView === "records" ? null : "records")}
                        >
                          <Briefcase className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview?.manifestRecords ?? 0}</span>
                          <span className="text-muted-foreground">Manifest Records</span>
                        </button>
                        <div className="flex items-center gap-1.5" title="Change updates">
                          <Activity className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-semibold">{dashboard.overview?.trackedChanges ?? 0}</span>
                          <span className="text-muted-foreground">updates</span>
                        </div>
                        <Separator orientation="vertical" className="h-5 hidden md:block" />
                        <div className="hidden md:flex items-center gap-3 text-xs text-muted-foreground">
                          <span><span className="text-foreground font-medium">{dashboard.flightStatus?.aircraft?.type || dashboard.schedule?.aircraftType || "—"}</span> {(dashboard.flightStatus?.aircraft?.registration) ? `/ ${dashboard.flightStatus.aircraft.registration}` : ""}</span>
                          {/* Schedule with delay indicator */}
                          <ScheduleDelay schedule={dashboard.flightStatus?.schedule} />
                        </div>
                        {/* Gate & Codeshare */}
                        {(dashboard.departureGate || (dashboard.codeshareInfo && dashboard.codeshareInfo.length > 0)) && (
                          <>
                            <Separator orientation="vertical" className="h-5 hidden md:block" />
                            <div className="hidden md:flex items-center gap-2 text-xs">
                              {dashboard.departureGate && (
                                <span className="flex items-center gap-1">
                                  <span className="text-muted-foreground">Gate</span>
                                  <span className="font-semibold text-violet-600 dark:text-violet-400">{dashboard.departureGate}</span>
                                </span>
                              )}
                              {dashboard.codeshareInfo && dashboard.codeshareInfo.length > 0 && (
                                <div className="flex items-center gap-1">
                                  {dashboard.codeshareInfo.map((info) => (
                                    <Badge key={info} variant="outline" className="text-[9px] px-1.5 border-indigo-300 text-indigo-600">
                                      {info}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                            </div>
                          </>
                        )}
                        {/* Last-fetched timestamp */}
                        {dashboard.fetchedAt && (
                          <>
                            <Separator orientation="vertical" className="h-5 hidden md:block" />
                            <span className="hidden md:flex items-center gap-1 text-[10px] text-muted-foreground" title={`Data fetched at ${dashboard.fetchedAt}`}>
                              <Clock className="h-3 w-3" />
                              {new Date(dashboard.fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                            </span>
                          </>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1.5 text-xs"
                          onClick={openTreeDialog}
                        >
                          <Network className="h-3.5 w-3.5" />
                          <span className="hidden md:inline">Pax Tree</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1.5 text-xs"
                          onClick={openMatrixDialog}
                        >
                          <Table2 className="h-3.5 w-3.5" />
                          <span className="hidden md:inline">Matrix</span>
                        </Button>
                      </div>
                    </div>
                  </div>
                  </ErrorBoundary>

                  {snapshotSequence && (
                    <div className="rounded-lg border border-amber-300/60 bg-amber-50/70 dark:bg-amber-950/20 px-3 py-2 flex items-center justify-between gap-3">
                      <div className="text-xs text-amber-800 dark:text-amber-300">
                        Viewing historical data as-of snapshot sequence #{snapshotSequence}.
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        onClick={() => setSnapshotSequence(null)}
                      >
                        Return to latest
                      </Button>
                    </div>
                  )}

                  {/* Stats Strip — Row 1: Core metrics */}
                  <div className="flex gap-2.5 mt-2 overflow-x-auto scrollbar-none pb-0.5">
                    {/* Cabin */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground">Cabin</p>
                        <button
                          type="button"
                          onClick={() => openInfo("cabin")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Cabin is calculated"
                          aria-label="Cabin info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-4">
                        <button onClick={() => { setFilterCabin(filterCabin === "Y" ? "all" : "Y"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterCabin === "Y" && "ring-1 ring-emerald-500")}>
                          <p className="text-xl font-bold text-emerald-600">{dashboard.analysis?.economy?.total ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Economy</p>
                        </button>
                        <button onClick={() => { setFilterCabin(filterCabin === "J" ? "all" : "J"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterCabin === "J" && "ring-1 ring-amber-500")}>
                          <p className="text-xl font-bold text-amber-600">{dashboard.analysis?.business?.total ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Business</p>
                        </button>
                      </div>
                    </div>

                    {/* Status */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground">Status</p>
                        <button
                          type="button"
                          onClick={() => openInfo("status")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Status is calculated"
                          aria-label="Status info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-4">
                        <button onClick={() => { setFilterStatus(filterStatus === "boarded" ? "all" : "boarded"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterStatus === "boarded" && "ring-1 ring-emerald-500")}>
                          <p className="text-xl font-bold text-emerald-600">{dashboard.analysis?.boarded ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Boarded</p>
                        </button>
                        <button onClick={() => { setFilterStatus(filterStatus === "checkedIn" ? "all" : "checkedIn"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterStatus === "checkedIn" && "ring-1 ring-blue-500")}>
                          <p className="text-xl font-bold text-blue-600">{(dashboard.analysis?.checkedIn ?? 0) - (dashboard.analysis?.boarded ?? 0)}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Checked-In</p>
                        </button>
                        <button onClick={() => { setFilterStatus(filterStatus === "booked" ? "all" : "booked"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterStatus === "booked" && "ring-1 ring-amber-500")}>
                          <p className="text-xl font-bold text-amber-600">{dashboard.analysis?.notCheckedIn ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Booked</p>
                        </button>
                      </div>
                    </div>

                    {/* Type */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground">Type</p>
                        <button
                          type="button"
                          onClick={() => openInfo("type")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Type is calculated"
                          aria-label="Type info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-4">
                        <button onClick={() => { setFilterType(filterType === "revenue" ? "all" : "revenue"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "revenue" && "ring-1 ring-blue-500")}>
                          <p className="text-xl font-bold text-blue-600">{dashboard.analysis?.revenue ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Revenue</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "nonRevenue" ? "all" : "nonRevenue"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "nonRevenue" && "ring-1 ring-purple-500")}>
                          <p className="text-xl font-bold text-purple-600">{dashboard.analysis?.nonRevenue ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Non-Rev</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "child" ? "all" : "child"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "child" && "ring-1 ring-amber-500")}>
                          <p className="text-xl font-bold text-amber-500">{dashboard.analysis?.totalChildren ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Children</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "infant" ? "all" : "infant"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "infant" && "ring-1 ring-teal-500")}>
                          <p className="text-xl font-bold text-teal-500">{dashboard.analysis?.totalInfants ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">w/ Infant</p>
                        </button>
                      </div>
                    </div>

                    {/* Pax Mix */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground">Pax Mix</p>
                        <button
                          type="button"
                          onClick={() => openInfo("paxMix")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Pax Mix is calculated"
                          aria-label="Pax Mix info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-4">
                        <div className="text-center">
                          <p className="text-xl font-bold text-sky-600">{dashboard.analysis?.totalMale ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Male</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xl font-bold text-pink-500">{dashboard.analysis?.totalFemale ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Female</p>
                        </div>
                        <button onClick={() => { setFilterType(filterType === "child" ? "all" : "child"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "child" && "ring-1 ring-amber-500")}>
                          <p className="text-xl font-bold text-amber-500">{dashboard.analysis?.totalChildren ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Child</p>
                        </button>
                        <button onClick={() => { setFilterType(filterType === "infant" ? "all" : "infant"); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 -mx-1", filterType === "infant" && "ring-1 ring-teal-500")}>
                          <p className="text-xl font-bold text-teal-500">{dashboard.analysis?.totalInfants ?? 0}</p>
                          <p className="text-[10px] text-muted-foreground mt-0.5">Infant</p>
                        </button>
                      </div>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        Unknown gender: {Math.max(0, (dashboard.passengerSummary?.totalPassengers ?? 0) - (dashboard.analysis?.totalChildren ?? 0) - (dashboard.analysis?.totalMale ?? 0) - (dashboard.analysis?.totalFemale ?? 0))}
                      </p>
                    </div>

                    {/* Loyalty */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground flex items-center gap-1"><Star className="h-3 w-3 text-amber-500" /> Loyalty</p>
                        <button
                          type="button"
                          onClick={() => openInfo("loyalty")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Loyalty is calculated"
                          aria-label="Loyalty info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-3">
                        {[
                          { key: "FF" as FilterLoyalty, label: "FF", val: dashboard.analysis?.loyaltyCounts?.FF ?? 0, color: "text-amber-500" },
                          { key: "BLU" as FilterLoyalty, label: "Blu", val: dashboard.analysis?.loyaltyCounts?.BLU ?? 0, color: "text-blue-500" },
                          { key: "SLV" as FilterLoyalty, label: "Slv", val: dashboard.analysis?.loyaltyCounts?.SLV ?? 0, color: "text-gray-400" },
                          { key: "GLD" as FilterLoyalty, label: "Gld", val: dashboard.analysis?.loyaltyCounts?.GLD ?? 0, color: "text-yellow-500" },
                          { key: "BLK" as FilterLoyalty, label: "Blk", val: dashboard.analysis?.loyaltyCounts?.BLK ?? 0, color: "text-gray-900 dark:text-gray-100" },
                        ].map((t) => (
                          <button key={t.key} onClick={() => { setFilterLoyalty(filterLoyalty === t.key ? "all" : t.key); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1", filterLoyalty === t.key && "ring-1 ring-current")}>
                            <p className={cn("text-xl font-bold", t.color)}>{t.val}</p>
                            <p className="text-[10px] text-muted-foreground mt-0.5">{t.label}</p>
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Nationalities — all from database, interactive */}
                    <div className="rounded-lg border bg-card px-3 py-3 shadow-sm shrink-0">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] font-medium text-muted-foreground">Top Nationalities</p>
                        <button
                          type="button"
                          onClick={() => openInfo("nationalities")}
                          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                          title="How Top Nationalities is calculated"
                          aria-label="Top Nationalities info"
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="flex items-baseline gap-4 overflow-x-auto">
                        {(() => {
                          const nc = dashboard.analysis?.nationalityCounts ?? {};
                          const sorted = Object.entries(nc).sort((a, b) => b[1] - a[1]);
                          if (sorted.length === 0) return <p className="text-xs text-muted-foreground">No data</p>;
                          return sorted.map(([nat, count]) => (
                            <button key={nat} onClick={() => { setFilterNationality(filterNationality === nat ? "all" : nat); setActiveTab("passengers"); }} className={cn("text-center transition-colors rounded px-1.5 shrink-0", filterNationality === nat && "ring-1 ring-sky-500")}>
                              <p className="text-xl font-bold">{count}</p>
                              <p className="text-[10px] text-muted-foreground mt-0.5">{nat}</p>
                            </button>
                          ));
                        })()}
                      </div>
                    </div>


                  </div>

                  {/* Tab Navigation — now handled by vertical nav rail */}
                  <div>
                    <div className="rounded-lg border bg-card px-3 py-2 mb-2 flex items-center justify-between">
                      <div className="text-sm font-medium">{tabLabels[activeTab]}</div>
                      <div className="flex items-center gap-2 flex-wrap">
                        {/* Special Requests — inline in tab header */}
                        {dashboard.specialRequestsSummary && (dashboard.specialRequestsSummary.totalSpecialMeals > 0 || dashboard.specialRequestsSummary.totalWheelchairs > 0 || dashboard.specialRequestsSummary.frequentFlyers > 0) && (
                          <>
                            {dashboard.specialRequestsSummary.totalSpecialMeals > 0 && (
                              <span className="text-xs font-semibold text-orange-500">{dashboard.specialRequestsSummary.totalSpecialMeals} <span className="text-[10px] font-normal text-muted-foreground">Meals</span></span>
                            )}
                            {dashboard.specialRequestsSummary.frequentFlyers > 0 && (
                              <span className="text-xs font-semibold text-yellow-500">{dashboard.specialRequestsSummary.frequentFlyers} <span className="text-[10px] font-normal text-muted-foreground">FF</span></span>
                            )}
                            {dashboard.specialRequestsSummary.emergencyContacts > 0 && (
                              <span className="text-xs font-semibold text-rose-500">{dashboard.specialRequestsSummary.emergencyContacts} <span className="text-[10px] font-normal text-muted-foreground">ECnt</span></span>
                            )}
                            {Object.entries(dashboard.specialRequestsSummary?.specialMeals ?? {}).sort((a, b) => b[1] - a[1]).map(([code, count]) => (
                              <Badge key={code} variant="outline" className="text-[9px] px-1 py-0 border-orange-300 text-orange-600">{code}: {count}</Badge>
                            ))}
                            {Object.entries(dashboard.specialRequestsSummary?.ffTiers ?? {}).sort((a, b) => b[1] - a[1]).map(([tier, count]) => (
                              <Badge key={tier} variant="outline" className="text-[9px] px-1 py-0 border-yellow-300 text-yellow-600">{tier}: {count}</Badge>
                            ))}
                          </>
                        )}
                        {snapshotSequence && (
                          <Badge variant="outline" className="text-[10px]">
                            Snapshot #{snapshotSequence}
                          </Badge>
                        )}
                      </div>
                    </div>

                    {/* Overview Tab — live operations dashboard */}
                    {activeTab === "overview" && (
                      <ErrorBoundary compact label="Overview">
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
                        onFilterClick={handlePanelFilter}
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
                                <p className="text-lg font-bold">{dashboard.passengerSummary?.adultCount ?? 0}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-muted-foreground">Children</p>
                                <p className="text-lg font-bold text-amber-500">{dashboard.passengerSummary?.childCount ?? 0}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-muted-foreground">Infants</p>
                                <p className="text-lg font-bold text-emerald-500">{dashboard.passengerSummary?.infantCount ?? 0}</p>
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

                      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <BoardingProgress
                          flightNumber={effectiveSelected.flightNumber}
                          origin={effectiveSelected.origin}
                          date={effectiveSelected.date}
                        />
                        <FlightTimeline
                          flightNumber={effectiveSelected.flightNumber}
                          origin={effectiveSelected.origin}
                          date={effectiveSelected.date}
                        />
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
                                {dashboard.schedule.segments.map((seg) => (
                                  <div key={`${seg.origin}-${seg.destination}-${seg.departureDateTime ?? seg.arrivalDateTime ?? seg.aircraftType ?? "segment"}`} className="flex items-center gap-2 text-[10px] text-muted-foreground">
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
                          snapshotSequence={snapshotSequence}
                          dashboard={dashboard}
                          onClose={() => setBottomView(null)}
                          onSelectPassenger={(pnr) => {
                            setDetailPnr(pnr);
                            setDetailOpen(true);
                          }}
                        />
                      )}
                    </div>
                    </ErrorBoundary>
                    )}

                    {activeTab === "commercial" && dashboard.insights && (
                      <ErrorBoundary compact label="Commercial">
                        <CommercialInsightsTab
                          insights={dashboard.insights}
                          totalPassengers={dashboard.passengerSummary.totalPassengers}
                          onOpenInfo={openInsightInfo}
                        />
                      </ErrorBoundary>
                    )}
                    {activeTab === "commercial" && !dashboard.insights && (
                      <div className="flex items-center justify-center py-20 text-muted-foreground">
                        <p>No commercial insights data available for this flight.</p>
                      </div>
                    )}

                    {activeTab === "readiness" && dashboard.insights && (
                      <ErrorBoundary compact label="Readiness">
                        <ReadinessInsightsTab
                          insights={dashboard.insights}
                          totalPassengers={dashboard.passengerSummary.totalPassengers}
                          onOpenInfo={openInsightInfo}
                        />
                      </ErrorBoundary>
                    )}
                    {activeTab === "readiness" && !dashboard.insights && (
                      <div className="flex items-center justify-center py-20 text-muted-foreground">
                        <p>No readiness insights data available for this flight.</p>
                      </div>
                    )}

                    {activeTab === "exceptions" && dashboard.insights && (
                      <ErrorBoundary compact label="Exceptions">
                        <ExceptionsInsightsTab
                          insights={dashboard.insights}
                          totalPassengers={dashboard.passengerSummary.totalPassengers}
                          onOpenInfo={openInsightInfo}
                        />
                      </ErrorBoundary>
                    )}
                    {activeTab === "exceptions" && !dashboard.insights && (
                      <div className="flex items-center justify-center py-20 text-muted-foreground">
                        <p>No exception insights data available for this flight.</p>
                      </div>
                    )}

                    {/* Passengers Tab */}
                    {activeTab === "passengers" && (
                      <ErrorBoundary compact label="Passengers">
                      <div className="mt-1">
                      <PassengerTable
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                        snapshotSequence={snapshotSequence}
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
                        filterNationality={filterNationality}
                        setFilterNationality={setFilterNationality}
                      />
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* Groups Tab */}
                    {activeTab === "groups" && (
                      <ErrorBoundary compact label="Group Bookings">
                      <div className="mt-1">
                        {dashboard.groupBookingSummary && dashboard.groupBookingSummary.totalGroups > 0 ? (
                          <div className="flex gap-3 min-h-[300px]">
                            {/* Left: Group list */}
                            <div className={cn(
                              "flex flex-col gap-1 border rounded-lg bg-card overflow-y-auto transition-all",
                              selectedGroupCode ? "w-1/3 min-w-[220px]" : "w-full"
                            )}>
                              <div className="sticky top-0 z-10 bg-card border-b px-3 py-2 flex items-center justify-between">
                                <div className="flex items-center gap-1.5">
                                  <Users className="h-3.5 w-3.5 text-violet-500" />
                                  <span className="text-xs font-medium">Groups</span>
                                </div>
                                <Badge variant="secondary" className="text-[10px] px-1.5">
                                  {dashboard.groupBookingSummary.totalGroupPassengers} pax / {dashboard.groupBookingSummary.totalGroups} grp
                                </Badge>
                              </div>
                              {dashboard.groupBookingSummary.groups.map((g) => {
                                const hasUnnamed = g.unnamedMembers > 0;
                                const isSelected = selectedGroupCode === g.groupCode;
                                return (
                                  <button
                                    key={g.groupCode}
                                    onClick={() => setSelectedGroupCode(isSelected ? null : g.groupCode)}
                                    className={cn(
                                      "flex items-center justify-between px-3 py-2.5 text-xs text-left transition-all mx-1 rounded-md",
                                      isSelected
                                        ? "bg-violet-500/10 border border-violet-500/30"
                                        : "hover:bg-muted/50 border border-transparent",
                                      hasUnnamed && !isSelected && "bg-rose-500/5"
                                    )}
                                  >
                                    <div className="flex items-center gap-2 min-w-0">
                                      <span className="font-mono font-semibold text-violet-600 dark:text-violet-400 shrink-0">{g.groupCode}</span>
                                      {!selectedGroupCode && (
                                        <span className="text-muted-foreground truncate">PNR {g.pnr}</span>
                                      )}
                                      {!selectedGroupCode && (
                                        <Badge variant="outline" className={cn("text-[9px] px-1 shrink-0", g.cabin === "J" ? "border-amber-300 text-amber-600" : "border-emerald-300 text-emerald-600")}>
                                          {g.cabin === "J" ? "BIZ" : "ECO"} / {g.bookingClass}
                                        </Badge>
                                      )}
                                    </div>
                                    <div className="flex items-center gap-1.5 shrink-0 ml-2">
                                      <span className="font-medium">{g.totalMembers}</span>
                                      {hasUnnamed ? (
                                        <Badge className="bg-rose-500/15 text-rose-600 border-transparent text-[9px] px-1">
                                          {g.unnamedMembers} ?
                                        </Badge>
                                      ) : (
                                        <Badge className="bg-emerald-500/15 text-emerald-600 border-transparent text-[9px] px-1">
                                          \u2713
                                        </Badge>
                                      )}
                                      <ChevronRight className={cn("h-3 w-3 text-muted-foreground transition-transform", isSelected && "rotate-90")} />
                                    </div>
                                  </button>
                                );
                              })}
                            </div>

                            {/* Right: Member details */}
                            {selectedGroupCode && (() => {
                              const group = dashboard.groupBookingSummary!.groups.find((g) => g.groupCode === selectedGroupCode);
                              if (!group) return null;
                              return (
                                <div className="flex-1 border rounded-lg bg-card overflow-y-auto">
                                  <div className="sticky top-0 z-10 bg-card border-b px-4 py-2.5 flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                      <span className="font-mono font-semibold text-violet-600 dark:text-violet-400">{group.groupCode}</span>
                                      <span className="text-xs text-muted-foreground">PNR {group.pnr}</span>
                                      <Badge variant="outline" className={cn("text-[9px] px-1", group.cabin === "J" ? "border-amber-300 text-amber-600" : "border-emerald-300 text-emerald-600")}>
                                        {group.cabin === "J" ? "BIZ" : "ECO"} / {group.bookingClass}
                                      </Badge>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                                        <span>{group.totalMembers} pax</span>
                                        {group.boarded > 0 && <span className="text-emerald-500">{group.boarded} boarded</span>}
                                        {group.checkedIn > 0 && <span className="text-blue-500">{group.checkedIn} CKIN</span>}
                                      </div>
                                      <button onClick={() => setSelectedGroupCode(null)} className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground">
                                        <X className="h-3.5 w-3.5" />
                                      </button>
                                    </div>
                                  </div>

                                  {group.members && group.members.length > 0 ? (
                                    <div className="divide-y">
                                      {group.members.map((m, idx) => (
                                        <button
                                          key={m.passengerId || idx}
                                          onClick={() => {
                                            if (m.pnr) {
                                              setDetailPnr(m.pnr);
                                              setDetailOpen(true);
                                            }
                                          }}
                                          className="flex items-center justify-between w-full px-4 py-2.5 text-xs text-left hover:bg-muted/50 transition-colors"
                                        >
                                          <div className="flex items-center gap-3 min-w-0">
                                            <span className="text-muted-foreground w-5 text-right shrink-0">{m.lineNumber || idx + 1}</span>
                                            {m.isUnnamed ? (
                                              <span className="text-rose-500 italic">Unnamed Passenger</span>
                                            ) : (
                                              <span className="font-medium">
                                                {m.lastName}{m.firstName ? `, ${m.firstName}` : ""}
                                              </span>
                                            )}
                                          </div>
                                          <div className="flex items-center gap-2 shrink-0 ml-2">
                                            {m.seat && (
                                              <Badge variant="outline" className="text-[9px] px-1 font-mono">
                                                {m.seat}
                                              </Badge>
                                            )}
                                            {m.isBoarded ? (
                                              <Badge className="bg-emerald-500/15 text-emerald-600 border-transparent text-[9px] px-1.5">
                                                Boarded
                                              </Badge>
                                            ) : m.isCheckedIn ? (
                                              <Badge className="bg-blue-500/15 text-blue-600 border-transparent text-[9px] px-1.5">
                                                Checked In
                                              </Badge>
                                            ) : m.isUnnamed ? (
                                              <Badge className="bg-rose-500/15 text-rose-600 border-transparent text-[9px] px-1.5">
                                                Unnamed
                                              </Badge>
                                            ) : (
                                              <Badge variant="secondary" className="text-[9px] px-1.5">
                                                Booked
                                              </Badge>
                                            )}
                                          </div>
                                        </button>
                                      ))}
                                    </div>
                                  ) : (
                                    <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
                                      No member details available
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                        ) : (
                          <div className="rounded-lg border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
                            No group bookings on this flight
                          </div>
                        )}
                      </div>
                      </ErrorBoundary>
                    )}

                    {/* Standby Tab */}
                    {activeTab === "standby" && (
                      <ErrorBoundary compact label="Standby">
                      <div className="mt-1">
                      <StandbyPanel
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                        snapshotSequence={snapshotSequence}
                      />
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* Changes Tab */}
                    {activeTab === "changes" && (
                      <ErrorBoundary compact label="Changes">
                      <div className="mt-1">
                      <ChangeTimeline
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                      />
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* History Tab */}
                    {activeTab === "history" && (
                      <ErrorBoundary compact label="History">
                      <div className="mt-1">
                      <StatusHistory
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                        selectedSnapshotSequence={snapshotSequence}
                        onLoadSnapshot={(sequence) => setSnapshotSequence(sequence)}
                        onClearSnapshot={() => setSnapshotSequence(null)}
                        onRestoreComplete={() => {
                          setSnapshotSequence(null);
                          queryClient.invalidateQueries({ queryKey: ["dashboard"] });
                          queryClient.invalidateQueries({ queryKey: ["tree"] });
                          queryClient.invalidateQueries({ queryKey: ["passengers"] });
                          queryClient.invalidateQueries({ queryKey: ["standby"] });
                          queryClient.invalidateQueries({ queryKey: ["reservations"] });
                          queryClient.invalidateQueries({ queryKey: ["snapshots"] });
                        }}
                      />
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* Reservations Tab */}
                    {activeTab === "reservations" && (
                      <ErrorBoundary compact label="Reservations">
                      <div className="mt-1">
                      <ReservationView
                        flightNumber={effectiveSelected.flightNumber}
                        origin={effectiveSelected.origin}
                        date={effectiveSelected.date}
                        snapshotSequence={snapshotSequence}
                      />
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* Activity Tab */}
                    {activeTab === "activity" && (
                      <ErrorBoundary compact label="Activity">
                      <div className="mt-1 space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <BoardingProgress
                            flightNumber={effectiveSelected.flightNumber}
                            origin={effectiveSelected.origin}
                            date={effectiveSelected.date}
                          />
                          <div>
                            <FlightTimeline
                              flightNumber={effectiveSelected.flightNumber}
                              origin={effectiveSelected.origin}
                              date={effectiveSelected.date}
                            />
                          </div>
                        </div>
                      </div>
                    </ErrorBoundary>
                    )}

                    {/* Audit Tab */}
                    {activeTab === "audit" && (
                      <ErrorBoundary compact label="Audit">
                        <AuditPanel
                          flightNumber={effectiveSelected.flightNumber}
                          origin={effectiveSelected.origin}
                          date={effectiveSelected.date}
                          onSelectPassenger={(pnr) => {
                            setDetailPnr(pnr);
                            setDetailOpen(true);
                          }}
                        />
                      </ErrorBoundary>
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
          </ErrorBoundary>
        </Panel>

        {(ingestOpen || infoOpen || insightInfoOpen) && (
          <>
            <PanelResizeHandle className="relative flex w-px items-center justify-center bg-border focus-visible:outline-none data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full transition-colors hover:bg-primary" />
            
            <Panel defaultSize="35" minSize="25" maxSize="60" collapsible collapsedSize="0%">
              <aside className="flex h-full flex-col bg-background shadow-2xl border-l relative overflow-hidden">
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
                      <ErrorBoundary compact label="Ingestion">
                      <IngestionPanel />
                      </ErrorBoundary>
                    </div>
                  </>
                )}
                {infoOpen && (
                  <ErrorBoundary compact label="Tile Info">
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
                  </ErrorBoundary>
                )}
                {insightInfoOpen && (
                  <ErrorBoundary compact label="Insight Info">
                  <>
                    <div className="flex items-center justify-between border-b bg-muted/30 p-5 sticky top-0 z-10">
                      <div className="flex items-center gap-2">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/10">
                          <Info className="h-4 w-4 text-violet-500" />
                        </div>
                        <div>
                          <h2 className="text-lg font-semibold tracking-tight">Insight Documentation</h2>
                          <p className="text-sm text-muted-foreground">How this insight is calculated</p>
                        </div>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => setInsightInfoOpen(false)}>Close</Button>
                    </div>
                    <div className="flex-1 overflow-y-auto p-6 bg-muted/10">
                      <InsightInfoPanel activeKey={activeInsightInfo} />
                    </div>
                  </>
                  </ErrorBoundary>
                )}
              </aside>
            </Panel>
          </>
        )}
      </PanelGroup>
      </div>

      <Sheet
        open={treeDialogOpen}
        onOpenChange={(open) => {
          if (open) {
            openTreeDialog();
            return;
          }
          setTreeDialogOpen(false);
        }}
      >
        <SheetContent
          side="bottom"
          className="w-screen max-w-none rounded-none border-none p-0 data-[side=bottom]:!h-dvh data-[side=bottom]:!max-h-dvh"
          showCloseButton={false}
        >
          <SheetTitle className="sr-only">Passenger Tree Dialog</SheetTitle>
          <SheetDescription className="sr-only">Hierarchical breakdown of flight passengers.</SheetDescription>
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <h3 className="text-base font-semibold">Passenger Tree</h3>
                <p className="text-xs text-muted-foreground">Hierarchical breakdown of flight passengers.</p>
              </div>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => refetchTree()} disabled={treeFetching}>
                  <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", treeFetching && "animate-spin")} />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setTreeDialogOpen(false)}>Close</Button>
              </div>
            </div>
            <ErrorBoundary label="Passenger Tree">
            <div className="min-h-0 flex-1 overflow-auto bg-muted/10 p-4">
              {treeLoading ? (
                <div className="flex items-center justify-center p-20 text-muted-foreground">
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  Processing topology...
                </div>
              ) : tree ? (
                <PassengerTree tree={tree} mode="tree" onClose={() => setTreeDialogOpen(false)} />
              ) : (
                <div className="text-center text-muted-foreground mt-10">No tree data found for this flight.</div>
              )}
            </div>
            </ErrorBoundary>
          </div>
        </SheetContent>
      </Sheet>

      <Sheet
        open={matrixDialogOpen}
        onOpenChange={(open) => {
          if (open) {
            openMatrixDialog();
            return;
          }
          setMatrixDialogOpen(false);
        }}
      >
        <SheetContent
          side="bottom"
          className="w-screen max-w-none rounded-none border-none p-0 data-[side=bottom]:!h-dvh data-[side=bottom]:!max-h-dvh"
          showCloseButton={false}
        >
          <SheetTitle className="sr-only">Passenger Matrix Dialog</SheetTitle>
          <SheetDescription className="sr-only">Tabular passenger and status breakdown.</SheetDescription>
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <h3 className="text-base font-semibold">Passenger Matrix</h3>
                <p className="text-xs text-muted-foreground">Tabular passenger and status breakdown.</p>
              </div>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => refetchTree()} disabled={treeFetching}>
                  <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", treeFetching && "animate-spin")} />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setMatrixDialogOpen(false)}>Close</Button>
              </div>
            </div>
            <ErrorBoundary label="Passenger Matrix">
            <div className="min-h-0 flex-1 overflow-auto bg-muted/10 p-4">
              {treeLoading ? (
                <div className="flex items-center justify-center p-20 text-muted-foreground">
                  <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                  Processing matrix...
                </div>
              ) : tree ? (
                <PassengerTree tree={tree} mode="matrix" />
              ) : (
                <div className="text-center text-muted-foreground mt-10">No matrix data found for this flight.</div>
              )}
            </div>
            </ErrorBoundary>
          </div>
        </SheetContent>
      </Sheet>

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
                  This starts a background ingestion job, then refreshes the UI when Sabre sync completes.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={executeIngest}>
              <CloudDownload className="h-4 w-4 mr-2" />
              Start Background Ingest
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Mobile Tab Bar — visible below lg breakpoint */}
      <nav className="md:hidden flex shrink-0 border-t bg-card overflow-x-auto scrollbar-none">
        {([
          { key: "overview" as const, icon: LayoutDashboard, label: "Ops" },
          { key: "commercial" as const, icon: Briefcase, label: "Sales" },
          { key: "readiness" as const, icon: CheckCircle2, label: "Ready" },
          { key: "exceptions" as const, icon: ShieldAlert, label: "Risk" },
          { key: "passengers" as const, icon: Users, label: "Pax" },
          { key: "groups" as const, icon: Users, label: "Groups" },
          { key: "standby" as const, icon: Timer, label: "Standby" },
          { key: "changes" as const, icon: Activity, label: "Changes" },
          { key: "history" as const, icon: History, label: "History" },
          { key: "reservations" as const, icon: BookOpen, label: "Res" },
          { key: "audit" as const, icon: ShieldAlert, label: "Audit" },
        ]).map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={cn(
              "flex flex-col items-center gap-0.5 flex-1 min-w-[3.5rem] py-2 text-[10px] transition-colors",
              activeTab === key
                ? "text-primary font-semibold"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
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
