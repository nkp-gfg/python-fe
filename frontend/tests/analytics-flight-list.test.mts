/**
 * The network analytics sidebar must list stored departures for the open range.
 *
 * These tests fail while the page passes fetchFlights straight to the data
 * client. That client calls the loader with its query context, the loader
 * writes the context into the date query, and the departure route refuses it.
 * They pass once the page calls the loader with no arguments and the loader
 * ignores any date that is not a YYYY-MM-DD string.
 *
 * Run from the repository root:
 *   node --experimental-strip-types --test frontend/tests/analytics-flight-list.test.mts
 */

import assert from "node:assert/strict";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "node:test";
import { format } from "date-fns";
import { QueryClient } from "@tanstack/query-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const departureDate = format(new Date(), "yyyy-MM-dd");
const flightLabel = "GF2057";
const calendarDate = /^\d{4}-\d{2}-\d{2}$/;

type RecordedRequest = {
  path: string;
  date: string | null;
  rawQuery: string;
  status: number;
};

let apiBase = "";
let pythonPath = "";
let apiProcess: ChildProcess | undefined;

function pythonEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PYTHONPATH: `${pythonPath}${path.delimiter}${repoRoot}`,
    FIXTURE_DEPARTURE_DATE: departureDate,
    ...extra,
  };
}

function ensurePythonDeps(): string {
  const target = path.join(tmpdir(), "falconeye-flight-list-test-py");
  const probe = spawnSync(
    "python3",
    ["-c", "import fastapi, uvicorn, pymongo, structlog"],
    { env: { ...process.env, PYTHONPATH: `${target}${path.delimiter}${repoRoot}` } },
  );
  if (probe.status !== 0) {
    const install = spawnSync(
      "pip3",
      [
        "install",
        "--target",
        target,
        "fastapi>=0.110,<1.0",
        "uvicorn>=0.29,<1.0",
        "pymongo>=4.6,<5",
        "structlog>=24.1,<26",
        "pydantic>=2,<3",
        "httpx",
      ],
      { stdio: "inherit" },
    );
    if (install.status !== 0) {
      throw new Error("Could not install the FastAPI packages these tests import");
    }
  }
  return target;
}

async function waitFor(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      last = `${response.status}`;
    } catch (error) {
      last = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for ${url} (${last})`);
}

async function resetRequests(): Promise<void> {
  const response = await fetch(`${apiBase}/__reset`, { method: "POST" });
  assert.equal(response.status, 200);
}

async function recordedRequests(): Promise<RecordedRequest[]> {
  const response = await fetch(`${apiBase}/__requests`);
  assert.equal(response.status, 200);
  return response.json() as Promise<RecordedRequest[]>;
}

function assertAcceptedFlightList(requests: RecordedRequest[]): void {
  const accepted = requests.filter((entry) => entry.path === "/flights" && entry.status === 200);
  assert.ok(
    accepted.length > 0,
    `expected a flight list response, got ${JSON.stringify(requests)}`,
  );
  for (const entry of accepted) {
    assert.ok(
      entry.date == null || calendarDate.test(entry.date),
      `flight list date must be a calendar day or absent, got ${JSON.stringify(entry)}`,
    );
    assert.equal(entry.rawQuery.includes(" "), false, entry.rawQuery);
    assert.equal(entry.rawQuery.includes("object"), false, entry.rawQuery);
  }
}

async function loadFetchFlights() {
  process.env.NEXT_PUBLIC_API_URL = apiBase;
  return import("../lib/api.ts");
}

function readPageText(webSocketUrl: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(webSocketUrl);
    const timer = setTimeout(() => {
      socket.close();
      reject(new Error("timed out reading the analytics page"));
    }, 5_000);
    socket.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error("devtools connection failed"));
    });
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({
        id: 1,
        method: "Runtime.evaluate",
        params: {
          expression: "document.body ? document.body.innerText : ''",
          returnByValue: true,
        },
      }));
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data)) as {
        id?: number;
        result?: { result?: { value?: string } };
      };
      if (message.id !== 1) return;
      clearTimeout(timer);
      socket.close();
      resolve(message.result?.result?.value ?? "");
    });
  });
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

describe("analytics flight list", { concurrency: 1 }, () => {
test.before(async () => {
  pythonPath = ensurePythonDeps();
  const port = await freePort();
  apiBase = `http://127.0.0.1:${port}`;
  apiProcess = spawn(
    "python3",
    [path.join(here, "flight_list_date_api.py")],
    {
      env: pythonEnv({ FLIGHT_LIST_PORT: String(port) }),
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let apiLog = "";
  apiProcess.stderr?.on("data", (chunk) => {
    apiLog += chunk;
  });
  apiProcess.stdout?.on("data", (chunk) => {
    apiLog += chunk;
  });
  try {
    await waitFor(`${apiBase}/__requests`, 20_000);
  } catch (error) {
    apiProcess.kill();
    throw new Error(`${error instanceof Error ? error.message : error}\n${apiLog}`);
  }
});

test.after(() => {
  apiProcess?.kill();
});

test("real departure route rejects the query-context date before opening the database", () => {
  const result = spawnSync(
    "python3",
    [path.join(repoRoot, "backend/tests/test_flight_list_query_context_date.py")],
    { env: pythonEnv(), encoding: "utf8" },
  );
  assert.equal(
    result.status,
    0,
    `${result.stdout}\n${result.stderr}`,
  );
});

test("a calendar day is still sent as the departure date", async () => {
  await resetRequests();
  const { fetchFlights } = await loadFetchFlights();
  const flights = await fetchFlights("2026-11-24");
  assert.equal(flights[0]?.flightNumber, "2057");
  const requests = await recordedRequests();
  assert.deepEqual(
    requests.map((entry) => entry.date),
    ["2026-11-24"],
  );
});

test("dates that are not calendar days are left out of the flight list URL", async () => {
  await resetRequests();
  const { fetchFlights } = await loadFetchFlights();
  const rejected = ["not-a-date", "2026 11 24", "[object Object]"];
  for (const date of rejected) {
    const flights = await fetchFlights(date);
    assert.equal(flights[0]?.flightNumber, "2057", date);
  }
  const requests = await recordedRequests();
  assert.equal(requests.length, rejected.length);
  for (const entry of requests) {
    assert.equal(entry.date, null, JSON.stringify(entry));
    assert.equal(entry.status, 200);
    assert.equal(entry.rawQuery, "");
  }
});

test("the flight-list loader used as the data client's query function returns stored departures", async () => {
  await resetRequests();
  const { fetchFlights } = await loadFetchFlights();
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: 2,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10_000),
      },
    },
  });
  let flights;
  try {
    flights = await client.fetchQuery({
      queryKey: ["flights"],
      queryFn: fetchFlights,
    });
  } catch (error) {
    const requests = await recordedRequests();
    assert.fail(`${error instanceof Error ? error.message : error}\n${JSON.stringify(requests)}`);
  }
  assert.equal(flights[0]?.flightNumber, "2057");
  assertAcceptedFlightList(await recordedRequests());
});

