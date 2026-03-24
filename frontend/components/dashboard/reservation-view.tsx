"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  Search,
  Users,
  Ticket,
  Plane,
  ChevronRight,
  Mail,
  Phone,
  MessageSquare,
  ShieldCheck,
  ShoppingBag,
} from "lucide-react";
import { format } from "date-fns";
import { fetchReservations } from "@/lib/api";
import { useDebounce } from "@/lib/hooks";
import type { Reservation } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

interface ReservationViewProps {
  flightNumber: string;
  origin: string;
  date: string;
}

function formatDate(d: string): string {
  try {
    return format(new Date(d), "dd MMM yyyy");
  } catch {
    return d;
  }
}

function ReservationCard({
  reservation,
  expanded,
  onToggle,
}: {
  reservation: Reservation;
  expanded: boolean;
  onToggle: () => void;
}) {
  const r = reservation;
  return (
    <Card className="overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full text-left"
      >
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <span className="font-mono">{r.pnr}</span>
              <Badge variant="secondary" className="text-[10px]">
                <Users className="h-3 w-3 mr-0.5" />
                {r.numberInParty}
              </Badge>
              {r.numberOfInfants > 0 && (
                <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-600">
                  {r.numberOfInfants} Inf
                </Badge>
              )}
            </CardTitle>
            <ChevronRight
              className={cn(
                "h-4 w-4 text-muted-foreground transition-transform",
                expanded && "rotate-90"
              )}
            />
          </div>
          <div className="flex gap-3 text-xs text-muted-foreground">
            {r.passengers.slice(0, 3).map((p, i) => (
              <span key={i}>{p.lastName}, {p.firstName}</span>
            ))}
            {r.passengers.length > 3 && <span>+{r.passengers.length - 3} more</span>}
          </div>
        </CardHeader>
      </button>

      {expanded && (
        <CardContent className="space-y-4 pt-2">
          {/* Passengers */}
          <div>
            <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
              <Users className="h-3 w-3" /> Passengers
            </h4>
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/30">
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Gender</TableHead>
                  <TableHead>DOB</TableHead>
                  <TableHead>Nationality</TableHead>
                  <TableHead>Seat</TableHead>
                  <TableHead>FF#</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.passengers.map((p, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium text-sm">{p.lastName}, {p.firstName}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-[10px]">{p.nameType || "ADT"}</Badge>
                    </TableCell>
                    <TableCell className="text-xs">{p.gender || "—"}</TableCell>
                    <TableCell className="text-xs">{p.dateOfBirth ? formatDate(p.dateOfBirth) : "—"}</TableCell>
                    <TableCell className="text-xs">{p.nationality || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{p.seatNumber || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {p.frequentFlyerNumber ? `${p.frequentFlyerAirline} ${p.frequentFlyerNumber}` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Segments */}
          {r.segments.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <Plane className="h-3 w-3" /> Segments
              </h4>
              <div className="space-y-2">
                {r.segments.map((seg, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs rounded border p-2">
                    <Badge variant="outline" className="text-[10px]">
                      {seg.marketingAirline}{seg.flightNumber}
                    </Badge>
                    <span className="font-mono font-medium">{seg.departureAirport}</span>
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                    <span className="font-mono font-medium">{seg.arrivalAirport}</span>
                    <span className="text-muted-foreground ml-auto">{seg.departureDate}</span>
                    <Badge variant="secondary" className="text-[10px]">{seg.bookingClass}</Badge>
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[10px]",
                        seg.status === "HK" ? "border-emerald-300 text-emerald-600" : ""
                      )}
                    >
                      {seg.status}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tickets */}
          {r.tickets.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <Ticket className="h-3 w-3" /> Tickets
              </h4>
              <div className="flex flex-wrap gap-2">
                {r.tickets.map((t, i) => (
                  <Badge key={i} variant="outline" className="font-mono text-[10px] px-2">
                    {t.ticketNumber}
                    {t.eTicket === "true" && <span className="ml-1 text-emerald-500">eT</span>}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* SSR — Special Service Requests */}
          {r.ssrRequests && r.ssrRequests.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <ShieldCheck className="h-3 w-3" /> Special Service Requests
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {r.ssrRequests.map((ssr, i) => (
                  <Badge
                    key={i}
                    variant="outline"
                    className={cn(
                      "text-[10px] px-2",
                      ssr.status === "HK" || ssr.status === "KK"
                        ? "border-emerald-300 text-emerald-600"
                        : ssr.status === "UC" || ssr.status === "UN"
                        ? "border-red-300 text-red-600"
                        : ""
                    )}
                  >
                    <span className="font-semibold">{ssr.code}</span>
                    {ssr.text && <span className="ml-1 text-muted-foreground">{ssr.text}</span>}
                    {ssr.status && <span className="ml-1 opacity-60">[{ssr.status}]</span>}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Contact Info — Email & Phone */}
          {((r.emails && r.emails.length > 0) || (r.phones && r.phones.length > 0)) && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <Mail className="h-3 w-3" /> Contact Info
              </h4>
              <div className="flex flex-wrap gap-2">
                {r.emails?.map((email, i) => (
                  <Badge key={`e-${i}`} variant="secondary" className="text-[10px] px-2">
                    <Mail className="h-3 w-3 mr-1" />
                    {email}
                  </Badge>
                ))}
                {r.phones?.map((ph, i) => (
                  <Badge key={`p-${i}`} variant="secondary" className="text-[10px] px-2">
                    <Phone className="h-3 w-3 mr-1" />
                    {ph.number}
                    {ph.type && <span className="ml-1 opacity-60">({ph.type})</span>}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Remarks */}
          {r.remarks && r.remarks.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <MessageSquare className="h-3 w-3" /> Remarks
              </h4>
              <div className="space-y-1">
                {r.remarks.map((rm, i) => (
                  <div key={i} className="text-xs rounded border p-2 flex items-start gap-2">
                    {rm.type && (
                      <Badge variant="outline" className="text-[9px] px-1 shrink-0">{rm.type}</Badge>
                    )}
                    <span className="text-muted-foreground">{rm.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Ancillary Services */}
          {r.ancillaryServices && r.ancillaryServices.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                <ShoppingBag className="h-3 w-3" /> Ancillary Services
              </h4>
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/30">
                    <TableHead>Service</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Qty</TableHead>
                    <TableHead>Group</TableHead>
                    <TableHead>EMD</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.ancillaryServices.map((anc, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs font-medium">{anc.code}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={cn(
                          "text-[9px]",
                          anc.status === "HD" || anc.status === "HI"
                            ? "border-emerald-300 text-emerald-600"
                            : ""
                        )}>{anc.status}</Badge>
                      </TableCell>
                      <TableCell className="text-xs">{anc.quantity}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{anc.groupCode}{anc.subGroupCode ? `/${anc.subGroupCode}` : ""}</TableCell>
                      <TableCell className="font-mono text-[10px]">{anc.emdNumber || "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Meta */}
          <div className="flex gap-4 text-[10px] text-muted-foreground pt-1">
            <span>Created: {formatDate(r.createdAt)}</span>
            <span>Updated: {formatDate(r.updatedAt)}</span>
            {r.receivedFrom && <span>Agent: {r.receivedFrom}</span>}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

export function ReservationView({ flightNumber, origin, date }: ReservationViewProps) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search);
  const [expandedPnr, setExpandedPnr] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["reservations", flightNumber, origin, date],
    queryFn: () => fetchReservations(flightNumber, origin, date),
  });

  const reservations = useMemo(() => data?.reservations ?? [], [data]);

  const filtered = useMemo(() => {
    if (!debouncedSearch) return reservations;
    const q = debouncedSearch.toLowerCase();
    return reservations.filter(
      (r) =>
        r.pnr.toLowerCase().includes(q) ||
        r.passengers.some(
          (p) =>
            p.lastName.toLowerCase().includes(q) ||
            p.firstName.toLowerCase().includes(q)
        )
    );
  }, [reservations, debouncedSearch]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading reservations...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/20 bg-destructive/10 p-6 text-center text-sm text-destructive">
        Failed to load reservations.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search PNR or passenger name..."
            aria-label="Search reservations"
            className="w-full rounded-md border border-input bg-background px-9 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>
        <span className="text-xs text-muted-foreground">
          {filtered.length} of {reservations.length} reservations
        </span>
      </div>

      {/* Reservation Cards */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          No reservations found.
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((r) => (
            <ReservationCard
              key={r.pnr}
              reservation={r}
              expanded={expandedPnr === r.pnr}
              onToggle={() => setExpandedPnr(expandedPnr === r.pnr ? null : r.pnr)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
