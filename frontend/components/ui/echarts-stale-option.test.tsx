/**
 * Spec for the stale ECharts option race.
 *
 * These tests fail while frontend/components/ui/echarts.tsx applies the option
 * captured when its dynamic import() started and ignores option/loading updates
 * that arrive before that import resolves.
 */
import { act, type ReactNode } from "react"
import { createRoot, type Root } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest"

import type { EChartsOption } from "echarts"
import type {
  BoardingProgressResponse,
  FlightDashboard,
  FlightInsights,
  NetworkAnalyticsResponse,
  PhaseSnapshot,
  PhaseSummary,
} from "@/lib/types"

const gate = vi.hoisted(() => {
  let openGate: () => void = () => {}
  const opened = new Promise<void>((resolve) => {
    openGate = resolve
  })
  return {
    opened,
    release: () => openGate(),
  }
})

const api = vi.hoisted(() => ({
  fetchBoardingProgress: vi.fn(),
  fetchNetworkAnalytics: vi.fn(),
  fetchFlights: vi.fn(async () => []),
}))

vi.mock("echarts", async () => {
  await gate.opened
  return await vi.importActual<typeof import("echarts")>("echarts")
})

vi.mock("@/lib/api", () => api)

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: () => undefined, replace: () => undefined }),
  usePathname: () => "/analytics",
}))

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>,
}))

vi.mock("next-themes", () => ({
  useTheme: () => ({ setTheme: () => undefined, resolvedTheme: "dark" }),
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
}))

const STALE = 1111
const LATEST = 7777
const LATER = 3333

type Mounted = {
  container: HTMLDivElement
  rerender: (ui: ReactNode) => Promise<void>
  unmount: () => void
}

function installDomShims() {
  class ResizeObserverShim {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverShim as unknown as typeof ResizeObserver
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() {
      return 640
    },
  })
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get() {
      return 360
    },
  })
  if (!window.matchMedia) {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener() {},
        removeListener() {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {
          return false
        },
      }) as MediaQueryList
  }
  const rendererRoot = globalThis as unknown as { __ECHARTS__DEFAULT__RENDERER__?: string }
  rendererRoot.__ECHARTS__DEFAULT__RENDERER__ = "svg"
  ;(window as unknown as { __ECHARTS__DEFAULT__RENDERER__?: string }).__ECHARTS__DEFAULT__RENDERER__ = "svg"
  ;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

async function mount(ui: ReactNode): Promise<Mounted> {
  const container = document.createElement("div")
  document.body.appendChild(container)
  const root: Root = createRoot(container)
  await act(async () => {
    root.render(ui)
  })
  await flush()
  return {
    container,
    rerender: async (next) => {
      await act(async () => {
        root.render(next)
      })
      await flush()
    },
    unmount: () => {
      act(() => {
        root.unmount()
      })
      container.remove()
    },
  }
}

async function waitFor(predicate: () => boolean, label: string) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (predicate()) return
    await flush()
  }
  throw new Error(`timed out waiting for ${label}`)
}

function hosts(root: ParentNode = document) {
  return [...root.querySelectorAll<HTMLElement>('[role="img"][id]')]
}

function expectStillImporting(root: ParentNode, label: string) {
  const charts = hosts(root)
  expect(charts.length, `${label} should have mounted a chart host`).toBeGreaterThan(0)
  for (const host of charts) {
    expect(host.getAttribute("_echarts_instance_"), `${label} initialized before the import resolved`).toBeNull()
  }
}

async function click(selector: string, root: ParentNode = document) {
  const target = root.querySelector<HTMLElement>(selector)
  if (!target) throw new Error(`missing ${selector}`)
  await act(async () => {
    target.click()
  })
  await flush()
}

function optionText(chart: { getOption: () => { series?: unknown } | null | undefined }) {
  const option = chart.getOption()
  if (!option) return "NO_OPTION"
  return JSON.stringify(option)
}

function includes(text: string, needle: string) {
  return text.includes(needle)
}

