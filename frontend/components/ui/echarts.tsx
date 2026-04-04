"use client"

import { useEffect, useId, useRef, type CSSProperties } from "react"
import * as echarts from "echarts"
import type { EChartsOption } from "echarts"

import { cn } from "@/lib/utils"

type InitOptions = Parameters<typeof echarts.init>[2]
type SetOptionOptions = Parameters<ReturnType<typeof echarts.init>["setOption"]>[1]

const DEFAULT_SET_OPTION_OPTS: SetOptionOptions = {
  lazyUpdate: true,
}

type EChartProps = {
  option: EChartsOption
  className?: string
  style?: CSSProperties
  initOptions?: InitOptions
  setOptionOpts?: SetOptionOptions
  loading?: boolean
  ariaLabel?: string
}

export function EChart({
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
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    const chart = echarts.getInstanceByDom(host) ?? echarts.init(host, undefined, initOptions)
    chartRef.current = chart

    const resizeObserver = new ResizeObserver(() => {
      chart.resize()
    })

    resizeObserver.observe(host)

    return () => {
      resizeObserver.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [initOptions])

  useEffect(() => {
    const chart = chartRef.current

    if (!chart) {
      return
    }

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