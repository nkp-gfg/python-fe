"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Info,
  Database,
  Code,
  Calculator,
  Server,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

/* ── Insight key type ────────────────────────────────── */

export type InsightInfoKey =
  | "connectingPassengers"
  | "bookingChannels"
  | "paymentMethods"
  | "documentCompliance"
  | "checkInSequence"
  | "bookingLeadTime"
  | "seatOccupancy"
  | "baggage"
  | "editCodes"
  | "multiSegment"
  | "pnrPartySize"
  | "infantTracking"
  | "wheelchairTypes"
  | "mealCodes"
  | "boardingRate"
  | "changeVelocity"
  | "revenueClassMix"
  | "ticketStatus"
  | "flightInfo"
  | "corporateTravel"
  | "priorityPassengers"
  | "seniority"
  | "connectionRisk"
  | "classMismatch"
  | "passengerTypes"
  | "boardingPasses"
  | "reservationRecency"
  | "equipment";

/* ── Shared building blocks ──────────────────────────── */

function Section({
  title,
  icon,
  children,
  defaultOpen = true,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-4 py-3 text-left text-sm font-medium bg-muted/40 hover:bg-muted/60 transition-colors"
      >
        {icon}
        <span className="flex-1">{title}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-4 py-3 text-sm space-y-2">{children}</div>}
    </div>
  );
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="bg-muted/60 border rounded-md px-3 py-2 text-xs font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
      {children}
    </pre>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-muted-foreground min-w-[100px] shrink-0">{label}:</span>
      <span className="text-foreground">{children}</span>
    </div>
  );
}

function FieldBadge({ name }: { name: string }) {
  return <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{name}</code>;
}

function SourceBadge({ source }: { source: string }) {
  return <Badge variant="outline" className="shrink-0 text-[10px]">{source}</Badge>;
}

/* ── Individual insight info content ─────────────────── */

function ConnectingPassengersInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Connecting vs Local</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Separates passengers into <strong>connecting (thru)</strong> passengers who are transiting through this airport,
          and <strong>local (O&amp;D)</strong> passengers whose journey starts or ends here.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>Connecting passengers have <FieldBadge name="isThru = true" /> — they boarded at a prior station and are
          continuing through this flight. Local passengers have <FieldBadge name="isThru = false" /> or no thru flag.</p>
        <p className="text-muted-foreground text-xs mt-1">
          High connecting percentages indicate hub traffic. These passengers may have tighter connection windows
          and require priority boarding attention.
        </p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].isThru" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → PassengerInfo → ThruSegmentIndicator</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger in passengers[]:
  if passenger.isThru == true:
      connecting += 1
  else:
      local += 1
connectingPct = connecting / totalPassengers × 100`}</CodeBlock>
      </Section>
      <Section title="API Response" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Path"><FieldBadge name="insights.connectingPassengers" /></KV>
        <KV label="Fields"><FieldBadge name="connecting" /> <FieldBadge name="local" /> <FieldBadge name="connectingPct" /></KV>
      </Section>
    </div>
  );
}

function BookingChannelsInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Booking Channels</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of how reservations were created — online (WEB, MOB, NDC), travel agent, or other channels.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>Each reservation&apos;s <FieldBadge name="pointOfSale.agentSine" /> is extracted and counted.
          Known online sines (WEB, MOB, APP, ND1, ND2, NDC) are categorized as &ldquo;online&rdquo;.
          Short codes (≤4 chars) or STX-prefixed codes are &ldquo;agent&rdquo;.</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Field"><FieldBadge name="reservations[].pointOfSale.agentSine" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → Reservation → PointOfSale → AgentSine</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation:
  src = reservation.pointOfSale.agentSine
  channel_counts[src] += reservation.numberInParty
  classify into: online | agent | other`}</CodeBlock>
      </Section>
      <Section title="API Response" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Path"><FieldBadge name="insights.bookingChannels" /></KV>
        <KV label="Fields"><FieldBadge name="channels" /> (sorted map) <FieldBadge name="categories" /> (online/agent/corporate/other)</KV>
      </Section>
    </div>
  );
}

function PaymentMethodsInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Payment Methods</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of form-of-payment codes across reservations (e.g. VI=Visa, CA=Cash, AX=Amex, TBM=Travel Bank).
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Field"><FieldBadge name="reservations[].formOfPayment" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → OpenReservationElement[@type=&apos;FP&apos;] → FormOfPayment</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation:
  fop = reservation.formOfPayment
  if fop: payment_counts[fop] += 1
