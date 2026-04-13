"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { format, subDays, parseISO, parse } from "date-fns";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  CalendarDays,
  Filter,
  Globe,
  Loader2,
  Plane,
  RefreshCw,
  Search,
  TrendingUp,
  Users,
  Utensils,
  Accessibility,
  ArrowUpRight,
  ArrowRightLeft,
  ShoppingBag,
  MapPin,
  X,
} from "lucide-react";

import { fetchNetworkAnalytics, fetchFlights } from "@/lib/api";
import type { NetworkAnalyticsResponse, FlightListItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ThemeToggle } from "@/components/theme-toggle";
import { getStatusColor } from "@/components/dashboard/workbench-utils";
import { FlightFilters } from "@/components/dashboard/flight-filters";

/* ─────────── lazy chart components ─────────── */
import { EChart } from "@/components/ui/echarts";

/* ─────────── constants ─────────── */

const COUNTRY_NAMES: Record<string, string> = {
  IN: "India", PH: "Philippines", MA: "Morocco", PK: "Pakistan", BD: "Bangladesh",
  BH: "Bahrain", GB: "United Kingdom", SA: "Saudi Arabia", KW: "Kuwait", EG: "Egypt",
  KE: "Kenya", NP: "Nepal", US: "United States", ID: "Indonesia", KR: "South Korea",
  TH: "Thailand", AE: "UAE", JP: "Japan", SG: "Singapore", NL: "Netherlands",
  IL: "Israel", LK: "Sri Lanka", IND: "India", PHL: "Philippines",
};

const MEAL_NAMES: Record<string, string> = {
  AVML: "Asian Veg", CHML: "Child", HNML: "Hindu Non-Veg", FPML: "Fruit Platter",
  VJML: "Vegetarian Jain", VGML: "Vegan", BLML: "Bland", DBML: "Diabetic",
  MOML: "Muslim", LCML: "Low Calorie", VOML: "Vegetarian Oriental",
  GFML: "Gluten Free", VLML: "Vegetarian Lacto", LFML: "Low Fat",
  NLML: "No Salt", KSML: "Kosher", LSML: "Low Sodium", SPML: "Special",
  SFML: "Seafood", BBML: "Baby", RVML: "Raw Vegan",
};

const WHEELCHAIR_NAMES: Record<string, string> = {
  WCHR: "Can walk short distance", WCHS: "Cannot walk steps",
  WCHC: "Completely immobile", WCMP: "Manual wheelchair",
  WCOB: "Onboard wheelchair",
};

const CHANGE_LABELS: Record<string, string> = {
  DOCUMENT_ADDED: "Doc Added", BOARDED: "Boarded", CHECKED_IN: "Checked In",
  SEAT_CHANGE: "Seat Change", BAG_COUNT_CHANGE: "Bag Change",
  PASSENGER_ADDED: "Pax Added", RESERVATION_REMOVED: "Res Removed",
  RESERVATION_ADDED: "Res Added", PASSENGER_REMOVED: "Pax Removed",
  COUNT_CHANGE: "Count Change", LOYALTY_STATUS_ADDED: "Loyalty Added",
  PRIORITY_CHANGE: "Priority Change", CLASS_CHANGE: "Class Change",
  CABIN_CHANGE: "Cabin Change", UPGRADE_CONFIRMED: "Upgrade",
  RESERVATION_PARTY_CHANGE: "Party Change", TERMINAL_CHANGE: "Terminal",
  GATE_CHANGE: "Gate Change", STATUS_CHANGE: "Status Change",
  BOARDING_TIME_CHANGE: "Board Time",
};

const CLASS_COLORS: Record<string, string> = {
  Q: "#38bdf8", L: "#a78bfa", Y: "#34d399", M: "#f59e0b", W: "#fb7185",
  E: "#22c55e", X: "#f97316", S: "#06b6d4", K: "#818cf8", H: "#e879f9",
  N: "#facc15", J: "#3b82f6", B: "#ef4444", V: "#14b8a6", G: "#f472b6",
};

const ECHARTS_TEXT_COLOR = "#e2e8f0";
const ECHARTS_AXIS_COLOR = "#94a3b8";
const ECHARTS_SPLIT_LINE = "rgba(148, 163, 184, 0.16)";
const PALETTE = ["#38bdf8", "#a78bfa", "#34d399", "#f59e0b", "#fb7185", "#22c55e", "#f97316", "#06b6d4", "#818cf8", "#e879f9", "#facc15", "#f472b6", "#14b8a6", "#ef4444", "#3b82f6"];

/* ─────────── helper components ─────────── */

