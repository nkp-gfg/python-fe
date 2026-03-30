"use client";

import { useMemo } from "react";
import {
  BarChart3,
  Briefcase,
  CreditCard,
  FileCheck,
  Info,
  Luggage,
  Plane,
  Shield,
  Star,
  TrendingUp,
  Users,
  Armchair,
  Baby,
  Accessibility,
  UtensilsCrossed,
  Ticket,
  ArrowUpDown,
  AlertTriangle,
  ClipboardList,
  BookOpen,
  Clock,
  Cpu,
  Globe,
  HeartPulse,
  Map,
  ArrowUpCircle,
  ShieldAlert,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { FlightInsights } from "@/lib/types";
import type { InsightInfoKey } from "@/components/dashboard/insight-info-panel";

/* ── helpers ─────────────────────────────────────────── */

function ProgressBar({ value, max, color = "bg-blue-500" }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
      <div className={cn("h-full transition-all", color)} style={{ width: `${Math.max(2, pct)}%` }} />
    </div>
  );
}

function MiniStat({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div>
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("text-lg font-bold", color)}>{value}</p>
      {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  );
}

function HBarRow({ label, value, max, color = "bg-blue-500" }: { label: string; value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground truncate max-w-[140px]">{label}</span>
        <span className="font-medium tabular-nums">{value} <span className="text-muted-foreground text-[10px]">({pct}%)</span></span>
      </div>
      <ProgressBar value={value} max={max} color={color} />
    </div>
  );
}

