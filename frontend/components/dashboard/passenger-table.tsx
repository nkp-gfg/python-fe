"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  Loader2,
  ChevronUp,
  ChevronDown,
  Filter,
  Luggage,
  X,
} from "lucide-react";
import { fetchPassengers } from "@/lib/api";
import { useDebounce } from "@/lib/hooks";
import type { PassengerRecord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

interface PassengerTableProps {
  flightNumber: string;
  origin: string;
  date: string;
  onSelectPassenger?: (pnr: string) => void;
}

type SortKey = "lastName" | "cabin" | "seat" | "status" | "bookingClass" | "bagCount";
type SortDir = "asc" | "desc";

type FilterCabin = "all" | "Y" | "J";
type FilterStatus = "all" | "booked" | "checkedIn" | "boarded";
type FilterType = "all" | "revenue" | "nonRevenue" | "child" | "infant";

export function PassengerTable({
  flightNumber,
  origin,
  date,
  onSelectPassenger,
}: PassengerTableProps) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search);
  const [sortKey, setSortKey] = useState<SortKey>("lastName");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [filterCabin, setFilterCabin] = useState<FilterCabin>("all");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [filterType, setFilterType] = useState<FilterType>("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["passengers", flightNumber, origin, date],
    queryFn: () => fetchPassengers(flightNumber, origin, date),
  });

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const passengers = useMemo(() => data?.passengers ?? [], [data]);

  const filtered = useMemo(() => {
    let list = [...passengers];

    // Text search
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase();
      list = list.filter(
        (p) =>
          p.lastName.toLowerCase().includes(q) ||
          p.firstName.toLowerCase().includes(q) ||
          p.pnr.toLowerCase().includes(q) ||
          p.seat.toLowerCase().includes(q)
      );
    }

    // Cabin filter
    if (filterCabin !== "all") {
      list = list.filter((p) => p.cabin === filterCabin);
    }

    // Status filter
    if (filterStatus === "boarded") list = list.filter((p) => p.isBoarded);
    else if (filterStatus === "checkedIn")
      list = list.filter((p) => p.isCheckedIn && !p.isBoarded);
    else if (filterStatus === "booked")
      list = list.filter((p) => !p.isCheckedIn && !p.isBoarded);

    // Type filter
    if (filterType === "revenue") list = list.filter((p) => p.isRevenue);
    else if (filterType === "nonRevenue") list = list.filter((p) => !p.isRevenue);
    else if (filterType === "child") list = list.filter((p) => p.isChild);
    else if (filterType === "infant") list = list.filter((p) => p.hasInfant);

    // Sort
    list.sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortKey === "status") {
        const sa = a.isBoarded ? 3 : a.isCheckedIn ? 2 : 1;
        const sb = b.isBoarded ? 3 : b.isCheckedIn ? 2 : 1;
        return (sa - sb) * dir;
      }
      const va = a[sortKey] ?? "";
      const vb = b[sortKey] ?? "";
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });

    return list;
  }, [passengers, search, filterCabin, filterStatus, filterType, sortKey, sortDir]);

  function getStatusBadge(p: PassengerRecord) {
    if (p.isBoarded) return <Badge className="bg-emerald-500/15 text-emerald-600 border-transparent text-[10px]">Boarded</Badge>;
    if (p.isCheckedIn) return <Badge className="bg-blue-500/15 text-blue-600 border-transparent text-[10px]">Checked-In</Badge>;
    return <Badge className="bg-amber-500/15 text-amber-600 border-transparent text-[10px]">Booked</Badge>;
  }

  function renderSortHeader(label: string, field: SortKey) {
    const active = sortKey === field;
    return (
      <button className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort(field)}>
        {label}
        {active && (sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)}
      </button>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading manifest...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/20 bg-destructive/10 p-6 text-center text-sm text-destructive">
        Failed to load passenger list. Ensure the flight has been ingested.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Controls Row */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, PNR, seat..."
            aria-label="Search passengers"
            className="w-full rounded-md border border-input bg-background px-9 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <Filter className="h-3.5 w-3.5 text-muted-foreground" />
          <div className="relative">
            <select
              value={filterCabin}
              onChange={(e) => setFilterCabin(e.target.value as FilterCabin)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6",
                filterCabin !== "all"
                  ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Cabins</option>
              <option value="J">Business (J)</option>
              <option value="Y">Economy (Y)</option>
            </select>
            {filterCabin !== "all" && (
              <button
                onClick={() => setFilterCabin("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                aria-label="Clear cabin filter"
              >
                <X className="h-3 w-3 text-blue-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value as FilterStatus)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6",
                filterStatus !== "all"
                  ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Status</option>
              <option value="booked">Booked</option>
              <option value="checkedIn">Checked-In</option>
              <option value="boarded">Boarded</option>
            </select>
            {filterStatus !== "all" && (
              <button
                onClick={() => setFilterStatus("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                aria-label="Clear status filter"
              >
                <X className="h-3 w-3 text-blue-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value as FilterType)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6",
                filterType !== "all"
                  ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Types</option>
              <option value="revenue">Revenue</option>
              <option value="nonRevenue">Non-Revenue</option>
              <option value="child">Children</option>
              <option value="infant">With Infant</option>
            </select>
            {filterType !== "all" && (
              <button
                onClick={() => setFilterType("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-blue-500/20 transition-colors"
                aria-label="Clear type filter"
              >
                <X className="h-3 w-3 text-blue-500" />
              </button>
            )}
          </div>
          {(filterCabin !== "all" || filterStatus !== "all" || filterType !== "all") && (
            <button
              onClick={() => { setFilterCabin("all"); setFilterStatus("all"); setFilterType("all"); }}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors ml-1"
            >
              <X className="h-3 w-3" />
              Clear all
            </button>
          )}
        </div>
        <span className="text-xs text-muted-foreground ml-auto">
          {filtered.length} of {passengers.length} passengers
        </span>
      </div>

      {/* Table */}
      <div className="rounded-lg border bg-card shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead>{renderSortHeader("Name", "lastName")}</TableHead>
              <TableHead>PNR</TableHead>
              <TableHead>{renderSortHeader("Cabin", "cabin")}</TableHead>
              <TableHead>{renderSortHeader("Seat", "seat")}</TableHead>
              <TableHead>{renderSortHeader("Class", "bookingClass")}</TableHead>
              <TableHead>{renderSortHeader("Status", "status")}</TableHead>
              <TableHead>{renderSortHeader("Bags", "bagCount")}</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Codes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-center py-8 text-muted-foreground">
                  No passengers match the current filters.
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((p, i) => (
                <TableRow
                  key={`${p.pnr}-${p.lastName}-${i}`}
                  className={cn(
                    "cursor-pointer transition-colors",
                    onSelectPassenger && "hover:bg-primary/5"
                  )}
                  onClick={() => onSelectPassenger?.(p.pnr)}
                >
                  <TableCell className="font-medium">
                    {p.lastName}, {p.firstName}
                    {p.isChild && <Badge variant="outline" className="ml-1.5 text-[9px] px-1 border-amber-300 text-amber-600">CHD</Badge>}
                    {p.hasInfant && <Badge variant="outline" className="ml-1.5 text-[9px] px-1 border-emerald-300 text-emerald-600">INF</Badge>}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{p.pnr}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("text-[10px] px-1.5", p.cabin === "J" ? "border-amber-300 text-amber-600" : "border-emerald-300 text-emerald-600")}>
                      {p.cabin === "J" ? "BIZ" : "ECO"}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{p.seat || "—"}</TableCell>
                  <TableCell className="text-xs">{p.bookingClass}</TableCell>
                  <TableCell>{getStatusBadge(p)}</TableCell>
                  <TableCell>
                    {p.bagCount > 0 && (
                      <span className="flex items-center gap-1 text-xs">
                        <Luggage className="h-3 w-3 text-muted-foreground" />
                        {p.bagCount}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {!p.isRevenue && <Badge variant="outline" className="text-[9px] px-1 border-purple-300 text-purple-600">NR</Badge>}
                    {p.isThru && <Badge variant="outline" className="text-[9px] px-1 ml-0.5">THRU</Badge>}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-0.5 max-w-[120px]">
                      {p.editCodes.slice(0, 4).map((code) => (
                        <Badge key={code} variant="secondary" className="text-[9px] px-1">{code}</Badge>
                      ))}
                      {p.editCodes.length > 4 && (
                        <Badge variant="secondary" className="text-[9px] px-1">+{p.editCodes.length - 4}</Badge>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
