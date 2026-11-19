import assert from "node:assert/strict"
import { test } from "node:test"
import { act, createElement, useMemo, useState, type ReactElement } from "react"
import { createRoot } from "react-dom/client"

import { buildCabinStackedBarOption } from "../components/dashboard/echarts-option-builders"
import { CommercialInsightsTab } from "../components/dashboard/flight-insight-tabs"
import { EChart } from "../components/ui/echarts"
import type { FlightInsights } from "../lib/types"

const BUSINESS_CLASSES = new Set(["J", "C", "D", "Z", "R", "F"])

type CabinStack = {
  cabin: "Y" | "J"
  total: number
  segments: Array<{ bookingClass: string; value: number; fill: string }>
}

/**
 * Same grouping the commercial insights tab applies before it builds the
 * stacked-bar option: known economy letters stack on Y, known business
 * letters stack on J, and a class that dropped out of the mix is omitted.
 */
function cabinStacks(mix: Record<string, number>): CabinStack[] {
  const economy = new Set(["Y", "B", "H", "K", "W", "V", "S", "Q", "N", "O", "X", "G", "M", "L", "U", "T"])
  const grouped: Record<"Y" | "J", CabinStack> = {
    Y: { cabin: "Y", total: 0, segments: [] },
    J: { cabin: "J", total: 0, segments: [] },
  }
  for (const [bookingClass, value] of Object.entries(mix)) {
    const cabin = BUSINESS_CLASSES.has(bookingClass) ? "J" : economy.has(bookingClass) ? "Y" : null
    if (!cabin) continue
    grouped[cabin].total += value
    grouped[cabin].segments.push({ bookingClass, value, fill: "#38bdf8" })
  }
  return [grouped.Y, grouped.J]
}

function ClassMixChart({ mix }: { mix: Record<string, number> }) {
  const option = useMemo(
    () => buildCabinStackedBarOption({ data: cabinStacks(mix), businessClasses: BUSINESS_CLASSES }),
    [mix],
  )
  return createElement(EChart, { option, ariaLabel: "Revenue Class Mix", className: "h-[220px]" })
}

function insights(mix: Record<string, number>): FlightInsights {
  return {
    bookingChannels: {
      channels: { WEB: 14, AGT: 6 },
      categories: { online: 14, agent: 6, corporate: 2, other: 0 },
    },
    paymentMethods: { CARD: 16, CASH: 4 },
    revenueClassMix: mix,
    bookingLeadTime: null,
    corporateTravel: { companies: { "ACME AIR": 2 }, corporatePct: 10, totalCorporate: 2 },
    ticketStatus: { withTicket: 12, ticketPct: 60, withoutTicket: 8 },
    boardingPasses: { issued: 5, issuedPct: 25 },
    boardingRate: { checkedIn: 6, checkedInPct: 30, boarded: 2, boardedPct: 10 },
  } as FlightInsights
}

async function loadEcharts() {
  return import("echarts")
}

async function seriesSnapshot(label: string) {
  const chartApi = await loadEcharts()
  const host = document.querySelector(`[aria-label="${label}"]`)
  assert.ok(host instanceof HTMLElement, `chart host ${label} was not mounted`)
  const chart = chartApi.getInstanceByDom(host)
  assert.ok(chart, `ECharts instance for ${label} was not created`)
  const option = chart.getOption() as {
    series?: Array<{ name?: string; data?: unknown }>
  }
  const series = (option.series ?? []).filter((item) => item && item.name)
  return {
    id: chart.id,
    names: series.map((item) => String(item.name)),
    rows: series.map((item) => ({
      name: String(item.name),
      values: numericValues(item.data),
    })),
  }
}

function numericValues(data: unknown): number[] {
  if (!Array.isArray(data)) return []
  return data.map((item) => {
    if (typeof item === "number") return item
    if (item && typeof item === "object" && "value" in item) {
      const value = (item as { value?: unknown }).value
      return typeof value === "number" ? value : Number(value ?? 0)
    }
    return Number(item ?? 0)
  })
}

