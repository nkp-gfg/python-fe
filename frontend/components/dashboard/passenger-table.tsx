"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  Loader2,
  ChevronUp,
  ChevronDown,
  ArrowUpDown,
  Filter,
  Luggage,
  X,
  AlertTriangle,
} from "lucide-react";
import { fetchPassengers, fetchAudit } from "@/lib/api";
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

export type FilterCabin = "all" | "Y" | "J";
export type FilterStatus = "all" | "booked" | "checkedIn" | "boarded";
export type FilterType = "all" | "revenue" | "nonRevenue" | "child" | "infant" | "group" | "unnamedGroup";
export type FilterLoyalty = "all" | "FF" | "BLU" | "SLV" | "GLD" | "BLK";
export type FilterNationality = string; // "all" or a country code like "GB"
export type FilterSSR = "all" | "meal" | "wheelchair" | "emergencyContact" | "ffTier";
export type FilterBoardingPass = "all" | "issued" | "notIssued";
export type FilterSeat = "all" | "assigned" | "unassigned";
export type FilterConnecting = "all" | "thru" | "local" | "standby";
export type FilterDestination = string; // "all" or destination code

interface PassengerTableProps {
  flightNumber: string;
  origin: string;
  date: string;
  snapshotSequence?: number | null;
  onSelectPassenger?: (pnr: string) => void;
  filterCabin: FilterCabin;
  setFilterCabin: (v: FilterCabin) => void;
  filterStatus: FilterStatus;
  setFilterStatus: (v: FilterStatus) => void;
  filterType: FilterType;
  setFilterType: (v: FilterType) => void;
  filterLoyalty: FilterLoyalty;
  setFilterLoyalty: (v: FilterLoyalty) => void;
  filterNationality: FilterNationality;
  setFilterNationality: (v: FilterNationality) => void;
}

type SortKey = "lastName" | "cabin" | "seat" | "status" | "bookingClass" | "bagCount";
type SortDir = "asc" | "desc";

const LOYALTY_TIERS: { value: FilterLoyalty; label: string; color: string }[] = [
  { value: "FF", label: "FalconFlyer", color: "text-amber-500" },
  { value: "BLU", label: "Blue", color: "text-blue-500" },
  { value: "SLV", label: "Silver", color: "text-gray-400" },
  { value: "GLD", label: "Gold", color: "text-yellow-500" },
  { value: "BLK", label: "Black", color: "text-gray-900 dark:text-gray-100" },
];

