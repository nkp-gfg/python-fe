/**
 * Render the flight screens with the aircraft value produced by
 * convert_flight_status for an ACS Flight Detail response.
 *
 * The screens must show the type code as text. Rendering the parsed
 * AircraftType object is a failure: React 19 refuses object children.
 */
import { readFileSync } from "node:fs";
import { createElement, type ReactNode } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";

import { FlightHeader } from "@/components/dashboard/flight-header";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { StatusHistory } from "@/components/dashboard/status-history";
import ComparePage from "@/app/compare/page";
import { ToastProvider } from "@/components/ui/toast";
import { setFlightSearch } from "next/navigation";

type Payload = {
  typeCode: string;
  flightNumber: string;
  origin: string;
  date: string;
  gate: string;
  status: string;
  flightStatus: Record<string, unknown>;
  dashboard: Record<string, unknown>;
  historyRecord: Record<string, unknown>;
};

function readPayload(): Payload {
  const path = process.argv[2];
  if (!path) {
    throw new Error("usage: aircraft-change-screens.tsx <payload.json>");
  }
  return JSON.parse(readFileSync(path, "utf8")) as Payload;
}

function clientFor(seed: (queryClient: QueryClient) => void) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
    },
  });
  seed(queryClient);
  return queryClient;
}

function renderScreen(name: string, node: ReactNode) {
  try {
    return renderToString(node);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${name} failed to render: ${message}`);
  }
}

function assertShowsType(name: string, html: string, payload: Payload) {
  if (html.includes("Objects are not valid as a React child") || html.includes("server rendering errored")) {
    throw new Error(
      `${name} failed to render: Objects are not valid as a React child (found: object with keys {@change, #text})`,
    );
  }
  if (!html.includes(payload.typeCode)) {
    throw new Error(
      `${name} did not render aircraft type ${payload.typeCode}`,
    );
  }
  if (html.includes("[object Object]") || html.includes("#text") || html.includes("@change")) {
    throw new Error(`${name} rendered the AircraftType object instead of the type code`);
  }
  if (html.includes("Something went wrong")) {
    throw new Error(`${name} hit an error boundary instead of showing the flight`);
  }
}

function shell(queryClient: QueryClient, child: ReactNode) {
  return createElement(
    ThemeProvider,
    { attribute: "class", defaultTheme: "light", enableSystem: false },
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(ToastProvider, null, child),
    ),
  );
}

function compareAnalysis() {
  const passengers = { male: 0, female: 0, children: 0, infants: 0 };
  return {
    economy: { total: 0, passengers, staff: { total: 0, male: 0, female: 0 } },
    business: { total: 0, passengers },
    revenue: 0,
    totalMale: 0,
    totalFemale: 0,
    totalChildren: 0,
  };
}

function check(failures: string[], name: string, run: () => void) {
  try {
    run();
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error));
    process.stderr.write(`${name} failed\n`);
  }
}

function main() {
  const payload = readPayload();
  const { flightNumber, origin, date } = payload;
  const failures: string[] = [];

  check(failures, "flight header", () => {
    const headerHtml = renderScreen(
      "flight header",
      createElement(FlightHeader, {
        flightStatus: payload.flightStatus as never,
        flightNumber,
      }),
    );
    assertShowsType("flight header", headerHtml, payload);
    if (!headerHtml.includes(payload.gate) || !headerHtml.includes(payload.status)) {
      throw new Error("flight header dropped the gate or status while rendering the aircraft");
    }
  });

  check(failures, "status history", () => {
    const historyClient = clientFor((queryClient) => {
      queryClient.setQueryData(
        ["statusHistory", flightNumber, origin, date],
        [payload.historyRecord],
      );
      queryClient.setQueryData(
        ["snapshots", flightNumber, origin, date],
        [],
      );
    });
    const historyHtml = renderScreen(
      "status history",
      shell(
        historyClient,
        createElement(StatusHistory, { flightNumber, origin, date }),
      ),
    );
    assertShowsType("status history", historyHtml, payload);
  });

  check(failures, "compare page", () => {
    const dashboard = {
      ...payload.dashboard,
      analysis: {
        ...(payload.dashboard.analysis as object | undefined),
        ...compareAnalysis(),
      },
    };
    setFlightSearch(`flights=${flightNumber}:${origin}:${date}`);
    const compareClient = clientFor((queryClient) => {
      queryClient.setQueryData(["flights"], []);
      queryClient.setQueryData(
        ["dashboard", flightNumber, origin, date],
        dashboard,
      );
    });
    const compareHtml = renderScreen(
      "compare page",
      shell(compareClient, createElement(ComparePage)),
    );
    assertShowsType("compare page", compareHtml, payload);
    if (!compareHtml.includes(payload.gate)) {
      throw new Error("compare page dropped the gate while rendering the aircraft");
    }
  });

  check(failures, "flight workbench", () => {
    const workbenchClient = clientFor((queryClient) => {
      queryClient.setQueryData(["flights"], []);
      queryClient.setQueryData(
        ["dashboard", flightNumber, origin, date, null],
        payload.dashboard,
      );
      queryClient.setQueryData(
        ["tree", flightNumber, origin, date, null],
        { nodes: [], edges: [] },
      );
    });
    const workbenchHtml = renderScreen(
      "flight workbench",
      shell(
        workbenchClient,
        createElement(FlightWorkbench, {
          initialSelection: { flightNumber, origin, date },
        }),
      ),
    );
    assertShowsType("flight workbench", workbenchHtml, payload);
    if (!workbenchHtml.includes(payload.gate) || !workbenchHtml.includes(payload.status)) {
      throw new Error("flight workbench dropped the gate or status while rendering the aircraft");
    }
  });

  if (failures.length > 0) {
    throw new Error(failures.join("\n"));
  }
  process.stdout.write("rendered aircraft type on header, workbench, history, and compare\n");
}

main();
