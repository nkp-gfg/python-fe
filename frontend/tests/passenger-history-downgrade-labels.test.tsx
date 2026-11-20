/**
 * History-tab badges for passenger cabin and class moves.
 *
 * PassengerDetailSheet renders PassengerTimeline on the History tab
 * (frontend/components/dashboard/passenger-detail-sheet.tsx). These cases
 * render that timeline against the payload shape get_passenger_timeline
 * returns. A DOWNGRADE direction must win over the shared "upgrade"
 * category: the badge reads Downgrade and the arrow points down. A cabin
 * that is lower than the original booking must use the down arrow on the
 * current-status card as well.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerTimeline } from "../components/dashboard/passenger-timeline";
import type { PassengerTimelineResponse } from "../lib/types";

declare global {
  // Set by tests/run-passenger-history-downgrade-labels.mjs before import.
  // eslint-disable-next-line no-var
  var __setTimelinePayload: (body: PassengerTimelineResponse) => void;
}

function timeline(partial: PassengerTimelineResponse): PassengerTimelineResponse {
  return partial;
}

function baseState(cabin: string, bookingClass: string) {
  return {
    cabin,
    bookingClass,
    seat: "12A",
    isCheckedIn: false,
    isBoarded: false,
    bagCount: 0,
  };
}

const cabinDowngrade = timeline({
  flightNumber: "2057",
  pnr: "DWNCAB",
  departureDate: "2026-03-19",
  origin: "BAH",
  originalBooking: { cabin: "J", bookingClass: "D" },
  currentState: baseState("Y", "V"),
  eventCount: 1,
  events: [
    {
      timestamp: "2026-03-19T10:00:00Z",
      changeType: "CABIN_CHANGE",
      category: "upgrade",
      description: "Cabin downgrade: J → Y",
      details: { field: "cabin", oldValue: "J", newValue: "Y" },
      upgradeInfo: { direction: "DOWNGRADE" },
    },
  ],
});

const classDowngrade = timeline({
  flightNumber: "2057",
  pnr: "DWNCLS",
  departureDate: "2026-03-19",
  origin: "BAH",
  originalBooking: { cabin: "J", bookingClass: "F" },
  currentState: baseState("J", "D"),
  eventCount: 1,
  events: [
    {
      timestamp: "2026-03-19T10:00:00Z",
      changeType: "CLASS_CHANGE",
      category: "upgrade",
      description: "Booking class changed: F → D",
      details: { field: "bookingClass", oldValue: "F", newValue: "D" },
      upgradeInfo: { direction: "DOWNGRADE" },
    },
  ],
});

const downgradeCategory = timeline({
  flightNumber: "2057",
  pnr: "DWNCAT",
  departureDate: "2026-03-19",
  origin: "BAH",
  originalBooking: { cabin: "J", bookingClass: "D" },
  currentState: baseState("Y", "V"),
  eventCount: 1,
  events: [
    {
      timestamp: "2026-03-19T10:00:00Z",
      changeType: "CABIN_CHANGE",
      category: "downgrade" as "upgrade",
      description: "Cabin downgrade: J → Y",
      details: { field: "cabin", oldValue: "J", newValue: "Y" },
      upgradeInfo: { direction: "DOWNGRADE" },
    },
  ],
});

const classUpgrade = timeline({
  flightNumber: "2057",
  pnr: "UPGCLS",
  departureDate: "2026-03-19",
  origin: "BAH",
  originalBooking: { cabin: "Y", bookingClass: "V" },
  currentState: baseState("Y", "J"),
  eventCount: 1,
  events: [
    {
      timestamp: "2026-03-19T10:00:00Z",
      changeType: "CLASS_CHANGE",
      category: "upgrade",
      description: "Booking class changed: V → J",
      details: { field: "bookingClass", oldValue: "V", newValue: "J" },
      upgradeInfo: { direction: "UPGRADE" },
    },
  ],
});

const cabinUpgrade = timeline({
  flightNumber: "2057",
  pnr: "UPGCAB",
  departureDate: "2026-03-19",
  origin: "BAH",
  originalBooking: { cabin: "Y", bookingClass: "V" },
  currentState: baseState("J", "D"),
  eventCount: 1,
  events: [
    {
      timestamp: "2026-03-19T10:00:00Z",
      changeType: "CABIN_CHANGE",
      category: "upgrade",
      description: "Cabin upgrade: Y → J",
      details: { field: "cabin", oldValue: "Y", newValue: "J" },
      upgradeInfo: { direction: "UPGRADE" },
    },
  ],
});

function badgeTexts(): string[] {
  return Array.from(document.querySelectorAll("[data-slot=badge]")).map((node) =>
    (node.textContent || "").replace(/\s+/g, " ").trim(),
  );
}

function iconCount(name: "arrow-up" | "arrow-down"): number {
  return document.querySelectorAll(`svg.lucide-${name}`).length;
}

async function shown(payload: PassengerTimelineResponse): Promise<{ host: HTMLDivElement; root: Root }> {
  globalThis.__setTimelinePayload(payload);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <PassengerTimeline
          flightNumber={payload.flightNumber}
          pnr={payload.pnr}
          origin={payload.origin || ""}
          date={payload.departureDate || ""}
        />
      </QueryClientProvider>,
    );
  });
  const started = Date.now();
  let last = "";
  while (Date.now() - started < 5000) {
    last = host.textContent || "";
    if (last.includes("Failed to load timeline")) {
      throw new Error(`timeline request failed: ${last}`);
    }
    if (last && !last.includes("Loading timeline")) {
      return { host, root };
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
  throw new Error(`timeline stayed on the loading state: ${last}`);
}

async function renderCase(payload: PassengerTimelineResponse) {
  const { host, root } = await shown(payload);
  return {
    badges: badgeTexts(),
    up: iconCount("arrow-up"),
    down: iconCount("arrow-down"),
    text: host.textContent || "",
    async done() {
      await act(async () => {
        root.unmount();
      });
      host.remove();
    },
  };
}

function assert(condition: unknown, message: string) {
  if (!condition) {
    throw new Error(message);
  }
}

function collect(checks: Array<[unknown, string]>) {
  const failures = checks.filter(([condition]) => !condition).map(([, message]) => message);
  if (failures.length > 0) {
    throw new Error(failures.join("\n"));
  }
}

async function testCabinDowngradeBadgeAndCurrentStatus() {
  const view = await renderCase(cabinDowngrade);
  try {
    collect([
      [
        view.badges.includes("Downgrade"),
        `cabin downgrade badge should read Downgrade, badges=${JSON.stringify(view.badges)}`,
      ],
      [
        !view.badges.includes("Upgrade"),
        `cabin downgrade must not keep an Upgrade badge, badges=${JSON.stringify(view.badges)}`,
      ],
      [view.down >= 1, `cabin downgrade should draw a down arrow, down=${view.down}`],
      [
        view.up === 0,
        `cabin downgrade current status and event should not draw an up arrow, up=${view.up}`,
      ],
    ]);
  } finally {
    await view.done();
  }
  console.log("  PASS: testCabinDowngradeBadgeAndCurrentStatus");
}

async function testClassDowngradeBadgePointsDown() {
  const view = await renderCase(classDowngrade);
  try {
    collect([
      [
        view.badges.includes("Downgrade"),
        `class decrease badge should read Downgrade, badges=${JSON.stringify(view.badges)}`,
      ],
      [
        !view.badges.includes("Upgrade"),
        `class decrease must not keep an Upgrade badge, badges=${JSON.stringify(view.badges)}`,
      ],
      [view.down >= 1, `class decrease should draw a down arrow, down=${view.down}`],
      [view.up === 0, `class decrease should not draw an up arrow, up=${view.up}`],
    ]);
  } finally {
    await view.done();
  }
  console.log("  PASS: testClassDowngradeBadgePointsDown");
}

async function testDowngradeCategoryIsLabeled() {
  const view = await renderCase(downgradeCategory);
  try {
    assert(
      view.badges.includes("Downgrade"),
      `category downgrade should read Downgrade, badges=${JSON.stringify(view.badges)} text=${view.text}`,
    );
    assert(view.down >= 1, `category downgrade should draw a down arrow, down=${view.down}`);
    assert(view.up === 0, `category downgrade should not draw an up arrow, up=${view.up}`);
  } finally {
    await view.done();
  }
  console.log("  PASS: testDowngradeCategoryIsLabeled");
}

async function testUpwardMovesStayUpgrades() {
  for (const payload of [classUpgrade, cabinUpgrade]) {
    const view = await renderCase(payload);
    try {
      assert(
        view.badges.includes("Upgrade"),
        `upward ${payload.pnr} should read Upgrade, badges=${JSON.stringify(view.badges)}`,
      );
      assert(
        !view.badges.includes("Downgrade"),
        `upward ${payload.pnr} must not read Downgrade, badges=${JSON.stringify(view.badges)}`,
      );
      assert(view.up >= 1, `upward ${payload.pnr} should draw an up arrow, up=${view.up}`);
      assert(view.down === 0, `upward ${payload.pnr} should not draw a down arrow, down=${view.down}`);
    } finally {
      await view.done();
    }
  }
  console.log("  PASS: testUpwardMovesStayUpgrades");
}

async function main() {
  const failures: string[] = [];
  for (const run of [
    testUpwardMovesStayUpgrades,
    testCabinDowngradeBadgeAndCurrentStatus,
    testClassDowngradeBadgePointsDown,
    testDowngradeCategoryIsLabeled,
  ]) {
    try {
      await run();
    } catch (error) {
      failures.push(error instanceof Error ? error.message : String(error));
    }
  }
  if (failures.length > 0) {
    throw new Error(failures.join("\n\n"));
  }
  console.log("ALL TESTS PASS");
}

export default main;
