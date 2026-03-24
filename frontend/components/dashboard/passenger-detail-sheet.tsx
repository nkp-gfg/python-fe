"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  Plane,
  Luggage,
  ShieldCheck,
  FileText,
  Globe,
  Tag,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  ArrowUpDown,
} from "lucide-react";
import { fetchPassengerDetail } from "@/lib/api";
import type {
  DetailedPassenger,
  ItinerarySegment,
  DocsData,
  TimaticEntry,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/* ── reusable sort header ── */
function SortHeader<K extends string>({
  label,
  field,
  sortKey,
  sortDir,
  onToggle,
}: {
  label: string;
  field: K;
  sortKey: K;
  sortDir: "asc" | "desc";
  onToggle: (k: K) => void;
}) {
  const active = sortKey === field;
  return (
    <button className="flex items-center gap-0.5 hover:text-foreground" onClick={() => onToggle(field)}>
      {label}
      {active
        ? (sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)
        : <ArrowUpDown className="h-3 w-3 opacity-30" />}
    </button>
  );
}

function useSortable<K extends string>(defaultKey: K) {
  const [sortKey, setSortKey] = useState<K>(defaultKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  function toggle(key: K) {
    if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  }
  return { sortKey, sortDir, toggle };
}

function sortBy<T>(list: T[], key: string, dir: "asc" | "desc", accessor: (item: T, key: string) => string | number): T[] {
  return [...list].sort((a, b) => {
    const m = dir === "asc" ? 1 : -1;
    const va = accessor(a, key);
    const vb = accessor(b, key);
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * m;
    return String(va).localeCompare(String(vb)) * m;
  });
}

/** Safely render a value that may be an XML-to-JSON object like {"@unit":"KG","#text":"23"} */
function safeText(val: unknown): string {
  if (val == null) return "";
  if (typeof val === "string" || typeof val === "number") return String(val);
  if (typeof val === "object") {
    const obj = val as Record<string, unknown>;
    // Handle XML attr pattern: {"@unit": "KG", "#text": "23"} → "23 KG"
    if ("#text" in obj) {
      const text = String(obj["#text"] ?? "");
      const unit = obj["@unit"] ?? obj["@Unit"] ?? "";
      return unit ? `${text} ${unit}` : text;
    }
    return JSON.stringify(val);
  }
  return String(val);
}

interface PassengerDetailSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  flightNumber: string;
  origin: string;
  date: string;
  pnr: string | null;
}

function DocsSection({ docs }: { docs: DocsData }) {
  if (!docs.documentNumber && !docs.nationality) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" />
          Travel Documents
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          {docs.documentType && <><dt className="text-muted-foreground">Type</dt><dd>{docs.documentType}</dd></>}
          {docs.documentNumber && <><dt className="text-muted-foreground">Number</dt><dd className="font-mono">{docs.documentNumber}</dd></>}
          {docs.documentCountry && <><dt className="text-muted-foreground">Issuing Country</dt><dd>{docs.documentCountry}</dd></>}
          {docs.nationality && <><dt className="text-muted-foreground">Nationality</dt><dd>{docs.nationality}</dd></>}
          {docs.dateOfBirth && <><dt className="text-muted-foreground">Date of Birth</dt><dd>{docs.dateOfBirth}</dd></>}
          {docs.gender && <><dt className="text-muted-foreground">Gender</dt><dd>{docs.gender}</dd></>}
          {docs.expiryDate && <><dt className="text-muted-foreground">Expiry</dt><dd>{docs.expiryDate}</dd></>}
        </dl>
      </CardContent>
    </Card>
  );
}