function insights(partial: {
  channel: string
  channelCount: number
  payment: string
  paymentCount: number
  hour: string
  hourCount: number
  standbyCabin: string
  standbyCount: number
  passengerType: string
  passengerTypeCount: number
}): FlightInsights {
  return {
    connectingPassengers: { connecting: 1, local: 1, connectingPct: 50 },
    bookingChannels: {
      channels: { [partial.channel]: partial.channelCount },
      categories: { online: partial.channelCount, agent: 0, corporate: 0, other: 0 },
    },
    paymentMethods: { [partial.payment]: partial.paymentCount },
    documentCompliance: {
      DOCS: { count: 1, pct: 1 },
      DOCV: { count: 0, pct: 0 },
      DOCA: { count: 0, pct: 0 },
    },
    checkInSequence: { total: 1, earliest: 1, latest: 1, median: 1 },
    bookingLeadTime: {
      avgDays: 1,
      minDays: 0,
      maxDays: 2,
      medianDays: 1,
      distribution: { sameDay: partial.channelCount, within7d: 0, within30d: 0, within90d: 0, over90d: 0 },
    },
    seatOccupancy: { seated: 1, unseated: 0, seatPct: 100 },
    baggage: {
      withBags: 1,
      withoutBags: 0,
      totalBags: 1,
      avgBags: 1,
      dataAvailablePct: 100,
      withBagRoutes: 1,
    },
    editCodes: { uniqueCodes: 0, topCodes: [] },
    multiSegment: { distribution: {}, multiSegmentPct: 0 },
    pnrPartySize: { distribution: {}, avgSize: 1 },
    infantTracking: { total: 0, details: [] },
    wheelchairTypes: {},
    mealCodes: {},
    boardingRate: { boarded: 1, checkedIn: 1, notCheckedIn: 0, boardedPct: 100, checkedInPct: 100 },
    changeVelocity: { totalChanges: 0, changeTypes: {} },
    revenueClassMix: { Y: partial.channelCount },
    ticketStatus: { vcrTypes: {}, withTicket: 1, withoutTicket: 0, ticketPct: 100 },
    flightInfo: null,
    corporateTravel: { totalCorporate: 1, corporatePct: 10, companies: { ACME: partial.channelCount } },
    priorityPassengers: { total: 0, codes: {} },
    seniority: { withSeniority: 0, pct: 0 },
    connectionRisk: { atRiskCount: 0, totalConnecting: 1, riskPct: 0 },
    classMismatch: { total: 0, upgrades: 0, downgrades: 0 },
    passengerTypes: { [partial.passengerType]: partial.passengerTypeCount },
    boardingPasses: { issued: 1, notIssued: 0, issuedPct: 100 },
    reservationRecency: null,
    equipment: { aircraftType: "320", seatConfig: "Y" },
    checkInTimeline: {
      totalWithTime: partial.hourCount,
      hourDistribution: { [partial.hour]: partial.hourCount },
      peakHour: partial.hour,
      coveragePct: 100,
    },
    emergencyContacts: { withContact: 0, withoutContact: 1, coveragePct: 0 },
    nationalityBreakdown: { countries: {}, uniqueCountries: 0, unknown: 0, coveragePct: 0 },
    baggageRouting: { destinations: { DOH: partial.channelCount }, paxWithRoutes: 1, coveragePct: 100 },
    standbyUpgrade: {
      standbyTotal: partial.standbyCount,
      upgradeTotal: 0,
      standbyCabins: { [partial.standbyCabin]: partial.standbyCount },
      standbyPct: 1,
    },
    operationalReadiness: {
      noSeat: 0,
      checkedInNoBP: 0,
      notCheckedIn: 0,
      thruNoSeat: 0,
      readinessPct: 100,
    },
  }
}

function staleInsights() {
  return insights({
    channel: "OLDCHANNEL",
    channelCount: STALE,
    payment: "OLDPAY",
    paymentCount: STALE,
    hour: "04",
    hourCount: STALE,
    standbyCabin: "OLD",
    standbyCount: STALE,
    passengerType: "OLDTYPE",
    passengerTypeCount: STALE,
  })
}

function latestInsights() {
  return insights({
    channel: "NEWCHANNEL",
    channelCount: LATEST,
    payment: "NEWPAY",
    paymentCount: LATEST,
    hour: "19",
    hourCount: LATEST,
    standbyCabin: "NEW",
    standbyCount: LATEST,
    passengerType: "NEWTYPE",
    passengerTypeCount: LATEST,
  })
}