sorted descending by count`}</CodeBlock>
      </Section>
    </div>
  );
}

function DocumentComplianceInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Document Compliance (APIS)</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Tracks APIS (Advance Passenger Information System) document submission rates.
          <strong> DOCS</strong> = passport/travel document, <strong>DOCV</strong> = visa,
          <strong> DOCA</strong> = destination address.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>Passengers whose <FieldBadge name="editCodes[]" /> array contains DOCS, DOCV, or DOCA edit codes.
          These codes are set by Sabre when APIS documents are submitted for the passenger.</p>
        <p className="text-muted-foreground text-xs mt-1">
          Low DOCS compliance may indicate regulatory risk for international flights. Many countries require 100% APIS
          submission before departure.
        </p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].editCodes" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → EditInfo → EditCode (DOCS/DOCV/DOCA)</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  for code in [DOCS, DOCV, DOCA]:
    if code in passenger.editCodes:
        doc_codes[code] += 1
pct = count / totalPassengers × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function CheckInSequenceInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Check-in Sequence</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Analysis of check-in sequence numbers assigned by Sabre DCS. Shows the earliest, latest, and median
          sequence numbers to understand check-in patterns.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].checkInSequence" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → CheckIn_Info → CheckInNumber</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`sequences = [p.checkInSequence for p in passengers
             if checkInSequence > 0]
sort(sequences)
median = sequences[len // 2]`}</CodeBlock>
      </Section>
    </div>
  );
}

function BookingLeadTimeInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Booking Lead Time</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          How far in advance reservations were created relative to the departure date. Bucketed into
          same-day, within 7 days, 30 days, 90 days, and over 90 days.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>For each reservation, calculates <FieldBadge name="departureDate − createdAt" /> in days.
          High same-day bookings may indicate walk-up traffic. Long lead times suggest leisure or corporate advance bookings.</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /> + <FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="reservations[].createdAt" /> and <FieldBadge name="passenger_list.departureDate" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → Reservation → CreatedDateTime</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation:
  days = departureDate − createdAt (in days)
  if 0 <= days <= 365:
      lead_times.append(days)
bucket into: sameDay | within7d | within30d
             | within90d | over90d`}</CodeBlock>
      </Section>
    </div>
  );
}

function SeatOccupancyInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Seat Occupancy</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Percentage of passengers with an assigned seat number versus those without.
          Useful for identifying seat assignment gaps before departure.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].seat" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → SeatAssignment</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  if passenger.seat is not empty:
      seated += 1
  else:
      unseated += 1
seatPct = seated / totalPassengers × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function BaggageInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Baggage Analytics</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Checked baggage counts per passenger, total bag volume, and baggage routing data availability.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p><strong>With Bags / No Bags</strong> — passengers who have checked at least one bag vs zero.
          <strong> Total Bags / Avg</strong> — fleet-level bag count.
          <strong> Data available</strong> — percentage of passengers with <FieldBadge name="bagCount" /> populated.</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="passengers[].bagCount" /> and <FieldBadge name="passengers[].baggageRoutes" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → BagInfo → NumberOfPieces; GetPassengerDataRQ → BaggageInfo</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`bag_counts = [p.bagCount for p in passengers
              if bagCount is not None]
withBags = count where bagCount > 0
totalBags = sum(bag_counts)
avgBags = totalBags / len(bag_counts)`}</CodeBlock>
      </Section>
    </div>
  );
}

function EditCodesInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Edit Codes</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Sabre DCS edit codes are 2–4 character codes attached to passengers indicating special conditions,
          documents, services, or statuses. Shows top 20 most frequent codes.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>Common codes include: <FieldBadge name="ET" /> (e-ticket), <FieldBadge name="DOCS" /> (passport),
          <FieldBadge name="DOCV" /> (visa), <FieldBadge name="FF" /> (frequent flyer),
          <FieldBadge name="CHD" /> (child), <FieldBadge name="INF" /> (infant),
          <FieldBadge name="WCHR" /> (wheelchair ramp), <FieldBadge name="WCHC" /> (wheelchair cabin).</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].editCodes" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → EditInfo → EditCode (array)</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  for code in passenger.editCodes:
      code_freq[code] += 1
top_codes = sorted by count, limit 20`}</CodeBlock>
      </Section>
    </div>
  );
}

function MultiSegmentInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Multi-Segment Itinerary</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of how many flight segments each reservation contains. Multi-segment reservations
          indicate connecting itineraries or round trips.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Field"><FieldBadge name="reservations[].segments" /> (array length)</KV>
        <KV label="Sabre Source">Trip_SearchRS → Reservation → FlightSegment (array)</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation:
  segCount = len(reservation.segments)
  seg_counts[segCount] += 1
multiSegmentPct = count(segments > 1)
                / total × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function PnrPartySizeInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">PNR Party Size</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of how many passengers are in each PNR (reservation). Solo travelers, couples,
          families, or groups are distinguished by party size.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Field"><FieldBadge name="reservations[].numberInParty" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → Reservation → NumberInParty</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation:
  ps = reservation.numberInParty
  party_sizes[ps] += 1
avgSize = weighted average`}</CodeBlock>
      </Section>
    </div>
  );
}

function InfantTrackingInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Infant Tracking</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Counts passengers carrying lap infants. Infants don&apos;t have their own seat — they&apos;re
          flagged on the accompanying adult&apos;s record via the <FieldBadge name="hasInfant" /> boolean.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].hasInfant" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → EditInfo → EditCode contains &quot;INF&quot;</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  if passenger.hasInfant:
      total += 1
      details.append(lastName + pnr)`}</CodeBlock>
      </Section>
    </div>
  );
}

function WheelchairTypesInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Wheelchair Breakdown</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Breaks down wheelchair service requests by IATA SSR code:
          <strong> WCHR</strong> = wheelchair to/from ramp,
          <strong> WCHS</strong> = wheelchair to/from steps,
          <strong> WCHC</strong> = wheelchair to/from cabin seat (immobile).
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].editCodes" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → EditInfo → EditCode (WCHR/WCHS/WCHC)</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  for code in editCodes:
    if code in [WCHR, WCHS, WCHC]:
        wc_types[code] += 1`}</CodeBlock>
      </Section>
    </div>
  );
}

function MealCodesInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Meal Codes</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Special meal requests from reservations, using IATA special meal codes (e.g. VGML=Vegetarian,
          DBML=Diabetic, KSML=Kosher, MOML=Muslim, BBML=Baby, CHML=Child).
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Field"><FieldBadge name="reservations[].passengers[].specialMeal" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → Passenger → SpecialRequests → SpecialMealRequest</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each reservation passenger:
  meal = passenger.specialMeal
  if meal: meal_codes[meal] += 1
sorted descending by count`}</CodeBlock>
      </Section>
    </div>
  );
}

function BoardingRateInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Boarding Funnel</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Three-stage funnel showing how many passengers have progressed through:
          Not Checked-In → Checked-In → Boarded. The boarding percentage gives a quick readiness indicator.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="passengers[].isBoarded" /> and <FieldBadge name="passengers[].isCheckedIn" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → BoardStatus and CheckInStatus</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`boarded = count(isBoarded == true)
checkedIn = count(isCheckedIn == true
                  AND isBoarded == false)
notCheckedIn = total - checkedIn - boarded
boardedPct = boarded / total × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function ChangeVelocityInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Change Velocity</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Total tracked changes for this flight from the change detection pipeline. Broken down by change type
          (e.g. seatChange, cabinUpgrade, checkin, boarding, addPassenger).
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="changes" /></KV>
        <KV label="Aggregation">Count of changes grouped by <FieldBadge name="changeType" /></KV>
        <KV label="Source">Snapshot-diff pipeline comparing consecutive passenger_list snapshots</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`changeSummary = pre-computed from changes
  collection (grouped by changeType)
totalChanges = sum(all counts)
sorted by count descending`}</CodeBlock>
      </Section>
    </div>
  );
}

function RevenueClassMixInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Revenue Class Mix</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of booking classes (fare buckets) across passengers. Classes like Y/B/M/H are economy
          fare levels; J/C/D/I are business class fare levels.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].bookingClass" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → BookingClass</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  bc = passenger.bookingClass
  if bc: class_dist[bc] += 1
sorted descending by count`}</CodeBlock>
      </Section>
    </div>
  );
}

function TicketStatusInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Ticket / VCR Status</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Shows how many passengers have a ticket number and the distribution of VCR (Validated Coupon Record) types.
          VCR types indicate the ticket validation status in Sabre.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="passengers[].vcrType" /> and <FieldBadge name="passengers[].ticketNumber" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → VCR/VCRType and TKNE/TicketNumber</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  if passenger.vcrType:
      vcr_types[vcrType] += 1
  if passenger.ticketNumber:
      has_ticket += 1
