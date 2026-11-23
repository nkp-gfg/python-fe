import { createElement } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";

import AnalyticsPage from "../app/analytics/page";
import { Providers } from "../components/providers";

export { act, createElement, createRoot };

export function App() {
  return createElement(Providers, null, createElement(AnalyticsPage));
}