async function renderChart(element: ReactElement) {
  const host = document.createElement("div")
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(element)
  })
  return {
    async rerender(next: ReactElement) {
      await act(async () => {
        root.render(next)
      })
    },
    async unmount() {
      await act(async () => {
        root.unmount()
      })
      host.remove()
    },
  }
}

async function waitForSeries(label: string, predicate: (names: string[]) => boolean) {
  const started = Date.now()
  let last = ""
  while (Date.now() - started < 4000) {
    const chartApi = await loadEcharts()
    const host = document.querySelector(`[aria-label="${label}"]`)
    const chart = host instanceof HTMLElement ? chartApi.getInstanceByDom(host) : undefined
    if (chart) {
      const names = ((chart.getOption() as { series?: Array<{ name?: string }> }).series ?? [])
        .map((item) => item?.name)
        .filter((name): name is string => Boolean(name))
      last = names.join(",")
      if (predicate(names)) return chart
    }
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  const host = document.querySelector(`[aria-label="${label}"]`)
  const details = host instanceof HTMLElement
    ? {
        attrs: [...host.attributes].map((attr) => `${attr.name}=${attr.value}`),
        child: host.innerHTML.slice(0, 300),
        instance: Boolean((await loadEcharts()).getInstanceByDom(host)),
      }
    : null
  throw new Error(`timed out waiting for ${label}; last series: ${last}; host: ${JSON.stringify(details)}`)
}

test("revenue class chart drops booking classes that the refetched mix no longer contains", async () => {
  const wide = { Y: 40, B: 20, M: 10, C: 8, Z: 4 }
  const narrow = { Y: 12, C: 3 }
  const view = await renderChart(createElement(ClassMixChart, { mix: wide }))
  try {
    const chart = await waitForSeries("Revenue Class Mix", (names) => names.includes("B") && names.includes("Z"))
    const instanceId = chart.id

    await view.rerender(createElement(ClassMixChart, { mix: narrow }))
    await new Promise((resolve) => setTimeout(resolve, 50))

    const shown = await seriesSnapshot("Revenue Class Mix")
    assert.equal(shown.id, instanceId, "filter/refetch must update the existing chart")
    assert.deepEqual(
      [...shown.names].sort(),
      ["C", "Y"],
      `leftover series stayed on the chart: ${JSON.stringify(shown.rows)}`,
    )
    const passengerTotal = shown.rows.reduce(
      (sum, row) => sum + row.values.reduce((inner, value) => inner + value, 0),
      0,
    )
    assert.equal(
      passengerTotal,
      15,
      `chart totals ${passengerTotal} passengers after the mix shrank to 15: ${JSON.stringify(shown.rows)}`,
    )
  } finally {
    await view.unmount()
  }
})

function ClassMixHarness() {
  const [mix, setMix] = useState<Record<string, number>>({ Y: 40, B: 20, M: 10, C: 8, Z: 4 })
  return createElement(
    "div",
    null,
    createElement("button", {
      type: "button",
      onClick: () => setMix({ Y: 12, C: 3 }),
    }, "refetch"),
    createElement(CommercialInsightsTab, {
      insights: insights(mix),
      totalPassengers: Object.values(mix).reduce((sum, value) => sum + value, 0),
    }),
  )
}

test("commercial insights tab keeps a shrunk class mix on the same chart instance", async () => {
  const view = await renderChart(createElement(ClassMixHarness))
  try {
    const chart = await waitForSeries("Revenue Class Mix", (names) => names.includes("M") && names.includes("Z"))
    const instanceId = chart.id
    const button = document.querySelector("button")
    assert.ok(button)
    await act(async () => {
      button.dispatchEvent(new window.MouseEvent("click", { bubbles: true }))
    })
    await new Promise((resolve) => setTimeout(resolve, 50))

    const shown = await seriesSnapshot("Revenue Class Mix")
    assert.equal(shown.id, instanceId)
    assert.deepEqual([...shown.names].sort(), ["C", "Y"], JSON.stringify(shown.rows))
    const passengerTotal = shown.rows.reduce(
      (sum, row) => sum + row.values.reduce((inner, value) => inner + value, 0),
      0,
    )
    assert.equal(passengerTotal, 15, JSON.stringify(shown.rows))
  } finally {
    await view.unmount()
  }
})
