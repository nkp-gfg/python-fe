"use client";

import { BarChart3, Briefcase, Clock3, Target, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export function ExecutiveValueFramework() {
  const baseline = [
    "Decision latency (median/p90): manual inventory analysis to action",
    "Manual effort hours/week for availability insights",
    "Late intervention rate on constrained flights",
    "Reaccommodation decision turnaround time",
  ];

  const targets = [
    "-30% median decision latency",
    "-40% manual analyst effort",
    "-20% late interventions",
    ">=98% availability API success rate",
  ];

  const pilot = [
    "2 high-volume routes + 1 disruption-prone route",
    "Commercial control + OCC + airport duty control",
    "6-week active pilot with weekly KPI governance",
  ];

  return (
    <Card className="shadow-sm border-sky-200/70 bg-gradient-to-br from-sky-50/70 to-white dark:from-slate-900/50 dark:to-slate-950">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <div className="rounded-md bg-sky-500/15 p-1.5">
            <Briefcase className="h-4 w-4 text-sky-600" />
          </div>
          <CardTitle className="text-sm">Executive Value Framework</CardTitle>
          <Badge variant="outline" className="ml-auto text-[10px]">90-Day Plan</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-3">
        <section className="rounded-md border bg-card/70 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
            <Clock3 className="h-3.5 w-3.5 text-amber-500" />
            Baseline Metrics
          </div>
          <ul className="space-y-1 text-[11px] text-muted-foreground">
            {baseline.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </section>

        <section className="rounded-md border bg-card/70 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
            <Target className="h-3.5 w-3.5 text-emerald-500" />
            90-Day Targets
          </div>
          <ul className="space-y-1 text-[11px] text-muted-foreground">
            {targets.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </section>

        <section className="rounded-md border bg-card/70 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
            <TrendingUp className="h-3.5 w-3.5 text-blue-500" />
            Pilot Scope
          </div>
          <ul className="space-y-1 text-[11px] text-muted-foreground">
            {pilot.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
          <div className="mt-2 rounded border border-dashed bg-muted/40 p-2 text-[10px] text-muted-foreground">
            Source: knowledge_base/multiflight_value_framework.md
          </div>
        </section>
      </CardContent>
      <div className="px-6 pb-4 text-[10px] text-muted-foreground flex items-center gap-1.5">
        <BarChart3 className="h-3 w-3" />
        Use this panel in leadership reviews to track adoption and measurable ROI.
      </div>
    </Card>
  );
}
