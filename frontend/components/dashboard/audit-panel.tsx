"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  AlertCircle,
  Info,
  Loader2,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  Filter,
  X,
} from "lucide-react";
import { fetchAudit } from "@/lib/api";
import type { AuditAlert, AuditSeverity } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface AuditPanelProps {
  flightNumber: string;
  origin: string;
  date: string;
  onSelectPassenger?: (pnr: string) => void;
}

type FilterSeverity = "all" | AuditSeverity;
type FilterRule = "all" | string;

const SEVERITY_CONFIG: Record<AuditSeverity, {
  icon: typeof AlertCircle;
  color: string;
  bgColor: string;
  borderColor: string;
  label: string;
  badgeVariant: string;
}> = {
  critical: {
    icon: AlertCircle,
    color: "text-red-600 dark:text-red-400",
    bgColor: "bg-red-50 dark:bg-red-950/30",
    borderColor: "border-red-200 dark:border-red-800",
    label: "Critical",
    badgeVariant: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  },
  warning: {
    icon: AlertTriangle,
    color: "text-amber-600 dark:text-amber-400",
    bgColor: "bg-amber-50 dark:bg-amber-950/30",
    borderColor: "border-amber-200 dark:border-amber-800",
    label: "Warning",
    badgeVariant: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  },
  info: {
    icon: Info,
    color: "text-blue-600 dark:text-blue-400",
    bgColor: "bg-blue-50 dark:bg-blue-950/30",
    borderColor: "border-blue-200 dark:border-blue-800",
    label: "Info",
    badgeVariant: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  },
};

const RULE_LABELS: Record<string, string> = {
  UPGRADE_DESIRED_NOT_PROCESSED: "Upgrade Not Processed",
  BOARDED_WITHOUT_CHECKIN: "Boarded Without Check-In",
  BOARDED_WITHOUT_BP: "Boarded Without Boarding Pass",
  CABIN_OVERCAPACITY: "Cabin Overcapacity",
  DUPLICATE_SEAT: "Duplicate Seat Assignment",
  CABIN_AVAILABLE_MISMATCH: "Cabin Available Mismatch",
  BOARDED_WITHOUT_DOCS: "Boarded Without DOCS",
  CHECKEDIN_NO_BP: "Checked-In Without BP",
  PARTY_SIZE_MISMATCH: "Party Size Mismatch",
  UPGRADE_QUEUE_SKIPPED: "Upgrade Queue Skipped",
  INFORMAL_CABIN_MOVE: "Informal Cabin Move",
  STAFF_PREMIUM_NO_UPGRADE: "Staff in Premium (No Upgrade)",
};

