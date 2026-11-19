/**
 * Spec for the Other Passengers drill-down.
 *
 * These tests fail while the panel lists non-revenue staff under a heading
 * that says the rows are jump seats, offloads, and no-shows, and while the
 * figure clicks open the checked-in, booked, or non-revenue manifest instead
 * of the trip-report names. They pass once each figure opens its own roster:
 * non-revenue lists non-revenue manifest rows; offloaded and no-show list the
 * trip-report names, or the view says those names are not on the current
 * manifest. The staff list must not be described as jump seats, offloads,
 * and no-shows.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/flights/2057",
  useSearchParams: () => new URLSearchParams("origin=BAH&date=2026-03-20"),
}));

import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { Providers } from "@/components/providers";

type Person = { lastName: string; firstName: string; pnr: string };

type Scenario = {
  flightNumber: string;
  origin: string;
  date: string;
  withTripReports: boolean;
  dashboard: Record<string, unknown>;
  passengers: { passengers: Person[] };
  offloadedNames: Person[];
  noShowNames: Person[];
  staffNames: Person[];
  checkedInNotBoardedNames: Person[];
  bookedNames: Person[];
  jumpSeat: number;
  nonRevenue: number;
  offloaded: number | null;
  noShow: number | null;
};

const REPO_ROOT = path.resolve(__dirname, "../..");
const ABSENT = /not on the current manifest/i;

// jsdom does not implement the browser observers the workbench layout uses.
class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = window.ResizeObserver ?? ResizeObserverPolyfill;
window.matchMedia = window.matchMedia ?? ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return false; },
}));

function loadScenario(which: "with-reports" | "without-reports"): Scenario {
  const raw = execFileSync(
    "python3",
    ["frontend/tests/build_others_scenario.py", which],
    { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 20 * 1024 * 1024 },
  );
  return JSON.parse(raw) as Scenario;
}

function displayName(person: Person): string {
  return `${person.lastName}, ${person.firstName}`;
}

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  };
}

function installFetch(scenario: Scenario) {
  const passengers = scenario.passengers;
  const dashboard = scenario.dashboard;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(raw, "http://127.0.0.1:8000");
    if (url.pathname.endsWith("/passengers")) {
      return jsonResponse(passengers);
    }
    if (url.pathname.endsWith("/dashboard")) {
      return jsonResponse(dashboard);
    }
    if (url.pathname.endsWith("/tree")) {
      return jsonResponse(dashboard.tree ?? {
        title: "", badge: "", width: 0, height: 0, nodes: [], edges: [], statusCards: [],
      });
    }
    if (url.pathname.endsWith("/audit")) {
      return jsonResponse({ passengerAlerts: {} });
    }
    if (url.pathname.endsWith("/timeline")) {
      return jsonResponse({ events: [], eventCount: 0, stats: {} });
    }
    if (url.pathname === "/flights") {
      return jsonResponse([]);
    }
    // Endpoints the overview mounts but this drill-down does not read.
    return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "not used by this test" }) };
  }));
}

function renderBoard(scenario: Scenario) {
  installFetch(scenario);
  return render(
    <Providers>
      <FlightWorkbench
        initialSelection={{
          flightNumber: scenario.flightNumber,
          origin: scenario.origin,
          date: scenario.date,
        }}
      />
    </Providers>,
  );
}

function othersCard(): HTMLElement {
  const label = screen.getByText("Other Passengers");
  const card = label.closest(".cursor-pointer");
  if (!card) throw new Error("Other Passengers card was not rendered");
  return card as HTMLElement;
}

function stateCard(index: number): HTMLElement {
  const card = othersCard();
  const grid = card.parentElement;
  const cards = [...(grid?.querySelectorAll(":scope > .cursor-pointer") ?? [])];
  const target = cards[index];
  if (!target) throw new Error(`state card ${index} was not rendered`);
  return target as HTMLElement;
}

async function clickFigure(label: string) {
  const card = othersCard();
  const rowLabel = within(card).getByText(label);
  const row = rowLabel.parentElement;
  const button = row?.querySelector("button");
  if (!button) throw new Error(`No drill-down control for ${label}`);
  await userEvent.click(button);
}

function tableText(): string {
  return [...document.querySelectorAll("tbody tr")].map((row) => row.textContent ?? "").join("\n");
}

async function openedDetailText(): Promise<string> {
  const table = await screen.findByRole("table", {}, { timeout: 4000 }).catch(() => null);
  if (table) return `${table.textContent ?? ""}\n${document.body.textContent ?? ""}`;
  const text = document.body.textContent ?? "";
  if (ABSENT.test(text)) return text;
  throw new Error("the click did not open a passenger roster or a manifest-gap notice");
}

function detailText(): string {
  const heading = screen.queryByRole("heading", { name: "Other Passengers" });
  const panel = heading?.closest(".rounded-lg");
  if (panel) return panel.textContent ?? "";
  return `${document.body.textContent ?? ""}\n${tableText()}`;
}

function namesListed(people: Person[], text: string): boolean {
  return people.every((person) => text.includes(displayName(person)));
}

function manifestRowsListed(people: Person[]): boolean {
  const rows = tableText();
  return people.some((person) => rows.includes(displayName(person)));
}

/**
 * Offloaded and no-show drill-downs must name the trip-report passengers, or
 * say those names are not on the current manifest. A manifest row that is not
 * one of those passengers must not be the roster underneath that figure.
 */
