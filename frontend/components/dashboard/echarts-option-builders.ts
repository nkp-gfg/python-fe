import * as echarts from "echarts"
import type { EChartsOption } from "echarts"

const ECHARTS_PALETTE = ["#38bdf8", "#a78bfa", "#34d399", "#f59e0b", "#fb7185", "#22c55e", "#f97316", "#06b6d4"]
const ECHARTS_AXIS_COLOR = "#94a3b8"
const ECHARTS_TEXT_COLOR = "#e2e8f0"
const ECHARTS_SPLIT_LINE = "rgba(148, 163, 184, 0.16)"

type DonutChartDatum = {
  label: string
  value: number
  fill?: string
}

type FunnelStageDatum = {
  label: string
  value: number
  fill?: string
}

type CabinStackSegment = {
  bookingClass: string
  value: number
  fill: string
}

type CabinStackDatum = {
  cabin: string
  total: number
  segments: CabinStackSegment[]
}

type HistoryAreaPoint = {
  label: string
  timestamp: string
  booked: number
  onBoard: number
  boardingPasses: number
}

type BoardingTimelinePoint = {
  label: string
  timestamp: string
  checkedIn: number
  boarded: number
}

type HourlyAreaPoint = {
  hour: string
  value: number
}

type BarDatum = {
  label: string
  shortLabel?: string
  value: number
  fill?: string
}

function normalizeNumericValue(value: unknown) {
  const rawValue = Array.isArray(value) ? value[0] : value
  return typeof rawValue === "number" ? rawValue : Number(rawValue ?? 0)
}

export function buildDonutChartOption({
  title,
  centerLabel,
  data,
}: {
  title: string
  centerLabel: string
  data: DonutChartDatum[]
}): EChartsOption {
  const total = data.reduce((sum, item) => sum + item.value, 0)

  return {
    animationDuration: 450,
    color: data.map((item, index) => item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length]),
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        const value = normalizeNumericValue(item?.value)
        const percent = typeof item?.percent === "number" ? item.percent : 0
        return `${item?.name ?? "Unknown"}<br/>${value.toLocaleString()} passengers (${percent}%)`
      },
    },
    series: [
      {
        name: title,
        type: "pie",
        radius: ["58%", "80%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 10,
          borderColor: "rgba(15, 23, 42, 0.85)",
          borderWidth: 2,
        },
        label: { show: false },
        emphasis: {
          scale: true,
          label: {
            show: true,
            color: ECHARTS_TEXT_COLOR,
            fontWeight: 600,
            formatter: "{b}\n{d}%",
          },
        },
        data: data.map((item, index) => ({
          name: item.label,
          value: item.value,
          itemStyle: { color: item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length] },
        })),
      },
    ],
    graphic: [
      {
        type: "text",
        left: "center",
        top: "42%",
        style: {
          text: centerLabel,
          fill: ECHARTS_AXIS_COLOR,
          fontSize: 11,
          fontWeight: 600,
        },
      },
      {
        type: "text",
        left: "center",
        top: "52%",
        style: {
          text: total.toLocaleString(),
          fill: ECHARTS_TEXT_COLOR,
          fontSize: 26,
          fontWeight: 700,
        },
      },
    ],
  }
}

export function buildPassengerProgressFunnelOption({
  title,
  data,
}: {
  title: string
  data: FunnelStageDatum[]
}): EChartsOption {
  return {
    animationDuration: 450,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        const value = normalizeNumericValue(item?.value)
        return `${item?.name ?? "Unknown"}<br/>${value.toLocaleString()} passengers`
      },
    },
    series: [
      {
        name: title,
        type: "funnel",
        left: "8%",
        top: 8,
        bottom: 8,
        width: "84%",
        minSize: "30%",
        maxSize: "100%",
        sort: "none",
        gap: 4,
        label: {
          show: true,
          position: "inside",
          color: ECHARTS_TEXT_COLOR,
          formatter: (params) => `${params.name}\n${normalizeNumericValue(params.value).toLocaleString()}`,
          fontWeight: 600,
        },
        labelLine: { show: false },
        itemStyle: {
          borderColor: "rgba(15, 23, 42, 0.92)",
          borderWidth: 2,
          borderRadius: 6,
        },
        emphasis: {
          label: {
            color: "#ffffff",
          },
        },
        data: data.map((item, index) => ({
          name: item.label,
          value: item.value,
          itemStyle: {
            color: item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length],
          },
        })),
      },
    ],
  }
}

