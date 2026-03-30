"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPassengers } from "@/lib/api";
import type { PassengerRecord, FlightDashboard } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Users,
  UserCheck,
  PlaneTakeoff,
  Info,
  Loader2,
  X,
  Baby,
  Briefcase,
  ShieldCheck,
  ChevronUp,
  ChevronDown,
  ArrowUpDown,
} from "lucide-react";

export type DetailView =
  | "booked"
  | "checkedIn"
  | "boarded"
  | "others"
  | "sob"       // Souls on board (boarded passengers + infants)
  | "souls"     // All souls on manifest
  | "records";  // All manifest records

interface BottomDetailPanelProps {
  view: DetailView;
  flightNumber: string;
  origin: string;
  date: string;
  snapshotSequence?: number | null;
  dashboard: FlightDashboard;
  onClose: () => void;
  onSelectPassenger?: (pnr: string) => void;
}

const VIEW_CONFIG: Record<DetailView, { title: string; icon: React.ReactNode; description: string }> = {
  booked: {
    title: "Booked Passengers",
    icon: <Users className="h-4 w-4 text-blue-500" />,
    description: "Passengers who are booked but have not yet checked in.",
  },
  checkedIn: {
    title: "Checked-In Passengers",
    icon: <UserCheck className="h-4 w-4 text-amber-500" />,
    description: "Passengers who have checked in but not yet boarded.",
  },
  boarded: {
    title: "Boarded Passengers",
    icon: <PlaneTakeoff className="h-4 w-4 text-emerald-500" />,
    description: "Passengers who have boarded the aircraft.",
  },
  others: {
    title: "Other Passengers",
    icon: <Info className="h-4 w-4 text-muted-foreground" />,
    description: "Jump seat, non-revenue, offloaded, and no-show passengers.",
  },
  sob: {
    title: "Passengers on Board (POB)",
    icon: <ShieldCheck className="h-4 w-4 text-emerald-500" />,
    description: "All souls physically on board: boarded passengers + their lap infants.",
  },
  souls: {
    title: "Total Souls on Manifest",
    icon: <Users className="h-4 w-4 text-blue-500" />,
    description: "All passengers on manifest plus their lap infants (totalPassengers + infantCount).",
  },
  records: {
    title: "Manifest Records",
    icon: <Briefcase className="h-4 w-4 text-muted-foreground" />,
    description: "All seated passenger records on the manifest (excludes lap infants who share a seat).",
  },
};

type SortKey = "lastName" | "pnr" | "nationality" | "cabin" | "seat" | "status" | "ticketNumber" | "bagCount";
type SortDir = "asc" | "desc";

function sortPassengers(list: PassengerRecord[], key: SortKey, dir: SortDir): PassengerRecord[] {
  return [...list].sort((a, b) => {
    const m = dir === "asc" ? 1 : -1;
    if (key === "status") {
      const sa = a.isBoarded ? 3 : a.isCheckedIn ? 2 : 1;
      const sb = b.isBoarded ? 3 : b.isCheckedIn ? 2 : 1;
      return (sa - sb) * m;
    }
    if (key === "bagCount") return ((a.bagCount ?? 0) - (b.bagCount ?? 0)) * m;
    const va = key === "lastName" ? `${a.lastName}, ${a.firstName}` : String(a[key] ?? "");
    const vb = key === "lastName" ? `${b.lastName}, ${b.firstName}` : String(b[key] ?? "");
    return va.localeCompare(vb) * m;
  });
}

function filterPassengers(passengers: PassengerRecord[], view: DetailView): PassengerRecord[] {
  switch (view) {
    case "booked":
      return passengers.filter((p) => !p.isCheckedIn && !p.isBoarded);
    case "checkedIn":
      return passengers.filter((p) => p.isCheckedIn && !p.isBoarded);
    case "boarded":
      return passengers.filter((p) => p.isBoarded);
    case "others":
      // Non-revenue or staff (employee type)
      return passengers.filter((p) => !p.isRevenue || p.passengerType === "E");
    case "sob":
      // Souls on board = boarded passengers (infants are flagged via hasInfant)
      return passengers.filter((p) => p.isBoarded);
    case "souls":
      // All passengers on manifest
      return passengers;
    case "records":
      // All manifest records (same as full list)
      return passengers;
    default:
      return passengers;
  }
}

