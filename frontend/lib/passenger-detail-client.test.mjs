/**
 * The passenger sheet calls fetchPassengerDetail with flight number, record
 * locator, origin, and date only. That call must not turn into a Gulf Air
 * Sabre lookup.
 *
 * Fails while the client defaults a missing airline to GF. Passes once a
 * missing airline is left off the request. Supplying the stored airline must
 * still be forwarded.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { fetchPassengerDetail } from "./api.ts";

async function captureDetailUrl(airline) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ passengers: [] }),
    };
  };
  try {
    if (airline === undefined) {
      await fetchPassengerDetail("512", "ABC123", "BAH", "2026-04-01");
    } else {
      await fetchPassengerDetail("512", "ABC123", "BAH", "2026-04-01", airline);
    }
  } finally {
    globalThis.fetch = original;
  }
  return new URL(calls[0]);
}

test("passenger sheet detail call does not substitute Gulf Air", async () => {
  const url = await captureDetailUrl(undefined);
  assert.equal(
    url.searchParams.get("airline"),
    null,
    "omitting the carrier must not send airline=GF",
  );
  assert.equal(url.pathname, "/flights/512/passengers/ABC123/detail");
  assert.equal(url.searchParams.get("origin"), "BAH");
  assert.equal(url.searchParams.get("date"), "2026-04-01");
});

test("explicit stored airline is forwarded on the detail request", async () => {
  const url = await captureDetailUrl("WY");
  assert.equal(url.searchParams.get("airline"), "WY");
});