export function buildCabinStackedBarOption({
  data,
  businessClasses,
}: {
  data: CabinStackDatum[]
  businessClasses: ReadonlySet<string>
}): EChartsOption {
  const seriesOrder = Array.from(new Set(data.flatMap((item) => item.segments.map((segment) => segment.bookingClass))))
  const chartData = data.map((item) => {
    const row: Record<string, string | number> = {
      cabin: item.cabin,
      total: item.total,
    }

    item.segments.forEach((segment) => {
      row[segment.bookingClass] = segment.value
    })

    return row
  })

  const legendItems = data.flatMap((item) => item.segments.map((segment) => ({
    cabin: item.cabin,
    bookingClass: segment.bookingClass,
    value: segment.value,
    fill: segment.fill,
  })))
  const colorByClass = Object.fromEntries(
    legendItems.map((item, index) => [item.bookingClass, item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length]])
  )

  return {
    animationDuration: 450,
    grid: { left: 8, right: 8, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params])
          .map((item) => ({
            seriesName: item.seriesName,
            axisValueLabel: String(item.name ?? ""),
            value: normalizeNumericValue(item.value),
          }))
          .filter((item) => item.value > 0)
        const totalValue = rows.reduce((sum, item) => sum + item.value, 0)
        const lines = rows.map((item) => `${item.seriesName}: ${item.value.toLocaleString()}`).join("<br/>")
        return `${rows[0]?.axisValueLabel ?? ""}<br/>Total: ${totalValue.toLocaleString()}${lines ? `<br/>${lines}` : ""}`
      },
    },
    xAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    yAxis: {
      type: "category",
      data: chartData.map((item) => `${item.cabin} Class`),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: ECHARTS_TEXT_COLOR, fontWeight: 600 },
    },
    series: seriesOrder.map((bookingClass) => ({
      name: bookingClass,
      type: "bar",
      stack: businessClasses.has(bookingClass) ? "J" : "Y",
      barWidth: 22,
      emphasis: { focus: "series" },
      itemStyle: { color: colorByClass[bookingClass], borderRadius: 6 },
      data: chartData.map((item) => Number(item[bookingClass] ?? 0)),
    })),
  }
}

export function buildHistoryStackedAreaOption({
  data,
}: {
  data: HistoryAreaPoint[]
}): EChartsOption {
  const gradients = [
    new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: "rgba(56, 189, 248, 0.9)" },
      { offset: 1, color: "rgba(14, 165, 233, 0.18)" },
    ]),
    new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: "rgba(167, 139, 250, 0.86)" },
      { offset: 1, color: "rgba(124, 58, 237, 0.18)" },
    ]),
    new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: "rgba(52, 211, 153, 0.88)" },
      { offset: 1, color: "rgba(5, 150, 105, 0.18)" },
    ]),
  ]
  const lineColors = ["#38bdf8", "#a78bfa", "#34d399"]

  return {
    animationDuration: 450,
    color: lineColors,
    legend: {
      top: 0,
      textStyle: { color: ECHARTS_AXIS_COLOR },
      data: ["Booked", "Boarding Passes", "On Board"],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        label: { backgroundColor: "#334155", color: "#e2e8f0" },
      },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params]).map((item) => ({
          name: item.seriesName,
          value: normalizeNumericValue(item.value),
          marker: item.marker,
        }))
        const sourcePoint = data[(Array.isArray(params) ? params[0] : params)?.dataIndex ?? 0]
        const lines = rows.map((item) => `${item.marker}${item.name}: ${item.value.toLocaleString()}`).join("<br/>")
        return `${sourcePoint?.timestamp ?? ""}<br/>${lines}`
      },
    },
    grid: {
      left: 8,
      right: 8,
      top: 40,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: data.map((point) => point.label),
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisTick: { show: false },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: [
      {
        name: "Booked",
        type: "line",
        stack: "total",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 0 },
        areaStyle: { opacity: 0.82, color: gradients[0] },
        emphasis: { focus: "series" },
        data: data.map((point) => point.booked),
      },
      {
        name: "Boarding Passes",
        type: "line",
        stack: "total",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 0 },
        areaStyle: { opacity: 0.78, color: gradients[1] },
        emphasis: { focus: "series" },
        data: data.map((point) => point.boardingPasses),
      },
      {
        name: "On Board",
        type: "line",
        stack: "total",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 0 },
        areaStyle: { opacity: 0.74, color: gradients[2] },
        emphasis: { focus: "series" },
        data: data.map((point) => point.onBoard),
      },
    ],
  }
}

