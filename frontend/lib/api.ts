import type { FlightListItem, FlightDashboard, FlightTree } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
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