function KpiCard({ icon: Icon, label, value, sub, accent }: {
  icon: typeof Plane; label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <div className={cn(
      "rounded-xl border bg-card/60 backdrop-blur p-4 flex flex-col gap-1",
      accent ? `border-l-4 ${accent}` : "border-border/40"
    )}>
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <p className="text-2xl font-bold tracking-tight text-foreground">
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}

function SectionCard({ title, icon: Icon, children, className }: {
  title: string; icon: typeof BarChart3; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("rounded-xl border border-border/40 bg-card/60 backdrop-blur", className)}>
      <div className="flex items-center gap-2 px-5 py-3.5 border-b border-border/30">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

/* ─────────── chart builders ─────────── */

function fmtVal(v: number) {
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
  return v.toLocaleString();
}

function buildBarOption(labels: string[], values: number[], color: string, yAxisName?: string) {
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "shadow" as const },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
    },
    grid: { left: 8, right: 16, top: 20, bottom: 8, containLabel: true },
    xAxis: {
      type: "category" as const,
      data: labels,
      axisLabel: { color: ECHARTS_AXIS_COLOR, rotate: labels.length > 10 ? 45 : 0, fontSize: 11 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    yAxis: {
      type: "value" as const,
      name: yAxisName,
      nameTextStyle: { color: ECHARTS_AXIS_COLOR, fontSize: 11 },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: [{
      type: "bar" as const,
      data: values,
      itemStyle: { color, borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 40,
      label: {
        show: true,
        position: "top" as const,
        color: ECHARTS_TEXT_COLOR,
        fontSize: 10,
        fontWeight: 600,
        formatter: (p: any) => fmtVal(p.value as number),
      },
    }],
  };
}

function buildHorizontalBarOption(labels: string[], values: number[], colors: string[]) {
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "shadow" as const },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
    },
    grid: { left: 8, right: 50, top: 8, bottom: 8, containLabel: true },
    xAxis: {
      type: "value" as const,
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    yAxis: {
      type: "category" as const,
      data: labels,
      axisLabel: { color: ECHARTS_TEXT_COLOR, fontSize: 11 },
      axisTick: { show: false },
      axisLine: { show: false },
    },
    series: [{
      type: "bar" as const,
      data: values.map((v, i) => ({ value: v, itemStyle: { color: colors[i % colors.length] } })),
      barMaxWidth: 22,
      itemStyle: { borderRadius: [0, 4, 4, 0] },
      label: {
        show: true,
        position: "right" as const,
        color: ECHARTS_TEXT_COLOR,
        fontSize: 10,
        fontWeight: 600,
        formatter: (p: any) => fmtVal(p.value as number),
      },
    }],
  };
}

function buildDonutOption(data: { name: string; value: number; color?: string }[], centerLabel: string) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return {
    animationDuration: 450,
    color: data.map((d, i) => d.color ?? PALETTE[i % PALETTE.length]),
    tooltip: {
      trigger: "item" as const,
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params: any) => {
        const item = Array.isArray(params) ? params[0] : params;
        return `${item?.name ?? ""}<br/>${(item?.value ?? 0).toLocaleString()} (${item?.percent ?? 0}%)`;
      },
    },
    series: [{
      type: "pie" as const,
      radius: ["55%", "80%"],
      center: ["50%", "52%"],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 8, borderColor: "rgba(15, 23, 42, 0.85)", borderWidth: 2 },
      label: {
        show: true,
        position: "outside" as const,
        color: ECHARTS_TEXT_COLOR,
        fontSize: 11,
        fontWeight: 600,
        formatter: (p: any) => `${p.name}\n${(p.value as number).toLocaleString()} (${p.percent}%)`,
      },
      labelLine: { show: true, length: 8, length2: 12, lineStyle: { color: ECHARTS_AXIS_COLOR } },
      emphasis: { scale: true, label: { show: true, color: ECHARTS_TEXT_COLOR, fontWeight: 700, fontSize: 12, formatter: "{b}\n{c} ({d}%)" } },
      data: data.map((d, i) => ({ name: d.name, value: d.value, itemStyle: { color: d.color ?? PALETTE[i % PALETTE.length] } })),
    }],
    graphic: [
      { type: "text", left: "center", top: "44%", style: { text: centerLabel, fill: ECHARTS_AXIS_COLOR, fontSize: 11, fontWeight: 600 } },
      { type: "text", left: "center", top: "53%", style: { text: total.toLocaleString(), fill: ECHARTS_TEXT_COLOR, fontSize: 22, fontWeight: 700 } },
    ],
  };
}

