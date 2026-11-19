/**
 * Live passenger detail must not paint every Sabre check-in status as a failure.
 *
 * The sheet loads GetPassengerData through fetchPassengerDetail. These tests
 * build that payload with SabreClient._parse_xml, SabreClient._extract_body,
 * and convert_passenger_data, then render PassengerDetailSheet.
 *
 * They fail while ValidationRequired, ValidationFailed, and WithDOCRequired
 * share one failure color, and pass once those three are distinct:
 * ValidationFailed is a hard stop, ValidationRequired is still pending,
 * and WithDOCRequired is a document block rather than that same hard stop.
 *
 * Both copies of the comparison are covered: the itinerary segment list and
 * the extras-tab summary. No other component reads detailStatus.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { PassengerDetailResponse } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  fetchPassengerDetail: vi.fn(),
}));

import { fetchPassengerDetail } from "@/lib/api";
import { PassengerDetailSheet } from "./passenger-detail-sheet";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const FIXTURE = path.join(
  ROOT,
  "backend/tests/fixtures/get_passenger_data_checkin_statuses.xml",
);
const VENDOR_SAMPLE = path.join(
  ROOT,
  "sabre_docs/GetPassengerDataRQ_v4-0-4_Samples/GetPassengerDataRQ_v4-0-0_SamplePayload_4_2_1.xml",
);

const HARD_STOP = /(?:^|\s)(?:text|border|bg)-red-|(?:^|\s)(?:text|border|bg)-destructive(?:\/|\s|$)/;

function convertPassengerData(source: "fixture" | "vendor"): PassengerDetailResponse {
  const script = `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend.sabre.client import SabreClient
from backend.feeder.converter import convert_passenger_data
text = Path(sys.argv[2]).read_text()
if sys.argv[3] == "vendor":
    start = text.find("<soap-env:Envelope")
    end = text.find("</soap-env:Envelope>") + len("</soap-env:Envelope>")
    text = text[start:end]
    airline, flight, date, origin = "EY", "655", "2017-01-11", "AUH"
else:
    airline, flight, date, origin = "GF", "2057", "2026-04-02", "BAH"
parsed = SabreClient._parse_xml(text)
body = SabreClient._extract_body(None, parsed, "GetPassengerDataRS")
json.dump(convert_passenger_data(body, airline, flight, date, origin), sys.stdout)
`;
  const raw = execFileSync(
    "python3",
    ["-c", script, ROOT, source === "vendor" ? VENDOR_SAMPLE : FIXTURE, source],
    { encoding: "utf8" },
  );
  return JSON.parse(raw) as PassengerDetailResponse;
}

function renderSheet(detail: PassengerDetailResponse, pnr: string) {
  vi.mocked(fetchPassengerDetail).mockReset();
  vi.mocked(fetchPassengerDetail).mockResolvedValue(detail);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber={detail.flightNumber}
        origin={detail.origin}
        date={detail.departureDate}
        pnr={pnr}
      />
    </QueryClientProvider>,
  );
  return view;
}

function statusNode(row: HTMLElement, status: string): HTMLElement {
  const node = within(row).getByText(status);
  expect(node.tagName).toBe("SPAN");
  return node;
}

function badgeNode(row: HTMLElement, code: string): HTMLElement {
  const node = within(row).getByText(code);
  expect(node.tagName).toBe("SPAN");
  return node;
}

function rowFor(scope: HTMLElement, freeText: string): HTMLElement {
  const text = within(scope).getByText(freeText);
  const row = text.parentElement;
  if (!row) throw new Error(`no row for ${freeText}`);
  return row;
}

function activePanel(): HTMLElement {
  const panel = screen.getByRole("tabpanel");
  return panel;
}

describe("live passenger detail check-in statuses", () => {
  let vendor: PassengerDetailResponse;
  let mixed: PassengerDetailResponse;

  afterEach(() => {
    cleanup();
  });

  beforeAll(() => {
    vendor = convertPassengerData("vendor");
    mixed = convertPassengerData("fixture");
  });

  it("keeps a missing passport and a missing Timatic result pending on the itinerary", async () => {
    renderSheet(vendor, "FRJZGW");
    await waitFor(() => {
      expect(screen.getAllByText("ValidationRequired").length).toBeGreaterThan(0);
    });
    expect(fetchPassengerDetail).toHaveBeenCalledWith(
      vendor.flightNumber,
      "FRJZGW",
      vendor.origin,
      vendor.departureDate,
    );
    const statuses = screen.getAllByText("ValidationRequired");
    expect(statuses.length).toBeGreaterThan(0);
    for (const status of statuses) {
      expect(status.className).not.toMatch(HARD_STOP);
    }
  });

  it("styles the three Sabre statuses differently on the itinerary segment", async () => {
    renderSheet(mixed, "ABC123");
    await screen.findByRole("tab", { name: "Itinerary", selected: true });
    const panel = activePanel();
    await within(panel).findByText("TIMATIC VALIDATION FAILED");

    const pending = rowFor(panel, "!DOCS REQUIRED");
    const failed = rowFor(panel, "TIMATIC VALIDATION FAILED");
    const documentBlock = rowFor(panel, "WITH DOCUMENT REQUIRED");

    const pendingStatus = statusNode(pending, "ValidationRequired");
    const failedStatus = statusNode(failed, "ValidationFailed");
    const documentStatus = statusNode(documentBlock, "WithDOCRequired");

    expect(pendingStatus.className).not.toMatch(HARD_STOP);
    expect(failedStatus.className).toMatch(HARD_STOP);
    expect(documentStatus.className).not.toMatch(HARD_STOP);
    expect(pendingStatus.className).not.toBe(failedStatus.className);
    expect(documentStatus.className).not.toBe(failedStatus.className);
    expect(documentStatus.className).not.toBe(pendingStatus.className);

    const pendingBadge = badgeNode(pending, "DOCS");
    const failedBadge = badgeNode(failed, "TIM");
    const documentBadge = badgeNode(documentBlock, "DOCV");
    expect(pendingBadge.className).not.toBe(failedBadge.className);
    expect(documentBadge.className).not.toBe(failedBadge.className);
    expect(documentBadge.className).not.toBe(pendingBadge.className);
  });

  it("styles the three Sabre statuses differently on the extras summary", async () => {
    const user = userEvent.setup();
    renderSheet(mixed, "ABC123");
    await screen.findByRole("tab", { name: "Extras" });
    await user.click(screen.getByRole("tab", { name: "Extras" }));
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Extras" }).getAttribute("aria-selected")).toBe("true");
    });
    const panel = activePanel();
    await within(panel).findByText("TIMATIC VALIDATION FAILED");

    const pending = rowFor(panel, "!DOCS REQUIRED");
    const failed = rowFor(panel, "TIMATIC VALIDATION FAILED");
    const documentBlock = rowFor(panel, "WITH DOCUMENT REQUIRED");

    const pendingStatus = statusNode(pending, "ValidationRequired");
    const failedStatus = statusNode(failed, "ValidationFailed");
    const documentStatus = statusNode(documentBlock, "WithDOCRequired");

    expect(pendingStatus.className).not.toMatch(HARD_STOP);
    expect(failedStatus.className).toMatch(HARD_STOP);
    expect(documentStatus.className).not.toMatch(HARD_STOP);
    expect(pendingStatus.className).not.toBe(failedStatus.className);
    expect(documentStatus.className).not.toBe(failedStatus.className);
    expect(documentStatus.className).not.toBe(pendingStatus.className);

    expect(badgeNode(pending, "DOCS").className).not.toBe(badgeNode(failed, "TIM").className);
    expect(badgeNode(documentBlock, "DOCV").className).not.toBe(badgeNode(failed, "TIM").className);
    expect(badgeNode(documentBlock, "DOCV").className).not.toBe(badgeNode(pending, "DOCS").className);
  });
});