test("the analytics page lists a stored departure in the open date range", { timeout: 180_000 }, async () => {
  await resetRequests();
  const nextPort = String(3400 + Math.floor(Math.random() * 400));
  const nextUrl = `http://127.0.0.1:${nextPort}/analytics`;
  let nextLog = "";
  const nextProcess = spawn(
    path.join(frontendRoot, "node_modules/next/dist/bin/next"),
    ["dev", "--hostname", "127.0.0.1", "--port", nextPort],
    {
      cwd: frontendRoot,
      detached: true,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_URL: apiBase,
        CI: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  nextProcess.stdout?.on("data", (chunk) => {
    nextLog += chunk;
  });
  nextProcess.stderr?.on("data", (chunk) => {
    nextLog += chunk;
  });

  const userData = mkdtempSync(path.join(tmpdir(), "analytics-chrome-"));
  let chrome: ChildProcess | undefined;
  try {
    await waitFor(nextUrl, 120_000);
    const debugPort = await freePort();
    chrome = spawn(
      "google-chrome",
      [
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        `--remote-debugging-port=${debugPort}`,
        `--user-data-dir=${userData}`,
        "about:blank",
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    await waitFor(`http://127.0.0.1:${debugPort}/json/version`, 20_000);
    const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(nextUrl)}`, {
      method: "PUT",
    });
    const targetBody = await target.text();
    assert.equal(target.ok, true, targetBody);
    const page = JSON.parse(targetBody) as { webSocketDebuggerUrl: string };
    let text = "";
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline) {
      text = await readPageText(page.webSocketDebuggerUrl);
      if (text.includes(flightLabel)) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    const requests = await recordedRequests();
    assert.ok(
      text.includes("Network Analytics"),
      `analytics page did not render\n${text.slice(0, 500)}\n${nextLog.slice(-2000)}`,
    );
    assert.ok(
      text.includes(flightLabel),
      `sidebar did not list ${flightLabel}. Requests: ${JSON.stringify(requests)}\n${text.includes("No flights in range") ? "page says No flights in range" : `visible text: ${text.slice(0, 500)}`}`,
    );
    assert.equal(text.includes("No flights in range"), false);
    assertAcceptedFlightList(requests.filter((entry) => entry.path === "/flights"));
  } finally {
    chrome?.kill();
    if (nextProcess.pid) {
      try {
        process.kill(-nextProcess.pid, "SIGTERM");
      } catch {
        nextProcess.kill();
      }
    }
  }
});
});
