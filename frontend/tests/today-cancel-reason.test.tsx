import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TodayPage from "@/app/today/page";
import { ToastProvider } from "@/components/ui/toast";
import type { OtpFlight } from "@/lib/types";

/**
 * Fixture matches the JSON shape of backend.api.routes.otp.OtpFlight after
 * _row_to_flight copies otp.flight_xml_current.cancel_reason_code onto
 * cancelReasonCode and sets isCancelled from the status text.
 */
const fetchOtpFlights = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchOtpFlights: (...args: unknown[]) => fetchOtpFlights(...args),
  fetchJobStatus: vi.fn(),
  getIngestFailureMessage: vi.fn(),
  ingestBatch: vi.fn(),
  ingestFlight: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/components/theme-toggle", () => ({
  ThemeToggle: () => <button type="button">Toggle theme</button>,
}));

const WEATHER = "WX-FOG";
const COMMERCIAL = "COM-LOAD";
const TECHNICAL = "TECH-42";

function flight(overrides: Partial<OtpFlight> & Pick<OtpFlight, "flightSequenceNumber" | "flightNumber" | "flightStatus">): OtpFlight {
  return {
    carrierCode: "GF",
    origin: "BAH",
    destination: "LHR",
    flightDate: "2026-03-30",
    scheduledDepartureUtc: "2026-03-30T09:55:00",
    scheduledArrivalUtc: "2026-03-30T14:10:00",
    isCancelled: false,
    totalPax: 120,
    cancelReasonCode: null,
    ...overrides,
  };
}

const weatherCancelled = flight({
  flightSequenceNumber: 9001,
  flightNumber: "2057",
  flightStatus: "CANCELLED",
  isCancelled: true,
  cancelReasonCode: WEATHER,
});

const commercialCancelled = flight({
  flightSequenceNumber: 9002,
  flightNumber: "188",
  flightStatus: "CANCELLED",
  isCancelled: true,
  cancelReasonCode: COMMERCIAL,
  origin: "BAH",
  destination: "DXB",
});

const technicalCnx = flight({
  flightSequenceNumber: 9003,
  flightNumber: "3001",
  flightStatus: "CNX",
  isCancelled: true,
  cancelReasonCode: TECHNICAL,
  origin: "BAH",
  destination: "MCT",
});

const cancelledWithoutReason = flight({
  flightSequenceNumber: 9004,
  flightNumber: "4002",
  flightStatus: "CANCELLED",
  isCancelled: true,
  cancelReasonCode: null,
  origin: "BAH",
  destination: "DOH",
});

const operating = flight({
  flightSequenceNumber: 9005,
  flightNumber: "1001",
  flightStatus: "SCHEDULED",
  isCancelled: false,
  cancelReasonCode: null,
});

function renderToday(flights: OtpFlight[]) {
  fetchOtpFlights.mockResolvedValue(flights);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <TodayPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function statusCell(flightCode: string): HTMLElement {
  const ingest = screen.getByRole("button", { name: `Ingest flight ${flightCode}` });
  const row = ingest.parentElement?.parentElement;
  if (!row) {
    throw new Error(`missing row for ${flightCode}`);
  }
  const badge = within(row).getByText(flightCode === "GF3001" ? "CNX" : "CANCELLED");
  const cell = badge.parentElement;
  if (!cell) {
    throw new Error(`missing status cell for ${flightCode}`);
  }
  return cell;
}

function cancelledWarning(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Cancelled Flights Selected" });
  const dialog = heading.parentElement?.parentElement;
  if (!dialog) {
    throw new Error("missing cancelled-ingest warning");
  }
  return dialog;
}

async function openBatchWarning(flightCodes: string[]) {
  for (const code of flightCodes) {
    fireEvent.click(screen.getByRole("checkbox", { name: `Select flight ${code}` }));
  }
  const label = flightCodes.length === 1 ? "Ingest 1 flight" : `Ingest ${flightCodes.length} flights`;
  fireEvent.click(screen.getByRole("button", { name: label }));
  return cancelledWarning();
}

describe("Today's Flights cancel reason", () => {
  beforeEach(() => {
    fetchOtpFlights.mockReset();
  });

  it("prints the stored cancel reason next to the cancelled badge on a row that can still be ingested", async () => {
    renderToday([weatherCancelled, operating]);
    await screen.findByRole("button", { name: "Ingest flight GF2057" });

    const cell = statusCell("GF2057");
    expect(within(cell).getByText("CANCELLED")).toBeTruthy();
    expect(cell.textContent).toContain(WEATHER);
    expect(screen.getByRole("button", { name: "Ingest flight GF2057" }).hasAttribute("disabled")).toBe(false);
  });

  it("prints the stored cancel reason next to a CNX badge", async () => {
    renderToday([technicalCnx]);
    await screen.findByRole("button", { name: "Ingest flight GF3001" });

    const cell = statusCell("GF3001");
    expect(within(cell).getByText("CNX")).toBeTruthy();
    expect(cell.textContent).toContain(TECHNICAL);
  });

  it("does not invent a cancel reason when OTP omitted the code", async () => {
    renderToday([cancelledWithoutReason]);
    await screen.findByRole("button", { name: "Ingest flight GF4002" });

    expect(statusCell("GF4002").textContent?.replace(/\s+/g, " ").trim()).toBe("CANCELLED");
  });

  it("names only the selected flight's cancel reason in the cancelled-ingest warning", async () => {
    renderToday([weatherCancelled, commercialCancelled, operating]);
    await screen.findByRole("button", { name: "Ingest flight GF2057" });

    const dialog = await openBatchWarning(["GF2057"]);
    expect(dialog.textContent).toContain(WEATHER);
    expect(dialog.textContent).not.toContain(COMMERCIAL);
    expect(screen.getByRole("button", { name: "Ingest Anyway" })).toBeTruthy();
  });

  it("names every selected flight's cancel reason in the cancelled-ingest warning", async () => {
    renderToday([weatherCancelled, commercialCancelled]);
    await screen.findByRole("button", { name: "Ingest flight GF2057" });

    const dialog = await openBatchWarning(["GF2057", "GF188"]);
    expect(dialog.textContent).toContain(WEATHER);
    expect(dialog.textContent).toContain(COMMERCIAL);
  });
});