function dashboardFor(flightInsights: FlightInsights): FlightDashboard {
  return {
    insights: flightInsights,
    stateSummary: { others: { noShow: 0 } },
    passengerSummary: { totalPassengers: LATEST },
    analysis: { revenue: 1, nonRevenue: 0, loyaltyCounts: {} },
  } as FlightDashboard
}

function phase(label: string, booked: number, male: number): PhaseSnapshot {
  const bucket = (totalPassengers: number) => ({ totalPassengers }) as PhaseSnapshot["summary"]["booked"]
  return {
    phase: label,
    label,
    sequenceNumber: 1,
    capturedAt: "2026-03-20T10:00:00Z",
    summary: {
      booked: bucket(booked),
      checkedIn: bucket(2),
      boarded: bucket(3),
      totalMale: male,
      totalFemale: 4,
      totalChildren: 5,
      totalInfants: 6,
      cabinTotals: {
        economy: { passengers: booked, souls: booked },
        business: { passengers: 0, souls: 0 },
      },
      economy: { total: booked },
      business: { total: 0 },
    } as PhaseSummary,
    passengerSummary: {} as PhaseSnapshot["passengerSummary"],
  }
}

function boarding(cumulative: number): BoardingProgressResponse {
  const point = {
    timestamp: "2026-03-20T08:00:00Z",
    count: 1,
    cumulativeCount: cumulative,
  }
  return {
    flightNumber: "100",
    origin: "BAH",
    departureDate: "2026-03-20",
    totalPassengers: 200,
    checkinProgress: { current: cumulative, total: 200, percentage: 50, data: [point] },
    boardingProgress: { current: 1, total: 200, percentage: 1, data: [{ ...point, cumulativeCount: 1 }] },
    milestones: [],
  }
}

function analytics(route: string, passengers: number): NetworkAnalyticsResponse {
  return {
    dateRange: { from: "2026-01-01", to: "2026-01-02" },
    filters: { origin: null, cabin: null, flightNumber: null },
    overview: {
      totalFlights: 1,
      uniqueRoutes: 1,
      uniqueStations: 1,
      daysTracked: 1,
      totalPassengers: passengers,
      totalSouls: passengers,
      avgPassengersPerFlight: passengers,
      maxPassengersOnFlight: passengers,
      economyPassengers: passengers,
      businessPassengers: 0,
      avgLoadFactor: 50,
    },
    routePerformance: [{ origin: route, totalPassengers: passengers, flightCount: 1, avgPassengers: passengers }],
    bookingClassDistribution: [{ cabin: "Y", bookingClass: route, count: passengers }],
    nationalityDistribution: [{ nationality: route, count: passengers }],
    loyaltyDistribution: [{ tier: "GLD", tierName: route, count: passengers }],
    mealDistribution: [{ mealCode: route, count: passengers }],
    wheelchairDistribution: [{ code: "WCHR", count: 1 }],
    changeDistribution: [{ changeType: route, count: passengers }],
    totalChanges: passengers,
    dailyTrends: [{ date: "2026-01-01", flights: 1, passengers, changes: 1 }],
    partySizeDistribution: [{ partySize: 1, count: passengers }],
    pointOfSaleDistribution: [{ country: route, count: passengers }],
    operationalRates: {
      totalPassengers: passengers,
      checkedIn: passengers,
      boarded: 1,
      revenue: passengers,
      standby: 0,
      checkInRate: 100,
      boardingRate: 1,
    },
    upgradeAnalytics: { cabinChanges: 0, upgradesConfirmed: 0 },
    bookingLeadTime: { sameDay: passengers, within7d: 0, within30d: 0, within90d: 0, over90d: 0 },
  }
}

function barOption(marker: string, value: number): EChartsOption {
  return {
    series: [{ name: marker, type: "pie", data: [{ name: marker, value }] }],
  }
}