ticketPct = has_ticket / total × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function FlightInfoInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Flight Info</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Flight duration, air miles, aircraft type, and meal service code from the published schedule.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="flight_schedules" /></KV>
        <KV label="Fields"><FieldBadge name="elapsedTime" />, <FieldBadge name="airMilesFlown" />, <FieldBadge name="aircraftType" />, <FieldBadge name="mealCode" /></KV>
        <KV label="Sabre Source">VerifyFlightDetailsLLSRQ response</KV>
      </Section>
    </div>
  );
}

function CorporateTravelInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Corporate Travel</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Passengers with a corporate ID (<FieldBadge name="corpId" />) indicating they&apos;re traveling
          under a corporate travel agreement. Grouped by company code.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].corpId" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → CorporateID</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  cid = passenger.corpId
  if cid: corp_ids[cid] += 1
corporatePct = sum(corp_ids) / total × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function PriorityPassengersInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Priority Passengers</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Passengers with a priority code assigned by Sabre DCS. Priority codes determine boarding order
          and service levels (e.g. CIP, VIP, diplomatic).
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].priorityCode" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → PriorityCode</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`for each passenger:
  pc = passenger.priorityCode
  if pc: priority_codes[pc] += 1`}</CodeBlock>
      </Section>
    </div>
  );
}

function SeniorityInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Seniority / Employee</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Passengers with a <FieldBadge name="seniorityDate" /> — typically airline employees traveling on staff
          tickets. The seniority date determines their standby priority.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].seniorityDate" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → SeniorityDate</KV>
      </Section>
    </div>
  );
}

function ConnectionRiskInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Connection Risk</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Identifies connecting (thru) passengers who have <strong>not yet checked in</strong> and
          <strong> not yet boarded</strong>. These are at risk of missing their connection.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>A passenger is &ldquo;at risk&rdquo; when <FieldBadge name="isThru = true" /> AND
          <FieldBadge name="isCheckedIn = false" /> AND <FieldBadge name="isBoarded = false" />.
          The risk percentage is at-risk / total connecting × 100.</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="isThru" />, <FieldBadge name="isCheckedIn" />, <FieldBadge name="isBoarded" /></KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`at_risk = 0
for each passenger:
  if isThru AND NOT isCheckedIn
     AND NOT isBoarded:
      at_risk += 1
riskPct = at_risk / totalConnecting × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function ClassMismatchInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Class Mismatch</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Compares each passenger&apos;s <FieldBadge name="bookingClass" /> (actual) vs
          <FieldBadge name="desiredBookingClass" /> (original). Mismatches indicate upgrades or downgrades.
        </p>
      </div>
      <Section title="What It Shows" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <p>An <strong>upgrade</strong> is when the actual class is business (J/C/D/I/R) but the desired was economy.
          A <strong>downgrade</strong> is the reverse. Other mismatches (e.g. between economy fare buckets) are counted
          but not classified.</p>
      </Section>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Fields"><FieldBadge name="passengers[].bookingClass" /> and <FieldBadge name="passengers[].desiredBookingClass" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → BookingClass and DesiredBookingClass</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`bizClasses = {J, C, D, I, R}
for each passenger:
  if desired != actual:
    mismatch += 1
    if actual in bizClasses
       AND desired not in bizClasses:
        upgrade += 1
    elif desired in bizClasses
         AND actual not in bizClasses:
        downgrade += 1`}</CodeBlock>
      </Section>
    </div>
  );
}

function PassengerTypesInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Passenger Types</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Distribution of Sabre passenger type codes. Common types: blank (normal revenue),
          <strong> E</strong> = Employee (non-revenue), <strong>S</strong> = Standby, <strong>G</strong> = Group.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].passengerType" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → PassengerType</KV>
      </Section>
    </div>
  );
}

function BoardingPassesInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Boarding Passes</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          How many passengers have had a boarding pass issued. A passenger may be checked in but not yet have
          a physical or mobile boarding pass printed/generated.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="passenger_list" /></KV>
        <KV label="Field"><FieldBadge name="passengers[].boardingPassIssued" /></KV>
        <KV label="Sabre Source">GetPassengerListRS → BoardingPassFlag</KV>
      </Section>
      <Section title="Calculation" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <CodeBlock>{`bp_issued = count(boardingPassIssued == true)