export function buildBoardingProgressAreaOption({
  data,
}: {
  data: BoardingTimelinePoint[]
}): EChartsOption {
  const gradients = [
    new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: "rgba(52, 211, 153, 0.78)" },
      { offset: 1, color: "rgba(5, 150, 105, 0.12)" },
    ]),
    new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: "rgba(168, 85, 247, 0.76)" },
      { offset: 1, color: "rgba(126, 34, 206, 0.12)" },
    ]),
  ]

  return {
    animationDuration: 450,
    color: ["#34d399", "#a855f7"],
    legend: {
      top: 0,
      textStyle: { color: ECHARTS_AXIS_COLOR },
      data: ["Checked In", "Boarded"],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        label: { backgroundColor: "#334155", color: "#e2e8f0" },
      },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params]).map((item) => ({
          name: item.seriesName,
          value: normalizeNumericValue(item.value),
          marker: item.marker,
        }))
        const sourcePoint = data[(Array.isArray(params) ? params[0] : params)?.dataIndex ?? 0]
        const lines = rows.map((item) => `${item.marker}${item.name}: ${item.value.toLocaleString()}`).join("<br/>")
        return `${sourcePoint?.timestamp ?? ""}<br/>${lines}`
      },
    },
    grid: {
      left: 8,
      right: 8,
      top: 40,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: data.map((point) => point.label),
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisTick: { show: false },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: [
      {
        name: "Checked In",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: "#34d399" },
        areaStyle: { opacity: 0.72, color: gradients[0] },
        emphasis: { focus: "series" },
        data: data.map((point) => point.checkedIn),
      },
      {
        name: "Boarded",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: "#a855f7" },
        areaStyle: { opacity: 0.64, color: gradients[1] },
        emphasis: { focus: "series" },
        data: data.map((point) => point.boarded),
      },
    ],
  }
}

export function buildCheckInTimelineAreaOption({
  data,
}: {
  data: HourlyAreaPoint[]
}): EChartsOption {
  const gradient = new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: "rgba(56, 189, 248, 0.62)" },
    { offset: 1, color: "rgba(14, 165, 233, 0.08)" },
  ])

  return {
    animationDuration: 450,
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        label: { backgroundColor: "#334155", color: "#e2e8f0" },
      },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        const value = normalizeNumericValue(item?.value)
        return `${item?.name ?? ""}<br/>Check-ins: ${value.toLocaleString()}`
      },
    },
    grid: {
      left: 8,
      right: 8,
      top: 8,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: data.map((point) => point.hour),
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisTick: { show: false },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: [
      {
        name: "Check-ins",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: "#38bdf8" },
        areaStyle: { opacity: 0.9, color: gradient },
        emphasis: { focus: "series" },
        data: data.map((point) => point.value),
      },
    ],
  }
}

export function buildHorizontalBarOption({
  data,
  valueLabel,
  valueFormatter,
}: {
  data: BarDatum[]
  valueLabel: string
  valueFormatter?: (value: number) => string
}): EChartsOption {
  return {
    animationDuration: 450,
    grid: {
      left: 8,
      right: 18,
      top: 8,
      bottom: 8,
      containLabel: true,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        const source = data[item?.dataIndex ?? 0]
        const value = normalizeNumericValue(item?.value)
        return `${source?.label ?? item?.name ?? ""}<br/>${valueLabel}: ${valueFormatter ? valueFormatter(value) : value.toLocaleString()}`
      },
    },
    xAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    yAxis: {
      type: "category",
      data: data.map((item) => item.shortLabel ?? item.label),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
    },
    series: [
      {
        name: valueLabel,
        type: "bar",
        barWidth: 18,
        itemStyle: {
          borderRadius: 8,
        },
        label: {
          show: true,
          position: "right",
          color: ECHARTS_TEXT_COLOR,
          formatter: (params) => {
            const value = normalizeNumericValue(params.value)
            return valueFormatter ? valueFormatter(value) : value.toLocaleString()
          },
        },
        data: data.map((item, index) => ({
          value: item.value,
          itemStyle: {
            color: item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length],
          },
        })),
      },
    ],
  }
}