function buildMultiLineAreaOption(
  dates: string[],
  series: { name: string; data: number[]; color: string }[],
) {
  return {
    animationDuration: 450,
    color: series.map((s) => s.color),
    legend: { top: 0, textStyle: { color: ECHARTS_AXIS_COLOR }, data: series.map((s) => s.name) },
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "cross" as const, label: { backgroundColor: "#334155", color: "#e2e8f0" } },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
    },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: {
      type: "category" as const,
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisTick: { show: false },
      axisLabel: { color: ECHARTS_AXIS_COLOR, fontSize: 11 },
    },
    yAxis: {
      type: "value" as const,
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: series.map((s) => ({
      name: s.name,
      type: "line" as const,
      smooth: true,
      showSymbol: true,
      symbolSize: 6,
      lineStyle: { width: 2, color: s.color },
      areaStyle: { opacity: 0.18, color: s.color },
      emphasis: { focus: "series" as const },
      data: s.data,
      label: {
        show: true,
        position: "top" as const,
        color: s.color,
        fontSize: 10,
        fontWeight: 600,
        formatter: (p: any) => fmtVal(p.value as number),
      },
    })),
  };
}

/* ─────────── MAIN PAGE ─────────── */

/* ─────────── cabin labels ─────────── */
const CABIN_LABELS: Record<string, string> = { Y: "Economy", J: "Business" };

