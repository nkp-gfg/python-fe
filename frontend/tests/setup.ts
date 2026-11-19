import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { createElement } from "react";

afterEach(() => {
  cleanup();
});

if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof window.ResizeObserver !== "function") {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

vi.mock("@/components/ui/echarts", () => ({
  EChart: ({
    option,
  }: {
    option: { series?: { name?: string; data?: unknown }[]; xAxis?: { data?: unknown } };
  }) => {
    const series = Array.isArray(option?.series) ? option.series : [];
    const isTrend = series.some((item) => item?.name === "Passengers");
    if (!isTrend) return createElement("div", { "data-testid": "echart-other" });
    return createElement("div", {
      "data-testid": "daily-trend",
      "data-x": JSON.stringify(option?.xAxis?.data ?? []),
      "data-series": JSON.stringify(series.map((item) => ({ name: item.name, data: item.data }))),
    });
  },
}));
