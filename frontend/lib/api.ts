import type { 
  FlightListItem, FlightDashboard, FlightTree,
  SabreIngestRequest, SabreIngestResponse,
  SabreBatchRequest, SabreBatchAccepted, SabreJobStatus,
  PassengerListResponse, StandbyListResponse,
  PassengerDetailResponse, ChangeRecord, ChangeSummaryResponse,
  SnapshotMeta, FlightStatusRecord, ReservationsResponse,
  SnapshotCompareResponse, SnapshotRestoreResponse,
  FlightSchedule, ScheduleLookupRequest,
  PassengerTimelineResponse,
  FlightTimelineResponse, ActivityFeedResponse,
  BoardingProgressResponse, PassengerHistoryBadges,
  GroupBookingsResponse,
  AuditResponse,
  OtpFlight,
  ComparisonResult,
  PassengerComparisonResult,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const REQUEST_TIMEOUT_MS = 30_000;
const INGEST_POLL_INTERVAL_MS = 3_000;
const INGEST_JOB_TIMEOUT_MS = 10 * 60_000;

function isTerminalIngestJobStatus(status: SabreJobStatus["status"]): boolean {
  return status === "completed" || status === "partial" || status === "failed";
}

function withTimeout(ms: number): AbortSignal {
  return AbortSignal.timeout(ms);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    signal: withTimeout(REQUEST_TIMEOUT_MS),
  });
  if (!res.ok) {
    let errMsg = `API ${res.status}: ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson.detail)
        errMsg =
          typeof errJson.detail === "string"
            ? errJson.detail
            : JSON.stringify(errJson.detail);
    } catch {
      /* response body not JSON */
    }
    throw new Error(errMsg);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal: withTimeout(REQUEST_TIMEOUT_MS),
  });
  if (!res.ok) {
    let errMsg = `API ${res.status}: ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson.detail)
        errMsg =
          typeof errJson.detail === "string"
            ? errJson.detail
            : JSON.stringify(errJson.detail);
    } catch {
      /* response body not JSON */
    }
    throw new Error(errMsg);
  }
  return res.json() as Promise<T>;
}

export function getIngestFailureMessage(result: SabreFlightIngestResult): string {
  const orderedApis = [
    result.apis.flightStatus,
    result.apis.passengerList,
    result.apis.reservations,
  ];
  const apiError = orderedApis.find((apiResult) => apiResult?.error)?.error;
  if (apiError) {
    return apiError;
  }
  const flight = result.flight;
  return `Ingestion failed for GF${flight.flightNumber} ${flight.origin} ${flight.departureDate}.`;
}

export function fetchFlights(date?: string): Promise<FlightListItem[]> {
  const qs = date ? `?date=${date}` : "";
  return get<FlightListItem[]>(`/flights${qs}`);
}

export function fetchOtpFlights(date: string): Promise<OtpFlight[]> {
  return get<OtpFlight[]>(`/otp/flights?date=${date}`);
}

export function fetchDashboard(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<FlightDashboard> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightDashboard>(`/flights/${flightNumber}/dashboard${qs}`);
}

export function fetchFlightTree(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<FlightTree> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightTree>(`/flights/${flightNumber}/tree${qs}`);
}

export async function ingestFlight(payload: SabreIngestRequest): Promise<SabreIngestResponse> {
  const accepted = await ingestBatch({ flights: [payload] });
  const deadline = Date.now() + INGEST_JOB_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const job = await fetchJobStatus(accepted.jobId);

    if (job.status === "completed" || job.status === "partial") {
      const result = job.results?.[0];
      if (!result) {
        throw new Error("Ingestion completed without a flight result");
      }
      if (!result.success) {
        throw new Error(getIngestFailureMessage(result));
      }
      return {
        message: "Sabre ingestion completed",
        processedFlights: job.flightsProcessed,
        result,
      };
    }

    if (job.status === "failed") {
      throw new Error(job.error ?? "Ingestion job failed");
    }

    if (isTerminalIngestJobStatus(job.status)) {
      throw new Error(job.error ?? "Ingestion job failed");
    }

    await sleep(INGEST_POLL_INTERVAL_MS);
  }

  throw new Error("Ingestion job timed out");
}

export function ingestBatch(payload: SabreBatchRequest): Promise<SabreBatchAccepted> {
  return post<SabreBatchAccepted>("/flights/ingest/batch", payload);
}

export function fetchJobStatus(jobId: string): Promise<SabreJobStatus> {
  return get<SabreJobStatus>(`/flights/ingest/jobs/${jobId}`);
}

// --- Passengers ---

export function fetchPassengers(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<PassengerListResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<PassengerListResponse>(`/flights/${flightNumber}/passengers${qs}`);
}

export function fetchStandbyList(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<StandbyListResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<StandbyListResponse>(`/flights/${flightNumber}/passengers/standby-list${qs}`);
}

export function fetchPassengerDetail(
  flightNumber: string,
  pnr: string,
  origin: string,
  date: string,
  airline = "GF",
): Promise<PassengerDetailResponse> {
  const params = new URLSearchParams({ origin, date, airline });
  return get<PassengerDetailResponse>(`/flights/${flightNumber}/passengers/${pnr}/detail?${params}`);
}

