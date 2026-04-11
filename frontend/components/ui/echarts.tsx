"use client"

import { useEffect, useId, useRef, type CSSProperties } from "react"
import type { EChartsOption } from "echarts"

import { cn } from "@/lib/utils"

type EChartInstance = ReturnType<Awaited<typeof import("echarts")>["init"]>

const DEFAULT_SET_OPTION_OPTS = {
  lazyUpdate: true,
} as const

export type EChartProps = {
  option: EChartsOption
  className?: string
  style?: CSSProperties
  initOptions?: Record<string, unknown>
  setOptionOpts?: Record<string, unknown>
  loading?: boolean
  ariaLabel?: string
}

function EChartInner({
  option,
  className,
  style,
  initOptions,
  setOptionOpts = DEFAULT_SET_OPTION_OPTS,
  loading = false,
  ariaLabel,
}: EChartProps) {
  const chartId = useId()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<EChartInstance | null>(null)
  const echartsRef = useRef<typeof import("echarts") | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    let cancelled = false

    import("echarts").then((echarts) => {
      if (cancelled) return
      echartsRef.current = echarts
      const chart = echarts.getInstanceByDom(host) ?? echarts.init(host, undefined, initOptions)
      chartRef.current = chart

      const resizeObserver = new ResizeObserver(() => {
        chart.resize()
      })
      resizeObserver.observe(host)

      // Apply initial option
      if (loading) {
        chart.showLoading()
      } else {
        chart.hideLoading()
        chart.setOption(option, setOptionOpts)
        chart.resize()
      }

      // Store cleanup for the resize observer
      ;(host as HTMLDivElement & { __ro?: ResizeObserver }).__ro = resizeObserver
    })

    return () => {
      cancelled = true
      const ro = (host as HTMLDivElement & { __ro?: ResizeObserver }).__ro
      if (ro) ro.disconnect()
      if (chartRef.current) {
        chartRef.current.dispose()
        chartRef.current = null
      }
    }
  }, [initOptions]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (loading) {
      chart.showLoading()
      return
    }

    chart.hideLoading()
    chart.setOption(option, setOptionOpts)
    chart.resize()
  }, [loading, option, setOptionOpts])

  return (
    <div
      id={chartId}
      ref={hostRef}
      role="img"
      aria-label={ariaLabel}
      className={cn("min-h-[220px] w-full", className)}
      style={style}
    />
  )
}

// --- Dynamic wrapper: keeps echarts out of the initial bundle ---

import dynamic from "next/dynamic"

export const EChart = dynamic(
  () => Promise.resolve(EChartInner),
  {
    ssr: false,
    loading: () => (
      <div className="min-h-[220px] w-full animate-pulse rounded bg-muted/30" />
    ),
  },
)