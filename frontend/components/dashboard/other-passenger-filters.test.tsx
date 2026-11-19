/**
 * Other Passengers figures must open the people they count.
 *
 * The live board is the real FlightWorkbench. Counts and manifests come from
 * `_build_dashboard_payload` (see backend/tests/other_passenger_board.py).
 * Clicking a figure runs StatePanels → handlePanelFilter → PassengerTable.
 *
 * These tests fail while Offloaded, No Show, and Jump Seat open a different
 * manifest filter than the population the figure displays.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/components/providers";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";

interface ExpectedPopulations {
  cancelled: string[];
  noShows: string[];
  checkedInNotBoarded: string[];
  notCheckedIn: string[];
  nonRevenue: string[];
  manifest: string[];
  jumpSeat: number;
  offloadedCount: number | null;
  noShowCount: number | null;
  offloadedAvailable: boolean;
  noShowAvailable: boolean;
  flightClosed: boolean;
}

interface Board {
  flight: { flightNumber: string; origin: string; departureDate: string };
  dashboard: { tree: unknown };
  passengers: { passengers: unknown[] };
  expected: ExpectedPopulations;
}

const { scenarioBox } = vi.hoisted(() => ({
  scenarioBox: { current: null as Board | null },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", () => ({
  fetchFlights: vi.fn(async () => [scenarioBox.current!.flight]),
  fetchDashboard: vi.fn(async () => scenarioBox.current!.dashboard),
  fetchFlightTree: vi.fn(async () => scenarioBox.current!.dashboard.tree),
  fetchPassengers: vi.fn(async () => scenarioBox.current!.passengers),
  fetchAudit: vi.fn(async () => ({ passengerAlerts: {} })),
  ingestFlight: vi.fn(async () => ({})),
}));

const boards = new Map<string, Board>();

function loadBoard(name: string): Board {
  const cached = boards.get(name);
  if (cached) return cached;
  const repo = path.resolve(__dirname, "../..", "..");
  const raw = execFileSync(
    "python3",
    [path.join(repo, "backend/tests/other_passenger_board.py"), name],
    { encoding: "utf8", cwd: repo },
  );
  const board = JSON.parse(raw) as Board;
  boards.set(name, board);
  return board;
}

function visibleTravelerNames(): string[] {
  const names = new Set<string>();
  for (const el of document.body.querySelectorAll("span, td, li, div, p")) {
    const text = [...el.childNodes]
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent ?? "")
      .join("")
      .replace(/\s+/g, " ")
      .trim();
    if (/^[A-Z]{3,}, [A-Z]{2,}$/.test(text)) names.add(text);
  }
  return [...names];
}

function otherCard(): HTMLElement {
  const title = screen.getByText("Other Passengers");
  const card = title.closest("[data-slot='card']");
  if (!card) throw new Error("Other Passengers card was not rendered");
  return card as HTMLElement;
}

function figureButton(label: string): HTMLButtonElement {
  const labelNode = within(otherCard()).getByText(label);
  const row = labelNode.parentElement;
  if (!row) throw new Error(`No row for ${label}`);
  return within(row).getByRole("button");
}

function headlineButton(): HTMLButtonElement {
  const button = within(otherCard())
    .getAllByRole("button")
    .find(
      (el) =>
        el.getAttribute("title") === "Click to filter passengers" &&
        el.className.includes("text-xl"),
    );
  if (!button) throw new Error("Other Passengers headline is not clickable");
  return button as HTMLButtonElement;
}

async function renderBoard(name: string): Promise<Board> {
  const board = loadBoard(name);
  scenarioBox.current = board;
  render(
    <Providers>
      <FlightWorkbench
        initialSelection={{
          flightNumber: board.flight.flightNumber,
          origin: board.flight.origin,
          date: board.flight.departureDate,
        }}
      />
    </Providers>,
  );
  await screen.findByText("Other Passengers", undefined, { timeout: 15_000 });
  expect(visibleTravelerNames()).toEqual([]);
  return board;
}

async function openedNames(): Promise<string[]> {
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline) {
    const settled =
      screen.queryByText(/\d+ of \d+ passengers/) ||
      document.querySelector("[data-testid='opened-passenger-population']");
    if (settled) {
      await new Promise((resolve) => setTimeout(resolve, 40));
      return visibleTravelerNames();
    }
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  return visibleTravelerNames();
}

function expectSamePeople(actual: string[], expected: string[]) {
  expect([...actual].sort()).toEqual([...expected].sort());
}

afterEach(() => {
  cleanup();
});

describe("authoritative trip-report figures", () => {
  it("opens cancelled passengers when Offloaded is clicked, not travelers still expected to fly", async () => {
    const board = await renderBoard("authoritative");
    const button = figureButton("Offloaded");
    expect(button.textContent).toBe(String(board.expected.offloadedCount));
    expect(button.parentElement?.textContent ?? "").not.toContain("~");
    button.click();
    expectSamePeople(await openedNames(), board.expected.cancelled);
  });

  it("opens ever-booked names missing from the manifest when No Show is clicked", async () => {
    const board = await renderBoard("authoritative");
    const button = figureButton("No Show");
    expect(button.textContent).toBe(String(board.expected.noShowCount));
    expect(button.parentElement?.textContent ?? "").not.toContain("~");
    button.click();
    expectSamePeople(await openedNames(), board.expected.noShows);
  });

  it("does not present non-revenue manifest passengers as the jump-seat population when the count is zero", async () => {
    const board = await renderBoard("authoritative");
    expect(board.expected.jumpSeat).toBe(0);
    const button = figureButton("Jump Seat");
    expect(button.textContent).toBe("0");
    button.click();
    const names = await openedNames();
    expect(names).not.toEqual(board.expected.nonRevenue);
    if (names.length > 0) {
      expectSamePeople(names, board.expected.manifest);
    }
  });

  it("does not open the non-revenue manifest from the card headline", async () => {
    const board = await renderBoard("authoritative");
    headlineButton().click();
    const names = await openedNames();
    expect(names).not.toEqual(board.expected.nonRevenue);
    if (names.length > 0) {
      expectSamePeople(names, board.expected.manifest);
    }
  });

  it("still opens only non-revenue passengers from the Non-Revenue row", async () => {
    const board = await renderBoard("authoritative");
    figureButton("Non-Revenue").click();
    expectSamePeople(await openedNames(), board.expected.nonRevenue);
  });
});

describe("jump seats occupied", () => {
  it("keeps jump-seat occupants off the non-revenue manifest filter", async () => {
    const board = await renderBoard("jump-seats");
    expect(board.expected.jumpSeat).toBe(3);
    figureButton("Jump Seat").click();
    const names = await openedNames();
    expect(names).not.toEqual(board.expected.nonRevenue);
    if (names.length > 0) {
      expectSamePeople(names, board.expected.manifest);
    }
  });

  it("does not treat the headline total as the non-revenue manifest", async () => {
    const board = await renderBoard("jump-seats");
    const headline = headlineButton();
    expect(headline.textContent).toBe(String(board.expected.jumpSeat + board.expected.nonRevenue.length));
    headline.click();
    const names = await openedNames();
    expect(names).not.toEqual(board.expected.nonRevenue);
    if (names.length > 0) {
      expectSamePeople(names, board.expected.manifest);
    }
  });
});

describe("authoritative counts of zero", () => {
  it("does not list checked-in travelers when nobody was offloaded", async () => {
    const board = await renderBoard("empty-exceptions");
    const button = figureButton("Offloaded");
    expect(button.textContent).toBe("0");
    expect(button.parentElement?.textContent ?? "").not.toContain("~");
    button.click();
    expectSamePeople(await openedNames(), []);
  });

  it("does not list booked travelers when the trip-report no-show count is zero", async () => {
    const board = await renderBoard("empty-exceptions");
    const button = figureButton("No Show");
    expect(button.textContent).toBe("0");
    button.click();
    expectSamePeople(await openedNames(), []);
  });
});

describe("inferred manifest figures when the trip report is missing", () => {
  it("labels Offloaded as inferred and opens the checked-in, not boarded passengers", async () => {
    const board = await renderBoard("inferred");
    const button = figureButton("Offloaded");
    expect(button.parentElement?.textContent ?? "").toContain("~");
    expect(button.textContent).toBe(String(board.expected.checkedInNotBoarded.length));
    button.click();
    expectSamePeople(await openedNames(), board.expected.checkedInNotBoarded);
  });

  it("labels No Show as inferred and opens passengers still on the manifest who have not checked in", async () => {
    const board = await renderBoard("inferred");
    const button = figureButton("No Show");
    expect(button.parentElement?.textContent ?? "").toContain("~");
    expect(button.textContent).toBe(String(board.expected.notCheckedIn.length));
    button.click();
    expectSamePeople(await openedNames(), board.expected.notCheckedIn);
  });
});

describe("offloaded while check-in is still open", () => {
  it("opens the cancelled list from Offloaded", async () => {
    const board = await renderBoard("open-checkin");
    expect(board.expected.flightClosed).toBe(false);
    expect(board.expected.noShowAvailable).toBe(false);
    const offloaded = figureButton("Offloaded");
    expect(offloaded.textContent).toBe(String(board.expected.offloadedCount));
    offloaded.click();
    expectSamePeople(await openedNames(), board.expected.cancelled);
  });

  it("labels Not Checked In as inferred and opens passengers still on the manifest", async () => {
    const board = await renderBoard("open-checkin");
    const notCheckedIn = figureButton("Not Checked In");
    expect(notCheckedIn.parentElement?.textContent ?? "").toContain("~");
    notCheckedIn.click();
    expectSamePeople(await openedNames(), board.expected.notCheckedIn);
  });
});
