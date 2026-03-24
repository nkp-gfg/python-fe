"use client";

import type { FlightDashboard, FlightPhaseCode } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Users, UserCheck, PlaneTakeoff, Info, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TileInfoKey } from "@/components/dashboard/tile-info-panel";

export type StateCardKey = "booked" | "checkedIn" | "boarded" | "others";

interface Props {
  stateSummary: FlightDashboard["stateSummary"];
  phase: FlightPhaseCode;
  focusCard: StateCardKey;
  onInfoClick: (key: TileInfoKey) => void;
  onCardClick?: (key: StateCardKey) => void;
  activeCard?: StateCardKey | null;
}

/* ── Phase-aware card emphasis ring colors ───────────────── */
const FOCUS_RING: Record<StateCardKey, string> = {
  booked: "ring-2 ring-blue-500/50 shadow-blue-500/10 shadow-lg",
  checkedIn: "ring-2 ring-amber-500/50 shadow-amber-500/10 shadow-lg",
  boarded: "ring-2 ring-emerald-500/50 shadow-emerald-500/10 shadow-lg",
  others: "ring-2 ring-red-500/50 shadow-red-500/10 shadow-lg",
};

/* ── Dimmed style for non-focus cards ────────────────────── */
const DIM = "opacity-60";

/* ── Phase-aware label morphing for Booked card ──────────── */
function bookedTitle(phase: FlightPhaseCode): string {
  switch (phase) {
    case "CHECK_IN": return "Awaiting Check-in";
    case "BOARDING": return "Not Checked In";
    case "CLOSED":
    case "DEPARTED": return "No Show";
    default: return "Booked";
  }
}

function bookedIconColor(phase: FlightPhaseCode): string {
  switch (phase) {
    case "CHECK_IN": return "text-blue-500";
    case "BOARDING": return "text-amber-500";
    case "CLOSED":
    case "DEPARTED": return "text-red-500";
    default: return "text-blue-500";
  }
}

function bookedCountColor(phase: FlightPhaseCode, count: number): string {
  if (count === 0) return "";
  switch (phase) {
    case "CLOSED":
    case "DEPARTED": return "text-red-500";
    case "BOARDING": return "text-amber-500";
    default: return "";
  }
}

/* ── Phase-aware: what extra row to show in Checked-In card ── */
function checkedInExtraRow(
  phase: FlightPhaseCode,
  others: FlightDashboard["stateSummary"]["others"],
  totalPax: number,
  checkedInCount: number,
  boardedCount: number,
): { label: string; value: number | string; labelClass?: string; valueClass?: string } {
  switch (phase) {
    case "CHECK_IN":
      return {
        label: "Pending check-in",
        value: others.notCheckedIn,
        labelClass: others.notCheckedIn > 0 ? "text-blue-500 dark:text-blue-400" : undefined,
        valueClass: others.notCheckedIn > 0 ? "text-blue-500 dark:text-blue-400 font-semibold" : "text-foreground",
      };
    case "BOARDING":
      return {
        label: "Checked-in, not boarded",
        value: others.checkedInNotBoarded,
        labelClass: others.checkedInNotBoarded > 0 ? "text-amber-500 dark:text-amber-400" : undefined,
        valueClass: others.checkedInNotBoarded > 0 ? "text-amber-500 dark:text-amber-400 font-semibold" : "text-foreground",
      };
    case "CLOSED":
    case "DEPARTED": {
      const rate = totalPax > 0 ? Math.round(((checkedInCount + boardedCount) / totalPax) * 100) : 0;
      return {
        label: "Check-in rate",
        value: `${rate}%`,
        valueClass: rate === 100 ? "text-emerald-500 font-semibold" : "text-foreground font-semibold",
      };
    }
    default:
      return { label: "Pending check-in", value: others.notCheckedIn };
  }
}