export function buildVerticalBarOption({
  data,
  valueLabel,
}: {
  data: BarDatum[]
  valueLabel: string
}): EChartsOption {
  return {
    animationDuration: 450,
    grid: {
      left: 8,
      right: 8,
      top: 8,
      bottom: 8,
      containLabel: true,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        const source = data[item?.dataIndex ?? 0]
        const value = normalizeNumericValue(item?.value)
        return `${source?.label ?? item?.name ?? ""}<br/>${valueLabel}: ${value.toLocaleString()}`
      },
    },
    xAxis: {
      type: "category",
      data: data.map((item) => item.shortLabel ?? item.label),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisLabel: { color: ECHARTS_AXIS_COLOR },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: [
      {
        name: valueLabel,
        type: "bar",
        barWidth: 26,
        itemStyle: {
          borderRadius: [10, 10, 0, 0],
        },
        data: data.map((item, index) => ({
          value: item.value,
          itemStyle: {
            color: item.fill ?? ECHARTS_PALETTE[index % ECHARTS_PALETTE.length],
          },
        })),
      },
    ],
  }
}

// ─── Phase Journey chart builders ──────────────────────────────────

type SankeyNode = {
  name: string
  itemStyle?: { color: string }
}

type SankeyLink = {
  source: string
  target: string
  value: number
  lineStyle?: { color: string; opacity: number }
}

const SANKEY_STATE_COLORS: Record<string, string> = {
  booked: "#f59e0b",
  checkedIn: "#38bdf8",
  boarded: "#34d399",
  new: "#a78bfa",
  removed: "#fb7185",
}

export function buildPhaseTransitionSankeyOption({
  nodes,
  links,
}: {
  nodes: SankeyNode[]
  links: SankeyLink[]
}): EChartsOption {
  return {
    animationDuration: 600,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params
        if (item?.dataType === "edge") {
          const d = item.data as { source?: string; target?: string; value?: number }
          return `${d.source ?? ""} → ${d.target ?? ""}<br/>${(d.value ?? 0).toLocaleString()} passengers`
        }
        const d = item?.data as { name?: string; value?: number }
        return `${d?.name ?? ""}`
      },
    },
    series: [
      {
        type: "sankey",
        layout: "none",
        left: 20,
        right: 160,
        top: 20,
        bottom: 20,
        nodeWidth: 18,
        nodeGap: 10,
        draggable: true,
        emphasis: {
          focus: "adjacency",
        },
        label: {
          show: true,
          color: ECHARTS_TEXT_COLOR,
          fontSize: 10,
          fontWeight: 600,
          overflow: "truncate",
          ellipsis: "…",
          width: 140,
        },
        lineStyle: {
          color: "gradient",
          opacity: 0.4,
          curveness: 0.5,
        },
        itemStyle: {
          borderWidth: 0,
        },
        data: nodes,
        links: links,
      },
    ],
  }
}

type PhaseStackedDatum = {
  phase: string
  booked: number
  checkedIn: number
  boarded: number
}