function getStatusBadge(p: PassengerRecord) {
  if (p.isBoarded)
    return (
      <div className="flex items-center gap-1">
        <Badge className="bg-emerald-500/15 text-emerald-600 border-transparent text-[10px]">Boarded</Badge>
        {p.boardingPassIssued && <span className="text-[9px] text-emerald-500" title="Boarding pass issued">BP</span>}
      </div>
    );
  if (p.isCheckedIn)
    return (
      <div className="flex items-center gap-1">
        <Badge className="bg-blue-500/15 text-blue-600 border-transparent text-[10px]">Checked-In</Badge>
        {p.boardingPassIssued && <span className="text-[9px] text-emerald-500" title="Boarding pass issued">BP</span>}
      </div>
    );
  return <Badge className="bg-amber-500/15 text-amber-600 border-transparent text-[10px]">Booked</Badge>;
}

function getCabinLabel(cabin: string) {
  return cabin === "J" ? "Business" : "Economy";
}

function getTypeBadge(p: PassengerRecord) {
  const badges: React.ReactNode[] = [];
  if (p.passengerType === "E")
    badges.push(<Badge key="staff" className="bg-purple-500/15 text-purple-600 border-transparent text-[10px]">Staff</Badge>);
  if (!p.isRevenue && p.passengerType !== "E")
    badges.push(<Badge key="nr" className="bg-purple-500/15 text-purple-600 border-transparent text-[10px]">Non-Rev</Badge>);
  if (p.isChild)
    badges.push(<Badge key="chd" className="bg-amber-500/15 text-amber-600 border-transparent text-[10px]">Child</Badge>);
  if (p.hasInfant)
    badges.push(<Badge key="inf" className="bg-teal-500/15 text-teal-600 border-transparent text-[10px]">+Infant</Badge>);
  if (p.isStandby)
    badges.push(<Badge key="sb" className="bg-rose-500/15 text-rose-600 border-transparent text-[10px]">Standby</Badge>);
  if (p.corpId)
    badges.push(<Badge key="corp" className="bg-cyan-500/15 text-cyan-600 border-transparent text-[10px]" title={`Corporate ID: ${p.corpId}`}>Corp</Badge>);
  return badges.length > 0 ? <div className="flex gap-1 flex-wrap">{badges}</div> : null;
}

