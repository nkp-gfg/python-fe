import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchOtpFlights } from "@/lib/api";
import type { OtpFlight } from "@/lib/types";

const diverted: OtpFlight = {
  flightSequenceNumber: 205,
  carrierCode: "GF",
  flightNumber: "205",
  origin: "BAH",
  destination: "LHR",
  actualOrigin: "AUH",
  actualDestination: "DXB",
  flightDate: "2026-03-30",
  flightStatus: "SCHEDULED",
  isCancelled: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchOtpFlights", () => {
  it("returns actual airports beside the scheduled pair from the OTP list", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(JSON.stringify([diverted]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });

    const flights = await fetchOtpFlights("2026-03-30");

    expect(calls).toEqual(["http://127.0.0.1:8000/otp/flights?date=2026-03-30"]);
    expect(flights).toHaveLength(1);
    expect(flights[0].origin).toBe("BAH");
    expect(flights[0].destination).toBe("LHR");
    expect(flights[0].actualOrigin).toBe("AUH");
    expect(flights[0].actualDestination).toBe("DXB");
  });
});
