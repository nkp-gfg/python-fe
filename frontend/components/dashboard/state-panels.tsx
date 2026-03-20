"use client";

import type { FlightDashboard } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Users, UserCheck, PlaneTakeoff, Info } from "lucide-react";
import type { TileInfoKey } from "@/components/dashboard/tile-info-panel";

export type StateCardKey = "booked" | "checkedIn" | "boarded" | "others";

interface Props {
  stateSummary: FlightDashboard["stateSummary"];
  onInfoClick: (key: TileInfoKey) => void;
  onCardClick?: (key: StateCardKey) => void;
  activeCard?: StateCardKey | null;
}

export function StatePanels({ stateSummary, onInfoClick, onCardClick, activeCard }: Props) {
  const { booked, checkedIn, boarded, others } = stateSummary;

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {/* BOOKED */}
        <PanelCard
          title="Booked"
          icon={<Users className="h-4 w-4 text-blue-500" />}
          count={booked.totalPassengers}
          soulsCount={booked.totalSouls}
          rows={[
            { label: "Total Booked", value: booked.totalPassengers },
            { label: "Business", value: booked.business },
            { label: "Economy", value: booked.economy },
          ]}
          footer={{ adults: booked.adults, children: booked.children, infants: booked.infants }}
          onInfoClick={() => onInfoClick("booked")}
          onClick={() => onCardClick?.("booked")}
          active={activeCard === "booked"}
        />

        {/* CHECKED-IN */}
        <PanelCard
          title="Checked-In"
          icon={<UserCheck className="h-4 w-4 text-amber-500" />}
          count={checkedIn.totalPassengers}
          soulsCount={checkedIn.totalSouls}
          rows={[
            { label: "Total Checked-in", value: checkedIn.totalPassengers },
            {
              label: others.flightClosed ? "No Show" : "Not Checked In",
              value: others.notCheckedIn,
              labelClass: others.notCheckedIn > 0 ? "text-orange-500 dark:text-orange-400" : undefined,
              valueClass: others.notCheckedIn > 0 ? "text-orange-500 dark:text-orange-400 font-semibold" : "text-foreground",
            },
            { label: "Business", value: checkedIn.business },
            { label: "Economy", value: checkedIn.economy },
          ]}
          footer={{ adults: checkedIn.adults, children: checkedIn.children, infants: checkedIn.infants }}
          onInfoClick={() => onInfoClick("checkedIn")}
          onClick={() => onCardClick?.("checkedIn")}
          active={activeCard === "checkedIn"}
        />

        {/* BOARDED */}
        <PanelCard
          title="Boarded"
          icon={<PlaneTakeoff className="h-4 w-4 text-emerald-500" />}
          count={boarded.totalPassengers}
          soulsCount={boarded.totalSouls}
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
        />

        {/* OTHERS */}
        <Card
          className={`shadow-sm cursor-pointer transition-all hover:ring-1 hover:ring-primary/30 ${activeCard === "others" ? "ring-2 ring-primary" : ""}`}
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
  soulsCount,
  rows,
  footer,
  onInfoClick,
  onClick,
  active,
}: {
  title: string;
  icon: React.ReactNode;
  count: number;
  soulsCount?: number;
  rows: { label: string; value: number | null; labelClass?: string; valueClass?: string; unavailable?: boolean }[];
  footer: { adults: number; children: number; infants: number };
  onInfoClick: () => void;
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <Card
      className={`shadow-sm flex flex-col justify-between cursor-pointer transition-all hover:ring-1 hover:ring-primary/30 ${active ? "ring-2 ring-primary" : ""}`}
      onClick={onClick}
    >
      <CardContent className="p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5 text-muted-foreground font-medium text-xs">
            {icon}
            <span>{title}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xl font-bold tracking-tight">{count}</span>
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
  value: number | null;
  labelClass?: string;
  valueClass?: string;
  unavailable?: boolean;
  inferred?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className={labelClass ?? "text-muted-foreground"}>{label}</span>
      <span className={`font-medium ${valueClass ?? "text-foreground"}`}>
        {unavailable ? "N/A" : (value ?? 0)}
        {inferred && <span className="text-[9px] text-muted-foreground ml-1" title="Inferred from manifest data">~</span>}
      </span>
    </div>
  );
}
