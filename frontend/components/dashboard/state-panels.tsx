import type { FlightDashboard } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { ChevronDown } from "lucide-react";

interface Props {
  stateSummary: FlightDashboard["stateSummary"];
}

export function StatePanels({ stateSummary }: Props) {
  const { booked, checkedIn, boarded, others } = stateSummary;
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {/* BOOKED */}
      <PanelCard
        title="BOOKED"
        titleClass="text-muted-foreground"
        count={booked.totalSouls}
        rows={[
          { label: "Not Checked-In", value: booked.totalSouls },
          { label: "Business", value: booked.business, labelClass: "text-blue-400" },
          { label: "Economy", value: booked.economy, labelClass: "text-emerald-400" },
        ]}
        footer={{ adults: booked.adults, children: booked.children, infants: booked.infants }}
      />

      {/* CHECKED-IN */}
      <PanelCard
        title="CHECKED-IN"
        titleClass="text-amber-400"
        count={checkedIn.totalSouls}
        rows={[
          { label: "Total Checked-in", value: checkedIn.totalSouls },
          { label: "Business", value: checkedIn.business, labelClass: "text-blue-400" },
          { label: "Economy", value: checkedIn.economy, labelClass: "text-emerald-400" },
        ]}
        footer={{ adults: checkedIn.adults, children: checkedIn.children, infants: checkedIn.infants }}
      />

      {/* BOARDED */}
      <PanelCard
        title="BOARDED"
        titleClass="text-destructive"
        count={boarded.totalSouls}
        rows={[
          {
            label: "Total Boarded",
            value: boarded.totalSouls,
            valueClass: "text-destructive",
          },
          { label: "Business", value: boarded.business, labelClass: "text-blue-400" },
          { label: "Economy", value: boarded.economy, labelClass: "text-emerald-400" },
        ]}
        footer={{
          adults: boarded.adults,
          children: boarded.children,
          infants: boarded.infants,
        }}
      />

      {/* OTHERS */}
      <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
        <CardContent className="px-4 py-3">
          <div className="flex items-center justify-between mb-3 pb-2 border-b border-border">
            <span className="text-[10px] font-bold tracking-[0.22em] uppercase text-slate-300">
              OTHERS
            </span>
            <div className="flex items-center gap-2">
              <span className="text-lg font-bold tabular-nums">
                  {others.jumpSeat + others.nonRevenue}
              </span>
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
          </div>
          <Row label="Jump Seat" value={others.jumpSeat} valueClass="text-emerald-400" />
          <Row label="Non-Revenue" value={others.nonRevenue} valueClass="text-emerald-400" />
          <Row label="Offloaded" value={others.offloaded} valueClass="text-slate-300" unavailable={!others.offloadedAvailable} />
          <Row label="No Show" value={others.noShow} valueClass="text-slate-300" unavailable={!others.noShowAvailable} />
          <div className="mt-3 border-t border-border pt-2 text-[10px] text-muted-foreground">
            Live REST response
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function PanelCard({
  title,
  titleClass,
  count,
  rows,
  footer,
}: {
  title: string;
  titleClass: string;
  count: number;
  rows: { label: string; value: number; labelClass?: string; valueClass?: string }[];
  footer: { adults: number; children: number; infants: number };
}) {
  return (
    <Card className="border-white/10 bg-white/[0.035] shadow-[0_14px_40px_rgba(0,0,0,0.24)]">
      <CardContent className="px-4 py-3">
        <div className="flex items-center justify-between mb-3 pb-2 border-b border-border">
          <span
            className={`text-[10px] font-bold tracking-[0.22em] uppercase ${titleClass}`}
          >
            {title}
          </span>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold tabular-nums">{count}</span>
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
        </div>
        {rows.map((r) => (
          <Row key={r.label} {...r} />
        ))}
        <div className="mt-3 flex gap-3 border-t border-border pt-2 text-[10px] text-muted-foreground">
          <span>
            Adults: <b className="text-emerald-400">{footer.adults}</b>
          </span>
          <span>
            Children: <b className="text-purple-400">{footer.children}</b>
          </span>
          <span>
            Infants: <b className="text-amber-400">{footer.infants}</b>
          </span>
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
}: {
  label: string;
  value: number | null;
  labelClass?: string;
  valueClass?: string;
  unavailable?: boolean;
}) {
  return (
    <div className="flex justify-between py-0.5 text-sm">
      <span className={labelClass ?? "text-muted-foreground"}>{label}</span>
      <span className={`font-semibold tabular-nums ${valueClass ?? ""}`}>
        {unavailable ? "N/A" : (value ?? 0)}
      </span>
    </div>
  );
}