// --- Changes ---

export function fetchChanges(
  flightNumber: string,
  origin?: string,
  date?: string,
  changeType?: string,
  limit = 100,
): Promise<ChangeRecord[]> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (changeType) params.set("change_type", changeType);
  params.set("limit", String(limit));
  return get<ChangeRecord[]>(`/flights/${flightNumber}/changes?${params}`);
}

export function fetchChangeSummary(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<ChangeSummaryResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<ChangeSummaryResponse>(`/flights/${flightNumber}/changes/summary${qs}`);
}

export function fetchPassengerChanges(
  flightNumber: string,
  pnr: string,
  date?: string,
): Promise<ChangeRecord[]> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<ChangeRecord[]>(`/flights/${flightNumber}/changes/passenger/${pnr}${qs}`);
}

export function fetchPassengerTimeline(
  flightNumber: string,
  pnr: string,
  origin?: string,
  date?: string,
): Promise<PassengerTimelineResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<PassengerTimelineResponse>(`/flights/${flightNumber}/passengers/${pnr}/timeline${qs}`);
}

// --- Flight Timeline & Activity ---

export function fetchFlightTimeline(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<FlightTimelineResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightTimelineResponse>(`/flights/${flightNumber}/timeline${qs}`);
}

export function fetchActivityFeed(
  date?: string,
  limit = 50,
  categories?: string,
): Promise<ActivityFeedResponse> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  params.set("limit", String(limit));
  if (categories) params.set("categories", categories);
  return get<ActivityFeedResponse>(`/activity/feed?${params}`);
}

export function fetchBoardingProgress(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<BoardingProgressResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<BoardingProgressResponse>(`/flights/${flightNumber}/boarding-progress${qs}`);
}

export function fetchPassengerHistoryBadges(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<PassengerHistoryBadges> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<PassengerHistoryBadges>(`/flights/${flightNumber}/passengers/history-badges${qs}`);
}

// --- Status History & Snapshots ---

export function fetchStatusHistory(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<FlightStatusRecord[]> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightStatusRecord[]>(`/flights/${flightNumber}/status/history${qs}`);
}

export function fetchSnapshots(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotType?: string,
): Promise<SnapshotMeta[]> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotType) params.set("snapshot_type", snapshotType);
  const qs = params.toString() ? `?${params}` : "";
  return get<SnapshotMeta[]>(`/flights/${flightNumber}/snapshots${qs}`);
}

export function compareSnapshot(
  flightNumber: string,
  snapshotSequence: number,
  origin?: string,
  date?: string,
): Promise<SnapshotCompareResponse> {
  const params = new URLSearchParams();
  params.set("snapshot_sequence", String(snapshotSequence));
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  return get<SnapshotCompareResponse>(`/flights/${flightNumber}/snapshots/compare?${params}`);
}

export function restoreSnapshotVersion(
  flightNumber: string,
  snapshotSequence: number,
  origin?: string,
  date?: string,
): Promise<SnapshotRestoreResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return post<SnapshotRestoreResponse>(`/flights/${flightNumber}/snapshots/${snapshotSequence}/restore${qs}`);
}

// --- Reservations ---

export function fetchReservations(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<ReservationsResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<ReservationsResponse>(`/flights/${flightNumber}/reservations${qs}`);
}

// --- Flight Schedule ---

export function fetchSchedule(
  flightNumber: string,
  date?: string,
): Promise<FlightSchedule> {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightSchedule>(`/flights/${flightNumber}/schedule${qs}`);
}

export function lookupSchedule(
  payload: ScheduleLookupRequest,
): Promise<FlightSchedule> {
  return post<FlightSchedule>("/flights/schedule/lookup", payload);
}

// --- Group Bookings ---

export function fetchGroupBookings(
  flightNumber: string,
  origin?: string,
  date?: string,
  snapshotSequence?: number | null,
): Promise<GroupBookingsResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (snapshotSequence) params.set("snapshot_sequence", String(snapshotSequence));
  const qs = params.toString() ? `?${params}` : "";
  return get<GroupBookingsResponse>(`/flights/${flightNumber}/passengers/groups${qs}`);
}

// --- Process Audit ---

export function fetchAudit(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<AuditResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<AuditResponse>(`/flights/${flightNumber}/audit${qs}`);
}

// --- Data Audit (Cross-DB Comparison) ---

export function fetchDataAuditCompare(
  flightNumber: string,
  origin?: string,
  date?: string,
  seq?: number,
): Promise<ComparisonResult> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (seq) params.set("seq", String(seq));
  const qs = params.toString() ? `?${params}` : "";
  return get<ComparisonResult>(`/data-audit/${flightNumber}/compare${qs}`);
}

export function fetchDataAuditPassengers(
  flightNumber: string,
  origin?: string,
  date?: string,
  seq?: number,
): Promise<PassengerComparisonResult> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  if (seq) params.set("seq", String(seq));
  const qs = params.toString() ? `?${params}` : "";
  return get<PassengerComparisonResult>(`/data-audit/${flightNumber}/passengers${qs}`);
}
