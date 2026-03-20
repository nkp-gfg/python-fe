"use client";

import type { FlightTree } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";

interface Props {
  tree: FlightTree;
}

interface TreeNode {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function PassengerTree({ tree }: Props) {
  const nodes = Object.fromEntries(
    tree.nodes.map((node) => [
      node.id,
      { x: node.x, y: node.y, w: node.w, h: node.h },
    ]),
  ) as Record<string, TreeNode>;

  function conn(from: TreeNode, to: TreeNode) {
    const fy = from.y + from.h / 2;
    const ty = to.y - to.h / 2;
    const mid = (fy + ty) / 2;
    return (
      <path
        key={`${from.x}-${from.y}-${to.x}-${to.y}`}
        d={`M${from.x},${fy} C${from.x},${mid} ${to.x},${mid} ${to.x},${ty}`}
        className="fill-none stroke-border drop-shadow-sm"
        strokeWidth={1.5}
        strokeLinecap="round"
      />
    );
  }

  const badgeColors: Record<string, { bg: string; fg: string }> = {
    M: { bg: "rgba(59,130,246,0.15)", fg: "rgb(59,130,246)" }, // blue-500 — Male
    F: { bg: "rgba(236,72,153,0.15)", fg: "rgb(236,72,153)" }, // pink-500 — Female
    A: { bg: "rgba(59,130,246,0.15)", fg: "rgb(59,130,246)" }, // blue-500 — Adults
    C: { bg: "rgba(16,185,129,0.15)", fg: "rgb(16,185,129)" }, // emerald-500 — Children
    I: { bg: "rgba(245,158,11,0.15)", fg: "rgb(245,158,11)" }, // amber-500 — Infants
  };

  function nodeBox(
    nd: TreeNode,
    borderColor: string,
    textColor: string,
    label: string,
    val: number | string,
    sub?: string,
    bdg?: FlightTree["nodes"][number]["badges"],
  ) {
    const left = nd.x - nd.w / 2;
    const top = nd.y - nd.h / 2;
    const isUnavailable = typeof val === "string";
    const displayBorder = isUnavailable ? borderColor : borderColor;
    const displayStroke = isUnavailable ? [4, 3] : undefined;
    return (
      <g key={`node-${nd.x}-${nd.y}`}>
        <rect
          x={left}
          y={top}
          width={nd.w}
          height={nd.h}
          rx={8}
          className="fill-card shadow-sm drop-shadow-sm"
          stroke={displayBorder}
          strokeWidth={1.5}
          strokeDasharray={displayStroke ? displayStroke.join(",") : undefined}
        />
        <text
          x={nd.x}
          y={top + 18}
          textAnchor="middle"
          className="fill-muted-foreground"
          fontSize={11}
          fontWeight={500}
          fontFamily="inherit"
        >
          {label}
        </text>
        <text
          x={nd.x}
          y={top + 38}
          textAnchor="middle"
          fill={textColor}
          fontSize={20}
          fontWeight={600}
          fontFamily="inherit"
        >
          {val}
        </text>
        {sub && (
          <text
            x={nd.x}
            y={top + 52}
            textAnchor="middle"
            className="fill-muted-foreground"
            fontSize={10}
            fontFamily="inherit"
          >
            {sub}
          </text>
        )}
        {bdg &&
          bdg.length > 0 &&
          (() => {
            let bx = nd.x - (bdg.length * 28) / 2;
            const by = top + nd.h - 18;
            return bdg.map((b) => {
              const col = badgeColors[b.type] || { bg: "hsl(var(--muted))", fg: "hsl(var(--muted-foreground))" };
              const txt = `${b.type} ${b.value}`;
              const tw = txt.length * 6 + 10;
              const el = (
                <g key={`bdg-${b.type}-${nd.x}`}>
                  <rect
                    x={bx}
                    y={by}
                    width={tw}
                    height={16}
                    rx={4}
                    fill={col.bg}
                  />
                  <text
                    x={bx + tw / 2}
                    y={by + 11.5}
                    textAnchor="middle"
                    fill={col.fg}
                    fontSize={9}
                    fontWeight={600}
                    fontFamily="inherit"
                  >
                    {txt}
                  </text>
                </g>
              );
              bx += tw + 4;
              return el;
            });
          })()}
      </g>
    );
  }

  return (
    <Card className="shadow-none border-transparent bg-transparent">
      <CardContent className="p-0">
        <div className="flex items-center justify-center gap-3 mb-6">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            {tree.title}
          </h3>
          {tree.badge && (
            <span className="rounded-full bg-blue-500/10 px-2.5 py-0.5 text-xs font-semibold text-blue-500">
              {tree.badge}
            </span>
          )}
        </div>

        <div className="overflow-auto flex justify-center pb-4">
          <svg
            viewBox={`0 0 ${tree.width} ${tree.height}`}
            className="w-full h-auto font-sans"
            preserveAspectRatio="xMidYMin meet"
            role="img"
            aria-label="Passenger breakdown tree diagram"
          >
            {/* Connections */}
            {tree.edges.map((edge) => {
              const from = nodes[edge.from];
              const to = nodes[edge.to];
              if (!from || !to) return null;
              return conn(from, to);
            })}

            {tree.nodes.map((node) =>
              nodeBox(
                nodes[node.id],
                node.borderColor,
                node.textColor,
                node.label,
                node.value,
                node.subLabel,
                node.badges,
              ),
            )}
          </svg>
        </div>
      </CardContent>
    </Card>
  );
}