describe("ECharts options that change before the bundle import resolves", () => {
  const mounts: Mounted[] = []
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  beforeAll(() => {
    installDomShims()
  })

  afterAll(() => {
    for (const mounted of mounts) mounted.unmount()
    queryClient.clear()
  })

  it("keeps the chart on the option that was current when the import finished", async () => {
    const { EChart } = await import("@/components/ui/echarts")
    const {
      CommercialInsightsTab,
      ReadinessInsightsTab,
      ExceptionsInsightsTab,
    } = await import("@/components/dashboard/flight-insight-tabs")
    const { RevenueTab } = await import("@/components/dashboard/revenue-tab")
    const { HistoryOverview } = await import("@/components/dashboard/history-overview")
    const { PhaseTransitionChart } = await import("@/components/dashboard/phase-transition-chart")
    const { BoardingProgress } = await import("@/components/dashboard/boarding-progress")
    const AnalyticsPage = (await import("@/app/analytics/page")).default

    api.fetchBoardingProgress.mockResolvedValue(boarding(STALE))
    let network = analytics("OLDROUTE", STALE)
    api.fetchNetworkAnalytics.mockImplementation(async () => network)

    const direct = await mount(<EChart option={barOption("direct-first", STALE)} ariaLabel="Direct chart" />)
    mounts.push(direct)
    await waitFor(() => hosts(direct.container).length === 1, "direct chart host")
    await direct.rerender(<EChart option={barOption("direct-second", 2222)} ariaLabel="Direct chart" />)
    await direct.rerender(<EChart option={barOption("direct-latest", LATER)} ariaLabel="Direct chart" />)
    expectStillImporting(direct.container, "direct chart")

    const loadingChart = await mount(
      <EChart option={barOption("loading-first", STALE)} loading ariaLabel="Loading chart" />,
    )
    mounts.push(loadingChart)
    await waitFor(() => hosts(loadingChart.container).length === 1, "loading chart host")
    await loadingChart.rerender(
      <EChart option={barOption("loading-latest", LATEST)} loading={false} ariaLabel="Loading chart" />,
    )
    expectStillImporting(loadingChart.container, "loading chart")

    const commercial = await mount(
      <CommercialInsightsTab insights={staleInsights()} totalPassengers={STALE} />,
    )
    mounts.push(commercial)
    await waitFor(() => hosts(commercial.container).length > 0, "commercial charts")
    await commercial.rerender(<CommercialInsightsTab insights={latestInsights()} totalPassengers={LATEST} />)
    expect(commercial.container.textContent).toContain("NEWCHANNEL")
    expectStillImporting(commercial.container, "commercial charts")

    const readiness = await mount(<ReadinessInsightsTab insights={staleInsights()} totalPassengers={STALE} />)
    mounts.push(readiness)
    await waitFor(() => hosts(readiness.container).length > 0, "readiness charts")
    await readiness.rerender(<ReadinessInsightsTab insights={latestInsights()} totalPassengers={LATEST} />)
    expectStillImporting(readiness.container, "readiness charts")

    const exceptions = await mount(<ExceptionsInsightsTab insights={staleInsights()} totalPassengers={STALE} />)
    mounts.push(exceptions)
    await waitFor(() => hosts(exceptions.container).length > 0, "exception charts")
    await exceptions.rerender(<ExceptionsInsightsTab insights={latestInsights()} totalPassengers={LATEST} />)
    expectStillImporting(exceptions.container, "exception charts")

    const revenue = await mount(<RevenueTab dashboard={dashboardFor(staleInsights())} />)
    mounts.push(revenue)
    await waitFor(() => hosts(revenue.container).length === 1, "revenue chart")
    await revenue.rerender(<RevenueTab dashboard={dashboardFor(latestInsights())} />)
    expect(revenue.container.textContent).toContain("NEWCHANNEL")
    expectStillImporting(revenue.container, "revenue chart")

    const historyProps = (route: string, value: number) => ({
      historySeries: [{
        key: route,
        label: route,
        timestamp: "2026-03-20T08:00:00Z",
        booked: value,
        onBoard: 1,
        boardingPasses: 1,
      }],
      changeHotspots: [{ label: route, count: value }],
      snapshots: [],
      compareMode: false,
    })
    const history = await mount(<HistoryOverview {...historyProps("OLDHISTORY", STALE)} />)
    mounts.push(history)
    await waitFor(() => hosts(history.container).length === 2, "history charts")
    await history.rerender(<HistoryOverview {...historyProps("NEWHISTORY", LATEST)} />)
    expectStillImporting(history.container, "history charts")

    const phases = await mount(
      <PhaseTransitionChart phases={[phase("Departure", STALE, 4)]} transitions={[]} />,
    )
    mounts.push(phases)
    await waitFor(() => hosts(phases.container).length === 1, "phase chart")
    await phases.rerender(
      <PhaseTransitionChart phases={[phase("Departure", LATEST, 4)]} transitions={[]} />,
    )
    expectStillImporting(phases.container, "phase chart")

    const phaseView = await mount(
      <PhaseTransitionChart phases={[phase("Departure", STALE, LATEST)]} transitions={[]} />,
    )
    mounts.push(phaseView)
    await waitFor(() => hosts(phaseView.container).length === 1, "phase inline chart")
    await click('button[title="Expand chart"]', phaseView.container)
    await waitFor(
      () => document.querySelector('[aria-label^="Phase comparison —"]') != null,
      "fullscreen phase chart",
    )
    expectStillImporting(document.body, "fullscreen phase chart")
    const viewButtons = [...phaseView.container.querySelectorAll("button")].filter((button) =>
      (button.textContent ?? "").includes("Demographics"),
    )
    if (viewButtons.length === 0) {
      throw new Error(`missing demographics view button; labels=${[...document.querySelectorAll('[role="img"]')].map((node) => node.getAttribute("aria-label")).join(",")}`)
    }
    await act(async () => {
      viewButtons[0].click()
    })
    await flush()
    const fullscreenLabel = document.querySelector('[aria-label^="Phase comparison —"]')?.getAttribute("aria-label")
    expect(fullscreenLabel, "fullscreen chart should follow the demographics view").toBe(
      "Phase comparison — Demographics (fullscreen)",
    )

    const boardingView = await mount(
      <QueryClientProvider client={queryClient}>
        <BoardingProgress flightNumber="100" origin="BAH" date="2026-03-20" />
      </QueryClientProvider>,
    )
    mounts.push(boardingView)
    await waitFor(
      () => boardingView.container.querySelector('[aria-label="Boarding Progress Timeline"]') != null,
      "boarding chart",
    )
    expectStillImporting(boardingView.container, "boarding chart")
    await act(async () => {
      queryClient.setQueryData(["boardingProgress", "100", "BAH", "2026-03-20"], boarding(LATEST))
    })
    await flush()
    expect(boardingView.container.textContent).toContain(String(LATEST))
    expectStillImporting(boardingView.container, "boarding chart after data arrived")

    const analyticsView = await mount(
      <QueryClientProvider client={queryClient}>
        <AnalyticsPage />
      </QueryClientProvider>,
    )
    mounts.push(analyticsView)
    await waitFor(() => analyticsView.container.textContent?.includes("OLDROUTE") === true, "analytics route label")
    await waitFor(() => hosts(analyticsView.container).length > 0, "analytics charts")
    expectStillImporting(analyticsView.container, "analytics charts")
    network = analytics("NEWROUTE", LATEST)
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["network-analytics"] })
    })
    await flush()
    expect(analyticsView.container.textContent).toContain("NEWROUTE")
    expectStillImporting(analyticsView.container, "analytics charts after refetch")

    await act(async () => {
      gate.release()
      await gate.opened
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    await flush()

    const echarts = await import("echarts")
    const read = (root: ParentNode) =>
      hosts(root).map((host) => {
        const chart = echarts.getInstanceByDom(host)
        const label = host.getAttribute("aria-label") ?? "chart"
        if (!chart) return `${label}:NO_INSTANCE`
        return `${label}:${optionText(chart)}`
      }).join("\n")

    const directOption = read(direct.container)
    expect.soft(includes(directOption, "direct-latest"), "direct chart kept an older series").toBe(true)
    expect.soft(includes(directOption, String(LATER)), "direct chart kept an older value").toBe(true)
    expect.soft(includes(directOption, "direct-first"), "direct chart still has the first series").toBe(false)
    expect.soft(includes(directOption, "direct-second"), "direct chart still has the middle series").toBe(false)

    const loadingOption = read(loadingChart.container)
    expect.soft(includes(loadingOption, "loading-latest"), "loading chart never applied the option that arrived while loading").toBe(true)
    expect.soft(includes(loadingOption, String(LATEST)), "loading chart never applied the latest value").toBe(true)
    expect.soft(includes(loadingOption, "loading-first"), "loading chart still has the option from the first render").toBe(false)

    const commercialOptions = read(commercial.container)
    expect.soft(includes(commercialOptions, "NEWCHANNEL"), "commercial charts kept the first flight channel").toBe(true)
    expect.soft(includes(commercialOptions, "NEWPAY"), "commercial charts kept the first flight payment mix").toBe(true)
    expect.soft(includes(commercialOptions, "OLDCHANNEL"), "commercial charts still describe the first channel").toBe(false)
    expect.soft(includes(commercialOptions, "OLDPAY"), "commercial charts still describe the first payment mix").toBe(false)

    const readinessOptions = read(readiness.container)
    expect.soft(includes(readinessOptions, "19:00"), "readiness timeline kept the first hour").toBe(true)
    expect.soft(includes(readinessOptions, String(LATEST)), "readiness charts kept the first counts").toBe(true)
    expect.soft(includes(readinessOptions, "04:00"), "readiness timeline still shows the first hour").toBe(false)

    const exceptionOptions = read(exceptions.container)
    expect.soft(includes(exceptionOptions, "Cabin NEW"), "exception charts kept the first standby cabin").toBe(true)
    expect.soft(includes(exceptionOptions, "NEWTYPE"), "exception charts kept the first passenger type").toBe(true)
    expect.soft(includes(exceptionOptions, "Cabin OLD"), "exception charts still show the first standby cabin").toBe(false)
    expect.soft(includes(exceptionOptions, "OLDTYPE"), "exception charts still show the first passenger type").toBe(false)

    const revenueOption = read(revenue.container)
    expect.soft(includes(revenueOption, "NEWCHANNEL"), "revenue chart kept the first booking channel").toBe(true)
    expect.soft(includes(revenueOption, "OLDCHANNEL"), "revenue chart still shows the first booking channel").toBe(false)

    const historyOptions = read(history.container)
    expect.soft(includes(historyOptions, "NEWHISTORY"), "history charts kept the first series label").toBe(true)
    expect.soft(includes(historyOptions, String(LATEST)), "history charts kept the first counts").toBe(true)
    expect.soft(includes(historyOptions, "OLDHISTORY"), "history charts still show the first series label").toBe(false)

    const phaseOption = read(phases.container)
    expect.soft(includes(phaseOption, String(LATEST)), "phase chart kept the first booked count").toBe(true)
    expect.soft(includes(phaseOption, String(STALE)), "phase chart still shows the first booked count").toBe(false)

    const fullscreen = document.querySelector<HTMLElement>('[aria-label="Phase comparison — Demographics (fullscreen)"]')
    expect.soft(fullscreen, "fullscreen host").not.toBeNull()
    const fullscreenChart = fullscreen ? echarts.getInstanceByDom(fullscreen) : null
    const fullscreenOption = fullscreenChart ? optionText(fullscreenChart) : "NO_INSTANCE"
    expect.soft(includes(fullscreenOption, "Male"), "fullscreen chart kept the first view").toBe(true)
    expect.soft(includes(fullscreenOption, "Checked-In"), "fullscreen chart still shows the stacked view").toBe(false)

    const boardingOption = read(boardingView.container)
    expect.soft(includes(boardingOption, String(LATEST)), "boarding chart kept the first check-in count").toBe(true)
    expect.soft(includes(boardingOption, String(STALE)), "boarding chart still shows the first check-in count").toBe(false)

    const analyticsOptions = read(analyticsView.container)
    expect.soft(includes(analyticsOptions, "NEWROUTE"), "network charts kept the first route").toBe(true)
    expect.soft(includes(analyticsOptions, "OLDROUTE"), "network charts still show the first route").toBe(false)

    await direct.rerender(<EChart option={barOption("direct-after-init", 4444)} ariaLabel="Direct chart" />)
    await flush()
    const afterInit = read(direct.container)
    expect.soft(includes(afterInit, "direct-after-init"), "an option change after init did not update the chart").toBe(true)
    expect.soft(includes(afterInit, "4444"), "an option change after init did not update the value").toBe(true)
  })
})