export function BottomDetailPanel({
  view,
  flightNumber,
  origin,
  date,
  snapshotSequence,
  dashboard,
  onClose,
  onSelectPassenger,
}: BottomDetailPanelProps) {
  const config = VIEW_CONFIG[view];

  const { data, isLoading } = useQuery({
    queryKey: ["passengers", flightNumber, origin, date, snapshotSequence],
    queryFn: () => fetchPassengers(flightNumber, origin, date, snapshotSequence),
  });

  const [sortKey, setSortKey] = useState<SortKey>("lastName");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  }

  function renderSortHeader(label: string, field: SortKey, className?: string) {
    const active = sortKey === field;
    return (
      <button className={cn("flex items-center gap-0.5 hover:text-foreground", className)} onClick={() => toggleSort(field)}>
        {label}
        {active
          ? (sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)
          : <ArrowUpDown className="h-3 w-3 opacity-30" />}
      </button>
    );
  }

  const allPassengers = useMemo(() => data?.passengers ?? [], [data]);
  const filtered = useMemo(() => {
    const base = filterPassengers(allPassengers, view);
    return sortPassengers(base, sortKey, sortDir);
  }, [allPassengers, view, sortKey, sortDir]);

  // Summary counts for the filtered set
  const summary = useMemo(() => {
    const adults = filtered.filter((p) => !p.isChild).length;
    const children = filtered.filter((p) => p.isChild).length;
    const infants = filtered.filter((p) => p.hasInfant).length;
    const revenue = filtered.filter((p) => p.isRevenue).length;
    const nonRevenue = filtered.filter((p) => !p.isRevenue).length;
    const staff = filtered.filter((p) => p.passengerType === "E").length;
    const economy = filtered.filter((p) => p.cabin !== "J").length;
    const business = filtered.filter((p) => p.cabin === "J").length;
    const souls = filtered.length + infants;
    return { adults, children, infants, revenue, nonRevenue, staff, economy, business, souls, total: filtered.length };
  }, [filtered]);

  // For "others" view, also compute jump seat and offloaded/no-show from dashboard
  const othersInfo = view === "others" ? {
    jumpSeat: dashboard.stateSummary?.others?.jumpSeat ?? 0,
    offloaded: dashboard.stateSummary?.others?.offloaded ?? 0,
    noShow: dashboard.stateSummary?.others?.noShow ?? 0,
    offloadedAvailable: dashboard.stateSummary?.others?.offloadedAvailable ?? false,
    noShowAvailable: dashboard.stateSummary?.others?.noShowAvailable ?? false,
  } : null;

  return (
    <div className="rounded-lg border bg-card shadow-sm mt-3 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/30">
        <div className="flex items-center gap-2">
          {config.icon}
          <div>
            <h3 className="text-sm font-semibold">{config.title}</h3>
            <p className="text-[10px] text-muted-foreground">{config.description}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Quick stats */}
          <div className="hidden sm:flex items-center gap-3 text-[10px] text-muted-foreground">
            <span><span className="font-medium text-foreground">{summary.total}</span> passengers</span>
            {summary.infants > 0 && <span><span className="font-medium text-teal-600">+{summary.infants}</span> infants</span>}
            <span><span className="font-medium text-foreground">{summary.souls}</span> souls</span>
            <span className="text-muted-foreground/50">|</span>
            <span><span className="font-medium text-foreground">{summary.economy}</span> Y</span>
            <span><span className="font-medium text-foreground">{summary.business}</span> J</span>
            {summary.staff > 0 && <span><span className="font-medium text-purple-600">{summary.staff}</span> staff</span>}
          </div>
          <button
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center rounded-full hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Others-specific info bar */}
      {othersInfo && (
        <div className="px-4 py-1.5 bg-muted/20 border-b flex items-center gap-4 text-xs">
          <span className="text-muted-foreground">
            Jump Seat: <span className="font-medium text-foreground">{othersInfo.jumpSeat}</span>
          </span>
          <span className="text-muted-foreground">
            Offloaded: <span className={cn("font-medium", (othersInfo.offloaded ?? 0) > 0 ? "text-rose-500" : "text-foreground")}>
              {othersInfo.offloadedAvailable ? othersInfo.offloaded : "N/A"}
            </span>
          </span>
          <span className="text-muted-foreground">
            No Show: <span className={cn("font-medium", (othersInfo.noShow ?? 0) > 0 ? "text-orange-500" : "text-foreground")}>
              {othersInfo.noShowAvailable ? othersInfo.noShow : "N/A"}
            </span>
          </span>
        </div>
      )}

      {/* SOB-specific info bar */}
      {view === "sob" && (
        <div className="px-4 py-1.5 bg-emerald-50/50 dark:bg-emerald-950/20 border-b flex items-center gap-4 text-xs">
          <span className="text-muted-foreground">
            Boarded passengers: <span className="font-semibold text-foreground">{summary.total}</span>
          </span>
          <span className="text-muted-foreground">
            Lap infants aboard: <span className="font-semibold text-teal-600">{summary.infants}</span>
          </span>
          <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
            Total Passengers on Board: {summary.souls}
          </span>
        </div>
      )}

      {/* Content */}
      <div className="max-h-[280px] overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading passenger data...
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground text-sm">
            No passengers in this category.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="text-[10px]">
                <TableHead className="py-1.5 px-2">#</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Name", "lastName")}</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("PNR", "pnr")}</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Nat.", "nationality")}</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Cabin", "cabin")}</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Seat", "seat")}</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Status", "status")}</TableHead>
                <TableHead className="py-1.5 px-2">Type</TableHead>
                <TableHead className="py-1.5 px-2">Check-In</TableHead>
                <TableHead className="py-1.5 px-2">{renderSortHeader("Ticket", "ticketNumber")}</TableHead>
                <TableHead className="py-1.5 px-2 text-right">{renderSortHeader("Bags", "bagCount", "ml-auto")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((p, idx) => (
                <TableRow
                  key={`${p.pnr}-${p.passengerId}`}
                  className={cn(
                    "text-xs cursor-pointer hover:bg-muted/50 transition-colors",
                    p.hasInfant && "bg-teal-50/30 dark:bg-teal-950/10"
                  )}
                  onClick={() => onSelectPassenger?.(p.pnr)}
                >
                  <TableCell className="py-1 px-2 text-muted-foreground">{idx + 1}</TableCell>
                  <TableCell className="py-1 px-2 font-medium">
                    {p.lastName}, {p.firstName}
                    {p.hasInfant && (
                      <Baby className="inline ml-1 h-3 w-3 text-teal-500" />
                    )}
                  </TableCell>
                  <TableCell className="py-1 px-2 font-mono text-muted-foreground">{p.pnr}</TableCell>
                  <TableCell className="py-1 px-2 text-muted-foreground">{p.nationality || "—"}</TableCell>
                  <TableCell className="py-1 px-2">
                    <span className={cn(
                      "text-[10px] font-medium px-1.5 py-0.5 rounded",
                      p.cabin === "J" ? "bg-amber-500/10 text-amber-600" : "bg-emerald-500/10 text-emerald-600"
                    )}>
                      {getCabinLabel(p.cabin)}
                    </span>
                  </TableCell>
                  <TableCell className="py-1 px-2 font-mono">{p.seat || "—"}</TableCell>
                  <TableCell className="py-1 px-2">{getStatusBadge(p)}</TableCell>
                  <TableCell className="py-1 px-2">{getTypeBadge(p)}</TableCell>
                  <TableCell className="py-1 px-2 text-[10px] text-muted-foreground">
                    {p.checkInSequence ? (
                      <span title={`${p.checkInDate || ""} ${p.checkInTime || ""}`.trim()}>
                        #{p.checkInSequence}
                        {p.checkInTime && <span className="ml-0.5">{p.checkInTime}</span>}
                      </span>
                    ) : "—"}
                  </TableCell>
                  <TableCell className="py-1 px-2 font-mono text-muted-foreground">
                    {p.vcrType && <span className="text-[9px] mr-0.5" title={`VCR Type: ${p.vcrType}`}>{p.vcrType}</span>}
                    {p.ticketNumber || "—"}
                  </TableCell>
                  <TableCell className="py-1 px-2 text-right">{p.bagCount}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Footer summary */}
      {filtered.length > 0 && (
        <div className="px-4 py-1.5 border-t bg-muted/20 flex items-center gap-4 text-[10px] text-muted-foreground">
          <span>Adults: <span className="font-medium text-foreground">{summary.adults}</span></span>
          <span>Children: <span className="font-medium text-amber-600">{summary.children}</span></span>
          <span>Infants: <span className="font-medium text-teal-600">{summary.infants}</span></span>
          <span className="text-muted-foreground/50">|</span>
          <span>Revenue: <span className="font-medium text-blue-600">{summary.revenue}</span></span>
          <span>Non-Revenue: <span className="font-medium text-purple-600">{summary.nonRevenue}</span></span>
        </div>
      )}
    </div>
  );
}
