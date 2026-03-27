"use client";

import { useState } from "react";
import { Search, X } from "lucide-react";
import type { FlightTree, FlightTreeNode } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* ── Badge colour map ──────────────────────────── */

const BADGE_COLORS: Record<string, { bg: string; fg: string }> = {
  M: { bg: "rgba(59,130,246,0.15)", fg: "rgb(59,130,246)" },
  F: { bg: "rgba(236,72,153,0.15)", fg: "rgb(236,72,153)" },
  A: { bg: "rgba(59,130,246,0.15)", fg: "rgb(59,130,246)" },
  C: { bg: "rgba(16,185,129,0.15)", fg: "rgb(16,185,129)" },
  I: { bg: "rgba(245,158,11,0.15)", fg: "rgb(245,158,11)" },
};

const FALLBACK_BADGE = { bg: "hsl(var(--muted))", fg: "hsl(var(--muted-foreground))" };

/* ── SVG primitives ────────────────────────────── */

function Edge({ from, to }: { from: FlightTreeNode; to: FlightTreeNode }) {
  const fy = from.y + from.h / 2;
  const ty = to.y - to.h / 2;
  const mid = (fy + ty) / 2;
  return (
    <path
      d={`M${from.x},${fy} C${from.x},${mid} ${to.x},${mid} ${to.x},${ty}`}
      className="fill-none stroke-border"
      strokeWidth={1.5}
      strokeLinecap="round"
    />
  );
}

function NodeBadges({ badges, cx, baseY }: {
  badges: FlightTreeNode["badges"];
  cx: number;
  baseY: number;
}) {
  if (!badges?.length) return null;

  // Pre-compute widths, then cumulative x-offsets — no mutation during render
  const items = badges.map((b) => {
    const text = `${b.type} ${b.value}`;
    return { ...b, text, tw: text.length * 6 + 10 };
  });
  const totalWidth = items.reduce((s, i) => s + i.tw + 4, -4);
  const startX = cx - totalWidth / 2;

  // Build cumulative offsets array
  const offsets: number[] = [];
  items.reduce((acc, item) => {
    offsets.push(acc);
    return acc + item.tw + 4;
  }, 0);

  return (
    <>
      {items.map((item, idx) => {
        const col = BADGE_COLORS[item.type] ?? FALLBACK_BADGE;
        const x = startX + offsets[idx];
        return (
          <g key={`${item.type}-${cx}`}>
            <rect x={x} y={baseY} width={item.tw} height={16} rx={4} fill={col.bg} />
            <text
              x={x + item.tw / 2}
              y={baseY + 11.5}
              textAnchor="middle"
              fill={col.fg}
              fontSize={9}
              fontWeight={600}
              fontFamily="inherit"
            >
              {item.text}
            </text>
          </g>
        );
      })}
    </>
  );
}

function Node({ node }: { node: FlightTreeNode }) {
  const left = node.x - node.w / 2;
  const top = node.y - node.h / 2;
  const isUnavailable = typeof node.value === "string";

  return (
    <g>
      <rect
        x={left}
        y={top}
        width={node.w}
        height={node.h}
        rx={8}
        className="fill-card"
        stroke={node.borderColor}
        strokeWidth={1.5}
        strokeDasharray={isUnavailable ? "4,3" : undefined}
      />
      <text
        x={node.x}
        y={top + 18}
        textAnchor="middle"
        className="fill-muted-foreground"
        fontSize={11}
        fontWeight={500}
        fontFamily="inherit"
      >
        {node.label}
      </text>
      <text
        x={node.x}
        y={top + 38}
        textAnchor="middle"
        fill={node.textColor}
        fontSize={20}
        fontWeight={600}
        fontFamily="inherit"
      >
        {node.value}
      </text>
      {node.subLabel && (
        <text
          x={node.x}
          y={top + 52}
          textAnchor="middle"
          className="fill-muted-foreground"
          fontSize={10}
          fontFamily="inherit"
        >
          {node.subLabel}
        </text>
      )}
      <NodeBadges badges={node.badges} cx={node.x} baseY={top + node.h - 18} />
    </g>
  );
}

/* ── Main export ───────────────────────────────── */

interface PaxTreeProps {
  tree: FlightTree;
  className?: string;
  onClose?: () => void;
}

export function PaxTree({ tree, className, onClose }: PaxTreeProps) {
  const [magnified, setMagnified] = useState(true);

  // Only render visible nodes in the SVG (display !== false)
  const visibleNodes = tree.nodes.filter((n) => n.display !== false);
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = tree.edges.filter(
    (e) => visibleIds.has(e.from) && visibleIds.has(e.to),
  );
  const nodeMap = new Map(visibleNodes.map((n) => [n.id, n]));

  function renderSvg(extraClass?: string) {
    return (
      <svg
        viewBox={`0 0 ${tree.width} ${tree.height}`}
        className={cn("h-auto font-sans", extraClass)}
        preserveAspectRatio="xMidYMin meet"
        role="img"
        aria-label="Passenger breakdown tree diagram"
      >
        {visibleEdges.map((e) => {
          const from = nodeMap.get(e.from);
          const to = nodeMap.get(e.to);
          if (!from || !to) return null;
          return <Edge key={`${e.from}-${e.to}`} from={from} to={to} />;
        })}
        {visibleNodes.map((n) => (
          <Node key={n.id} node={n} />
        ))}
      </svg>
    );
  }

  return (
    <div className={className}>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">Tree</p>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          onClick={() => setMagnified(true)}
          title="Open large tree view"
        >
          <Search className="h-3.5 w-3.5" />
          Magnify
        </Button>
      </div>
      <div className="overflow-auto flex justify-center pb-2">
        {renderSvg("max-w-[860px] w-full")}
      </div>

      {magnified && (
        <div className="fixed inset-0 z-50 bg-background/95 backdrop-blur-sm">
          <div className="flex h-full flex-col">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold">Passenger Tree — Magnified</h3>
                <p className="text-xs text-muted-foreground">
                  Scroll to inspect all nodes in detail.
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 gap-1.5"
                onClick={() => { setMagnified(false); onClose?.(); }}
              >
                <X className="h-4 w-4" />
                Close
              </Button>
            </div>
            <div className="flex-1 overflow-auto p-4">
              <div className="mx-auto min-w-[860px] max-w-[1400px]">
                {renderSvg("w-full")}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
