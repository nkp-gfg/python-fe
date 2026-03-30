"use client";

import type { FlightDashboard, FlightPhaseCode, CabinDetail, StateBucket } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Users, UserCheck, PlaneTakeoff, Info, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TileInfoKey } from "@/components/dashboard/tile-info-panel";

export type StateCardKey = "booked" | "checkedIn" | "boarded" | "others";

/** Describes which filters to apply when a number is clicked */
export interface PanelFilter {
  status?: "all" | "booked" | "checkedIn" | "boarded";
  cabin?: "all" | "Y" | "J";
  type?: "all" | "revenue" | "nonRevenue" | "child" | "infant";
}

interface Props {
  stateSummary: FlightDashboard["stateSummary"];
  phase: FlightPhaseCode;
  focusCard: StateCardKey;
  onInfoClick: (key: TileInfoKey) => void;
  onCardClick?: (key: StateCardKey) => void;
  activeCard?: StateCardKey | null;
  onFilterClick?: (filter: PanelFilter) => void;
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

/* ── Compute footer totals from per-cabin detail ─────────── */
function detailFooter(bucket: StateBucket) {
  const ed = bucket.economyDetail ?? { adults: 0, children: 0, infants: 0, staff: 0 };
  const bd = bucket.businessDetail ?? { adults: 0, children: 0, infants: 0, staff: 0 };
  return {
    adults: ed.adults + bd.adults,
    children: ed.children + bd.children,
    infants: ed.infants + bd.infants,
    staff: ed.staff + bd.staff,
  };
}

export function StatePanels({ stateSummary, phase, focusCard, onInfoClick, onCardClick, activeCard, onFilterClick }: Props) {
  if (!stateSummary) return null;
  const { booked, checkedIn, boarded, others } = stateSummary;
  if (!booked || !checkedIn || !boarded || !others) return null;
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
          count={booked.totalSouls}
          countClass={bookedCountColor(phase, booked.totalPassengers)}
          rows={[]}
          cabinDetail={{
            economy: { detail: booked.economyDetail },
            business: { detail: booked.businessDetail },
          }}
          footer={detailFooter(booked)}
          onInfoClick={() => onInfoClick("booked")}
          onClick={() => onCardClick?.("booked")}
          active={activeCard === "booked"}
          focusRing={focusCard === "booked" ? FOCUS_RING.booked : undefined}
          dimmed={focusCard !== "booked" && phase !== "SCHEDULED"}
          statusFilter="booked"
          onFilterClick={onFilterClick}
        />

        {/* CHECKED-IN */}
        <PanelCard
          title="Checked-In"
          icon={<UserCheck className="h-4 w-4 text-amber-500" />}
          count={checkedIn.totalSouls}
          rows={[
            {
              label: extra.label,
              value: extra.value,
              labelClass: extra.labelClass,
              valueClass: extra.valueClass,
            },
          ]}
          cabinDetail={{
            economy: { detail: checkedIn.economyDetail },
            business: { detail: checkedIn.businessDetail },
          }}
          footer={detailFooter(checkedIn)}
          onInfoClick={() => onInfoClick("checkedIn")}
          onClick={() => onCardClick?.("checkedIn")}
          active={activeCard === "checkedIn"}
          focusRing={focusCard === "checkedIn" ? FOCUS_RING.checkedIn : undefined}
          dimmed={focusCard !== "checkedIn" && phase !== "SCHEDULED"}
          statusFilter="checkedIn"
          onFilterClick={onFilterClick}
        />

        {/* BOARDED */}
        <PanelCard
          title="Boarded"
          icon={<PlaneTakeoff className="h-4 w-4 text-emerald-500" />}
          count={boarded.totalSouls}
          rows={[]}
          cabinDetail={{
            economy: { detail: boarded.economyDetail },
            business: { detail: boarded.businessDetail },
          }}
          footer={detailFooter(boarded)}
          onInfoClick={() => onInfoClick("boarded")}
          onClick={() => onCardClick?.("boarded")}
          active={activeCard === "boarded"}
          focusRing={focusCard === "boarded" ? FOCUS_RING.boarded : undefined}
          dimmed={focusCard !== "boarded" && phase !== "SCHEDULED"}
          statusFilter="boarded"
          onFilterClick={onFilterClick}
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
                  <ClickableValue
                    value={others.jumpSeat + others.nonRevenue}
                    className="text-xl font-bold tracking-tight"
                    onClick={() => onFilterClick?.({ type: "nonRevenue" })}
                  />
                  <InfoButton onClick={() => onInfoClick("others")} />
                </div>
              </div>
              <div className="space-y-1">
                <ClickableRow
                  label="Jump Seat"
                  value={others.jumpSeat}
                  onClick={() => onFilterClick?.({ type: "nonRevenue" })}
                />
                <ClickableRow
                  label="Non-Revenue"
                  value={others.nonRevenue}
                  onClick={() => onFilterClick?.({ type: "nonRevenue" })}
                />
                <ClickableRow
                  label="Offloaded"
                  value={others.offloadedAvailable ? others.offloaded : others.checkedInNotBoarded}
                  valueClass={(others.offloadedAvailable ? (others.offloaded ?? 0) : others.checkedInNotBoarded) > 0 ? "text-rose-500 font-semibold" : undefined}
                  inferred={!others.offloadedAvailable}
                  onClick={() => onFilterClick?.({ status: "checkedIn" })}
                />
                <ClickableRow
                  label={others.flightClosed ? "No Show" : "Not Checked In"}
                  value={others.noShowAvailable ? others.noShow : others.notCheckedIn}
                  valueClass={(others.noShowAvailable ? (others.noShow ?? 0) : others.notCheckedIn) > 0 ? "text-orange-500 dark:text-orange-400 font-semibold" : undefined}
                  inferred={!others.noShowAvailable}
                  onClick={() => onFilterClick?.({ status: "booked" })}
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

/** A clickable numeric value that applies a filter on click */
function ClickableValue({
  value,
  className: cls,
  onClick,
}: {
  value: number | string;
  className?: string;
  onClick?: () => void;
}) {
  if (!onClick) return <span className={cls}>{value}</span>;
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      className={cn(
        cls,
        "hover:underline hover:decoration-dotted hover:underline-offset-2 cursor-pointer transition-opacity hover:opacity-80",
      )}
      title="Click to filter passengers"
    >
      {value}
    </button>
  );
}

/** A row with a clickable value */
function ClickableRow({
  label,
  value,
  labelClass,
  valueClass,
  unavailable,
  inferred,
  onClick,
}: {
  label: string;
  value: number | string | null;
  labelClass?: string;
  valueClass?: string;
  unavailable?: boolean;
  inferred?: boolean;
  onClick?: () => void;
}) {
  const display = unavailable ? "N/A" : (value ?? 0);
  return (
    <div className="flex items-center justify-between text-xs">
      <span className={labelClass ?? "text-muted-foreground"}>{label}</span>
      <span className={cn("font-medium", valueClass ?? "text-foreground")}>
        {onClick && !unavailable ? (
          <ClickableValue value={display} onClick={onClick} />
        ) : (
          display
        )}
        {inferred && <span className="text-[9px] text-muted-foreground ml-1" title="Inferred from manifest data">~</span>}
      </span>
    </div>
  );
}

function PanelCard({
  title,
  icon,
  count,
  countClass,
  rows,
  cabinDetail,
  footer,
  onInfoClick,
  onClick,
  active,
  focusRing,
  dimmed,
  statusFilter,
  onFilterClick,
}: {
  title: string;
  icon: React.ReactNode;
  count: number;
  countClass?: string;
  rows: { label: string; value: number | string | null; labelClass?: string; valueClass?: string; unavailable?: boolean }[];
  cabinDetail?: {
    economy: { detail?: CabinDetail };
    business: { detail?: CabinDetail };
  };
  footer: { adults: number; children: number; infants: number; staff?: number };
  onInfoClick: () => void;
  onClick?: () => void;
  active?: boolean;
  focusRing?: string;
  dimmed?: boolean;
  statusFilter?: "booked" | "checkedIn" | "boarded";
  onFilterClick?: (filter: PanelFilter) => void;
}) {
  const fire = (extra: Partial<PanelFilter>) => {
    onFilterClick?.({ status: statusFilter ?? "all", ...extra });
  };

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
            <ClickableValue
              value={count}
              className={cn("text-xl font-bold tracking-tight", countClass)}
              onClick={() => fire({})}
            />
            <InfoButton onClick={onInfoClick} />
          </div>
        </div>
        <div className="space-y-1 mb-2">
          {rows.map((r) => (
            <Row key={r.label} {...r} />
          ))}
        </div>
        {/* Cabin breakdown with per-cabin detail — clickable */}
        {cabinDetail && (
          <div className="space-y-1.5 mb-2">
            <CabinRow label="Economy" detail={cabinDetail.economy.detail} onFilterClick={(extra) => fire({ cabin: "Y", ...extra })} />
            <CabinRow label="Business" detail={cabinDetail.business.detail} onFilterClick={(extra) => fire({ cabin: "J", ...extra })} />
          </div>
        )}
        <div className="mt-auto grid grid-cols-4 gap-1.5 border-t pt-2 text-[10px]">
          <div className="flex flex-col">
            <span className="text-muted-foreground">Adults</span>
            <ClickableValue value={footer.adults} className="font-medium text-foreground" onClick={() => fire({})} />
          </div>
          <div className="flex flex-col">
            <span className="text-muted-foreground">Children</span>
            <ClickableValue value={footer.children} className="font-medium text-amber-600 dark:text-amber-500" onClick={() => fire({ type: "child" })} />
          </div>
          <div className="flex flex-col">
            <span className="text-muted-foreground">Infants</span>
            <ClickableValue value={footer.infants} className="font-medium text-emerald-600 dark:text-emerald-500" onClick={() => fire({ type: "infant" })} />
          </div>
          <div className="flex flex-col">
            <span className="text-muted-foreground">Staff</span>
            <ClickableValue value={footer.staff ?? 0} className="font-medium text-purple-600 dark:text-purple-400" onClick={() => fire({ type: "nonRevenue" })} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function CabinRow({ label, detail, onFilterClick }: { label: string; detail?: CabinDetail; onFilterClick?: (extra: Partial<PanelFilter>) => void }) {
  const d = detail ?? { adults: 0, children: 0, infants: 0, staff: 0 };
  const total = d.adults + d.children + d.infants + d.staff;
  return (
    <div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground font-medium">{label}</span>
        <ClickableValue value={total} className="font-semibold text-foreground" onClick={() => onFilterClick?.({})} />
      </div>
      <div className="text-[10px] text-muted-foreground pl-2 mt-0.5">
        <ClickableValue value={d.adults} onClick={() => onFilterClick?.({})} /> Adults ·{" "}
        <ClickableValue value={d.children} onClick={() => onFilterClick?.({ type: "child" })} /> Child ·{" "}
        <ClickableValue value={d.infants} onClick={() => onFilterClick?.({ type: "infant" })} /> Infant ·{" "}
        <ClickableValue value={d.staff} onClick={() => onFilterClick?.({ type: "nonRevenue" })} /> Staff
      </div>
    </div>
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
