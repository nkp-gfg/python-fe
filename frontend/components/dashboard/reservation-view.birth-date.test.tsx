import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchReservations } from "@/lib/api";
import type { ReservationsResponse } from "@/lib/types";
import { ReservationView } from "./reservation-view";

/**
 * Birth dates stored by Sabre are calendar dates (YYYY-MM-DD) with no time.
 * The reservations table must show that calendar day in every timezone.
 * These assertions fail while formatDate() passes the value through
 * `new Date()`, which reads a date-only string as midnight UTC.
 */

vi.mock("@/lib/api", () => ({
  fetchReservations: vi.fn(),
}));

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

interface PassengerExpectation {
  lastName: string;
  firstName: string;
  dateOfBirth: string;
}

interface ReservationExpectation {
  pnr: string;
  createdAt: string;
  updatedAt: string;
  passengers: PassengerExpectation[];
}

interface Fixture {
  response: ReservationsResponse;
  reservations: ReservationExpectation[];
}

function calendarDateLabel(value: string): string {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) ??
    /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}$/.exec(value);
  if (!match) {
    throw new Error(`expected a Sabre calendar date or local date-time, got ${value}`);
  }
  const year = match[1];
  const monthIndex = Number(match[2]) - 1;
  const day = match[3];
  if (monthIndex < 0 || monthIndex > 11) {
    throw new Error(`month out of range in ${value}`);
  }
  return `${day} ${MONTHS[monthIndex]} ${year}`;
}

function loadFixture(): Fixture {
  const fixturePath = process.env.FIXTURE_PATH;
  if (!fixturePath) {
    throw new Error("FIXTURE_PATH is required");
  }
  return JSON.parse(readFileSync(fixturePath, "utf8")) as Fixture;
}

function assertHarnessTimezone(): void {
  const zone = process.env.TZ;
  const offsetMinutes = new Date("1974-12-29T00:00:00.000Z").getTimezoneOffset();
  if (zone === "America/New_York") {
    if (offsetMinutes <= 0) {
      throw new Error(
        `America/New_York must be west of UTC for 1974-12-29, offset was ${offsetMinutes}`,
      );
    }
    return;
  }
  if (zone === "Asia/Bahrain") {
    if (offsetMinutes > 0) {
      throw new Error(
        `Asia/Bahrain must be east of UTC for 1974-12-29, offset was ${offsetMinutes}`,
      );
    }
    return;
  }
  throw new Error(
    `run under TZ=America/New_York or TZ=Asia/Bahrain, got ${zone ?? "(unset)"}`,
  );
}

describe("reservation birth dates", () => {
  const fixture = loadFixture();

  beforeEach(() => {
    assertHarnessTimezone();
    vi.mocked(fetchReservations).mockReset();
    vi.mocked(fetchReservations).mockResolvedValue(fixture.response);
  });

  it("shows each Sabre calendar birth date, not the previous local day", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ReservationView flightNumber="2057" origin="BOM" date="2026-03-18" />
      </QueryClientProvider>,
    );

    expect(fixture.reservations.length).toBeGreaterThan(0);

    for (const reservation of fixture.reservations) {
      const button = await screen.findByRole("button", {
        name: new RegExp(`\\b${reservation.pnr}\\b`),
      });
      fireEvent.click(button);
      const card = button.closest("[data-slot=card]");
      expect(card).not.toBeNull();
      expect(card?.textContent).toContain(
        `Created: ${calendarDateLabel(reservation.createdAt)}`,
      );
      expect(card?.textContent).toContain(
        `Updated: ${calendarDateLabel(reservation.updatedAt)}`,
      );

      for (const passenger of reservation.passengers) {
        const label = `${passenger.lastName}, ${passenger.firstName}`;
        const nameNodes = screen.getAllByText(label);
        const dobCell = nameNodes
          .map((node) => node.closest("td"))
          .find((cell): cell is HTMLTableCellElement => cell !== null);
        expect(dobCell, label).toBeTruthy();
        const row = dobCell?.closest("tr");
        const cells = row?.querySelectorAll("td");
        const displayed = cells?.[3]?.textContent ?? "";
        const expected = passenger.dateOfBirth
          ? calendarDateLabel(passenger.dateOfBirth)
          : "—";
        expect(displayed, `${label} date of birth`).toBe(expected);
      }
    }
  });
});
