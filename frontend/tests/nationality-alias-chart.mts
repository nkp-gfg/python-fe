/**
 * Renders the network analytics page and reads the bar charts it draws.
 *
 * Fails while IN/IND, PH/PHL, and BH/BHR are separate bars, including a raw
 * BHR label. The counts for India match the network analytics result for the
 * captured GF2057 BOM Trip Search (IN 12 + IND 2).
 */
import { JSDOM } from "jsdom";
import { createRequire } from "module";
import { mock } from "node:test";

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "http://localhost:3000/analytics",
  pretendToBeVisual: true,
});
const { window } = dom;

function install(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

install("window", window);
install("self", window);
install("document", window.document);
install("navigator", window.navigator);
install("HTMLElement", window.HTMLElement);
install("Element", window.Element);
install("Node", window.Node);
install("SVGElement", window.SVGElement);
install("DocumentFragment", window.DocumentFragment);
install("getComputedStyle", window.getComputedStyle.bind(window));
install("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 0));
install("cancelAnimationFrame", (id: number) => clearTimeout(id));
install("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});
install("IntersectionObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
  root = null;
  rootMargin = "";
  thresholds: number[] = [];
});
install("MutationObserver", window.MutationObserver);
install("DOMRect", window.DOMRect);
install("IS_REACT_ACT_ENVIRONMENT", true);

const chartOptions: Array<{ yAxis?: { data?: unknown }; series?: Array<{ data?: Array<{ value?: number }> }> }> = [];

const echartsMock = {
  init() {
    return {
      setOption(option: (typeof chartOptions)[number]) {
        chartOptions.push(option);
      },
      resize() {},
      dispose() {},
      showLoading() {},
      hideLoading() {},
    };
  },
  getInstanceByDom() {
    return null;
  },
};
mock.module("echarts", { defaultExport: echartsMock, namedExports: echartsMock });

mock.module("next/navigation", {
  namedExports: {
    useSearchParams() {
      return new URLSearchParams();
    },
    usePathname() {
      return "/analytics";
    },
    useRouter() {
      return { push() {}, replace() {}, prefetch() {} };
    },
  },
});

function dynamic(loader: () => Promise<unknown>) {
  let Loaded: any = () => null;
  const Dyn = (props: any) => {
    const React = require("react");
    const [Comp, setComp] = React.useState(() => Loaded);
    React.useEffect(() => {
      let cancelled = false;
      Promise.resolve(loader()).then((mod: any) => {
        const Component = mod?.default ?? mod;
        Loaded = Component;
        if (!cancelled) setComp(() => Component);
      });
      return () => {
        cancelled = true;
      };
    }, []);
    return React.createElement(Comp, props);
  };
  return Dyn;
}
mock.module("next/dynamic", { defaultExport: dynamic });

const require = createRequire(import.meta.url);
const React = require("react");
const { createRoot } = require("react-dom/client");
const { act } = require("react");
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");

const analytics = {
  dateRange: { from: "2026-03-18", to: "2026-03-18" },
  filters: { origin: null, cabin: null, flightNumber: null },
  overview: {
    totalFlights: 1,
    uniqueRoutes: 1,
    uniqueStations: 1,
    daysTracked: 1,
    totalPassengers: 24,
    totalSouls: 24,
    avgPassengersPerFlight: 24,
    maxPassengersOnFlight: 24,
    economyPassengers: 24,
    businessPassengers: 0,
    avgLoadFactor: 0,
  },
  routePerformance: [],
  bookingClassDistribution: [],
  nationalityDistribution: [
    { nationality: "IN", count: 12 },
    { nationality: "GB", count: 3 },
    { nationality: "IND", count: 2 },
    { nationality: "BH", count: 1 },
    { nationality: "BHR", count: 1 },
    { nationality: "PH", count: 4 },
    { nationality: "PHL", count: 1 },
  ],
  loyaltyDistribution: [],
  mealDistribution: [],
  wheelchairDistribution: [],
  changeDistribution: [],
  totalChanges: 0,
  dailyTrends: [],
  partySizeDistribution: [],
  pointOfSaleDistribution: [
    { country: "IN", count: 3 },
    { country: "IND", count: 2 },
    { country: "BH", count: 4 },
    { country: "BHR", count: 1 },
  ],
  operationalRates: {
    totalPassengers: 24,
    checkedIn: 0,
    boarded: 0,
    revenue: 0,
    standby: 0,
    checkInRate: 0,
    boardingRate: 0,
  },
  upgradeAnalytics: { cabinChanges: 0, upgradesConfirmed: 0 },
  bookingLeadTime: { sameDay: 0, within7d: 0, within30d: 0, within90d: 0, over90d: 0 },
};

const mockedFetch = (async (input: RequestInfo | URL) => {
  const url = typeof input === "string"
    ? input
    : input instanceof URL
      ? input.href
      : input.url;
  const body = url.includes("/analytics/network")
    ? analytics
    : url.includes("/flights")
      ? []
      : null;
  if (body === null) return new Response("not found", { status: 404 });
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}) as typeof fetch;
Object.defineProperty(globalThis, "fetch", { value: mockedFetch, configurable: true, writable: true });
Object.defineProperty(window, "fetch", { value: mockedFetch, configurable: true, writable: true });

const pageMod = await import("../app/analytics/page.tsx");
const Page = pageMod.default;
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const root = createRoot(document.getElementById("root")!);
await act(async () => {
  root.render(React.createElement(QueryClientProvider, { client }, React.createElement(Page)));
});
for (let i = 0; i < 40 && chartOptions.length === 0; i++) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 25));
  });
}

