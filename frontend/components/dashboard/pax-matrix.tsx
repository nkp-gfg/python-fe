"use client";

import type { FlightTree, FlightTreeCard } from "@/lib/types";
import { cn } from "@/lib/utils";

/* ── Helpers ───────────────────────────────────── */

function nv(tree: FlightTree, id: string): number | string {
  return tree.nodes.find((n) => n.id === id)?.value ?? 0;
}

function add(a: number | string, b: number | string): number | string {
  if (typeof a === "string" || typeof b === "string") return "—";
  return a + b;
}

/* ── Cell styles ───────────────────────────────── */

const cell = "px-2 py-1 text-xs";
const hdrCell = cn(cell, "font-semibold text-muted-foreground bg-muted/40 text-left");
const valCell = cn(cell, "text-right font-medium tabular-nums");
const sectionLabel =
  "px-2 py-1 text-[10px] font-semibold uppercase tracking-wider bg-muted/20 text-muted-foreground";

/* ── Passenger / Staff Table ───────────────────── */

function PassengerTable({ tree }: { tree: FlightTree }) {
  const v = (id: string) => nv(tree, id);

  const rows = [
    {
      label: "Economy",
      dot: "bg-emerald-500",
      total: v("economy"),
      totalColor: "text-emerald-500",
      pax: v("econPassengers"),
      staff: v("econStaff"),
      m: v("econPaxMale"),
      f: v("econPaxFemale"),
      chd: v("econPaxChildren"),
      inf: v("econPaxInfants"),
    },
    {
      label: "Business",
      dot: "bg-amber-500",
      total: v("business"),
      totalColor: "text-amber-500",
      pax: v("bizPassengers"),
      staff: v("bizStaff"),
      m: v("bizPaxMale"),
      f: v("bizPaxFemale"),
      chd: v("bizPaxChildren"),
      inf: v("bizPaxInfants"),
    },
  ];

  return (
    <div className="rounded-lg border overflow-hidden">
      <table className="w-full">
        <thead>
          <tr className="border-b">
            <th className={hdrCell} />
            <th className={cn(hdrCell, "text-right")}>Total</th>
            <th className={cn(hdrCell, "text-right")}>Pax</th>
            <th className={cn(hdrCell, "text-right")}>Staff</th>
            <th className={cn(hdrCell, "text-right")}>M</th>
            <th className={cn(hdrCell, "text-right")}>F</th>
            <th className={cn(hdrCell, "text-right")}>CHD</th>
            <th className={cn(hdrCell, "text-right")}>INF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-b hover:bg-muted/20">
              <td className={cn(cell, "font-medium")}>
                <span className="inline-flex items-center gap-1.5">
                  <span className={cn("h-2 w-2 rounded-full", r.dot)} />
                  {r.label}
                </span>
              </td>
              <td className={cn(valCell, r.totalColor, "font-bold")}>{r.total}</td>
              <td className={valCell}>{r.pax}</td>
              <td className={valCell}>{r.staff}</td>
              <td className={valCell}>{r.m}</td>
              <td className={valCell}>{r.f}</td>
              <td className={cn(valCell, "text-amber-500")}>{r.chd}</td>
              <td className={cn(valCell, "text-orange-500")}>{r.inf}</td>
            </tr>
          ))}
          <tr className="bg-muted/30">
            <td className={cn(cell, "font-semibold")}>Total</td>
            <td className={cn(valCell, "font-bold")}>{v("root")}</td>
            <td className={valCell}>{add(v("econPassengers"), v("bizPassengers"))}</td>
            <td className={valCell}>{add(v("econStaff"), v("bizStaff"))}</td>
            <td className={valCell}>{add(v("econPaxMale"), v("bizPaxMale"))}</td>
            <td className={valCell}>{add(v("econPaxFemale"), v("bizPaxFemale"))}</td>
            <td className={cn(valCell, "text-amber-500")}>
              {add(v("econPaxChildren"), v("bizPaxChildren"))}
            </td>
            <td className={cn(valCell, "text-orange-500")}>
              {add(v("econPaxInfants"), v("bizPaxInfants"))}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/* ── Crew Table ────────────────────────────────── */

function CrewTable({ tree }: { tree: FlightTree }) {
  const v = (id: string) => nv(tree, id);

  const rows = [
    { label: "Cabin Crew", total: v("cabinCrew"), m: v("cabinCrewMale"), f: v("cabinCrewFemale") },
    { label: "Flight Crew", total: v("flightCrew"), m: v("flightCrewMale"), f: v("flightCrewFemale") },
  ];

  return (
    <div className="rounded-lg border overflow-hidden">
      <div className={sectionLabel}>Crew</div>
      <table className="w-full">
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.label} className={cn("hover:bg-muted/20", i < rows.length - 1 && "border-b")}>
              <td className={cn(cell, "font-medium text-muted-foreground")}>{r.label}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{r.total}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{r.m}</td>
              <td className={cn(valCell, "text-muted-foreground")}>{r.f}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Operational Status Strip ──────────────────── */

function StatusCard({ card }: { card: FlightTreeCard }) {
  return (
    <div className="px-2 py-1.5 text-center">
      <div className="text-[10px] text-muted-foreground leading-tight">{card.label}</div>
      <div className="text-base font-bold leading-snug" style={{ color: card.textColor }}>
        {card.value}
      </div>
      {card.subLabel && (
        <div className="text-[9px] text-muted-foreground leading-tight">{card.subLabel}</div>
      )}
    </div>
  );
}

function StatusStrip({ cards }: { cards: FlightTreeCard[] }) {
  if (!cards.length) return null;
  return (
    <div className="rounded-lg border overflow-hidden">
      <div className={sectionLabel}>Status</div>
      <div className="grid grid-cols-5 divide-x">
        {cards.map((c) => (
          <StatusCard key={c.id} card={c} />
        ))}
      </div>
    </div>
  );
}

/* ── Main export ───────────────────────────────── */

interface PaxMatrixProps {
  tree: FlightTree;
  className?: string;
}

export function PaxMatrix({ tree, className }: PaxMatrixProps) {
  return (
    <div className={cn("space-y-2", className)}>
      <PassengerTable tree={tree} />
      <CrewTable tree={tree} />
      <StatusStrip cards={tree.statusCards} />
    </div>
  );
}