export function StatePanels({ stateSummary, phase, focusCard, onInfoClick, onCardClick, activeCard }: Props) {
  const { booked, checkedIn, boarded, others } = stateSummary;
  const totalPax = booked.totalPassengers + checkedIn.totalPassengers + boarded.totalPassengers;

  const extra = checkedInExtraRow(phase, others, totalPax, checkedIn.totalPassengers, boarded.totalPassengers);

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {/* BOOKED */}
        <PanelCard
          title={bookedTitle(phase)}
          icon={
            (phase === "CLOSED" || phase === "DEPARTED") && booked.totalPassengers > 0
              ? <AlertTriangle className={cn("h-4 w-4", bookedIconColor(phase))} />
              : <Users className={cn("h-4 w-4", bookedIconColor(phase))} />
          }
          count={booked.totalPassengers}
          countClass={bookedCountColor(phase, booked.totalPassengers)}
          rows={[
            { label: `Total ${bookedTitle(phase)}`, value: booked.totalPassengers },
            { label: "Business", value: booked.business },
            { label: "Economy", value: booked.economy },
          ]}
          footer={{ adults: booked.adults, children: booked.children, infants: booked.infants }}
          onInfoClick={() => onInfoClick("booked")}
          onClick={() => onCardClick?.("booked")}
          active={activeCard === "booked"}
          focusRing={focusCard === "booked" ? FOCUS_RING.booked : undefined}
          dimmed={focusCard !== "booked" && phase !== "SCHEDULED"}
        />

        {/* CHECKED-IN */}
        <PanelCard
          title="Checked-In"
          icon={<UserCheck className="h-4 w-4 text-amber-500" />}
          count={checkedIn.totalPassengers}
          rows={[
            { label: "Total Checked-in", value: checkedIn.totalPassengers },
            {
              label: extra.label,
              value: extra.value,
              labelClass: extra.labelClass,
              valueClass: extra.valueClass,
            },
            { label: "Business", value: checkedIn.business },
            { label: "Economy", value: checkedIn.economy },
          ]}
          footer={{ adults: checkedIn.adults, children: checkedIn.children, infants: checkedIn.infants }}
          onInfoClick={() => onInfoClick("checkedIn")}
          onClick={() => onCardClick?.("checkedIn")}
          active={activeCard === "checkedIn"}
          focusRing={focusCard === "checkedIn" ? FOCUS_RING.checkedIn : undefined}
          dimmed={focusCard !== "checkedIn" && phase !== "SCHEDULED"}
        />

        {/* BOARDED */}
        <PanelCard
          title="Boarded"
          icon={<PlaneTakeoff className="h-4 w-4 text-emerald-500" />}
          count={boarded.totalPassengers}
          rows={[
            {
              label: "Total Boarded",
              value: boarded.totalPassengers,
              valueClass: "text-emerald-600 dark:text-emerald-400 font-semibold",
            },
            { label: "Business", value: boarded.business },
            { label: "Economy", value: boarded.economy },
          ]}
          footer={{
            adults: boarded.adults,
            children: boarded.children,
            infants: boarded.infants,
          }}
          onInfoClick={() => onInfoClick("boarded")}
          onClick={() => onCardClick?.("boarded")}
          active={activeCard === "boarded"}
          focusRing={focusCard === "boarded" ? FOCUS_RING.boarded : undefined}
          dimmed={focusCard !== "boarded" && phase !== "SCHEDULED"}
        />

        {/* OTHERS */}
        <Card
          className={cn(
            "shadow-sm cursor-pointer transition-all hover:ring-1 hover:ring-primary/30",
            activeCard === "others" ? "ring-2 ring-primary" : "",
            focusCard === "others" ? FOCUS_RING.others : "",
            focusCard !== "others" && phase !== "SCHEDULED" ? DIM : "",
          )}
          onClick={() => onCardClick?.("others")}
        >
          <CardContent className="p-3 flex flex-col h-full justify-between">
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5 text-muted-foreground font-medium text-xs">
                  <Info className="h-3.5 w-3.5 text-muted-foreground" />
                  <span>Other Passengers</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-xl font-bold tracking-tight">
                    {others.jumpSeat + others.nonRevenue}
                  </span>
                  <InfoButton onClick={() => onInfoClick("others")} />
                </div>
              </div>
              <div className="space-y-1">
                <Row label="Jump Seat" value={others.jumpSeat} />
                <Row label="Non-Revenue" value={others.nonRevenue} />
                <Row
                  label="Offloaded"
                  value={others.offloadedAvailable ? others.offloaded : others.checkedInNotBoarded}
                  valueClass={(others.offloadedAvailable ? (others.offloaded ?? 0) : others.checkedInNotBoarded) > 0 ? "text-rose-500 font-semibold" : undefined}
                  inferred={!others.offloadedAvailable}
                />
                <Row
                  label={others.flightClosed ? "No Show" : "Not Checked In"}
                  value={others.noShowAvailable ? others.noShow : others.notCheckedIn}
                  valueClass={(others.noShowAvailable ? (others.noShow ?? 0) : others.notCheckedIn) > 0 ? "text-orange-500 dark:text-orange-400 font-semibold" : undefined}
                  inferred={!others.noShowAvailable}
                />
              </div>
            </div>
            <div className="mt-2 border-t pt-2 text-[10px] text-muted-foreground">
              {others.noShowAvailable || others.offloadedAvailable
                ? "Sabre manifest + trip reports"
                : "Sabre manifest (GetPassengerListRS)"}
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function InfoButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      className="flex h-6 w-6 items-center justify-center rounded-full border border-muted-foreground/20 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
      title="View calculation details"
    >
      <Info className="h-3 w-3" />
    </button>
  );
}