issuedPct = bp_issued / total × 100`}</CodeBlock>
      </Section>
    </div>
  );
}

function ReservationRecencyInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Reservation Recency</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Shows the most recently modified reservation and total reservation count.
          Useful for understanding how &ldquo;active&rdquo; the booking manifest still is.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Collection"><FieldBadge name="reservations" /></KV>
        <KV label="Fields"><FieldBadge name="reservations[].modifiedAt" /> or <FieldBadge name="reservations[].createdAt" /></KV>
        <KV label="Sabre Source">Trip_SearchRS → Reservation → CreatedDateTime / ModifiedDateTime</KV>
      </Section>
    </div>
  );
}

function EquipmentInfo() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">Equipment &amp; Config</h3>
        <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
          Aircraft type and seat configuration for the flight. Sourced from the flight schedule (VerifyFlightDetailsLLSRQ)
          or passenger list document.
        </p>
      </div>
      <Section title="Data Source" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <KV label="Primary"><FieldBadge name="flight_schedules.aircraftType" /></KV>
        <KV label="Fallback"><FieldBadge name="passenger_list.aircraftType" /></KV>
        <KV label="Config"><FieldBadge name="passenger_list.seatConfig" /> or <FieldBadge name="flight_schedules.seatConfig" /></KV>
      </Section>
    </div>
  );
}

/* ── Registry ────────────────────────────────────────── */

const INSIGHT_INFO_MAP: Record<
  InsightInfoKey,
  { component: React.ComponentType; label: string }
> = {
  connectingPassengers: { component: ConnectingPassengersInfo, label: "Connecting vs Local" },
  bookingChannels: { component: BookingChannelsInfo, label: "Booking Channels" },
  paymentMethods: { component: PaymentMethodsInfo, label: "Payment Methods" },
  documentCompliance: { component: DocumentComplianceInfo, label: "Document Compliance" },
  checkInSequence: { component: CheckInSequenceInfo, label: "Check-in Sequence" },
  bookingLeadTime: { component: BookingLeadTimeInfo, label: "Booking Lead Time" },
  seatOccupancy: { component: SeatOccupancyInfo, label: "Seat Occupancy" },
  baggage: { component: BaggageInfo, label: "Baggage Analytics" },
  editCodes: { component: EditCodesInfo, label: "Edit Codes" },
  multiSegment: { component: MultiSegmentInfo, label: "Multi-Segment" },
  pnrPartySize: { component: PnrPartySizeInfo, label: "PNR Party Size" },
  infantTracking: { component: InfantTrackingInfo, label: "Infant Tracking" },
  wheelchairTypes: { component: WheelchairTypesInfo, label: "Wheelchair Types" },
  mealCodes: { component: MealCodesInfo, label: "Meal Codes" },
  boardingRate: { component: BoardingRateInfo, label: "Boarding Funnel" },
  changeVelocity: { component: ChangeVelocityInfo, label: "Change Velocity" },
  revenueClassMix: { component: RevenueClassMixInfo, label: "Revenue Class Mix" },
  ticketStatus: { component: TicketStatusInfo, label: "Ticket / VCR Status" },
  flightInfo: { component: FlightInfoInfo, label: "Flight Info" },
  corporateTravel: { component: CorporateTravelInfo, label: "Corporate Travel" },
  priorityPassengers: { component: PriorityPassengersInfo, label: "Priority Passengers" },
  seniority: { component: SeniorityInfo, label: "Seniority / Employee" },
  connectionRisk: { component: ConnectionRiskInfo, label: "Connection Risk" },
  classMismatch: { component: ClassMismatchInfo, label: "Class Mismatch" },
  passengerTypes: { component: PassengerTypesInfo, label: "Passenger Types" },
  boardingPasses: { component: BoardingPassesInfo, label: "Boarding Passes" },
  reservationRecency: { component: ReservationRecencyInfo, label: "Reservation Recency" },
  equipment: { component: EquipmentInfo, label: "Equipment & Config" },
};

/* ── Exported panel ──────────────────────────────────── */

interface InsightInfoPanelProps {
  activeKey: InsightInfoKey;
}

export function InsightInfoPanel({ activeKey }: InsightInfoPanelProps) {
  const entry = INSIGHT_INFO_MAP[activeKey];
  const Content = entry.component;
  return (
    <div className="space-y-4">
      <Content />
    </div>
  );
}
