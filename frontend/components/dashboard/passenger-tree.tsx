"use client";

import { useState } from "react";
import { Network, Table2 } from "lucide-react";
import type { FlightTree } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

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
  const [view, setView] = useState<"tree" | "matrix">("tree");

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

  // ── Helper: extract node value by id ──
  function nv(id: string): number | string {
    const n = tree.nodes.find((n) => n.id === id);
    return n?.value ?? 0;
  }

  return (
    <Card className="shadow-none border-transparent bg-transparent">
      <CardContent className="p-0">
        {/* View Toggle */}
        <div className="flex items-center justify-center gap-1 mb-3">
          <Button
            variant={view === "tree" ? "default" : "outline"}
            size="sm"
            className="h-7 gap-1.5 text-xs"
            onClick={() => setView("tree")}
          >
            <Network className="h-3.5 w-3.5" />
            Tree
          </Button>
          <Button
            variant={view === "matrix" ? "default" : "outline"}
            size="sm"
            className="h-7 gap-1.5 text-xs"
            onClick={() => setView("matrix")}
          >
            <Table2 className="h-3.5 w-3.5" />
            Matrix
          </Button>
        </div>

        {view === "tree" ? (
          <div className="overflow-auto flex justify-center pb-4">
            <svg
              viewBox={`0 0 ${tree.width} ${tree.height}`}
              className="w-full h-auto font-sans"
              preserveAspectRatio="xMidYMin meet"
              role="img"
              aria-label="Passenger breakdown tree diagram"
            >
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
        ) : (
          <MatrixView tree={tree} nv={nv} />
        )}
      </CardContent>
    </Card>
  );
}

/* ── Matrix / Table View ────────────────────────── */

function MatrixView({ tree, nv }: { tree: FlightTree; nv: (id: string) => number | string }) {
  const cellBase = "px-2 py-1 text-xs";
  const headerCell = cn(cellBase, "font-semibold text-muted-foreground bg-muted/40 text-left");
  const valCell = cn(cellBase, "text-right font-medium tabular-nums");
  const sectionLabel = "px-2 py-1 text-[10px] font-semibold uppercase tracking-wider bg-muted/20 text-muted-foreground";

  return (
    <div className="space-y-2">
      {/* Passengers + Staff combined table */}
      <div className="rounded-lg border overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b">
              <th className={headerCell}></th>
              <th className={cn(headerCell, "text-right")}>Total</th>
              <th className={cn(headerCell, "text-right")}>Pax</th>
              <th className={cn(headerCell, "text-right")}>Staff</th>
              <th className={cn(headerCell, "text-right")}>M</th>
              <th className={cn(headerCell, "text-right")}>F</th>
              <th className={cn(headerCell, "text-right")}>CHD</th>
              <th className={cn(headerCell, "text-right")}>INF</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b hover:bg-muted/20">
              <td className={cn(cellBase, "font-medium")}>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  Economy
                </span>
              </td>
              <td className={cn(valCell, "text-emerald-500 font-bold")}>{nv("economy")}</td>
              <td className={valCell}>{nv("econPassengers")}</td>
              <td className={valCell}>{nv("econStaff")}</td>
              <td className={valCell}>{nv("econPaxMale")}</td>
              <td className={valCell}>{nv("econPaxFemale")}</td>
              <td className={cn(valCell, "text-amber-500")}>{nv("econPaxChildren")}</td>
              <td className={cn(valCell, "text-orange-500")}>{nv("econPaxInfants")}</td>
            </tr>
            <tr className="border-b hover:bg-muted/20">
              <td className={cn(cellBase, "font-medium")}>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                  Business
                </span>
              </td>
              <td className={cn(valCell, "text-amber-500 font-bold")}>{nv("business")}</td>
              <td className={valCell}>{nv("bizPassengers")}</td>
              <td className={valCell}>{nv("bizStaff")}</td>
              <td className={valCell}>{nv("bizPaxMale")}</td>
              <td className={valCell}>{nv("bizPaxFemale")}</td>
              <td className={cn(valCell, "text-amber-500")}>{nv("bizPaxChildren")}</td>
              <td className={cn(valCell, "text-orange-500")}>{nv("bizPaxInfants")}</td>
            </tr>
            <tr className="bg-muted/30">
              <td className={cn(cellBase, "font-semibold")}>Total</td>
              <td className={cn(valCell, "font-bold")}>{nv("root")}</td>
              <td className={valCell}>{add(nv("econPassengers"), nv("bizPassengers"))}</td>
              <td className={valCell}>{add(nv("econStaff"), nv("bizStaff"))}</td>
              <td className={valCell}>{add(nv("econPaxMale"), nv("bizPaxMale"))}</td>
              <td className={valCell}>{add(nv("econPaxFemale"), nv("bizPaxFemale"))}</td>
              <td className={cn(valCell, "text-amber-500")}>{add(nv("econPaxChildren"), nv("bizPaxChildren"))}</td>
              <td className={cn(valCell, "text-orange-500")}>{add(nv("econPaxInfants"), nv("bizPaxInfants"))}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Crew */}
      <div className="rounded-lg border overflow-hidden">
        <div className={sectionLabel}>Crew</div>
        <table className="w-full">
          <tbody>
            <tr className="border-b hover:bg-muted/20">
              <td className={cn(cellBase, "font-medium text-muted-foreground")}>Cabin Crew</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("cabinCrew")}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("cabinCrewMale")}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("cabinCrewFemale")}</td>
            </tr>
            <tr className="hover:bg-muted/20">
              <td className={cn(cellBase, "font-medium text-muted-foreground")}>Flight Crew</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("flightCrew")}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("flightCrewMale")}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{nv("flightCrewFemale")}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Operational Status */}
      {tree.statusCards.length > 0 && (
        <div className="rounded-lg border overflow-hidden">
          <div className={sectionLabel}>Status</div>
          <div className="grid grid-cols-5 divide-x">
            {tree.statusCards.map((card) => (
              <div key={card.id} className="px-2 py-1.5 text-center">
                <div className="text-[10px] text-muted-foreground leading-tight">{card.label}</div>
                <div className="text-base font-bold leading-snug" style={{ color: card.textColor }}>
                  {card.value}
                </div>
                {card.subLabel && (
                  <div className="text-[9px] text-muted-foreground leading-tight">{card.subLabel}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function add(a: number | string, b: number | string): number | string {
  if (typeof a === "string" || typeof b === "string") return "—";
  return a + b;
}