export function PassengerTable({
  flightNumber,
  origin,
  date,
  snapshotSequence,
  onSelectPassenger,
  filterCabin,
  setFilterCabin,
  filterStatus,
  setFilterStatus,
  filterType,
  setFilterType,
  filterLoyalty,
  setFilterLoyalty,
  filterNationality,
  setFilterNationality,
}: PassengerTableProps) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search);
  const [sortKey, setSortKey] = useState<SortKey>("lastName");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [filterSSR, setFilterSSR] = useState<FilterSSR>("all");
  const [filterBP, setFilterBP] = useState<FilterBoardingPass>("all");
  const [filterSeat, setFilterSeat] = useState<FilterSeat>("all");
  const [filterConnecting, setFilterConnecting] = useState<FilterConnecting>("all");
  const [filterDestination, setFilterDestination] = useState<FilterDestination>("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["passengers", flightNumber, origin, date, snapshotSequence],
    queryFn: () => fetchPassengers(flightNumber, origin, date, snapshotSequence),
  });

  const { data: auditData } = useQuery({
    queryKey: ["audit", flightNumber, origin, date],
    queryFn: () => fetchAudit(flightNumber, origin, date),
    enabled: Boolean(flightNumber),
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

  const uniqueDestinations = useMemo(() => {
    const dests = new Set<string>();
    for (const p of passengers) {
      if (p.destination) dests.add(p.destination);
    }
    return Array.from(dests).sort();
  }, [passengers]);

  const filtered = useMemo(() => {
    let list = [...passengers];

    // Text search (include nationality + ticket)
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase();
      list = list.filter(
        (p) =>
          p.lastName.toLowerCase().includes(q) ||
          p.firstName.toLowerCase().includes(q) ||
          p.pnr.toLowerCase().includes(q) ||
          p.seat.toLowerCase().includes(q) ||
          (p.nationality || "").toLowerCase().includes(q) ||
          (p.ticketNumber || "").includes(q)
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
    else if (filterType === "group") list = list.filter((p) => p.isGroup || p.groupCode);
    else if (filterType === "unnamedGroup") list = list.filter((p) => p.isUnnamedGroup || (p.groupCode && p.lastName === "PAX" && !p.firstName));

    // Loyalty filter
    if (filterLoyalty !== "all") {
      list = list.filter((p) => p.editCodes.includes(filterLoyalty));
    }

    // Nationality filter
    if (filterNationality !== "all") {
      list = list.filter((p) => p.nationality === filterNationality);
    }

    // SSR filter
    if (filterSSR === "meal") list = list.filter((p) => p.specialMeal);
    else if (filterSSR === "wheelchair") list = list.filter((p) => p.wheelchairCode);
    else if (filterSSR === "emergencyContact") list = list.filter((p) => p.hasEmergencyContact);
    else if (filterSSR === "ffTier") list = list.filter((p) => p.ffTierName);

    // Boarding pass filter
    if (filterBP === "issued") list = list.filter((p) => p.boardingPassIssued);
    else if (filterBP === "notIssued") list = list.filter((p) => !p.boardingPassIssued);

    // Seat filter
    if (filterSeat === "assigned") list = list.filter((p) => p.seat);
    else if (filterSeat === "unassigned") list = list.filter((p) => !p.seat);

    // Connecting filter
    if (filterConnecting === "thru") list = list.filter((p) => p.isThru);
    else if (filterConnecting === "local") list = list.filter((p) => !p.isThru && !p.isStandby);
    else if (filterConnecting === "standby") list = list.filter((p) => p.isStandby);

    // Destination filter
    if (filterDestination !== "all") {
      list = list.filter((p) => p.destination === filterDestination);
    }

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
  }, [passengers, debouncedSearch, filterCabin, filterStatus, filterType, filterLoyalty, filterNationality, filterSSR, filterBP, filterSeat, filterConnecting, filterDestination, sortKey, sortDir]);

  function getStatusBadge(p: PassengerRecord) {
    if (p.isBoarded) return <Badge className="bg-emerald-500/15 text-emerald-600 border-transparent text-[10px]">Boarded</Badge>;
    if (p.isCheckedIn) return <Badge className="bg-blue-500/15 text-blue-600 border-transparent text-[10px]">Checked-In</Badge>;
    return <Badge className="bg-amber-500/15 text-amber-600 border-transparent text-[10px]">Booked</Badge>;
  }

  function renderSortHeader(label: string, field: SortKey) {
    const active = sortKey === field;
    return (
      <button className="flex items-center gap-1 cursor-pointer hover:text-foreground" onClick={() => toggleSort(field)}>
        {label}
        {active
          ? (sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)
          : <ArrowUpDown className="h-3 w-3 opacity-30" />}
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
            className={cn(
              "w-full rounded-md border bg-background px-9 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              search ? "border-blue-500" : "border-input"
            )}
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-2 rounded-full p-0.5 hover:bg-muted transition-colors"
              aria-label="Clear search"
            >
              <X className="h-4 w-4 text-muted-foreground" />
            </button>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <Filter className="h-3.5 w-3.5 text-muted-foreground" />
          <div className="relative">
            <select
              value={filterCabin}
              onChange={(e) => setFilterCabin(e.target.value as FilterCabin)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
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
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
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
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
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
              <option value="group">Group Booking</option>
              <option value="unnamedGroup">Unnamed Group</option>
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
          <div className="relative">
            <select
              value={filterLoyalty}
              onChange={(e) => setFilterLoyalty(e.target.value as FilterLoyalty)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                filterLoyalty !== "all"
                  ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Loyalty</option>
              {LOYALTY_TIERS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            {filterLoyalty !== "all" && (
              <button
                onClick={() => setFilterLoyalty("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-amber-500/20 transition-colors"
                aria-label="Clear loyalty filter"
              >
                <X className="h-3 w-3 text-amber-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterSSR}
              onChange={(e) => setFilterSSR(e.target.value as FilterSSR)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                filterSSR !== "all"
                  ? "border-orange-500 bg-orange-500/10 text-orange-600 dark:text-orange-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All SSR</option>
              <option value="meal">Special Meal</option>
              <option value="wheelchair">Wheelchair</option>
              <option value="emergencyContact">Emergency Contact</option>
              <option value="ffTier">FF Tier</option>
            </select>
            {filterSSR !== "all" && (
              <button
                onClick={() => setFilterSSR("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-orange-500/20 transition-colors"
                aria-label="Clear SSR filter"
              >
                <X className="h-3 w-3 text-orange-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterBP}
              onChange={(e) => setFilterBP(e.target.value as FilterBoardingPass)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                filterBP !== "all"
                  ? "border-teal-500 bg-teal-500/10 text-teal-600 dark:text-teal-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">Boarding Pass</option>
              <option value="issued">BP Issued</option>
              <option value="notIssued">No BP</option>
            </select>
            {filterBP !== "all" && (
              <button
                onClick={() => setFilterBP("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-teal-500/20 transition-colors"
                aria-label="Clear boarding pass filter"
              >
                <X className="h-3 w-3 text-teal-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterSeat}
              onChange={(e) => setFilterSeat(e.target.value as FilterSeat)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                filterSeat !== "all"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Seats</option>
              <option value="assigned">Seat Assigned</option>
              <option value="unassigned">No Seat</option>
            </select>
            {filterSeat !== "all" && (
              <button
                onClick={() => setFilterSeat("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-indigo-500/20 transition-colors"
                aria-label="Clear seat filter"
              >
                <X className="h-3 w-3 text-indigo-500" />
              </button>
            )}
          </div>
          <div className="relative">
            <select
              value={filterConnecting}
              onChange={(e) => setFilterConnecting(e.target.value as FilterConnecting)}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                filterConnecting !== "all"
                  ? "border-cyan-500 bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 font-medium"
                  : "border-input bg-background"
              )}
            >
              <option value="all">All Routes</option>
              <option value="thru">Connecting</option>
              <option value="local">Local (O&D)</option>
              <option value="standby">Standby</option>
            </select>
            {filterConnecting !== "all" && (
              <button
                onClick={() => setFilterConnecting("all")}
                className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-cyan-500/20 transition-colors"
                aria-label="Clear connecting filter"
              >
                <X className="h-3 w-3 text-cyan-500" />
              </button>
            )}
          </div>
          {uniqueDestinations.length > 1 && (
            <div className="relative">
              <select
                value={filterDestination}
                onChange={(e) => setFilterDestination(e.target.value as FilterDestination)}
                className={cn(
                  "rounded-md border px-2 py-1.5 text-xs appearance-none pr-6 cursor-pointer",
                  filterDestination !== "all"
                    ? "border-rose-500 bg-rose-500/10 text-rose-600 dark:text-rose-400 font-medium"
                    : "border-input bg-background"
                )}
              >
                <option value="all">All Dest.</option>
                {uniqueDestinations.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
              {filterDestination !== "all" && (
                <button
                  onClick={() => setFilterDestination("all")}
                  className="absolute right-0.5 top-1 rounded-full p-0.5 hover:bg-rose-500/20 transition-colors"
                  aria-label="Clear destination filter"
                >
                  <X className="h-3 w-3 text-rose-500" />
                </button>
              )}
            </div>
          )}
          {(search || filterCabin !== "all" || filterStatus !== "all" || filterType !== "all" || filterLoyalty !== "all" || filterNationality !== "all" || filterSSR !== "all" || filterBP !== "all" || filterSeat !== "all" || filterConnecting !== "all" || filterDestination !== "all") && (
            <button
              onClick={() => { setSearch(""); setFilterCabin("all"); setFilterStatus("all"); setFilterType("all"); setFilterLoyalty("all"); setFilterNationality("all"); setFilterSSR("all"); setFilterBP("all"); setFilterSeat("all"); setFilterConnecting("all"); setFilterDestination("all"); }}
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

      {/* Active filter chips */}
      {(search || filterCabin !== "all" || filterStatus !== "all" || filterType !== "all" || filterLoyalty !== "all" || filterNationality !== "all" || filterSSR !== "all" || filterBP !== "all" || filterSeat !== "all" || filterConnecting !== "all" || filterDestination !== "all") && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-muted-foreground mr-0.5">Active:</span>
          {search && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Search: &ldquo;{search}&rdquo;
              <button onClick={() => setSearch("")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove search filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterCabin !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Cabin: {filterCabin === "J" ? "Business" : "Economy"}
              <button onClick={() => setFilterCabin("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove cabin filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterStatus !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Status: {filterStatus === "checkedIn" ? "Checked-In" : filterStatus.charAt(0).toUpperCase() + filterStatus.slice(1)}
              <button onClick={() => setFilterStatus("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove status filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterType !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Type: {filterType === "nonRevenue" ? "Non-Revenue" : filterType === "infant" ? "With Infant" : filterType === "group" ? "Group Booking" : filterType === "unnamedGroup" ? "Unnamed Group" : filterType.charAt(0).toUpperCase() + filterType.slice(1)}
              <button onClick={() => setFilterType("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove type filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterLoyalty !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Loyalty: {LOYALTY_TIERS.find((t) => t.value === filterLoyalty)?.label ?? filterLoyalty}
              <button onClick={() => setFilterLoyalty("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove loyalty filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterNationality !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Nationality: {filterNationality}
              <button onClick={() => setFilterNationality("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove nationality filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterSSR !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              SSR: {filterSSR === "meal" ? "Special Meal" : filterSSR === "wheelchair" ? "Wheelchair" : filterSSR === "emergencyContact" ? "Emergency Contact" : "FF Tier"}
              <button onClick={() => setFilterSSR("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove SSR filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterBP !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              BP: {filterBP === "issued" ? "Issued" : "Not Issued"}
              <button onClick={() => setFilterBP("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove boarding pass filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterSeat !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Seat: {filterSeat === "assigned" ? "Assigned" : "Unassigned"}
              <button onClick={() => setFilterSeat("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove seat filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterConnecting !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Route: {filterConnecting === "thru" ? "Connecting" : filterConnecting === "local" ? "Local (O&D)" : "Standby"}
              <button onClick={() => setFilterConnecting("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove connecting filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          {filterDestination !== "all" && (
            <Badge variant="secondary" className="gap-1 text-[10px] pl-2 pr-1 py-0.5">
              Dest: {filterDestination}
              <button onClick={() => setFilterDestination("all")} className="rounded-full hover:bg-muted-foreground/20 p-0.5" aria-label="Remove destination filter">
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          )}
          <button
            onClick={() => { setSearch(""); setFilterCabin("all"); setFilterStatus("all"); setFilterType("all"); setFilterLoyalty("all"); setFilterNationality("all"); setFilterSSR("all"); setFilterBP("all"); setFilterSeat("all"); setFilterConnecting("all"); setFilterDestination("all"); }}
            className="text-[10px] text-muted-foreground hover:text-foreground transition-colors ml-1 underline underline-offset-2"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Table */}
      <div className="rounded-lg border bg-card shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead>{renderSortHeader("Name", "lastName")}</TableHead>
              <TableHead>PNR</TableHead>
              <TableHead>Nat.</TableHead>
              <TableHead>{renderSortHeader("Cabin", "cabin")}</TableHead>
              <TableHead>{renderSortHeader("Seat", "seat")}</TableHead>
              <TableHead>{renderSortHeader("Class", "bookingClass")}</TableHead>
              <TableHead>{renderSortHeader("Status", "status")}</TableHead>
              <TableHead>{renderSortHeader("Bags", "bagCount")}</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>SSR</TableHead>
              <TableHead>Codes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={11} className="text-center py-8 text-muted-foreground">
                  No passengers match the current filters.
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((p) => (
                <TableRow
                  key={`${p.pnr}-${p.lastName}-${p.firstName || ""}-${p.seat || "noseat"}`}
                  className={cn(
                    "cursor-pointer transition-colors",
                    onSelectPassenger && "hover:bg-primary/5"
                  )}
                  onClick={() => onSelectPassenger?.(p.pnr)}
                >
                  <TableCell className="font-medium">
                    <span className={cn(
                      (p.isUnnamedGroup || (p.groupCode && p.lastName === "PAX" && !p.firstName)) && "italic text-muted-foreground"
                    )}>
                      {(p.isUnnamedGroup || (p.groupCode && p.lastName === "PAX" && !p.firstName))
                        ? <span className="flex items-center gap-1">Unnamed <Badge className="bg-rose-500/15 text-rose-500 border-transparent text-[9px] px-1">GRP</Badge></span>
                        : <>{p.lastName}, {p.firstName}</>
                      }
                    </span>
                    {p.isChild && <Badge variant="outline" className="ml-1.5 text-[9px] px-1 border-amber-300 text-amber-600">CHD</Badge>}
                    {p.hasInfant && <Badge variant="outline" className="ml-1.5 text-[9px] px-1 border-emerald-300 text-emerald-600">INF</Badge>}
                    {p.groupCode && !(p.isUnnamedGroup || (p.groupCode && p.lastName === "PAX" && !p.firstName)) && (
                      <Badge variant="outline" className="ml-1.5 text-[9px] px-1 border-violet-300 text-violet-600">GRP</Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    <span className="flex items-center gap-1">
                      {p.pnr}
                      {auditData?.passengerAlerts?.[p.pnr] && (
                        <span title={`${auditData.passengerAlerts[p.pnr].length} audit alert(s)`}>
                          <AlertTriangle className="h-3 w-3 text-amber-500 shrink-0" />
                        </span>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs">{p.nationality || "—"}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("text-[10px] px-1.5", p.cabin === "J" ? "border-amber-300 text-amber-600" : "border-emerald-300 text-emerald-600")}>
                      {p.cabin === "J" ? "BIZ" : "ECO"}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{p.seat || "—"}</TableCell>
                  <TableCell className="text-xs">
                    {p.bookingClass}
                    {p.desiredBookingClass && p.desiredBookingClass !== p.bookingClass && (
                      <span className="text-[9px] text-amber-500 ml-0.5" title={`Desired: ${p.desiredBookingClass}`}>→{p.desiredBookingClass}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      {getStatusBadge(p)}
                      {p.boardingPassIssued && (
                        <span className="text-[9px] text-emerald-500" title="Boarding pass issued">BP</span>
                      )}
                      {p.checkInSequence > 0 && (
                        <span className="text-[9px] text-muted-foreground" title={`Check-in #${p.checkInSequence}${p.checkInTime ? ` at ${p.checkInTime}` : ""}${p.checkInDate ? ` on ${p.checkInDate}` : ""}`}>
                          #{p.checkInSequence}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {p.bagCount > 0 && (
                      <span className="flex items-center gap-1 text-xs">
                        <Luggage className="h-3 w-3 text-muted-foreground" />
                        {p.bagCount}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-0.5">
                      {!p.isRevenue && <Badge variant="outline" className="text-[9px] px-1 border-purple-300 text-purple-600">NR</Badge>}
                      {p.isThru && <Badge variant="outline" className="text-[9px] px-1">THRU</Badge>}
                      {p.groupCode && (
                        <Badge variant="outline" className="text-[9px] px-1 border-violet-400 text-violet-500" title={`Group: ${p.groupCode}`}>
                          {p.groupCode}
                        </Badge>
                      )}
                      {p.corpId && (
                        <Badge variant="outline" className="text-[9px] px-1 border-cyan-300 text-cyan-600" title={`Corporate ID: ${p.corpId}`}>
                          CORP
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-0.5 max-w-[120px]">
                      {p.specialMeal && (
                        <Badge variant="outline" className="text-[9px] px-1 border-orange-300 text-orange-600" title={`Special Meal: ${p.specialMeal}`}>
                          {p.specialMeal}
                        </Badge>
                      )}
                      {p.wheelchairCode && (
                        <Badge variant="outline" className="text-[9px] px-1 border-sky-300 text-sky-600" title={`Wheelchair: ${p.wheelchairCode}`}>
                          {p.wheelchairCode}
                        </Badge>
                      )}
                      {p.ffTierName && (
                        <Badge variant="outline" className="text-[9px] px-1 border-yellow-300 text-yellow-600" title={`FF Tier: ${p.ffTierName}${p.ffStatus ? ` (${p.ffStatus})` : ""}`}>
                          {p.ffTierName}
                        </Badge>
                      )}
                      {p.hasEmergencyContact && (
                        <Badge variant="outline" className="text-[9px] px-1 border-rose-300 text-rose-500" title="Emergency contact on file">
                          EC
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-0.5 max-w-[120px]">
                      {(p.editCodes ?? []).slice(0, 4).map((code) => (
                        <Badge key={code} variant="secondary" className="text-[9px] px-1">{code}</Badge>
                      ))}
                      {(p.editCodes ?? []).length > 4 && (
                        <Badge variant="secondary" className="text-[9px] px-1">+{(p.editCodes ?? []).length - 4}</Badge>
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