export function buildPhaseStackedBarOption({
  data,
}: {
  data: PhaseStackedDatum[]
}): EChartsOption {
  const stateColors = ["#f59e0b", "#38bdf8", "#34d399"]
  const stateNames = ["Booked", "Checked-In", "Boarded"]
  const stateKeys: Array<keyof PhaseStackedDatum> = ["booked", "checkedIn", "boarded"]

  return {
    animationDuration: 450,
    color: stateColors,
    legend: {
      top: 0,
      textStyle: { color: ECHARTS_AXIS_COLOR },
      data: stateNames,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params])
          .map((item) => ({
            name: item.seriesName,
            value: normalizeNumericValue(item.value),
            marker: item.marker,
          }))
          .filter((r) => r.value > 0)
        const total = rows.reduce((sum, r) => sum + r.value, 0)
        const header = (Array.isArray(params) ? params[0] : params)?.name ?? ""
        const lines = rows.map((r) => `${r.marker}${r.name}: ${r.value.toLocaleString()}`).join("<br/>")
        return `<strong>${header}</strong><br/>Total: ${total.toLocaleString()}<br/>${lines}`
      },
    },
    grid: {
      left: 8,
      right: 8,
      top: 40,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: data.map((d) => d.phase),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisLabel: { color: ECHARTS_TEXT_COLOR, fontWeight: 600 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: stateKeys.map((key, i) => ({
      name: stateNames[i],
      type: "bar" as const,
      stack: "total",
      barWidth: 40,
      emphasis: { focus: "series" as const },
      itemStyle: {
        color: stateColors[i],
        borderRadius: i === stateKeys.length - 1 ? [6, 6, 0, 0] : [0, 0, 0, 0],
      },
      data: data.map((d) => Number(d[key] ?? 0)),
    })),
  }
}

type PhaseDemographicDatum = {
  phase: string
  male: number
  female: number
  children: number
  infants: number
}

export function buildPhaseDemographicBarOption({
  data,
}: {
  data: PhaseDemographicDatum[]
}): EChartsOption {
  const colors = ["#3b82f6", "#ec4899", "#f59e0b", "#34d399"]
  const names = ["Male", "Female", "Children", "Infants"]
  const keys: Array<keyof PhaseDemographicDatum> = ["male", "female", "children", "infants"]

  return {
    animationDuration: 450,
    color: colors,
    legend: {
      top: 0,
      textStyle: { color: ECHARTS_AXIS_COLOR },
      data: names,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params])
          .map((item) => ({
            name: item.seriesName,
            value: normalizeNumericValue(item.value),
            marker: item.marker,
          }))
          .filter((r) => r.value > 0)
        const header = (Array.isArray(params) ? params[0] : params)?.name ?? ""
        const lines = rows.map((r) => `${r.marker}${r.name}: ${r.value.toLocaleString()}`).join("<br/>")
        return `<strong>${header}</strong><br/>${lines}`
      },
    },
    grid: {
      left: 8,
      right: 8,
      top: 40,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: data.map((d) => d.phase),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisLabel: { color: ECHARTS_TEXT_COLOR, fontWeight: 600 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: keys.map((key, i) => ({
      name: names[i],
      type: "bar" as const,
      stack: "demo",
      barWidth: 40,
      emphasis: { focus: "series" as const },
      itemStyle: {
        color: colors[i],
        borderRadius: i === keys.length - 1 ? [6, 6, 0, 0] : [0, 0, 0, 0],
      },
      data: data.map((d) => Number(d[key] ?? 0)),
    })),
  }
}

type PhaseCabinDatum = {
  phase: string
  economy: number
  business: number
}

export function buildPhaseCabinBarOption({
  data,
}: {
  data: PhaseCabinDatum[]
}): EChartsOption {
  const colors = ["#2ec27e", "#c9a43a"]
  const names = ["Economy", "Business"]
  const keys: Array<keyof PhaseCabinDatum> = ["economy", "business"]

  return {
    animationDuration: 450,
    color: colors,
    legend: {
      top: 0,
      textStyle: { color: ECHARTS_AXIS_COLOR },
      data: names,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(15, 23, 42, 0.95)",
      borderColor: "rgba(148, 163, 184, 0.18)",
      textStyle: { color: ECHARTS_TEXT_COLOR },
    },
    grid: {
      left: 8,
      right: 8,
      top: 40,
      bottom: 8,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: data.map((d) => d.phase),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
      axisLabel: { color: ECHARTS_TEXT_COLOR, fontWeight: 600 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: ECHARTS_AXIS_COLOR },
      splitLine: { lineStyle: { color: ECHARTS_SPLIT_LINE } },
    },
    series: keys.map((key, i) => ({
      name: names[i],
      type: "bar" as const,
      stack: "cabin",
      barWidth: 40,
      emphasis: { focus: "series" as const },
      itemStyle: {
        color: colors[i],
        borderRadius: i === keys.length - 1 ? [6, 6, 0, 0] : [0, 0, 0, 0],
      },
      data: data.map((d) => Number(d[key] ?? 0)),
    })),
  }
}