function assertTripReportRoster(
  kind: "offloaded" | "no-show",
  expected: Person[],
  wrongManifestRows: Person[],
) {
  const text = detailText();
  const listed = expected.length > 0 && namesListed(expected, text);
  const admitsMissing = ABSENT.test(text);
  if (!listed && manifestRowsListed(wrongManifestRows)) {
    throw new Error(
      `${kind} drill-down lists ${wrongManifestRows.map(displayName).join(", ")} instead of ${expected.map(displayName).join(", ")}`,
    );
  }
  if (!listed && !admitsMissing) {
    throw new Error(
      `${kind} drill-down does not list ${expected.map(displayName).join(", ")} and does not say those names are not on the current manifest`,
    );
  }
}

describe("Other Passengers drill-down", () => {
  let scenario: Scenario;
  let withoutReports: Scenario;

  beforeAll(() => {
    scenario = loadScenario("with-reports");
    withoutReports = loadScenario("without-reports");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("builds offload and no-show counts from trip-report names that are not the staff row", () => {
    expect(scenario.jumpSeat).toBe(2);
    expect(scenario.nonRevenue).toBe(1);
    expect(scenario.offloaded).toBe(1);
    expect(scenario.noShow).toBe(1);
    expect(scenario.offloadedNames.map(displayName)).toEqual(["REMOVED, DANA"]);
    expect(scenario.noShowNames.map(displayName)).toEqual(["ABSENT, ERIK"]);
    expect(scenario.staffNames.map(displayName)).toEqual(["STAFF, ALICE"]);
    expect(scenario.staffNames.map((person) => person.pnr)).not.toEqual(
      scenario.offloadedNames.map((person) => person.pnr),
    );
    const others = (scenario.dashboard.stateSummary as { others: { offloadedAvailable: boolean; noShowAvailable: boolean } }).others;
    expect(others.offloadedAvailable).toBe(true);
    expect(others.noShowAvailable).toBe(true);
  });

  it("opens a roster whose names and count are the offloads and no-shows, or says those names are not on the manifest", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await userEvent.click(screen.getByText("Other Passengers"));

    const heading = await screen.findByRole("heading", { name: "Other Passengers" });
    const panel = heading.closest(".rounded-lg");
    expect(panel).not.toBeNull();
    const text = panel?.textContent ?? "";
    const description = heading.parentElement?.querySelector("p")?.textContent ?? "";

    const showsStaff = namesListed(scenario.staffNames, text);
    const showsOffloaded = namesListed(scenario.offloadedNames, text);
    const showsNoShow = namesListed(scenario.noShowNames, text);
    const admitsMissing = ABSENT.test(text);
    const describesRemoved =
      /offloaded/i.test(description) && /no-show/i.test(description);

    expect(
      showsStaff && describesRemoved && !showsOffloaded && !showsNoShow && !admitsMissing,
      "staff rows are described as jump seats, offloads, and no-shows",
    ).toBe(false);

    if (!showsOffloaded || !showsNoShow) {
      expect(admitsMissing, "missing trip-report names need an explicit manifest gap").toBe(true);
      expect(describesRemoved, "the staff list is still described as offloads and no-shows").toBe(false);
      expect(text).toMatch(/non-revenue/i);
      expect(manifestRowsListed(scenario.checkedInNotBoardedNames)).toBe(false);
    } else {
      expect(manifestRowsListed(scenario.checkedInNotBoardedNames)).toBe(false);
      expect(manifestRowsListed([{ lastName: "PAYING", firstName: "CAROL", pnr: "REV003" }])).toBe(false);
    }

    if (showsStaff && !showsOffloaded) {
      const headerCount = text.match(/(\d+)\s+passengers/);
      expect(headerCount?.[1]).toBe(String(scenario.staffNames.length));
    }
  });

  it("does not describe the staff list as offloads and no-shows when trip reports were not loaded", async () => {
    renderBoard(withoutReports);
    await screen.findByText("Other Passengers");
    await userEvent.click(screen.getByText("Other Passengers"));
    const heading = await screen.findByRole("heading", { name: "Other Passengers" });
    const description = heading.parentElement?.querySelector("p")?.textContent ?? "";
    const text = heading.closest(".rounded-lg")?.textContent ?? "";
    expect(/offloaded/i.test(description) && /no-show/i.test(description)).toBe(false);
    expect(namesListed(withoutReports.staffNames, text)).toBe(true);
    expect(text).toMatch(/non-revenue/i);
  });

  it("lists the non-revenue manifest row for the non-revenue figure", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await clickFigure("Non-Revenue");
    await screen.findByText(displayName(scenario.staffNames[0]));
    const rows = tableText();
    expect(rows).toContain(displayName(scenario.staffNames[0]));
    expect(rows).not.toContain(displayName(scenario.checkedInNotBoardedNames[0]));
  });

  it("lists the offloaded trip-report name, or says that name is not on the current manifest", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await clickFigure("Offloaded");
    await openedDetailText();
    assertTripReportRoster(
      "offloaded",
      scenario.offloadedNames,
      scenario.checkedInNotBoardedNames,
    );
  });

  it("lists the no-show trip-report name, or says that name is not on the current manifest", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await clickFigure("No Show");
    await openedDetailText();
    assertTripReportRoster(
      "no-show",
      scenario.noShowNames,
      scenario.bookedNames,
    );
  });

  it("does not present the staff row as the jump-seat occupants", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await clickFigure("Jump Seat");
    const text = await openedDetailText();
    const showsStaff = manifestRowsListed(scenario.staffNames);
    const explainsGap = /jump seat/i.test(text) && ABSENT.test(text);
    expect(showsStaff && !explainsGap, "Jump Seat opens the staff row without saying those occupants are not on the current manifest").toBe(false);
    expect(showsStaff || explainsGap, "Jump Seat click did not open a roster").toBe(true);
  });

  it("does not present one staff row as the jump-seat plus non-revenue total", async () => {
    renderBoard(scenario);
    const card = await screen.findByText("Other Passengers").then(() => othersCard());
    const headline = scenario.jumpSeat + scenario.nonRevenue;
    const button = within(card).getByRole("button", { name: String(headline) });
    await userEvent.click(button);
    await openedDetailText();
    const rows = [...document.querySelectorAll("tbody tr")].filter((row) =>
      (row.textContent ?? "").includes(", "),
    );
    const text = detailText();
    const explainsGap = /jump seat/i.test(text) && ABSENT.test(text);
    if (rows.length !== headline && !explainsGap) {
      throw new Error(
        `headline ${headline} opened ${rows.length} manifest row(s) without saying jump-seat occupants are not on the current manifest`,
      );
    }
    expect(rows.length === headline || explainsGap).toBe(true);
  });

  it("keeps the booked drill-down to passengers who have not checked in", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await userEvent.click(stateCard(0));
    const heading = await screen.findByRole("heading", { name: "Booked Passengers" });
    const text = heading.closest(".rounded-lg")?.textContent ?? "";
    expect(text).toContain("PAYING, CAROL");
    expect(text).not.toContain("PAYING, BOB");
  });

  it("keeps the checked-in drill-down to passengers who have checked in and not boarded", async () => {
    renderBoard(scenario);
    await screen.findByText("Other Passengers");
    await userEvent.click(stateCard(1));
    const heading = await screen.findByRole("heading", { name: "Checked-In Passengers" });
    const text = heading.closest(".rounded-lg")?.textContent ?? "";
    expect(text).toContain("PAYING, BOB");
    expect(text).not.toContain("PAYING, CAROL");
  });
});
