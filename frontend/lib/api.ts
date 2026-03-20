import type { 
  FlightListItem, FlightDashboard, FlightTree,
  SabreIngestRequest, SabreIngestResponse,
  SabreBatchRequest, SabreBatchAccepted, SabreJobStatus,
  PassengerListResponse, StandbyListResponse,
  PassengerDetailResponse, ChangeRecord, ChangeSummaryResponse,
  SnapshotMeta, FlightStatusRecord, ReservationsResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const REQUEST_TIMEOUT_MS = 30_000;

function withTimeout(ms: number): AbortSignal {
  return AbortSignal.timeout(ms);
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

export function fetchFlights(date?: string): Promise<FlightListItem[]> {
  const qs = date ? `?date=${date}` : "";
  return get<FlightListItem[]>(`/flights${qs}`);
}

export function fetchDashboard(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<FlightDashboard> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightDashboard>(`/flights/${flightNumber}/dashboard${qs}`);
}

export function fetchFlightTree(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<FlightTree> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<FlightTree>(`/flights/${flightNumber}/tree${qs}`);
}

export function ingestFlight(payload: SabreIngestRequest): Promise<SabreIngestResponse> {
  return post<SabreIngestResponse>("/flights/ingest", payload);
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
): Promise<PassengerListResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<PassengerListResponse>(`/flights/${flightNumber}/passengers${qs}`);
}

export function fetchStandbyList(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<StandbyListResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
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

// --- Reservations ---

export function fetchReservations(
  flightNumber: string,
  origin?: string,
  date?: string,
): Promise<ReservationsResponse> {
  const params = new URLSearchParams();
  if (origin) params.set("origin", origin);
  if (date) params.set("date", date);
  const qs = params.toString() ? `?${params}` : "";
  return get<ReservationsResponse>(`/flights/${flightNumber}/reservations${qs}`);
}