export default function AnalyticsPage() {
  const searchParams = useSearchParams();

  /* ── URL params from main flight screen ── */
  const urlFlight = searchParams.get("flight") || "";
  const urlOrigin = searchParams.get("origin") || "";
  const urlDestination = searchParams.get("destination") || "";
  const urlDate = searchParams.get("date") || "";

  const defaultTo = new Date();
  const defaultFrom = subDays(defaultTo, 25);

  /* ── date state ── */
  const [dateFrom, setDateFrom] = useState(() =>
    urlDate ? parseISO(urlDate) : defaultFrom
  );
  const [dateTo, setDateTo] = useState(() =>
    urlDate ? parseISO(urlDate) : defaultTo
  );
  const [fromOpen, setFromOpen] = useState(false);
  const [toOpen, setToOpen] = useState(false);

  /* ── filter state ── */
  const [selectedOrigin, setSelectedOrigin] = useState<string>(urlOrigin);
  const [selectedCabin, setSelectedCabin] = useState<string>("");
  const [selectedFlight, setSelectedFlight] = useState<string>(urlFlight);
  const [selectedDestination, setSelectedDestination] = useState<string>(urlDestination);
  const [selectedDate, setSelectedDate] = useState<string>(urlDate);

  /* ── flight list filters (same as main screen) ── */
  const [flightSearch, setFlightSearch] = useState("");
  const [dateFilter, setDateFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [calendarOpen, setCalendarOpen] = useState(false);

  const dateFromStr = format(dateFrom, "yyyy-MM-dd");
  const dateToStr = format(dateTo, "yyyy-MM-dd");

  /* ── fetch flights (same data as main screen) ── */
  const { data: flights, isLoading: flightsLoading, refetch: refetchFlights, isFetching: flightsFetching } = useQuery({
    queryKey: ["flights"],
    queryFn: fetchFlights,
    staleTime: 60_000,
  });

  /* ── flights filtered to date range ── */
  const flightsInRange = useMemo(() => {
    if (!flights) return [];
    return flights.filter((f) => f.departureDate >= dateFromStr && f.departureDate <= dateToStr);
  }, [flights, dateFromStr, dateToStr]);

  /* ── relative date map ── */
  const relativeDateMap = useMemo(() => {
    const toIso = (d: Date) => {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const dy = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${dy}`;
    };
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
    return { yesterday: toIso(yesterday), today: toIso(now), tomorrow: toIso(tomorrow) };
  }, []);

  const calendarSelectedDate = useMemo(() => {
    if (dateFilter === "all") return undefined;
    const resolved = (relativeDateMap as Record<string, string>)[dateFilter] ?? dateFilter;
    return parse(resolved, "yyyy-MM-dd", new Date());
  }, [dateFilter, relativeDateMap]);

  const availableDateSet = useMemo(() =>
    new Set(flightsInRange.map((f) => f.departureDate)),
  [flightsInRange]);

  const availableStatuses = useMemo(() =>
    [...new Set(flightsInRange.map((f) => f.status).filter(Boolean))].sort(),
  [flightsInRange]);

  const quickDateFilterItems = useMemo(() => {
    const presets = [
      { key: "yesterday", label: "Yesterday" },
      { key: "today", label: "Today" },
      { key: "tomorrow", label: "Tomorrow" },
    ] as const;
    return presets
      .map((item) => {
        const date = relativeDateMap[item.key];
        const count = flightsInRange.filter((f) => f.departureDate === date).length;
        return { ...item, count };
      })
      .filter((item) => item.count > 0);
  }, [flightsInRange, relativeDateMap]);

  /* ── filtered flight list for sidebar ── */
  const filteredFlights = useMemo(() => {
    const list = flightsInRange.filter((flight) => {
      if (dateFilter !== "all") {
        const relDate = relativeDateMap[dateFilter as keyof typeof relativeDateMap];
        if (relDate) {
          if (flight.departureDate !== relDate) return false;
        } else if (flight.departureDate !== dateFilter) return false;
      }
      if (statusFilter !== "all" && flight.status !== statusFilter) return false;
      if (flightSearch) {
        const hay = [flight.airline, flight.flightNumber, flight.origin, flight.destination, flight.status]
          .filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(flightSearch.toLowerCase())) return false;
      }
      return true;
    });
    list.sort((a, b) => {
      const dc = b.departureDate.localeCompare(a.departureDate);
      return dc !== 0 ? dc : a.flightNumber.localeCompare(b.flightNumber);
    });
    return list;
  }, [flightsInRange, dateFilter, statusFilter, flightSearch, relativeDateMap]);

  /* ── fetch analytics data with filters ── */
  const filters = useMemo(() => ({
    ...(selectedOrigin ? { origin: selectedOrigin } : {}),
    ...(selectedCabin ? { cabin: selectedCabin } : {}),
    ...(selectedFlight ? { flightNumber: selectedFlight } : {}),
    ...(selectedDestination ? { destination: selectedDestination } : {}),
  }), [selectedOrigin, selectedCabin, selectedFlight, selectedDestination]);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["network-analytics", dateFromStr, dateToStr, filters],
    queryFn: () => fetchNetworkAnalytics(dateFromStr, dateToStr, filters),
    staleTime: 60_000,
  });

  const activeFilterCount = [selectedOrigin, selectedCabin, selectedFlight, selectedDestination].filter(Boolean).length;

  const clearAllFilters = () => {
    setSelectedOrigin("");
    setSelectedCabin("");
    setSelectedFlight("");
    setSelectedDestination("");
    setSelectedDate("");
    setFlightSearch("");
  };

  /* ── the full FlightListItem for the selected flight ── */
  const selectedFlightItem = useMemo(() => {
    if (!selectedFlight || !flights) return null;
    return flights.find((f) =>
      f.flightNumber === selectedFlight &&
      f.origin === selectedOrigin &&
      (!selectedDate || f.departureDate === selectedDate)
    ) ?? null;
  }, [flights, selectedFlight, selectedOrigin, selectedDate]);

  return (
    <div className="flex h-screen flex-col bg-background">
      {/* ─── Header ─── */}
      <header className="sticky top-0 z-30 shrink-0 border-b border-border/40 bg-background/80 backdrop-blur-lg">
        <div className="flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-4">
            <Link href="/" className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors">
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-sky-400" />
              <h1 className="text-lg font-bold tracking-tight">Network Analytics</h1>
            </div>
            <Badge variant="outline" className="text-xs font-mono">Gulf Air</Badge>
            {activeFilterCount > 0 && (
              <Badge variant="secondary" className="text-xs font-medium">
                <Filter className="h-3 w-3 mr-1" />
                {activeFilterCount} filter{activeFilterCount > 1 ? "s" : ""} active
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="rounded-lg border border-border/50 bg-muted/30 p-1.5 hover:bg-muted/50 transition-colors disabled:opacity-50"
              aria-label="Refresh"
            >
              <RefreshCw className={cn("h-4 w-4 text-muted-foreground", isFetching && "animate-spin")} />
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* ─── Body: Sidebar + Content ─── */}
      <div className="flex flex-1 overflow-hidden">
        {/* ─── Left Sidebar ─── */}
        <aside className="hidden md:flex w-[280px] shrink-0 flex-col h-full border-r border-border/40 bg-muted/30">
          {/* Sidebar header — date range + cabin + flight filters */}
          <div className="px-4 pt-4 pb-3 border-b border-border/30 space-y-3">
            {/* Date Range */}
            <div className="space-y-1.5">
              <label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Date Range
              </label>
              <div className="flex items-center gap-2">
                <Popover open={fromOpen} onOpenChange={setFromOpen}>
                  <PopoverTrigger asChild>
                    <button className={cn(
                      "flex-1 flex items-center gap-1.5 rounded-md border py-1.5 px-2 text-xs shadow-sm transition-colors",
                      "border-input bg-background text-foreground hover:bg-accent"
                    )}>
                      <CalendarDays className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{format(dateFrom, "MMM d")}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start" side="right">
                    <Calendar mode="single" selected={dateFrom} onSelect={(d) => { if (d) { setDateFrom(d); setFromOpen(false); } }} />
                  </PopoverContent>
                </Popover>
                <span className="text-[10px] text-muted-foreground">→</span>
                <Popover open={toOpen} onOpenChange={setToOpen}>
                  <PopoverTrigger asChild>
                    <button className={cn(
                      "flex-1 flex items-center gap-1.5 rounded-md border py-1.5 px-2 text-xs shadow-sm transition-colors",
                      "border-input bg-background text-foreground hover:bg-accent"
                    )}>
                      <CalendarDays className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{format(dateTo, "MMM d")}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start" side="right">
                    <Calendar mode="single" selected={dateTo} onSelect={(d) => { if (d) { setDateTo(d); setToOpen(false); } }} />
                  </PopoverContent>
                </Popover>
              </div>
            </div>

            {/* Cabin toggle */}
            <div className="space-y-1.5">
              <label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Cabin
              </label>
              <div className="flex gap-1.5">
                {["", "Y", "J"].map((c) => {
                  const active = selectedCabin === c;
                  return (
                    <button
                      key={c}
                      onClick={() => setSelectedCabin(c)}
                      className={cn(
                        "flex-1 rounded-md border py-1.5 text-xs font-medium transition-colors",
                        active
                          ? c === "Y" ? "border-sky-500 bg-sky-500/15 text-sky-400"
                            : c === "J" ? "border-violet-500 bg-violet-500/15 text-violet-400"
                            : "border-emerald-500 bg-emerald-500/15 text-emerald-400"
                          : "border-border/50 bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                      )}
                    >
                      {c ? CABIN_LABELS[c] : "All"}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Flights header + FlightFilters */}
            <div className="flex items-center gap-2 pt-1">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex-1">
                Flights
                {flightsInRange.length > 0 && (
                  <span className="ml-1.5 text-[10px] font-normal text-muted-foreground/70">
                    ({filteredFlights.length})
                  </span>
                )}
              </span>
              <button
                onClick={() => refetchFlights()}
                disabled={flightsFetching}
                className="rounded-md p-1 hover:bg-accent transition-colors"
              >
                <RefreshCw className={cn("h-3.5 w-3.5 text-muted-foreground", flightsFetching && "animate-spin")} />
              </button>
            </div>
            <FlightFilters
              dateFilter={dateFilter}
              setDateFilter={setDateFilter}
              statusFilter={statusFilter}
              setStatusFilter={setStatusFilter}
              calendarOpen={calendarOpen}
              setCalendarOpen={setCalendarOpen}
              calendarSelectedDate={calendarSelectedDate}
              availableDateSet={availableDateSet}
              availableStatuses={availableStatuses}
              quickDateFilterItems={quickDateFilterItems}
            />
          </div>

          {/* Currently Selected Flight Card */}
          {selectedFlightItem && (
            <div className="border-b border-border/30 px-3 py-3 bg-primary/5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Currently Selected</span>
                <button
                  onClick={clearAllFilters}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              <div className="rounded-lg border-2 border-primary bg-primary/10 p-3 text-left">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-bold text-sm text-primary">
                    {selectedFlightItem.airline}{selectedFlightItem.flightNumber}
                  </span>
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-[9px] px-1.5 font-medium border-transparent",
                      getStatusColor(selectedFlightItem.flightPhase?.phase || selectedFlightItem.status)
                    )}
                  >
                    {selectedFlightItem.flightPhase?.label || selectedFlightItem.status || "UNKNOWN"}
                  </Badge>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-foreground mb-1.5">
                  <span className="font-medium">{selectedFlightItem.origin}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground" />
                  <span className="font-medium">{selectedFlightItem.destination || "Pending"}</span>
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {selectedFlightItem.departureDate}
                </div>
              </div>
            </div>
          )}

          {/* Flight list */}
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
                    flight.flightNumber === selectedFlight &&
                    flight.origin === selectedOrigin &&
                    flight.departureDate === selectedDate;
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
                        onClick={() => {
                          if (isActive) {
                            setSelectedFlight("");
                            setSelectedOrigin("");
                            setSelectedDestination("");
                            setSelectedDate("");
                          } else {
                            setSelectedFlight(flight.flightNumber);
                            setSelectedOrigin(flight.origin);
                            setSelectedDestination(flight.destination || "");
                            setSelectedDate(flight.departureDate);
                          }
                        }}
                        className={cn(
                          "w-full flex flex-col gap-1.5 rounded-lg p-3 text-left transition-colors",
                          isActive
                            ? "bg-emerald-600 text-white shadow-md ring-1 ring-emerald-400/50"
                            : "hover:bg-accent hover:text-accent-foreground text-foreground"
                        )}
                      >
                        {/* Row 1: Flight number + status badge */}
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-base">
                            {flight.airline}{flight.flightNumber}
                          </span>
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
                        </div>
                        {/* Row 2: Route + date */}
                        <div className={cn("flex items-center gap-1.5 text-xs", isActive ? "text-white/80" : "text-muted-foreground")}>
                          <span>{flight.origin}</span>
                          <ArrowRight className="h-3 w-3" />
                          <span>{flight.destination || "Pending"}</span>
                          <span className="mx-0.5">·</span>
                          <span>{flight.departureDate}</span>
                        </div>
                      </button>
                    </div>
                  );
                })}
                {filteredFlights.length === 0 && (
                  <div className="py-8 text-center text-xs text-muted-foreground">
                    No flights in range
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Sidebar footer — active filter summary */}
          {activeFilterCount > 0 && (
            <div className="border-t border-border/30 px-4 py-3 bg-primary/5">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                Active Filters
              </div>
              <div className="space-y-1">
                {selectedOrigin && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Station</span>
                    <span className="font-medium text-sky-400">{selectedOrigin}</span>
                  </div>
                )}
                {selectedCabin && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Cabin</span>
                    <span className={cn("font-medium", selectedCabin === "Y" ? "text-sky-400" : "text-violet-400")}>
                      {CABIN_LABELS[selectedCabin]}
                    </span>
                  </div>
                )}
                {selectedFlight && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Flight</span>
                    <span className="font-medium font-mono text-amber-400">GF{selectedFlight}</span>
                  </div>
                )}
                {selectedDestination && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Destination</span>
                    <span className="font-medium text-emerald-400">{selectedDestination}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </aside>

        {/* ─── Main Content ─── */}
        <main id="main-content" className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] px-6 py-6 space-y-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-32">
                <Loader2 className="h-8 w-8 animate-spin text-sky-400" />
                <span className="ml-3 text-sm text-muted-foreground">Loading network analytics…</span>
              </div>
            ) : error ? (
              <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-8 text-center">
                <p className="text-sm text-red-400">Failed to load analytics: {String(error)}</p>
              </div>
            ) : data ? (
              <AnalyticsDashboard data={data} />
            ) : null}
          </div>
        </main>
      </div>
    </div>
  );
}

/* ─────────── Dashboard content ─────────── */

function AnalyticsDashboard({ data }: { data: NetworkAnalyticsResponse }) {
  const { overview, routePerformance, bookingClassDistribution, nationalityDistribution,
    loyaltyDistribution, mealDistribution, wheelchairDistribution, changeDistribution,
    dailyTrends, partySizeDistribution, pointOfSaleDistribution, operationalRates,
    upgradeAnalytics, bookingLeadTime, totalChanges } = data;

  /* ── computed chart data ── */

  const routeChartData = useMemo(() => {
    const sorted = [...routePerformance].sort((a, b) => b.totalPassengers - a.totalPassengers).slice(0, 15);
    return {
      labels: sorted.map((r) => r.origin),
      values: sorted.map((r) => r.totalPassengers),
    };
  }, [routePerformance]);

  const cabinDonut = useMemo(() => [
    { name: "Economy", value: overview.economyPassengers, color: "#38bdf8" },
    { name: "Business", value: overview.businessPassengers, color: "#a78bfa" },
  ], [overview]);

  const classChart = useMemo(() => {
    const econ = bookingClassDistribution.filter((c) => c.cabin === "Y").slice(0, 12);
    const biz = bookingClassDistribution.filter((c) => c.cabin === "J").slice(0, 5);
    return { economy: econ, business: biz };
  }, [bookingClassDistribution]);

  const nationalityChart = useMemo(() => ({
    labels: nationalityDistribution.map((n) => COUNTRY_NAMES[n.nationality] ?? n.nationality),
    values: nationalityDistribution.map((n) => n.count),
  }), [nationalityDistribution]);

  const loyaltyDonut = useMemo(() =>
    loyaltyDistribution.filter((l) => l.tier).map((l, i) => ({
      name: l.tierName, value: l.count, color: PALETTE[i % PALETTE.length],
    })),
  [loyaltyDistribution]);

  const mealChart = useMemo(() => ({
    labels: mealDistribution.map((m) => MEAL_NAMES[m.mealCode] ?? m.mealCode),
    values: mealDistribution.map((m) => m.count),
  }), [mealDistribution]);

  const changeChart = useMemo(() => {
    const top = changeDistribution.slice(0, 12);
    return {
      labels: top.map((c) => CHANGE_LABELS[c.changeType] ?? c.changeType),
      values: top.map((c) => c.count),
    };
  }, [changeDistribution]);

  const trendDates = useMemo(() => dailyTrends.map((d) => d.date.slice(5)), [dailyTrends]);
  const trendSeries = useMemo(() => [
    { name: "Passengers", data: dailyTrends.map((d) => d.passengers), color: "#38bdf8" },
    { name: "Flights", data: dailyTrends.map((d) => d.flights * 10), color: "#a78bfa" },
    { name: "Changes", data: dailyTrends.map((d) => d.changes), color: "#f59e0b" },
  ], [dailyTrends]);

  const partySizeDonut = useMemo(() => {
    const labels: Record<number, string> = { 1: "Solo", 2: "Couple", 3: "3 pax", 4: "4 pax", 5: "5 pax" };
    const grouped = partySizeDistribution.reduce<{ name: string; value: number }[]>((acc, p) => {
      const name = p.partySize <= 5 ? (labels[p.partySize] ?? `${p.partySize}`) : "6+ pax";
      const existing = acc.find((a) => a.name === name);
      if (existing) existing.value += p.count;
      else acc.push({ name, value: p.count });
      return acc;
    }, []);
    return grouped;
  }, [partySizeDistribution]);

  const posChart = useMemo(() => ({
    labels: pointOfSaleDistribution.map((p) => COUNTRY_NAMES[p.country] ?? p.country),
    values: pointOfSaleDistribution.map((p) => p.count),
  }), [pointOfSaleDistribution]);

  const leadTimeDonut = useMemo(() => [
    { name: "Same Day", value: bookingLeadTime.sameDay, color: "#ef4444" },
    { name: "Within 7d", value: bookingLeadTime.within7d, color: "#f59e0b" },
    { name: "Within 30d", value: bookingLeadTime.within30d, color: "#38bdf8" },
    { name: "Within 90d", value: bookingLeadTime.within90d, color: "#a78bfa" },
    { name: "Over 90d", value: bookingLeadTime.over90d, color: "#34d399" },
  ], [bookingLeadTime]);

  const operationalDonut = useMemo(() => [
    { name: "Boarded", value: operationalRates.boarded, color: "#34d399" },
    { name: "Checked In (not boarded)", value: operationalRates.checkedIn - operationalRates.boarded, color: "#a78bfa" },
    { name: "Not Checked In", value: operationalRates.totalPassengers - operationalRates.checkedIn, color: "#94a3b8" },
  ], [operationalRates]);

  return (
    <>
      {/* ── KPI Row ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        <KpiCard icon={Plane} label="Total Flights" value={overview.totalFlights} sub={`${overview.uniqueRoutes} routes · ${overview.uniqueStations} stations`} accent="border-l-sky-400" />
        <KpiCard icon={Users} label="Total Passengers" value={overview.totalPassengers} sub={`${overview.totalSouls} souls`} accent="border-l-emerald-400" />
        <KpiCard icon={TrendingUp} label="Avg Load Factor" value={`${overview.avgLoadFactor}%`} sub={`Avg ${overview.avgPassengersPerFlight} pax/flight`} accent="border-l-amber-400" />
        <KpiCard icon={ArrowUpRight} label="Upgrades" value={upgradeAnalytics.cabinChanges} sub={`${upgradeAnalytics.upgradesConfirmed} confirmed`} accent="border-l-violet-400" />
        <KpiCard icon={ArrowRightLeft} label="Total Changes" value={totalChanges} sub={`${changeDistribution.length} types`} accent="border-l-rose-400" />
        <KpiCard icon={BarChart3} label="Days Tracked" value={overview.daysTracked} sub={`${data.dateRange.from} → ${data.dateRange.to}`} accent="border-l-cyan-400" />
      </div>

      {/* ── Daily Trends ── */}
      <SectionCard title="Daily Trends — Passengers · Flights (×10) · Changes" icon={TrendingUp}>
        <EChart option={buildMultiLineAreaOption(trendDates, trendSeries)} style={{ height: 280 }} />
      </SectionCard>

      {/* ── Row 1: Route Performance + Cabin Mix ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <SectionCard title="Top Routes by Passengers" icon={MapPin} className="lg:col-span-2">
          <EChart option={buildBarOption(routeChartData.labels, routeChartData.values, "#38bdf8", "Passengers")} style={{ height: 300 }} />
          <div className="mt-4 grid grid-cols-3 sm:grid-cols-5 gap-2">
            {routePerformance.slice(0, 5).map((r) => (
              <div key={r.origin} className="rounded-lg bg-muted/30 p-2 text-center">
                <p className="text-lg font-bold text-foreground">{r.origin}</p>
                <p className="text-[10px] text-muted-foreground">{r.flightCount} flights · avg {r.avgPassengers}</p>
              </div>
            ))}
          </div>
        </SectionCard>

        <div className="flex flex-col gap-6">
          <SectionCard title="Cabin Mix" icon={Plane}>
            <EChart option={buildDonutOption(cabinDonut, "Passengers")} style={{ height: 200 }} />
            <div className="mt-2 flex justify-center gap-6 text-xs text-muted-foreground">
              <span><span className="inline-block h-2 w-2 rounded-full bg-sky-400 mr-1" />Economy: {overview.economyPassengers.toLocaleString()}</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-violet-400 mr-1" />Business: {overview.businessPassengers.toLocaleString()}</span>
            </div>
          </SectionCard>
          <SectionCard title="Operational Readiness" icon={Users}>
            <EChart option={buildDonutOption(operationalDonut, "Status")} style={{ height: 200 }} />
            <div className="mt-2 flex justify-center gap-4 text-xs text-muted-foreground">
              <span>CI Rate: <strong className="text-emerald-400">{operationalRates.checkInRate}%</strong></span>
              <span>Board Rate: <strong className="text-violet-400">{operationalRates.boardingRate}%</strong></span>
            </div>
          </SectionCard>
        </div>
      </div>

      {/* ── Row 2: Booking Classes ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SectionCard title="Economy Booking Classes" icon={ShoppingBag}>
          <EChart
            option={buildBarOption(
              classChart.economy.map((c) => c.bookingClass),
              classChart.economy.map((c) => c.count),
              "#38bdf8",
              "Passengers",
            )}
            style={{ height: 260 }}
          />
        </SectionCard>
        <SectionCard title="Business Booking Classes" icon={ShoppingBag}>
          {classChart.business.length > 0 ? (
            <EChart
              option={buildBarOption(
                classChart.business.map((c) => c.bookingClass),
                classChart.business.map((c) => c.count),
                "#a78bfa",
                "Passengers",
              )}
              style={{ height: 260 }}
            />
          ) : (
            <p className="text-sm text-muted-foreground py-8 text-center">No business class data</p>
          )}
        </SectionCard>
      </div>

      {/* ── Row 3: Nationality + Loyalty ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <SectionCard title="Passenger Nationalities — Top 15" icon={Globe} className="lg:col-span-2">
          <EChart
            option={buildHorizontalBarOption(
              [...nationalityChart.labels].reverse(),
              [...nationalityChart.values].reverse(),
              [...PALETTE].reverse(),
            )}
            style={{ height: 380 }}
          />
        </SectionCard>
        <SectionCard title="Loyalty Tier Distribution" icon={Users}>
          <EChart option={buildDonutOption(loyaltyDonut, "FF Members")} style={{ height: 240 }} />
          <div className="mt-3 space-y-1.5">
            {loyaltyDistribution.filter((l) => l.tier).map((l, i) => (
              <div key={l.tier} className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: PALETTE[i % PALETTE.length] }} />
                  {l.tierName}
                </span>
                <span className="font-mono text-muted-foreground">{l.count.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      {/* ── Row 4: Meals + Wheelchair + Changes ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <SectionCard title="Special Meal Requests" icon={Utensils}>
          <EChart
            option={buildHorizontalBarOption(
              [...mealChart.labels].reverse(),
              [...mealChart.values].reverse(),
              ["#f59e0b"],
            )}
            style={{ height: 350 }}
          />
        </SectionCard>
        <SectionCard title="Wheelchair / Accessibility" icon={Accessibility}>
          <div className="space-y-3 pt-2">
            {wheelchairDistribution.map((w) => {
              const total = wheelchairDistribution.reduce((s, x) => s + x.count, 0);
              const pct = total > 0 ? Math.round(w.count / total * 100) : 0;
              return (
                <div key={w.code} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-foreground">{w.code}</span>
                    <span className="text-muted-foreground">{w.count.toLocaleString()} ({pct}%)</span>
                  </div>
                  <p className="text-[10px] text-muted-foreground pl-0.5">{WHEELCHAIR_NAMES[w.code] ?? ""}</p>
                  <div className="h-1.5 rounded-full bg-muted/30 overflow-hidden">
                    <div className="h-full rounded-full bg-sky-400 transition-all" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
            <p className="text-xs text-muted-foreground pt-2">
              Total SSR wheelchair requests: <strong className="text-foreground">{wheelchairDistribution.reduce((s, w) => s + w.count, 0).toLocaleString()}</strong>
            </p>
          </div>
        </SectionCard>
        <SectionCard title="Change Events Distribution" icon={ArrowRightLeft}>
          <EChart
            option={buildHorizontalBarOption(
              [...changeChart.labels].reverse(),
              [...changeChart.values].reverse(),
              ["#fb7185"],
            )}
            style={{ height: 350 }}
          />
        </SectionCard>
      </div>

      {/* ── Row 5: Booking Patterns — Party Size + Lead Time + POS ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <SectionCard title="Party Size Distribution" icon={Users}>
          <EChart option={buildDonutOption(partySizeDonut, "Bookings")} style={{ height: 240 }} />
          <div className="mt-3 space-y-1 text-xs text-muted-foreground">
            {partySizeDonut.map((p, i) => (
              <div key={p.name} className="flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: PALETTE[i % PALETTE.length] }} />
                  {p.name}
                </span>
                <span className="font-mono">{p.value.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </SectionCard>
        <SectionCard title="Booking Lead Time" icon={CalendarDays}>
          <EChart option={buildDonutOption(leadTimeDonut, "Lead Time")} style={{ height: 240 }} />
          <div className="mt-3 space-y-1 text-xs text-muted-foreground">
            {leadTimeDonut.map((l) => (
              <div key={l.name} className="flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: l.color }} />
                  {l.name}
                </span>
                <span className="font-mono">{l.value.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </SectionCard>
        <SectionCard title="Point of Sale — Top Countries" icon={Globe}>
          <EChart
            option={buildHorizontalBarOption(
              [...posChart.labels].reverse(),
              [...posChart.values].reverse(),
              ["#34d399"],
            )}
            style={{ height: 350 }}
          />
        </SectionCard>
      </div>
    </>
  );
}
