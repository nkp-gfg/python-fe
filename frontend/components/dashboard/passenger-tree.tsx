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
        fill="none"
        stroke="hsl(var(--border))"
        strokeWidth={2}
        strokeLinecap="round"
      />
    );
  }

  const badgeColors: Record<string, { bg: string; fg: string }> = {
    M: { bg: "rgba(59,142,237,.2)", fg: "#3b8eed" },
    F: { bg: "rgba(232,88,140,.2)", fg: "#e8588c" },
    C: { bg: "rgba(46,194,126,.2)", fg: "#2ec27e" },
    I: { bg: "rgba(232,154,60,.2)", fg: "#e89a3c" },
  };

  function nodeBox(
    nd: TreeNode,
    borderColor: string,
    textColor: string,
    label: string,
    val: number,
    sub?: string,
    bdg?: FlightTree["nodes"][number]["badges"],
  ) {
    const left = nd.x - nd.w / 2;
    const top = nd.y - nd.h / 2;
    return (
      <g key={`node-${nd.x}-${nd.y}`}>
        <rect
          x={left}
          y={top}
          width={nd.w}
          height={nd.h}
          rx={12}
          fill="hsl(var(--card))"
          stroke={borderColor}
          strokeWidth={2}
        />
        <text
          x={nd.x}
          y={top + 16}
          textAnchor="middle"
          fill="hsl(var(--muted-foreground))"
          fontSize={10}
          fontWeight={500}
          fontFamily="var(--font-roboto), system-ui, sans-serif"
        >
          {label}
        </text>
        <text
          x={nd.x}
          y={top + 37}
          textAnchor="middle"
          fill={textColor}
          fontSize={21}
          fontWeight={700}
          fontFamily="var(--font-roboto), system-ui, sans-serif"
        >
          {val}
        </text>
        {sub && (
          <text
            x={nd.x}
            y={top + 51}
            textAnchor="middle"
            fill="hsl(var(--muted-foreground))"
            fontSize={9}
            fontFamily="var(--font-roboto), system-ui, sans-serif"
          >
            {sub}
          </text>
        )}
        {bdg &&
          bdg.length > 0 &&
          (() => {
            let bx = nd.x - (bdg.length * 26) / 2;
            const by = top + nd.h - 16;
            return bdg.map((b) => {
              const col = badgeColors[b.type];
              const txt = `${b.type} ${b.value}`;
              const tw = txt.length * 5.5 + 8;
              const el = (
                <g key={`bdg-${b.type}-${nd.x}`}>
                  <rect
                    x={bx}
                    y={by}
                    width={tw}
                    height={13}
                    rx={3}
                    fill={col.bg}
                  />
                  <text
                    x={bx + tw / 2}
                    y={by + 10}
                    textAnchor="middle"
                    fill={col.fg}
                    fontSize={8.5}
                    fontWeight={700}
                    fontFamily="var(--font-roboto), system-ui, sans-serif"
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
    <Card className="border-white/10 bg-white/[0.03] shadow-[0_18px_50px_rgba(0,0,0,0.22)]">
      <CardContent className="py-5">
        <div className="flex items-center justify-center gap-3 mb-4">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-[0.2em]">
            {tree.title}
          </h3>
          <span className="rounded bg-blue-500/15 px-2 py-0.5 text-[10px] font-semibold text-blue-400">
            {tree.badge}
          </span>
        </div>

        <div className="overflow-x-auto flex justify-center">
          <svg
            width="100%"
            viewBox={`0 0 ${tree.width} ${tree.height}`}
            className="max-w-[940px]"
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
