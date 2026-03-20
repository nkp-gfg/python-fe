"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CloudDownload,
  Database,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  CalendarDays,
  Loader2,
  Plus,
  RefreshCw,
  ServerCog,
} from "lucide-react";

import { ingestFlight, ingestBatch, fetchJobStatus } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type {
  SabreIngestRequest,
  SabreFlightIngestResult,
  SabreApiResult,
} from "@/lib/types";

// Helper components for crisp input styles
const inputStyles = "flex h-9 w-full rounded-md border border-input bg-background/50 px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function IngestionPanel() {
  const dateInputRef = useRef<HTMLInputElement>(null);
  const [formData, setFormData] = useState({
    airline: "GF",
    flightNumber: "",
    origin: "",
    departureDate: "",
    departureTime: "08:00",
  });

  const [batchQueue, setBatchQueue] = useState<SabreIngestRequest[]>([]);
  const [singleResult, setSingleResult] = useState<SabreFlightIngestResult | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const singleMutation = useMutation({
    mutationKey: ["ingest"],
    mutationFn: ingestFlight,
    onSuccess: (data) => {
      setSingleResult(data.result);
    },
  });

  const batchMutation = useMutation({
    mutationKey: ["ingest-batch"],
    mutationFn: ingestBatch,
    onSuccess: (data) => {
      setActiveJobId(data.jobId);
    },
  });

  const { data: jobStatus, refetch: refetchJob, isFetching: jobFetching } = useQuery({
    queryKey: ["batch-job", activeJobId],
    queryFn: () => fetchJobStatus(activeJobId!),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 3000;
    },
  });

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  // Auto-format DD/MM/YYYY as user types
  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    let v = e.target.value.replace(/[^0-9]/g, "").slice(0, 8);
    if (v.length > 4) v = v.slice(0, 2) + "/" + v.slice(2, 4) + "/" + v.slice(4);
    else if (v.length > 2) v = v.slice(0, 2) + "/" + v.slice(2);
    setFormData((prev) => ({ ...prev, departureDate: v }));
  };

  // Native picker → DD/MM/YYYY
  const handlePickerChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const iso = e.target.value; // YYYY-MM-DD
    if (!iso) return;
    const [y, m, d] = iso.split("-");
    setFormData((prev) => ({ ...prev, departureDate: `${d}/${m}/${y}` }));
  };

  // Auto-format HH:MM as user types
  const handleTimeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    let v = e.target.value.replace(/[^0-9]/g, "").slice(0, 4);
    if (v.length > 2) v = v.slice(0, 2) + ":" + v.slice(2);
    setFormData((prev) => ({ ...prev, departureTime: v }));
  };

  // Convert DD/MM/YYYY → YYYY-MM-DD with calendar-date validation
  const toIsoDate = (ddmmyyyy: string): string | null => {
    const m = ddmmyyyy.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (!m) return null;
    const day = parseInt(m[1], 10);
    const month = parseInt(m[2], 10);
    const year = parseInt(m[3], 10);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    // Verify the date actually exists (e.g. no Feb 30)
    const d = new Date(year, month - 1, day);
    if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null;
    return `${m[3]}-${m[2]}-${m[1]}`;
  };

  const isoDate = toIsoDate(formData.departureDate);
  const timeValid = (() => {
    const tm = formData.departureTime.match(/^(\d{2}):(\d{2})$/);
    if (!tm) return false;
    const h = parseInt(tm[1], 10);
    const min = parseInt(tm[2], 10);
    return h >= 0 && h <= 23 && min >= 0 && min <= 59;
  })();

  const buildRequest = (): SabreIngestRequest => ({
    airline: formData.airline,
    flightNumber: formData.flightNumber,
    origin: formData.origin.toUpperCase(),
    departureDate: isoDate!,
    departureDateTime: `${isoDate}T${formData.departureTime}:00`,
  });

  const handleIngestSingle = () => {
    setSingleResult(null);
    singleMutation.mutate(buildRequest());
  };

  const handleAddToBatch = () => {
    setBatchQueue((prev) => [...prev, buildRequest()]);
    setFormData((prev) => ({
      ...prev,
      flightNumber: "",
      origin: "", 
    }));
  };

  const handleRunBatch = () => {
    if (batchQueue.length === 0) return;
    batchMutation.mutate({ flights: batchQueue });
  };

  const isBatchRunning = Boolean(activeJobId && (!jobStatus || jobStatus.status === "accepted" || jobStatus.status === "running"));
  const isFormValid = Boolean(formData.flightNumber && formData.origin && isoDate && timeValid);

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto p-4 md:p-6 bg-muted/10">
      {/* Form Section */}
      <Card className="shadow-sm">
        <CardContent className="p-5">
          <div className="mb-5 flex items-center gap-2 border-b pb-3">
            <ServerCog className="h-4 w-4 text-blue-500" />
            <h2 className="text-sm font-semibold tracking-wide">Single Flight Sync</h2>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <LabelInput label="Airline Code">
              <input name="airline" disabled value={formData.airline} className={inputStyles} />
            </LabelInput>
            <LabelInput label="Flight Number">
              <input
                name="flightNumber"
                placeholder="e.g. 2006"
                value={formData.flightNumber}
                onChange={handleInputChange}
                className={inputStyles}
              />
            </LabelInput>
            <LabelInput label="Origin">
              <input
                name="origin"
                placeholder="e.g. LHR"
                value={formData.origin}
                onChange={handleInputChange}
                className={cn(inputStyles, "uppercase")}
              />
            </LabelInput>
            <LabelInput label="Departure Date">
              <div className="relative">
                <input
                  name="departureDate"
                  placeholder="DD/MM/YYYY"
                  value={formData.departureDate}
                  onChange={handleDateChange}
                  maxLength={10}
                  className={inputStyles}
                />
                <input
                  ref={dateInputRef}
                  type="date"
                  value={isoDate ?? ""}
                  onChange={handlePickerChange}
                  className="sr-only"
                  tabIndex={-1}
                  aria-hidden
                />
                <button
                  type="button"
                  onClick={() => dateInputRef.current?.showPicker()}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <CalendarDays className="h-4 w-4" />
                </button>
              </div>
            </LabelInput>
            <LabelInput label="Departure Time (24h)">
              <input
                name="departureTime"
                placeholder="HH:MM"
                value={formData.departureTime}
                onChange={handleTimeChange}
                maxLength={5}
                className={inputStyles}
              />
            </LabelInput>
          </div>

          <div className="mt-6 flex flex-col sm:flex-row items-center gap-3">
            <Button
              onClick={handleIngestSingle}
              disabled={!isFormValid || singleMutation.isPending || isBatchRunning}
              className="w-full sm:flex-1"
            >
              {singleMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CloudDownload className="mr-2 h-4 w-4" />
              )}
              Ingest Flight
            </Button>
            <Button
              variant="outline"
              onClick={handleAddToBatch}
              disabled={!isFormValid || isBatchRunning}
              className="w-full sm:flex-1"
            >
              <Plus className="mr-2 h-4 w-4" />
              Add to Batch
            </Button>
          </div>

          {singleMutation.isError && (
            <div className="mt-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              Error ingesting flight: {singleMutation.error.message}
            </div>
          )}

          {singleResult && (
            <div className="mt-6 space-y-3">
              <h3 className="text-xs font-semibold uppercase text-muted-foreground">Sync Results</h3>
              <FlightResultCard result={singleResult} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Batch Section */}
      <Card className="shadow-sm">
        <CardContent className="p-5">
          <div className="mb-5 flex items-center justify-between border-b pb-3">
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-emerald-500" />
              <h2 className="text-sm font-semibold tracking-wide">Batch Queue</h2>
            </div>
            <Badge variant="secondary" className="font-mono">
              {batchQueue.length} items
            </Badge>
          </div>

          {batchQueue.length > 0 && !activeJobId && (
            <div className="mb-5 space-y-2">
              {batchQueue.map((f, i) => (
                <div key={i} className="flex flex-col sm:flex-row sm:items-center justify-between rounded-md border bg-muted/40 p-3 text-sm">
                  <div className="font-semibold text-foreground">
                    {f.airline}{f.flightNumber} <span className="font-normal text-muted-foreground ml-1">· {f.origin}</span>
                  </div>
                  <div className="text-muted-foreground text-xs mt-1 sm:mt-0">{f.departureDateTime}</div>
                </div>
              ))}
            </div>
          )}

          {batchQueue.length === 0 && !activeJobId && (
            <div className="py-8 text-center text-sm text-muted-foreground opacity-80">
              Queue is empty. Add flights above to process them in bulk.
            </div>
          )}

          {!activeJobId && batchQueue.length > 0 && (
            <Button
              onClick={handleRunBatch}
              disabled={batchMutation.isPending}
              className="w-full"
            >
              {batchMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-2 h-4 w-4" />
              )}
              Run Background Batch
            </Button>
          )}

          {batchMutation.isError && (
            <div className="mt-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              Error submitting batch: {batchMutation.error.message}
            </div>
          )}

          {activeJobId && jobStatus && (
            <div className="mt-4 space-y-4">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium capitalize text-foreground flex items-center gap-2">
                  Status: 
                  <span className={cn(
                    jobStatus.status === "completed" ? "text-emerald-500" :
                    jobStatus.status === "failed" ? "text-destructive" : "text-blue-500"
                  )}>{jobStatus.status}</span>
                </span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => refetchJob()}
                    disabled={jobFetching}
                    className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                  >
                    <RefreshCw className={cn("h-3.5 w-3.5", jobFetching && "animate-spin")} />
                  </button>
                  <span className="text-muted-foreground font-medium">
                    {jobStatus.flightsProcessed} / {jobStatus.flightsQueued}
                  </span>
                </div>
              </div>
              
              <div className="h-2.5 rounded-full bg-secondary overflow-hidden">
                <div 
                  className={cn(
                    "h-full transition-all duration-500",
                    jobStatus.status === "failed" ? "bg-destructive" :
                    jobStatus.status === "completed" ? "bg-emerald-500" : "bg-blue-500"
                  )}
                  style={{ width: `${Math.max(5, (jobStatus.flightsProcessed / Math.max(1, jobStatus.flightsQueued)) * 100)}%` }}
                />
              </div>

              {jobStatus.error && (
                <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                  {jobStatus.error}
                </div>
              )}

              {jobStatus.status === "completed" && jobStatus.results && (
                <div className="space-y-3 mt-6">
                  <h3 className="text-xs font-semibold uppercase text-muted-foreground">Results</h3>
                  {jobStatus.results.map((res, i) => (
                    <FlightResultCard key={i} result={res} />
                  ))}
                  <Button 
                    variant="secondary" 
                    className="w-full mt-4" 
                    onClick={() => {
                      setActiveJobId(null);
                      setBatchQueue([]);
                    }}
                  >
                    Clear Batch
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function LabelInput({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label className="text-xs font-medium text-foreground">{label}</label>
      {children}
    </div>
  );
}

function FlightResultCard({ result }: { result: SabreFlightIngestResult }) {
  const f = result.flight;
  return (
    <div className="rounded-lg border bg-card p-3 shadow-sm">
      <div className="mb-3 flex items-center justify-between border-b pb-2">
        <div className="font-semibold text-foreground">
          {f.airline}{f.flightNumber} <span className="font-normal text-muted-foreground ml-1">· {f.origin}</span>
        </div>
        {result.success ? (
          <Badge variant="outline" className="text-emerald-500 border-emerald-500/30 bg-emerald-500/10">Success</Badge>
        ) : (
          <Badge variant="outline" className="text-destructive border-destructive/30 bg-destructive/10">Failed</Badge>
        )}
      </div>
      <div className="space-y-2.5">
        <ApiStatusRow name="Flight Status" api={result.apis.flightStatus} />
        <ApiStatusRow name="Passenger List" api={result.apis.passengerList} />
        <ApiStatusRow name="Reservations" api={result.apis.reservations} />
      </div>
    </div>
  );
}

function ApiStatusRow({ name, api }: { name: string; api: SabreApiResult }) {
  if (!api) return null;
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{name}</span>
      <div className="flex items-center gap-3">
        {api.changesStored !== undefined && (
          <span className="text-xs text-muted-foreground font-medium">{api.changesStored} chg</span>
        )}
        {api.durationMs !== undefined && (
          <span className="flex items-center text-xs text-muted-foreground font-medium"><Clock className="mr-1 h-3 w-3" />{api.durationMs}ms</span>
        )}
        {api.status === "success" ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        ) : (
          <XCircle className="h-4 w-4 text-destructive" />
        )}
      </div>
    </div>
  );
}