function PanelCard({
  title,
  icon,
  count,
  countClass,
  rows,
  footer,
  onInfoClick,
  onClick,
  active,
  focusRing,
  dimmed,
}: {
  title: string;
  icon: React.ReactNode;
  count: number;
  countClass?: string;
  rows: { label: string; value: number | string | null; labelClass?: string; valueClass?: string; unavailable?: boolean }[];
  footer: { adults: number; children: number; infants: number };
  onInfoClick: () => void;
  onClick?: () => void;
  active?: boolean;
  focusRing?: string;
  dimmed?: boolean;
}) {
  return (
    <Card
      className={cn(
        "shadow-sm flex flex-col justify-between cursor-pointer transition-all hover:ring-1 hover:ring-primary/30",
        active ? "ring-2 ring-primary" : "",
        focusRing && !active ? focusRing : "",
        dimmed && !active ? DIM : "",
      )}
      onClick={onClick}
    >
      <CardContent className="p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5 text-muted-foreground font-medium text-xs">
            {icon}
            <span>{title}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={cn("text-xl font-bold tracking-tight", countClass)}>{count}</span>
            <InfoButton onClick={onInfoClick} />
          </div>
        </div>
        <div className="space-y-1 mb-2">
          {rows.map((r) => (
            <Row key={r.label} {...r} />
          ))}
        </div>
        <div className="mt-auto grid grid-cols-3 gap-2 border-t pt-2 text-[10px]">
          <div className="flex flex-col">
            <span className="text-muted-foreground">Adults</span>
            <span className="font-medium text-foreground">{footer.adults}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-muted-foreground">Children</span>
            <span className="font-medium text-amber-600 dark:text-amber-500">{footer.children}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-muted-foreground">Infants</span>
            <span className="font-medium text-emerald-600 dark:text-emerald-500">{footer.infants}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  value,
  labelClass,
  valueClass,
  unavailable,
  inferred,
}: {
  label: string;
  value: number | string | null;
  labelClass?: string;
  valueClass?: string;
  unavailable?: boolean;
  inferred?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className={labelClass ?? "text-muted-foreground"}>{label}</span>
      <span className={cn("font-medium", valueClass ?? "text-foreground")}>
        {unavailable ? "N/A" : (value ?? 0)}
        {inferred && <span className="text-[9px] text-muted-foreground ml-1" title="Inferred from manifest data">~</span>}
      </span>
    </div>
  );
}
