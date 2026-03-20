import type { FlightDashboard } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";

interface Props {
  overview: FlightDashboard["overview"];
  passengerSummary: FlightDashboard["passengerSummary"];
  analysis: FlightDashboard["analysis"];
}

export function StatCards({ passengerSummary: ps, overview, analysis }: Props) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <StatCard
        value={ps.totalPassengers}
        label="Total Pax"
        sub={`${ps.totalSouls} souls (incl. ${ps.infantCount} INF)`}
        accent="text-foreground"
      />
      <StatCard
        value={analysis.economy.total}
        label="Economy"
        sub={`Auth: ${ps.cabinSummary.find((c) => c.cabin === "Y")?.authorized ?? "—"}`}
        accent="text-emerald-400"
      />
      <StatCard
        value={analysis.business.total}
        label="Business"
        sub={`Auth: ${ps.cabinSummary.find((c) => c.cabin === "J")?.authorized ?? "—"}`}
        accent="text-amber-400"
      />
    </div>
  );
}

function StatCard({
  value,
  label,
  sub,
  accent,
}: {
  value: number;
  label: string;
  sub: string;
  accent: string;
}) {
  return (
    <Card className="overflow-hidden border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] shadow-[0_14px_40px_rgba(0,0,0,0.22)]">
      <CardContent className="relative py-6 text-center">
        <div className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-white/14 to-transparent" />
        <div className={`text-4xl font-semibold tabular-nums ${accent}`}>
          {value}
        </div>
        <div className="mt-1 text-[11px] uppercase tracking-[0.22em] text-muted-foreground">{label}</div>
        <div className="mt-1 text-[11px] text-muted-foreground/70">
          {sub}
        </div>
      </CardContent>
    </Card>
  );
}