function SectionHeader({ icon: Icon, title, onInfo }: { icon: React.ElementType; title: string; onInfo?: () => void }) {
  return (
    <div className="flex items-center gap-1.5 mb-2">
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      <h3 className="text-xs font-semibold flex-1">{title}</h3>
      {onInfo && (
        <button
          type="button"
          onClick={onInfo}
          className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
          title={`How ${title} is calculated`}
          aria-label={`${title} info`}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

function KVPill({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-medium tabular-nums", color)}>{value}</span>
    </div>
  );
}

/* ── main component ──────────────────────────────────── */

interface FlightInsightsPanelProps {
  insights: FlightInsights;
  totalPassengers: number;
  onOpenInfo?: (key: InsightInfoKey) => void;
}

export function FlightInsightsPanel({ insights, totalPassengers, onOpenInfo }: FlightInsightsPanelProps) {
  const topChannels = useMemo(() => {
    return Object.entries(insights?.bookingChannels?.channels ?? {}).slice(0, 8);
  }, [insights?.bookingChannels?.channels]);

  const topPayments = useMemo(() => {
    return Object.entries(insights?.paymentMethods ?? {}).slice(0, 8);
  }, [insights?.paymentMethods]);

  const topClasses = useMemo(() => {
    return Object.entries(insights?.revenueClassMix ?? {}).slice(0, 10);
  }, [insights?.revenueClassMix]);

  const topEditCodes = useMemo(() => {
    return (insights?.editCodes?.topCodes ?? []).slice(0, 12);
  }, [insights?.editCodes?.topCodes]);

  const topNationalities = useMemo(() => {
    return Object.entries(insights?.nationalityBreakdown?.countries ?? {}).slice(0, 10);
  }, [insights?.nationalityBreakdown?.countries]);

  const topBagDests = useMemo(() => {
    return Object.entries(insights?.baggageRouting?.destinations ?? {}).slice(0, 8);
  }, [insights?.baggageRouting?.destinations]);

  const checkInHours = useMemo(() => {
    return Object.entries(insights?.checkInTimeline?.hourDistribution ?? {}).slice(0, 24);
  }, [insights?.checkInTimeline?.hourDistribution]);

  if (!insights) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <p>No insights data available.</p>
      </div>
    );
  }

  const maxChannel = topChannels.length > 0 ? topChannels[0][1] : 1;
  const maxPayment = topPayments.length > 0 ? topPayments[0][1] : 1;
  const maxClass = topClasses.length > 0 ? topClasses[0][1] : 1;

  return (
    <div className="space-y-4 p-1">
      {/* ── Row 1: Key KPIs ────────────────────────── */}
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("connectingPassengers")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Connecting is calculated" aria-label="Connecting info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Connecting</p>
          <p className="text-xl font-bold text-blue-500">{insights.connectingPassengers?.connectingPct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.connectingPassengers?.connecting ?? 0} of {totalPassengers}</p>
        </CardContent></Card>

        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("seatOccupancy")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Seat Assigned is calculated" aria-label="Seat info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Seat Assigned</p>
          <p className="text-xl font-bold text-emerald-500">{insights.seatOccupancy?.seatPct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.seatOccupancy?.seated ?? 0} /{totalPassengers}</p>
        </CardContent></Card>

        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("documentCompliance")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Docs is calculated" aria-label="Docs info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Docs (APIS)</p>
          <p className="text-xl font-bold text-amber-500">{insights.documentCompliance?.DOCS?.pct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.documentCompliance?.DOCS?.count ?? 0} DOCS</p>
        </CardContent></Card>

        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("corporateTravel")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Corporate is calculated" aria-label="Corporate info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Corporate</p>
          <p className="text-xl font-bold text-purple-500">{insights.corporateTravel?.corporatePct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.corporateTravel?.totalCorporate ?? 0} pax</p>
        </CardContent></Card>

        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("boardingPasses")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Boarding Pass is calculated" aria-label="Boarding pass info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Boarding Pass</p>
          <p className="text-xl font-bold text-teal-500">{insights.boardingPasses?.issuedPct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.boardingPasses?.issued ?? 0} issued</p>
        </CardContent></Card>

        <Card className="shadow-sm"><CardContent className="p-3 text-center relative">
          {onOpenInfo && <button type="button" onClick={() => onOpenInfo("ticketStatus")} className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground" title="How Ticketed is calculated" aria-label="Ticket info"><Info className="h-3 w-3" /></button>}
          <p className="text-[10px] text-muted-foreground">Ticketed</p>
          <p className="text-xl font-bold text-indigo-500">{insights.ticketStatus?.ticketPct ?? 0}%</p>
          <p className="text-[10px] text-muted-foreground">{insights.ticketStatus?.withTicket ?? 0} of {totalPassengers}</p>
        </CardContent></Card>
      </div>

      {/* ── Row 2: Main analytics grid ─────────────── */}
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">

        {/* Connecting vs Local */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={ArrowUpDown} title="Connecting vs Local" onInfo={() => onOpenInfo?.("connectingPassengers")} />
          <div className="space-y-2">
            <HBarRow label="Connecting (thru)" value={insights.connectingPassengers?.connecting ?? 0} max={totalPassengers} color="bg-blue-500" />
            <HBarRow label="Local (O&D)" value={insights.connectingPassengers?.local ?? 0} max={totalPassengers} color="bg-emerald-500" />
          </div>
        </CardContent></Card>

        {/* Booking Channels */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={BarChart3} title="Booking Channels" onInfo={() => onOpenInfo?.("bookingChannels")} />
          <div className="space-y-1.5">
            {topChannels.map(([ch, cnt]) => (
              <HBarRow key={ch} label={ch} value={cnt} max={maxChannel} color="bg-violet-500" />
            ))}
            {topChannels.length === 0 && <p className="text-xs text-muted-foreground">No data</p>}
          </div>
        </CardContent></Card>

        {/* Payment Methods */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={CreditCard} title="Payment Methods" onInfo={() => onOpenInfo?.("paymentMethods")} />
          <div className="space-y-1.5">
            {topPayments.map(([m, cnt]) => (
              <HBarRow key={m} label={m} value={cnt} max={maxPayment} color="bg-amber-500" />
            ))}
            {topPayments.length === 0 && <p className="text-xs text-muted-foreground">No data</p>}
          </div>
        </CardContent></Card>

        {/* Document Compliance */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={FileCheck} title="Document Compliance (APIS)" onInfo={() => onOpenInfo?.("documentCompliance")} />
          <div className="space-y-2">
            <HBarRow label="DOCS (passport)" value={insights.documentCompliance?.DOCS?.count ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="DOCV (visa)" value={insights.documentCompliance?.DOCV?.count ?? 0} max={totalPassengers} color="bg-blue-500" />
            <HBarRow label="DOCA (address)" value={insights.documentCompliance?.DOCA?.count ?? 0} max={totalPassengers} color="bg-purple-500" />
          </div>
        </CardContent></Card>

        {/* Revenue Class Mix */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Ticket} title="Revenue Class Mix" onInfo={() => onOpenInfo?.("revenueClassMix")} />
          <div className="space-y-1.5">
            {topClasses.map(([cls, cnt]) => (
              <HBarRow key={cls} label={`Class ${cls}`} value={cnt} max={maxClass} color="bg-teal-500" />
            ))}
            {topClasses.length === 0 && <p className="text-xs text-muted-foreground">No data</p>}
          </div>
        </CardContent></Card>

        {/* Baggage */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Luggage} title="Baggage Analytics" onInfo={() => onOpenInfo?.("baggage")} />
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            <MiniStat label="With Bags" value={insights.baggage?.withBags ?? 0} color="text-blue-500" />
            <MiniStat label="No Bags" value={insights.baggage?.withoutBags ?? 0} color="text-muted-foreground" />
            <MiniStat label="Total Bags" value={insights.baggage?.totalBags ?? 0} color="text-emerald-500" />
            <MiniStat label="Avg per Pax" value={insights.baggage?.avgBags ?? 0} color="text-amber-500" />
          </div>
          <div className="mt-2">
            <ProgressBar value={insights.baggage?.withBags ?? 0} max={totalPassengers} color="bg-blue-500" />
            <p className="text-[10px] text-muted-foreground mt-0.5">Data available: {insights.baggage?.dataAvailablePct ?? 0}%</p>
          </div>
        </CardContent></Card>

        {/* Booking Lead Time */}
        {insights.bookingLeadTime && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Clock} title="Booking Lead Time" onInfo={() => onOpenInfo?.("bookingLeadTime")} />
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              <MiniStat label="Average" value={`${insights.bookingLeadTime.avgDays}d`} color="text-blue-500" />
              <MiniStat label="Median" value={`${insights.bookingLeadTime.medianDays}d`} color="text-emerald-500" />
              <MiniStat label="Earliest" value={`${insights.bookingLeadTime.maxDays}d`} color="text-purple-500" />
              <MiniStat label="Latest" value={`${insights.bookingLeadTime.minDays}d`} color="text-amber-500" />
            </div>
            <div className="mt-2 space-y-1">
              {Object.entries(insights.bookingLeadTime.distribution).map(([k, v]) => (
                <KVPill key={k} label={k.replace(/([A-Z])/g, ' $1').replace(/(\d+)/, ' $1').trim()} value={v} />
              ))}
            </div>
          </CardContent></Card>
        )}

        {/* Check-in Sequence */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={ClipboardList} title="Check-in Sequence" onInfo={() => onOpenInfo?.("checkInSequence")} />
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            <MiniStat label="Total" value={insights.checkInSequence?.total ?? 0} />
            <MiniStat label="Median Seq" value={insights.checkInSequence?.median ?? 0} color="text-blue-500" />
            <MiniStat label="First Seq" value={insights.checkInSequence?.earliest ?? 0} color="text-emerald-500" />
            <MiniStat label="Last Seq" value={insights.checkInSequence?.latest ?? 0} color="text-amber-500" />
          </div>
        </CardContent></Card>

        {/* Seat Occupancy */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Armchair} title="Seat Occupancy" onInfo={() => onOpenInfo?.("seatOccupancy")} />
          <div className="space-y-2">
            <HBarRow label="Seats assigned" value={insights.seatOccupancy?.seated ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="No seat" value={insights.seatOccupancy?.unseated ?? 0} max={totalPassengers} color="bg-rose-400" />
          </div>
        </CardContent></Card>

        {/* Corporate Travel */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Briefcase} title="Corporate Travel" onInfo={() => onOpenInfo?.("corporateTravel")} />
          <div className="space-y-1.5">
            {Object.entries(insights.corporateTravel?.companies ?? {}).slice(0, 6).map(([id, cnt]) => (
              <HBarRow key={id} label={id} value={cnt} max={Object.values(insights.corporateTravel?.companies ?? {})[0] || 1} color="bg-purple-500" />
            ))}
            {Object.keys(insights.corporateTravel?.companies ?? {}).length === 0 && (
              <p className="text-xs text-muted-foreground">No corporate IDs</p>
            )}
          </div>
          <div className="mt-2">
            <KVPill label="Total corporate pax" value={insights.corporateTravel?.totalCorporate ?? 0} color="text-purple-500" />
          </div>
        </CardContent></Card>

        {/* Ticket & VCR Status */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Ticket} title="Ticket / VCR Status" onInfo={() => onOpenInfo?.("ticketStatus")} />
          <div className="space-y-2">
            <HBarRow label="With ticket" value={insights.ticketStatus?.withTicket ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="No ticket" value={insights.ticketStatus?.withoutTicket ?? 0} max={totalPassengers} color="bg-rose-400" />
          </div>
          {Object.keys(insights.ticketStatus?.vcrTypes ?? {}).length > 0 && (
            <div className="mt-2 space-y-1">
              <p className="text-[10px] text-muted-foreground font-medium">VCR Types</p>
              {Object.entries(insights.ticketStatus?.vcrTypes ?? {}).map(([t, n]) => (
                <KVPill key={t} label={t} value={n} />
              ))}
            </div>
          )}
        </CardContent></Card>

        {/* Class Mismatch (Upgrades/Downgrades) */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={TrendingUp} title="Class Mismatch" onInfo={() => onOpenInfo?.("classMismatch")} />
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <p className="text-[10px] text-muted-foreground">Mismatched</p>
              <p className="text-lg font-bold">{insights.classMismatch?.total ?? 0}</p>
            </div>
            <div>
              <p className="text-[10px] text-muted-foreground">Upgrades</p>
              <p className="text-lg font-bold text-emerald-500">{insights.classMismatch?.upgrades ?? 0}</p>
            </div>
            <div>
              <p className="text-[10px] text-muted-foreground">Downgrades</p>
              <p className="text-lg font-bold text-rose-500">{insights.classMismatch?.downgrades ?? 0}</p>
            </div>
          </div>
        </CardContent></Card>

        {/* Connection Risk */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={AlertTriangle} title="Connection Risk" onInfo={() => onOpenInfo?.("connectionRisk")} />
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            <MiniStat label="At Risk" value={insights.connectionRisk?.atRiskCount ?? 0} color={(insights.connectionRisk?.atRiskCount ?? 0) > 0 ? "text-rose-500" : "text-emerald-500"} />
            <MiniStat label="Connecting" value={insights.connectionRisk?.totalConnecting ?? 0} color="text-blue-500" />
          </div>
          {(insights.connectionRisk?.totalConnecting ?? 0) > 0 && (
            <div className="mt-1">
              <ProgressBar value={insights.connectionRisk?.atRiskCount ?? 0} max={insights.connectionRisk?.totalConnecting ?? 1} color={(insights.connectionRisk?.atRiskCount ?? 0) > 0 ? "bg-rose-500" : "bg-emerald-500"} />
              <p className="text-[10px] text-muted-foreground mt-0.5">{insights.connectionRisk?.riskPct ?? 0}% risk rate</p>
            </div>
          )}
        </CardContent></Card>

        {/* Priority Passengers */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Star} title="Priority Passengers" onInfo={() => onOpenInfo?.("priorityPassengers")} />
          <div className="space-y-1.5">
            {Object.entries(insights.priorityPassengers?.codes ?? {}).map(([code, cnt]) => (
              <KVPill key={code} label={code} value={cnt} color="text-amber-500" />
            ))}
            {Object.keys(insights.priorityPassengers?.codes ?? {}).length === 0 && (
              <p className="text-xs text-muted-foreground">None</p>
            )}
          </div>
          <div className="mt-1"><KVPill label="Total" value={insights.priorityPassengers?.total ?? 0} color="font-semibold" /></div>
        </CardContent></Card>

        {/* Passenger Types */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Users} title="Passenger Types" onInfo={() => onOpenInfo?.("passengerTypes")} />
          <div className="space-y-1.5">
            {Object.entries(insights.passengerTypes ?? {}).map(([t, cnt]) => (
              <HBarRow key={t} label={t || "(blank)"} value={cnt} max={Object.values(insights.passengerTypes ?? {})[0] || 1} color="bg-indigo-500" />
            ))}
            {Object.keys(insights.passengerTypes ?? {}).length === 0 && <p className="text-xs text-muted-foreground">No data</p>}
          </div>
        </CardContent></Card>

        {/* Wheelchair Types */}
        {Object.keys(insights.wheelchairTypes ?? {}).length > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Accessibility} title="Wheelchair Breakdown" onInfo={() => onOpenInfo?.("wheelchairTypes")} />
            <div className="space-y-1.5">
              {Object.entries(insights.wheelchairTypes ?? {}).map(([t, cnt]) => (
                <KVPill key={t} label={t} value={cnt} color="text-blue-500" />
              ))}
            </div>
          </CardContent></Card>
        )}

        {/* Meal Codes */}
        {Object.keys(insights.mealCodes ?? {}).length > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={UtensilsCrossed} title="Meal Codes" onInfo={() => onOpenInfo?.("mealCodes")} />
            <div className="space-y-1.5">
              {Object.entries(insights.mealCodes ?? {}).slice(0, 8).map(([m, cnt]) => (
                <KVPill key={m} label={m} value={cnt} color="text-orange-500" />
              ))}
            </div>
          </CardContent></Card>
        )}

        {/* Infant Tracking */}
        {(insights.infantTracking?.total ?? 0) > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Baby} title="Infant Tracking" onInfo={() => onOpenInfo?.("infantTracking")} />
            <p className="text-lg font-bold text-amber-500 mb-1">{insights.infantTracking?.total ?? 0}</p>
            <div className="space-y-0.5 max-h-[120px] overflow-y-auto">
              {(insights.infantTracking?.details ?? []).map((d, i) => (
                <p key={i} className="text-[10px] text-muted-foreground truncate">{d}</p>
              ))}
            </div>
          </CardContent></Card>
        )}

        {/* Seniority */}
        {(insights.seniority?.withSeniority ?? 0) > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Shield} title="Seniority / Employee" onInfo={() => onOpenInfo?.("seniority")} />
            <MiniStat label="With seniority date" value={insights.seniority?.withSeniority ?? 0} sub={`${insights.seniority?.pct ?? 0}% of passengers`} color="text-indigo-500" />
          </CardContent></Card>
        )}

        {/* Boarding Rate */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={BarChart3} title="Boarding Funnel" onInfo={() => onOpenInfo?.("boardingRate")} />
          <div className="space-y-2">
            <HBarRow label="Boarded" value={insights.boardingRate?.boarded ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="Checked in (not boarded)" value={insights.boardingRate?.checkedIn ?? 0} max={totalPassengers} color="bg-blue-500" />
            <HBarRow label="Not checked in" value={insights.boardingRate?.notCheckedIn ?? 0} max={totalPassengers} color="bg-rose-400" />
          </div>
        </CardContent></Card>

        {/* Change Velocity */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={TrendingUp} title="Change Velocity" onInfo={() => onOpenInfo?.("changeVelocity")} />
          <p className="text-lg font-bold mb-1">{insights.changeVelocity?.totalChanges ?? 0} <span className="text-xs font-normal text-muted-foreground">total changes</span></p>
          <div className="space-y-1 max-h-[140px] overflow-y-auto">
            {Object.entries(insights.changeVelocity?.changeTypes ?? {}).slice(0, 10).map(([t, cnt]) => (
              <KVPill key={t} label={t.replace(/([A-Z])/g, ' $1').trim()} value={cnt} />
            ))}
          </div>
        </CardContent></Card>

        {/* Multi-Segment Itinerary */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Plane} title="Multi-Segment Itinerary" onInfo={() => onOpenInfo?.("multiSegment")} />
          <p className="text-xs text-muted-foreground mb-1">{insights.multiSegment?.multiSegmentPct ?? 0}% multi-segment</p>
          <div className="space-y-1">
            {Object.entries(insights.multiSegment?.distribution ?? {}).map(([segs, cnt]) => (
              <KVPill key={segs} label={`${segs} segment${Number(segs) !== 1 ? 's' : ''}`} value={cnt} />
            ))}
          </div>
        </CardContent></Card>

        {/* PNR Party Size */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={BookOpen} title="PNR Party Size" onInfo={() => onOpenInfo?.("pnrPartySize")} />
          <p className="text-xs text-muted-foreground mb-1">Avg: {insights.pnrPartySize?.avgSize ?? 0} per PNR</p>
          <div className="space-y-1">
            {Object.entries(insights.pnrPartySize?.distribution ?? {}).map(([sz, cnt]) => (
              <KVPill key={sz} label={`${sz} pax`} value={`${cnt} PNRs`} />
            ))}
          </div>
        </CardContent></Card>

        {/* Edit Codes */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={ClipboardList} title={`Edit Codes (${insights.editCodes?.uniqueCodes ?? 0} unique)`} onInfo={() => onOpenInfo?.("editCodes")} />
          <div className="space-y-1 max-h-[180px] overflow-y-auto">
            {topEditCodes.map(({ code, count }) => (
              <KVPill key={code} label={code} value={count} />
            ))}
          </div>
        </CardContent></Card>

        {/* Boarding Passes */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={Ticket} title="Boarding Passes" onInfo={() => onOpenInfo?.("boardingPasses")} />
          <div className="space-y-2">
            <HBarRow label="Issued" value={insights.boardingPasses?.issued ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="Not issued" value={insights.boardingPasses?.notIssued ?? 0} max={totalPassengers} color="bg-rose-400" />
          </div>
        </CardContent></Card>

        {/* Flight Info */}
        {insights.flightInfo && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Plane} title="Flight Info" onInfo={() => onOpenInfo?.("flightInfo")} />
            <div className="space-y-1">
              <KVPill label="Duration" value={insights.flightInfo.elapsedTime || "—"} />
              <KVPill label="Air Miles" value={insights.flightInfo.airMilesFlown || "—"} />
              <KVPill label="Aircraft" value={insights.flightInfo.aircraftType || "—"} />
              <KVPill label="Meal Service" value={insights.flightInfo.mealCode || "—"} />
            </div>
          </CardContent></Card>
        )}

        {/* Equipment */}
        {(insights.equipment?.aircraftType || insights.equipment?.seatConfig) && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Cpu} title="Equipment & Config" onInfo={() => onOpenInfo?.("equipment")} />
            <div className="space-y-1">
              <KVPill label="Aircraft Type" value={insights.equipment?.aircraftType || "—"} />
              <KVPill label="Seat Config" value={insights.equipment?.seatConfig || "—"} />
            </div>
          </CardContent></Card>
        )}

        {/* Reservation Recency */}
        {insights.reservationRecency && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Clock} title="Reservation Recency" onInfo={() => onOpenInfo?.("reservationRecency")} />
            <div className="space-y-1">
              <KVPill label="Total Reservations" value={insights.reservationRecency.totalReservations} />
              <KVPill label="Latest Modification" value={
                insights.reservationRecency.latestModification
                  ? new Date(insights.reservationRecency.latestModification).toLocaleString()
                  : "—"
              } />
            </div>
          </CardContent></Card>
        )}

        {/* Check-in Timeline */}
        {insights.checkInTimeline?.totalWithTime > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Clock} title="Check-in Timeline" />
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 mb-2">
              <MiniStat label="With Time Data" value={insights.checkInTimeline.totalWithTime} color="text-blue-500" />
              <MiniStat label="Peak Hour" value={insights.checkInTimeline.peakHour ?? "—"} color="text-emerald-500" />
            </div>
            <div className="space-y-1 max-h-[160px] overflow-y-auto">
              {checkInHours.map(([hour, cnt]) => (
                <HBarRow key={hour} label={hour} value={cnt} max={insights.checkInTimeline.totalWithTime} color="bg-blue-500" />
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">Coverage: {insights.checkInTimeline.coveragePct}% of passengers</p>
          </CardContent></Card>
        )}

        {/* Emergency Contacts */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={HeartPulse} title="Emergency Contacts" />
          <div className="space-y-2">
            <HBarRow label="With Contact" value={insights.emergencyContacts?.withContact ?? 0} max={totalPassengers} color="bg-emerald-500" />
            <HBarRow label="No Contact" value={insights.emergencyContacts?.withoutContact ?? 0} max={totalPassengers} color="bg-rose-400" />
          </div>
          <p className="text-[10px] text-muted-foreground mt-1">Coverage: {insights.emergencyContacts?.coveragePct ?? 0}%</p>
        </CardContent></Card>

        {/* Nationality Distribution */}
        {insights.nationalityBreakdown?.uniqueCountries > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Globe} title="Nationality Distribution" />
            <div className="flex items-center gap-3 mb-2">
              <MiniStat label="Countries" value={insights.nationalityBreakdown.uniqueCountries} color="text-blue-500" />
              <MiniStat label="Unknown" value={insights.nationalityBreakdown.unknown} color="text-muted-foreground" />
            </div>
            <div className="space-y-1 max-h-[160px] overflow-y-auto">
              {topNationalities.map(([nat, cnt]) => (
                <HBarRow key={nat} label={nat} value={cnt} max={topNationalities[0]?.[1] ?? 1} color="bg-indigo-500" />
              ))}
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">Coverage: {insights.nationalityBreakdown.coveragePct}%</p>
          </CardContent></Card>
        )}

        {/* Baggage Routing */}
        {insights.baggageRouting?.paxWithRoutes > 0 && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={Map} title="Baggage Routing" />
            <p className="text-[10px] text-muted-foreground mb-2">{insights.baggageRouting.paxWithRoutes} pax with routes ({insights.baggageRouting.coveragePct}%)</p>
            <div className="space-y-1 max-h-[140px] overflow-y-auto">
              {topBagDests.map(([dest, cnt]) => (
                <HBarRow key={dest} label={dest} value={cnt} max={topBagDests[0]?.[1] ?? 1} color="bg-amber-500" />
              ))}
            </div>
          </CardContent></Card>
        )}

        {/* Standby & Upgrade Queue */}
        {(insights.standbyUpgrade?.standbyTotal > 0 || insights.standbyUpgrade?.upgradeTotal > 0) && (
          <Card className="shadow-sm"><CardContent className="p-3">
            <SectionHeader icon={ArrowUpCircle} title="Standby & Upgrade" />
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 mb-2">
              <MiniStat label="Standby" value={insights.standbyUpgrade?.standbyTotal ?? 0} color="text-rose-500" />
              <MiniStat label="Upgrade" value={insights.standbyUpgrade?.upgradeTotal ?? 0} color="text-amber-500" />
            </div>
            {Object.keys(insights.standbyUpgrade?.standbyCabins ?? {}).length > 0 && (
              <div className="space-y-1">
                {Object.entries(insights.standbyUpgrade.standbyCabins).map(([cab, cnt]) => (
                  <HBarRow key={cab} label={cab === "J" ? "Business" : cab === "Y" ? "Economy" : cab} value={cnt} max={insights.standbyUpgrade.standbyTotal} color="bg-rose-400" />
                ))}
              </div>
            )}
            <p className="text-[10px] text-muted-foreground mt-1">Standby: {insights.standbyUpgrade?.standbyPct ?? 0}% of manifest</p>
          </CardContent></Card>
        )}

        {/* Operational Readiness */}
        <Card className="shadow-sm"><CardContent className="p-3">
          <SectionHeader icon={ShieldAlert} title="Operational Readiness" />
          <div className="space-y-2">
            <HBarRow label="No Seat Assigned" value={insights.operationalReadiness?.noSeat ?? 0} max={totalPassengers} color="bg-amber-500" />
            <HBarRow label="Checked-In, No BP" value={insights.operationalReadiness?.checkedInNoBP ?? 0} max={totalPassengers} color="bg-orange-500" />
            <HBarRow label="Not Checked-In" value={insights.operationalReadiness?.notCheckedIn ?? 0} max={totalPassengers} color="bg-rose-500" />
            {(insights.operationalReadiness?.thruNoSeat ?? 0) > 0 && (
              <HBarRow label="Thru Pax No Seat" value={insights.operationalReadiness.thruNoSeat} max={totalPassengers} color="bg-red-600" />
            )}
          </div>
          <div className="mt-2">
            <ProgressBar value={totalPassengers - (insights.operationalReadiness?.notCheckedIn ?? 0)} max={totalPassengers} color="bg-emerald-500" />
            <p className="text-[10px] text-muted-foreground mt-0.5">Readiness: {insights.operationalReadiness?.readinessPct ?? 0}%</p>
          </div>
        </CardContent></Card>
      </div>
    </div>
  );
}