type BarChart = { labels: string[]; values: number[] };

function horizontalBars(): BarChart[] {
  const charts: BarChart[] = [];
  for (const option of chartOptions) {
    const labels = option?.yAxis?.data;
    const points = option?.series?.[0]?.data;
    if (!Array.isArray(labels) || labels.length === 0 || !Array.isArray(points)) continue;
    charts.push({
      labels: labels.map((label) => String(label)),
      values: points.map((point) => Number(point.value)),
    });
  }
  return charts;
}

function valueOf(chart: BarChart, label: string): number {
  const indexes = chart.labels.flatMap((item, index) => item === label ? [index] : []);
  if (indexes.length !== 1) {
    throw new Error(
      `expected one ${label} bar, found ${indexes.length} in ${JSON.stringify(chart.labels)}`,
    );
  }
  return chart.values[indexes[0]];
}

function assertMerged(chart: BarChart, label: string, count: number, rawCodes: string[]) {
  const actual = valueOf(chart, label);
  if (actual !== count) {
    throw new Error(`${label} bar is ${actual}, expected the combined count ${count}`);
  }
  for (const code of rawCodes) {
    if (chart.labels.includes(code)) {
      throw new Error(`${code} is still its own bar beside ${label}: ${JSON.stringify(chart.labels)}`);
    }
  }
}

const charts = horizontalBars();
const nationality = charts.find((chart) => chart.labels.includes("United Kingdom") || chart.labels.includes("GB"));
const pointOfSale = charts.find((chart) => chart !== nationality && (
  chart.labels.includes("India") || chart.labels.includes("IN") || chart.labels.includes("IND")
));

if (!nationality) {
  throw new Error(`nationality chart was not drawn; charts=${JSON.stringify(charts)}`);
}
if (!pointOfSale) {
  throw new Error(`point-of-sale chart was not drawn; charts=${JSON.stringify(charts)}`);
}

assertMerged(nationality, "India", 14, ["IN", "IND"]);
assertMerged(nationality, "Bahrain", 2, ["BH", "BHR"]);
assertMerged(nationality, "Philippines", 5, ["PH", "PHL"]);
assertMerged(nationality, "United Kingdom", 3, ["GB"]);
assertMerged(pointOfSale, "India", 5, ["IN", "IND"]);
assertMerged(pointOfSale, "Bahrain", 5, ["BH", "BHR"]);

console.log("nationality alias chart checks passed");
process.exit(0);