function AlertRow({ alert, onSelectPassenger }: { alert: AuditAlert; onSelectPassenger?: (pnr: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const config = SEVERITY_CONFIG[alert.severity];
  const Icon = config.icon;

  return (
    <div className={cn("border rounded-lg", config.borderColor, config.bgColor)}>
      <button
        className="flex items-start gap-3 w-full px-3 py-2.5 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <Icon className={cn("h-4 w-4 mt-0.5 shrink-0", config.color)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={cn("text-xs font-semibold", config.badgeVariant, "px-1.5 py-0.5 rounded")}>
              {RULE_LABELS[alert.ruleId] || alert.ruleId}
            </span>
            {alert.pnr && (
              <button
                className="text-xs font-mono text-primary hover:underline"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectPassenger?.(alert.pnr!);
                }}
              >
                {alert.pnr}
              </button>
            )}
            {alert.passengerName && (
              <span className="text-xs text-muted-foreground truncate">{alert.passengerName}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">{alert.message}</p>
        </div>
        {alert.details && (
          <span className="shrink-0 text-muted-foreground">
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </span>
        )}
      </button>
      {expanded && alert.details && (
        <div className="px-3 pb-2.5 ml-7">
          <div className="text-xs bg-background/60 rounded p-2 space-y-1 font-mono">
            {Object.entries(alert.details).map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <span className="text-muted-foreground shrink-0">{key}:</span>
                <span className="text-foreground break-all">
                  {typeof value === "object" ? JSON.stringify(value) : String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function AuditPanel({ flightNumber, origin, date, onSelectPassenger }: AuditPanelProps) {
  const [filterSeverity, setFilterSeverity] = useState<FilterSeverity>("all");
  const [filterRule, setFilterRule] = useState<FilterRule>("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["audit", flightNumber, origin, date],
    queryFn: () => fetchAudit(flightNumber, origin, date),
    enabled: Boolean(flightNumber),
    refetchInterval: 60_000,
  });

  const uniqueRules = useMemo(() => {
    if (!data) return [];
    const rules = [...new Set(data.alerts.map((a) => a.ruleId))];
    return rules.sort();
  }, [data]);

  const filteredAlerts = useMemo(() => {
    if (!data) return [];
    return data.alerts.filter((a) => {
      if (filterSeverity !== "all" && a.severity !== filterSeverity) return false;
      if (filterRule !== "all" && a.ruleId !== filterRule) return false;
      return true;
    });
  }, [data, filterSeverity, filterRule]);

  // Group alerts by severity for display
  const groupedAlerts = useMemo(() => {
    const groups: Record<AuditSeverity, AuditAlert[]> = { critical: [], warning: [], info: [] };
    for (const a of filteredAlerts) {
      groups[a.severity].push(a);
    }
    return groups;
  }, [filteredAlerts]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground gap-2">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>Running audit rules...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20 text-red-500 gap-2">
        <AlertCircle className="h-5 w-5" />
        <span>Failed to load audit: {(error as Error).message}</span>
      </div>
    );
  }

  if (!data) return null;

  const hasFilters = filterSeverity !== "all" || filterRule !== "all";

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-3">
        <Card className={cn(
          "cursor-pointer transition-colors",
          filterSeverity === "all" && "ring-2 ring-primary"
        )} onClick={() => setFilterSeverity("all")}>
          <CardContent className="p-3 text-center">
            <ShieldAlert className="h-5 w-5 mx-auto mb-1 text-muted-foreground" />
            <p className="text-2xl font-bold">{data.totalAlerts}</p>
            <p className="text-[10px] text-muted-foreground">Total Issues</p>
          </CardContent>
        </Card>
        {(["critical", "warning", "info"] as const).map((sev) => {
          const config = SEVERITY_CONFIG[sev];
          const Icon = config.icon;
          const count = data.summary[sev] ?? 0;
          return (
            <Card
              key={sev}
              className={cn(
                "cursor-pointer transition-colors",
                filterSeverity === sev && "ring-2 ring-primary",
                count === 0 && "opacity-50"
              )}
              onClick={() => setFilterSeverity(filterSeverity === sev ? "all" : sev)}
            >
              <CardContent className="p-3 text-center">
                <Icon className={cn("h-5 w-5 mx-auto mb-1", config.color)} />
                <p className={cn("text-2xl font-bold", count > 0 && config.color)}>{count}</p>
                <p className="text-[10px] text-muted-foreground">{config.label}</p>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="h-3.5 w-3.5 text-muted-foreground" />
        <select
          className="text-xs border rounded px-2 py-1 bg-background"
          value={filterRule}
          onChange={(e) => setFilterRule(e.target.value)}
        >
          <option value="all">All Rules</option>
          {uniqueRules.map((r) => (
            <option key={r} value={r}>{RULE_LABELS[r] || r}</option>
          ))}
        </select>
        {hasFilters && (
          <button
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-0.5"
            onClick={() => { setFilterSeverity("all"); setFilterRule("all"); }}
          >
            <X className="h-3 w-3" /> Clear
          </button>
        )}
        <span className="text-xs text-muted-foreground ml-auto">
          Showing {filteredAlerts.length} of {data.totalAlerts}
        </span>
      </div>

      {/* No issues */}
      {data.totalAlerts === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-2">
          <ShieldAlert className="h-8 w-8 text-emerald-500" />
          <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">All Clear</p>
          <p className="text-xs">No process violations detected for this flight.</p>
        </div>
      )}

      {/* Alerts grouped by severity */}
      {(["critical", "warning", "info"] as const).map((sev) => {
        const alerts = groupedAlerts[sev];
        if (alerts.length === 0) return null;
        const config = SEVERITY_CONFIG[sev];
        return (
          <div key={sev} className="space-y-2">
            <div className="flex items-center gap-2">
              <config.icon className={cn("h-3.5 w-3.5", config.color)} />
              <h3 className={cn("text-xs font-semibold", config.color)}>
                {config.label} ({alerts.length})
              </h3>
            </div>
            <div className="space-y-1.5">
              {alerts.map((alert, i) => (
                <AlertRow key={`${alert.ruleId}-${alert.pnr}-${i}`} alert={alert} onSelectPassenger={onSelectPassenger} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
