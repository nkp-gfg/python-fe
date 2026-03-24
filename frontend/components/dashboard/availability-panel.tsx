"use client";

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Radar, RefreshCw, Sparkles } from "lucide-react";

import { fetchAvailability, lookupAvailability } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AvailabilityPanelProps {
  flightNumber: string;
  origin: string;
  date: string;
}

export function AvailabilityPanel({ flightNumber, origin, date }: AvailabilityPanelProps) {
  const queryClient = useQueryClient();

  const availabilityQuery = useQuery({
    queryKey: ["availability", flightNumber, origin, date],
    queryFn: () => fetchAvailability(flightNumber, origin, date),
    retry: false,
  });

  const lookupMutation = useMutation({
    mutationFn: () =>
      lookupAvailability(flightNumber, {
        airline: "GF",
        origin,
        departureDate: date,
        classCodes: "YJ",
        resolveIndicator: "Y",
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["availability", flightNumber, origin, date], data);
      queryClient.invalidateQueries({ queryKey: ["dashboard", flightNumber, origin, date] });
      queryClient.invalidateQueries({ queryKey: ["flights"] });
    },
  });

  const flattenedSegments = useMemo(() => {
    const data = availabilityQuery.data;
    if (!data) return [];

    const rows: Array<{
      route: string;
      segment: string;
      status: string;
      classes: Array<{ classCode: string; seats: number }>;
    }> = [];

    data.originDestinations.forEach((od) => {
      od.itineraries.forEach((itin, itinIdx) => {
        itin.segments.forEach((seg) => {
          rows.push({
            route: `${od.origin || seg.origin}-${od.destination || seg.destination}`,
            segment: `#${seg.segmentId} ${seg.carrierCode}${seg.flightNumber}`,
            status: seg.returnCode && seg.returnCode !== "0" ? `RC ${seg.returnCode}` : "OK",
            classes: seg.availabilityByClass,
          });
        });
        if (itin.segments.length === 0) {
          rows.push({
            route: `${od.origin}-${od.destination}`,
            segment: `Itinerary ${itinIdx + 1}`,
            status: "No segments",
            classes: [],
          });
        }
      });
    });

    return rows;
  }, [availabilityQuery.data]);

  if (availabilityQuery.isLoading) {
    return (
      <div className="rounded-xl border bg-card p-8 text-sm text-muted-foreground flex items-center justify-center">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading availability...
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Card className="border bg-card">
        <CardContent className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <Radar className="h-4 w-4 text-sky-500" />
                <h3 className="text-sm font-semibold">MultiFlight Availability</h3>
                {availabilityQuery.data?.success ? (
                  <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">Live</Badge>
                ) : (
                  <Badge variant="secondary">Needs refresh</Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Route-level class availability for revenue and disruption decisions.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-2"
                onClick={() => availabilityQuery.refetch()}
                disabled={availabilityQuery.isFetching || lookupMutation.isPending}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${availabilityQuery.isFetching ? "animate-spin" : ""}`} />
                Refresh
              </Button>
              <Button
                size="sm"
                className="h-8 gap-2"
                onClick={() => lookupMutation.mutate()}
                disabled={lookupMutation.isPending}
              >
                {lookupMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                Pull from Sabre
              </Button>
            </div>
          </div>

          {lookupMutation.isError && (
            <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              {(lookupMutation.error as Error).message}
            </div>
          )}
          {availabilityQuery.error && (
            <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700 flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5" />
              {(availabilityQuery.error as Error).message}
            </div>
          )}
          {availabilityQuery.data?.requestProfile && (
            <div className="mt-3 rounded-md border border-sky-200 bg-sky-50/60 px-3 py-2 text-[11px] text-sky-800">
              <span className="font-medium">Request Profile:</span>{" "}
              attempt {availabilityQuery.data.requestProfile.attempt}, action {availabilityQuery.data.requestProfile.action},
              ebXML {availabilityQuery.data.requestProfile.ebxmlVersion}, mustUnderstand {availabilityQuery.data.requestProfile.mustUnderstand}
            </div>
          )}
        </CardContent>
      </Card>

      {availabilityQuery.data && (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-4">
          <MetricCard label="Segments" value={availabilityQuery.data.summary.segments} />
          <MetricCard label="Polled" value={availabilityQuery.data.summary.polledSegments} />
          <MetricCard label="Error Segments" value={availabilityQuery.data.summary.errorSegments} tone="warn" />
          <MetricCard
            label="Classes Tracked"
            value={availabilityQuery.data.summary.availableClasses.length}
            sub={availabilityQuery.data.summary.availableClasses.join(", ") || "—"}
          />
        </div>
      )}

      <Card className="border bg-card">
        <CardContent className="p-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-2 font-medium">Route</th>
                  <th className="px-2 py-2 font-medium">Segment</th>
                  <th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 font-medium">Class Availability</th>
                </tr>
              </thead>
              <tbody>
                {flattenedSegments.length === 0 ? (
                  <tr>
                    <td className="px-2 py-6 text-muted-foreground" colSpan={4}>
                      No stored availability yet. Use Pull from Sabre to fetch MultiFlight data.
                    </td>
                  </tr>
                ) : (
                  flattenedSegments.map((row, idx) => (
                    <tr key={`${row.segment}-${idx}`} className="border-b last:border-0">
                      <td className="px-2 py-2 font-medium">{row.route}</td>
                      <td className="px-2 py-2">{row.segment}</td>
                      <td className="px-2 py-2">
                        <span className={row.status === "OK" ? "text-emerald-600" : "text-amber-600"}>{row.status}</span>
                      </td>
                      <td className="px-2 py-2">
                        <div className="flex flex-wrap gap-1">
                          {row.classes.length === 0 ? (
                            <span className="text-muted-foreground">—</span>
                          ) : (
                            row.classes.map((c) => (
                              <span
                                key={`${row.segment}-${c.classCode}`}
                                className="rounded-md border bg-muted/50 px-1.5 py-0.5 font-medium"
                              >
                                {c.classCode}:{c.seats}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function MetricCard({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: number;
  sub?: string;
  tone?: "default" | "warn";
}) {
  return (
    <Card className={`border ${tone === "warn" ? "border-amber-300 bg-amber-50/60" : "bg-card"}`}>
      <CardContent className="p-3">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
        <p className="mt-1 text-xl font-semibold leading-none">{value}</p>
        {sub && <p className="mt-1 text-[11px] text-muted-foreground truncate">{sub}</p>}
      </CardContent>
    </Card>
  );
}
