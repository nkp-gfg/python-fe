import type { FlightDashboard, SpecialRequestsSummary } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";

interface Props {
  passengerSummary: FlightDashboard["passengerSummary"];
  analysis: FlightDashboard["analysis"];
  specialRequestsSummary?: SpecialRequestsSummary | null;
  departureGate?: string;
}

export function StatCards({ passengerSummary: ps, analysis, specialRequestsSummary, departureGate }: Props) {
  const totalSSR = (specialRequestsSummary?.totalSpecialMeals ?? 0) + (specialRequestsSummary?.totalWheelchairs ?? 0);
  return (
    <div className="grid gap-4 md:grid-cols-4">
      <StatCard
        value={ps?.totalPassengers ?? 0}
        label="Total Pax"
        sub={`${ps?.totalSouls ?? 0} souls (incl. ${ps?.infantCount ?? 0} INF)`}
        accent="text-foreground"
      />
      <StatCard
        value={analysis?.economy?.total ?? 0}
        label="Economy"
        sub={`Auth: ${ps?.cabinSummary?.find((c) => c.cabin === "Y")?.authorized ?? "—"}`}
        accent="text-emerald-600 dark:text-emerald-400"
      />
      <StatCard
        value={analysis?.business?.total ?? 0}
        label="Business"
        sub={`Auth: ${ps?.cabinSummary?.find((c) => c.cabin === "J")?.authorized ?? "—"}`}
        accent="text-amber-600 dark:text-amber-400"
      />
      <StatCard
        value={totalSSR > 0 ? totalSSR : (departureGate || "—")}
        label={totalSSR > 0 ? "Special Requests" : "Gate"}
        sub={totalSSR > 0
          ? `${specialRequestsSummary?.totalSpecialMeals ?? 0} meals, ${specialRequestsSummary?.totalWheelchairs ?? 0} WCHR, ${specialRequestsSummary?.frequentFlyers ?? 0} FF`
          : departureGate ? `Gate ${departureGate}` : "No gate assigned"
        }
        accent={totalSSR > 0 ? "text-sky-600 dark:text-sky-400" : "text-violet-600 dark:text-violet-400"}
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
  value: number | string;
  label: string;
  sub: string;
  accent: string;
}) {
  return (
    <Card className="overflow-hidden border-border dark:border-white/10 bg-card dark:bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] shadow-sm dark:shadow-[0_14px_40px_rgba(0,0,0,0.22)]">
      <CardContent className="relative py-6 text-center">
        <div className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-border dark:via-white/14 to-transparent" />
        <div className={`text-4xl font-semibold tabular-nums leading-tight ${accent}`}>
          {value}
        </div>
        <div className="mt-1 text-[11px] uppercase tracking-[0.22em] text-muted-foreground leading-relaxed">{label}</div>
        <div className="mt-1 text-[11px] text-muted-foreground/70 leading-relaxed">
          {sub}
        </div>
      </CardContent>
    </Card>
  );
}