function ItinerarySection({ segments }: { segments: ItinerarySegment[] }) {
  if (segments.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Plane className="h-4 w-4 text-muted-foreground" />
          Itinerary ({segments.length} segment{segments.length > 1 ? "s" : ""})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {segments.map((seg, i) => (
          <div key={seg.segmentId || i} className="rounded-md border p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px]">{seg.airline}{seg.flight}</Badge>
              <span className="font-mono text-sm font-medium">{seg.origin}</span>
              <ChevronRight className="h-3 w-3 text-muted-foreground" />
              <span className="font-mono text-sm font-medium">{seg.destination}</span>
              <Badge variant="secondary" className="ml-auto text-[10px]">{seg.cabin}</Badge>
              {seg.seat && <Badge variant="outline" className="text-[10px]">{seg.seat}</Badge>}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>Class: {seg.bookingClass}</span>
              <span>Departs: {seg.departureDate}</span>
              {seg.aircraftType && <span>Aircraft: {seg.aircraftType}</span>}
              {seg.bagCount > 0 && <span>Bags: {seg.bagCount} ({safeText(seg.totalBagWeight)})</span>}
            </div>
            {seg.editCodes.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {seg.editCodes.map((c) => (
                  <Badge key={c} variant="secondary" className="text-[9px] px-1">{c}</Badge>
                ))}
              </div>
            )}
            {seg.bagTags.length > 0 && (
              <div className="space-y-1">
                <span className="text-xs font-medium text-muted-foreground">Bag Tags:</span>
                {seg.bagTags.map((bt) => (
                  <div key={bt.bagTagNumber} className="flex items-center gap-2 text-xs pl-2">
                    <Tag className="h-3 w-3 text-muted-foreground" />
                    <span className="font-mono">{bt.bagTagNumber}</span>
                    <span>{safeText(bt.weight)} {safeText(bt.unit)}</span>
                    <span className="text-muted-foreground">{bt.origin}→{bt.destination}</span>
                  </div>
                ))}
              </div>
            )}
            {seg.requiredInfo.length > 0 && (
              <div className="space-y-1">
                <span className="text-xs font-medium text-muted-foreground">Check-In Requirements:</span>
                {seg.requiredInfo.map((r, ri) => (
                  <div key={ri} className="flex items-center gap-2 text-xs pl-2">
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[9px] px-1",
                        r.detailStatus === "OK" || r.detailStatus === "Satisfied"
                          ? "border-emerald-300 text-emerald-600"
                          : r.detailStatus === "Required"
                          ? "border-red-300 text-red-600"
                          : "border-muted-foreground"
                      )}
                    >
                      {r.code}
                    </Badge>
                    <span className={cn(
                      r.detailStatus === "OK" || r.detailStatus === "Satisfied" ? "text-emerald-600" : "text-red-600"
                    )}>{r.detailStatus}</span>
                    {r.freeText && <span className="text-muted-foreground truncate">{r.freeText}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function BaggageSection({ passenger }: { passenger: DetailedPassenger }) {
  const routes = passenger.baggageRoutes;
  type BaggageSortKey = "flight" | "route" | "departureDate" | "bookingClass" | "segmentStatus";
  const { sortKey, sortDir, toggle } = useSortable<BaggageSortKey>("flight");
  const sorted = useMemo(
    () => sortBy(routes, sortKey, sortDir, (r, k) => {
      if (k === "flight") return `${r.airline}${r.flight}`;
      if (k === "route") return `${r.origin}${r.destination}`;
      if (k === "departureDate") return r.departureDate;
      if (k === "bookingClass") return r.bookingClass;
      return r.segmentStatus;
    }),
    [routes, sortKey, sortDir]
  );
  if (routes.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Luggage className="h-4 w-4 text-muted-foreground" />
          Baggage Routing
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead><SortHeader label="Flight" field="flight" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Route" field="route" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Date" field="departureDate" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Class" field="bookingClass" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Status" field="segmentStatus" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((r, i) => (
              <TableRow key={r.segmentId || i}>
                <TableCell className="font-mono text-xs">{r.airline}{r.flight}</TableCell>
                <TableCell className="text-xs">{r.origin}→{r.destination}</TableCell>
                <TableCell className="text-xs">{r.departureDate}</TableCell>
                <TableCell><Badge variant="outline" className="text-[10px]">{r.bookingClass}</Badge></TableCell>
                <TableCell><Badge variant="secondary" className="text-[10px]">{r.segmentStatus}</Badge></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function TimaticSection({ entries }: { entries: TimaticEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Globe className="h-4 w-4 text-muted-foreground" />
          Timatic Information
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {entries.map((t, i) => (
          <div key={i} className="rounded border p-2">
            <span className="text-xs font-medium">{t.country}</span>
            <p className="text-xs text-muted-foreground mt-0.5 whitespace-pre-wrap">{t.text}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AncillarySection({ segments }: { segments: ItinerarySegment[] }) {
  const allAe = useMemo(
    () => segments.flatMap((s) => s.aeDetails.map((ae) => ({ ...ae, flight: `${s.airline}${s.flight}` }))),
    [segments]
  );
  const { sortKey, sortDir, toggle } = useSortable<"flight" | "groupCode" | "statusCode" | "quantity" | "price" | "usedEMD">("flight");
  const sorted = useMemo(
    () => sortBy(allAe, sortKey, sortDir, (ae, k) => {
      if (k === "quantity") return Number(ae.quantity) || 0;
      if (k === "price") return Number(ae.price) || 0;
      return String((ae as Record<string, unknown>)[k] ?? "");
    }),
    [allAe, sortKey, sortDir]
  );
  if (allAe.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          Ancillary Purchases
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead><SortHeader label="Flight" field="flight" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Group" field="groupCode" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Status" field="statusCode" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Qty" field="quantity" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Price" field="price" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="EMD" field="usedEMD" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((ae, i) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">{ae.flight}</TableCell>
                <TableCell className="text-xs">{ae.groupCode}</TableCell>
                <TableCell><Badge variant="secondary" className="text-[10px]">{ae.statusCode}</Badge></TableCell>
                <TableCell className="text-xs">{ae.quantity}</TableCell>
                <TableCell className="text-xs">{ae.price} {ae.currency}</TableCell>
                <TableCell className="font-mono text-[10px]">{ae.usedEMD || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function FareBaggageSection({ segments }: { segments: ItinerarySegment[] }) {
  const allVcr = useMemo(
    () => segments.flatMap((s) => s.vcrInfo.map((v) => ({ ...v, flight: `${s.airline}${s.flight}`, origin: s.origin, destination: s.destination }))),
    [segments]
  );
  const { sortKey, sortDir, toggle } = useSortable<"flight" | "route" | "fareBasisCode" | "bagAllowance">("flight");
  const sorted = useMemo(
    () => sortBy(allVcr, sortKey, sortDir, (v, k) => {
      if (k === "route") return `${v.origin}${v.destination}`;
      if (k === "fareBasisCode" || k === "bagAllowance") return safeText((v as Record<string, unknown>)[k]);
      return String((v as Record<string, unknown>)[k] ?? "");
    }),
    [allVcr, sortKey, sortDir]
  );
  if (allVcr.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Tag className="h-4 w-4 text-muted-foreground" />
          Fare &amp; Baggage Allowance
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead><SortHeader label="Flight" field="flight" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Route" field="route" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Fare Basis" field="fareBasisCode" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
              <TableHead><SortHeader label="Bag Allowance" field="bagAllowance" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} /></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((v, i) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">{v.flight}</TableCell>
                <TableCell className="text-xs">{v.origin}→{v.destination}</TableCell>
                <TableCell className="font-mono text-xs font-medium">{safeText(v.fareBasisCode) || "—"}</TableCell>
                <TableCell className="text-xs">{safeText(v.bagAllowance) || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function CheckInRequirementsSection({ requirements }: { requirements: { code: string; detailStatus: string; freeText: string }[] }) {
  if (requirements.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          Check-In Requirements
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        {requirements.map((r, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <Badge
              variant="outline"
              className={cn(
                "text-[9px] px-1",
                r.detailStatus === "OK" || r.detailStatus === "Satisfied"
                  ? "border-emerald-300 text-emerald-600"
                  : r.detailStatus === "Required"
                  ? "border-red-300 text-red-600"
                  : "border-muted-foreground"
              )}
            >
              {r.code}
            </Badge>
            <span className={cn(
              r.detailStatus === "OK" || r.detailStatus === "Satisfied" ? "text-emerald-600" : "text-red-600"
            )}>{r.detailStatus}</span>
            {r.freeText && <span className="text-muted-foreground truncate">{r.freeText}</span>}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function PassengerDetailContent({
  flightNumber,
  origin,
  date,
  pnr,
}: {
  flightNumber: string;
  origin: string;
  date: string;
  pnr: string;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["passengerDetail", flightNumber, pnr, origin, date],
    queryFn: () => fetchPassengerDetail(flightNumber, pnr, origin, date),
    enabled: !!pnr,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Fetching live data from Sabre...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/20 bg-destructive/10 p-6 text-center text-sm text-destructive">
        Failed to load passenger details. The Sabre session may have expired.
      </div>
    );
  }

  if (!data || data.passengers.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground text-sm">
        No detail data found for PNR {pnr}.
      </div>
    );
  }

  const pax = data.passengers[0];

  return (
    <div className="space-y-4 pb-8">
      {/* Header info */}
      <div className="flex items-center gap-3">
        <div>
          <h3 className="text-lg font-semibold">{pax.lastName}, {pax.firstName}</h3>
          <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
            <span className="font-mono">{pax.pnr}</span>
            {pax.gender && <Badge variant="outline" className="text-[9px] px-1">{pax.gender}</Badge>}
            {pax.groupCode && <Badge variant="secondary" className="text-[9px] px-1">Group: {pax.groupCode}</Badge>}
            {pax.vcrNumber && <span>VCR: {pax.vcrNumber}</span>}
          </div>
        </div>
      </div>

      <Separator />

      <Tabs defaultValue="itinerary">
        <TabsList>
          <TabsTrigger value="itinerary">Itinerary</TabsTrigger>
          <TabsTrigger value="docs">Documents</TabsTrigger>
          <TabsTrigger value="baggage">Baggage</TabsTrigger>
          <TabsTrigger value="extras">Extras</TabsTrigger>
        </TabsList>

        <TabsContent value="itinerary" className="mt-4 space-y-4">
          <ItinerarySection segments={pax.itinerary} />
        </TabsContent>

        <TabsContent value="docs" className="mt-4 space-y-4">
          <DocsSection docs={pax.docsData} />
          <TimaticSection entries={pax.timaticInfo} />
        </TabsContent>

        <TabsContent value="baggage" className="mt-4 space-y-4">
          <BaggageSection passenger={pax} />
        </TabsContent>

        <TabsContent value="extras" className="mt-4 space-y-4">
          <AncillarySection segments={pax.itinerary} />
          <FareBaggageSection segments={pax.itinerary} />
          <CheckInRequirementsSection requirements={pax.requiredInfo} />
          {pax.freeText.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Free Text Entries</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                {pax.freeText.map((ft, i) => (
                  <div key={i} className="flex gap-2 text-xs">
                    <Badge variant="secondary" className="text-[9px] px-1 shrink-0">{ft.editCode}</Badge>
                    <span className="text-muted-foreground">{ft.text}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function PassengerDetailSheet({
  open,
  onOpenChange,
  flightNumber,
  origin,
  date,
  pnr,
}: PassengerDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[520px] sm:max-w-[520px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Passenger Detail</SheetTitle>
          <SheetDescription>
            Live data from Sabre GetPassengerDataRQ &middot; PNR {pnr}
          </SheetDescription>
        </SheetHeader>
        {pnr && (
          <PassengerDetailContent
            flightNumber={flightNumber}
            origin={origin}
            date={date}
            pnr={pnr}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
