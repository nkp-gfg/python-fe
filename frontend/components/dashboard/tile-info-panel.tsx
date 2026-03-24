"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Users,
  UserCheck,
  PlaneTakeoff,
  Info,
  Database,
  Code,
  Calculator,
  Server,
  GitBranch,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

export type TileInfoKey = "booked" | "checkedIn" | "boarded" | "others";

interface TileInfoPanelProps {
  activeTab: TileInfoKey;
}

/* ── Collapsible section ─────────────────────────────────── */
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
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {open && <div className="px-4 py-3 text-sm space-y-2">{children}</div>}
    </div>
  );
}

/* ── Code block ──────────────────────────────────────────── */
function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="bg-muted/60 border rounded-md px-3 py-2 text-xs font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
      {children}
    </pre>
  );
}

/* ── Key-value row ───────────────────────────────────────── */
function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-muted-foreground min-w-[100px] shrink-0">{label}:</span>
      <span className="text-foreground">{children}</span>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  BOOKED TILE INFO                                         */
/* ────────────────────────────────────────────────────────── */
function BookedInfo() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Users className="h-5 w-5 text-blue-500" />
        <h3 className="text-lg font-semibold">Booked Tile</h3>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">
        Shows passengers who have a reservation but have <strong>not yet checked in</strong> and{" "}
        <strong>not yet boarded</strong>. These are passengers in the earliest stage of the journey lifecycle.
      </p>

      <Section title="What This Number Means — Plain Language" icon={<Info className="h-4 w-4 text-blue-500" />}>
        <div className="space-y-3">
          <p className="leading-relaxed">
            <strong>The &ldquo;Booked&rdquo; count</strong> shows passengers whose names appear on the Sabre manifest
            (GetPassengerListRS) but who have <strong>not checked in</strong> at any counter, kiosk, or online channel,
            and have <strong>not scanned their boarding pass</strong> at the gate.
          </p>
          <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
            <p className="font-medium text-blue-800 dark:text-blue-300 mb-1">Example — GF2057 with 1 Booked:</p>
            <p className="text-blue-700 dark:text-blue-400 text-xs leading-relaxed">
              78 passengers are on the manifest. 77 have checked in and boarded. 1 passenger (e.g. THAKKAR NISCHAL)
              still has a reservation but never arrived at the check-in counter. He appears as <strong>&ldquo;1&rdquo; in the Booked tile</strong>.
            </p>
          </div>
          <Separator className="my-2" />
          <p className="font-medium">Phase-Aware Label Morphing</p>
          <div className="space-y-2 mt-1">
            <p className="text-xs text-muted-foreground">
              This card&apos;s title and color change based on the <strong>flight phase</strong> to communicate
              the operational significance of these passengers:
            </p>
            <table className="w-full text-xs border rounded-lg overflow-hidden">
              <thead>
                <tr className="bg-muted/40">
                  <th className="px-2 py-1.5 text-left">Phase</th>
                  <th className="px-2 py-1.5 text-left">Card Title</th>
                  <th className="px-2 py-1.5 text-left">Color</th>
                  <th className="px-2 py-1.5 text-left">Meaning</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t"><td className="px-2 py-1">SCHEDULED</td><td className="px-2 py-1">Booked</td><td className="px-2 py-1 text-blue-500">Blue</td><td className="px-2 py-1 text-muted-foreground">Normal reservations</td></tr>
                <tr className="border-t"><td className="px-2 py-1">CHECK-IN</td><td className="px-2 py-1">Awaiting Check-in</td><td className="px-2 py-1 text-blue-500">Blue</td><td className="px-2 py-1 text-muted-foreground">May still arrive</td></tr>
                <tr className="border-t"><td className="px-2 py-1">BOARDING</td><td className="px-2 py-1">Not Checked In</td><td className="px-2 py-1 text-amber-500">Amber</td><td className="px-2 py-1 text-muted-foreground">At risk of missing flight</td></tr>
                <tr className="border-t"><td className="px-2 py-1">CLOSED / DEPARTED</td><td className="px-2 py-1">No Show</td><td className="px-2 py-1 text-red-500">Red</td><td className="px-2 py-1 text-muted-foreground">Confirmed no-shows</td></tr>
              </tbody>
            </table>
          </div>
          <Separator className="my-2" />
          <p className="font-medium">Where this count also appears:</p>
          <div className="space-y-2 mt-1">
            <div className="flex gap-2">
              <Badge variant="outline" className="shrink-0">Others card</Badge>
              <span className="text-muted-foreground text-xs">
                Shows &ldquo;Not Checked In&rdquo; (open flights) or &ldquo;No Show&rdquo; (closed flights) as an
                <strong> operational alert</strong> — it answers &ldquo;are there any passengers we need to worry about?&rdquo;
              </span>
            </div>
            <div className="flex gap-2">
              <Badge variant="outline" className="shrink-0">Tree view</Badge>
              <span className="text-muted-foreground text-xs">
                &ldquo;Not Checked-In&rdquo; node in the passenger flow tree — structural visualization of the pipeline.
              </span>
            </div>
          </div>
        </div>
      </Section>

      <Section title="API Endpoint" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Method">GET</KV>
        <KV label="URL"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">/flights/&#123;flight_number&#125;/dashboard?origin=&#123;origin&#125;&amp;date=&#123;date&#125;</code></KV>
        <KV label="Response field"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateSummary.booked</code></KV>
      </Section>

      <Section title="Database Collections" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <p>The dashboard API queries <strong>four</strong> MongoDB collections. This tile uses two:</p>
        <div className="space-y-1.5 mt-1">
          <KV label="Primary"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">passenger_list</code> — contains the <code className="text-xs bg-muted px-1.5 py-0.5 rounded">passengers[]</code> array with individual passenger records</KV>
          <KV label="Gender"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">reservations</code> — DOCSEntry passport data, cross-referenced by PNR + lastName for gender enrichment</KV>
          <KV label="Sort">Latest document by <code className="text-xs bg-muted px-1.5 py-0.5 rounded">fetchedAt</code> (descending) in each collection</KV>
        </div>
        <Separator className="my-2" />
        <p className="font-medium">Key fields per passenger record:</p>
        <ul className="list-disc list-inside text-muted-foreground space-y-0.5 ml-1">
          <li><code className="text-xs bg-muted px-1 rounded">isCheckedIn</code> — boolean, from Sabre <code className="text-xs bg-muted px-1 rounded">CheckIn_Info/CheckInStatus</code></li>
          <li><code className="text-xs bg-muted px-1 rounded">isBoarded</code> — boolean, from Sabre <code className="text-xs bg-muted px-1 rounded">Boarding_Info/BoardStatus</code></li>
          <li><code className="text-xs bg-muted px-1 rounded">cabin</code> — &quot;Y&quot; (Economy) or &quot;J&quot; (Business)</li>
          <li><code className="text-xs bg-muted px-1 rounded">isChild</code> — boolean, true if CHD edit code present</li>
          <li><code className="text-xs bg-muted px-1 rounded">hasInfant</code> — boolean, true if INF edit code on parent</li>
          <li><code className="text-xs bg-muted px-1 rounded">isStandby</code> — boolean, true if passengerType is &quot;S&quot;</li>
          <li><code className="text-xs bg-muted px-1 rounded">boardingPassIssued</code> — boolean, from Sabre <code className="text-xs bg-muted px-1 rounded">BoardingPassFlag</code></li>
          <li><code className="text-xs bg-muted px-1 rounded">checkInSequence</code> — integer, from Sabre <code className="text-xs bg-muted px-1 rounded">CheckIn_Info/CheckInNumber</code></li>
          <li><code className="text-xs bg-muted px-1 rounded">editCodes</code> — array, e.g. [&quot;ET&quot;, &quot;CHD&quot;, &quot;DOCS&quot;]</li>
        </ul>
      </Section>

      <Section title="Calculation Logic" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <p className="font-medium">State Assignment Rule:</p>
        <CodeBlock>{`for each passenger in passengers[]:
  if passenger.isBoarded == true:
      state = "boarded"       ← NOT counted here
  elif passenger.isCheckedIn == true:
      state = "checkedIn"     ← NOT counted here
  else:
      state = "booked"        ← COUNTED in this tile`}</CodeBlock>

        <Separator className="my-2" />
        <p className="font-medium mt-2">Metrics Computed:</p>

        <div className="space-y-3 mt-1">
          <div>
            <Badge variant="secondary" className="mb-1">Total (headline number)</Badge>
            <CodeBlock>{`booked.totalPassengers = count of booked passengers (seated)
booked.totalSouls = totalPassengers + infants

Infants are NOT separate passenger records in Sabre.
They add +1 soul via the parent's hasInfant flag.`}</CodeBlock>
          </div>

          <div>
            <Badge variant="secondary" className="mb-1">Not Checked-In</Badge>
            <CodeBlock>{`booked.totalPassengers (same as headline)
All passengers in "booked" state are by definition not checked-in.`}</CodeBlock>
          </div>

          <div>
            <Badge variant="secondary" className="mb-1">Business / Economy</Badge>
            <CodeBlock>{`if cabin == "J":
    booked.business += 1  (seated passengers only)
else:  # cabin == "Y" (default)
    booked.economy += 1

Note: Cabin counts do NOT include infant inflation.`}</CodeBlock>
          </div>

          <div>
            <Badge variant="secondary" className="mb-1">Adults / Children / Infants</Badge>
            <CodeBlock>{`if passenger.isChild == true:
    booked.children += 1
else:
    booked.adults += 1

if passenger.hasInfant == true:
    booked.infants += 1

Note: A child (CHD) has own seat and counts as a passenger.
An infant (INF) is a lap baby on the parent — NOT a
separate passenger record. The parent has hasInfant=true.`}</CodeBlock>
          </div>
        </div>
      </Section>

      <Section title="Source Code Reference" icon={<Code className="h-4 w-4 text-purple-500" />}>
        <KV label="Backend">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">backend/api/routes/flights.py</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_analyze_passengers()</code>
        </KV>
        <KV label="Frontend">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">frontend/components/dashboard/state-panels.tsx</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">StatePanels</code>
        </KV>
        <KV label="Types">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">frontend/lib/types.ts</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">StateBucket</code>
        </KV>
      </Section>

      <Section title="Edit Codes Considered" icon={<GitBranch className="h-4 w-4 text-rose-500" />} defaultOpen={false}>
        <p>Sabre GetPassengerListRQ v4 edit codes used in classification:</p>
        <div className="mt-1 space-y-1">
          <KV label="CHD">Child passenger — has own seat, counted via <code className="text-xs bg-muted px-1 rounded">isChild</code></KV>
          <KV label="INF">Parent with lap infant — infant counted via <code className="text-xs bg-muted px-1 rounded">hasInfant</code></KV>
          <KV label="M">Meal code (standard meal) — <strong>NOT gender</strong></KV>
          <KV label="F">Full fare indicator — <strong>NOT gender</strong></KV>
          <KV label="ET">Electronic ticket issued</KV>
          <KV label="JS">Jump seat passenger</KV>
          <KV label="FF/GLD/SLV">Frequent flyer tier codes</KV>
        </div>
        <Separator className="my-2" />
        <p className="font-medium">Passenger Types (from Sabre):</p>
        <div className="mt-1 space-y-1">
          <KV label="F">Full fare passenger</KV>
          <KV label="P">Positive space passenger</KV>
          <KV label="S">Standby passenger — can be revenue or non-revenue</KV>
          <KV label="E">Employee / Non-revenue passenger</KV>
        </div>
        <Separator className="my-2" />
        <p className="text-muted-foreground text-xs">
          Note: Gender is <strong>not available</strong> from Sabre GetPassengerListRS.
          The &quot;M&quot; and &quot;F&quot; edit codes are meal/fare indicators, not gender.
          However, gender IS available from <strong>Trip_SearchRS</strong> (reservations)
          via the APIS DOCSEntry (passport data). The dashboard cross-references
          reservation data by PNR + lastName to enrich the passenger list with
          gender (~98% coverage for international flights).
        </p>
      </Section>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  CHECKED-IN TILE INFO                                     */
/* ────────────────────────────────────────────────────────── */
function CheckedInInfo() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <UserCheck className="h-5 w-5 text-amber-500" />
        <h3 className="text-lg font-semibold">Checked-In Tile</h3>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">
        Shows passengers who have <strong>completed check-in</strong> but have <strong>not yet boarded</strong> the aircraft.
        These passengers are in the intermediate stage — past the counter, but still in the terminal.
      </p>

      <Section title={"Phase-Aware Metrics Row — Explained"} icon={<Info className="h-4 w-4 text-amber-500" />}>
        <div className="space-y-3">
          <p className="leading-relaxed">
            The second row in this tile changes based on the <strong>flight phase</strong> to show the most
            operationally relevant metric:
          </p>
          <div className="bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
            <p className="font-medium text-amber-800 dark:text-amber-300 mb-2">Phase → Metric Shown:</p>
            <table className="w-full text-xs">
              <tbody>
                <tr className="border-b border-amber-200/50 dark:border-amber-800/50">
                  <td className="py-1.5 font-medium text-blue-600 dark:text-blue-400">CHECK-IN</td>
                  <td className="py-1.5">Pending check-in: X</td>
                  <td className="py-1.5 text-amber-700 dark:text-amber-400">How many still need to check in</td>
                </tr>
                <tr className="border-b border-amber-200/50 dark:border-amber-800/50">
                  <td className="py-1.5 font-medium text-amber-600 dark:text-amber-400">BOARDING</td>
                  <td className="py-1.5">Checked-in, not boarded: X</td>
                  <td className="py-1.5 text-amber-700 dark:text-amber-400">Who checked in but hasn&apos;t scanned at gate</td>
                </tr>
                <tr>
                  <td className="py-1.5 font-medium text-red-600 dark:text-red-400">CLOSED / DEPARTED</td>
                  <td className="py-1.5">Check-in rate: X%</td>
                  <td className="py-1.5 text-amber-700 dark:text-amber-400">Final check-in completion percentage</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            This replaces the previous static &ldquo;Not Checked In&rdquo; row — that information now lives
            exclusively on the <strong>Booked card</strong> (headline) and <strong>Others card</strong> (operational alert),
            eliminating redundancy while adding unique, phase-appropriate context to this card.
          </p>
        </div>
      </Section>

      <Section title="API Endpoint" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Method">GET</KV>
        <KV label="URL"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">/flights/&#123;flight_number&#125;/dashboard?origin=&#123;origin&#125;&amp;date=&#123;date&#125;</code></KV>
        <KV label="Response field"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateSummary.checkedIn</code></KV>
      </Section>

      <Section title="Database Collections" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <p>Same query as Booked tile — all tiles share one API call.</p>
        <div className="space-y-1.5 mt-1">
          <KV label="Collection"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">passenger_list</code></KV>
          <KV label="Key fields"><code className="text-xs bg-muted px-1 rounded">isCheckedIn</code>, <code className="text-xs bg-muted px-1 rounded">isBoarded</code></KV>
        </div>
      </Section>

      <Section title="Calculation Logic" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <p className="font-medium">State Assignment Rule:</p>
        <CodeBlock>{`for each passenger in passengers[]:
  if passenger.isBoarded == true:
      state = "boarded"       ← NOT counted here
  elif passenger.isCheckedIn == true:
      state = "checkedIn"     ← COUNTED in this tile
  else:
      state = "booked"        ← NOT counted here

IMPORTANT: A passenger who is BOTH isCheckedIn=true AND
isBoarded=true is counted as "boarded", NOT "checkedIn".
The precedence is: boarded > checkedIn > booked.`}</CodeBlock>

        <Separator className="my-2" />
        <p className="font-medium mt-2">Metrics:</p>

        <div className="space-y-3 mt-1">
          <div>
            <Badge variant="secondary" className="mb-1">Total Checked-in (headline)</Badge>
            <CodeBlock>{`checkedIn.totalPassengers = count of checked-in passengers (seated)
checkedIn.totalSouls = totalPassengers + infants`}</CodeBlock>
          </div>

          <div>
            <Badge variant="secondary" className="mb-1">Business / Economy</Badge>
            <CodeBlock>{`Same cabin logic as Booked:
  cabin "J" → business, else → economy
  Counts seated passengers only (no infant inflation)`}</CodeBlock>
          </div>

          <div>
            <Badge variant="secondary" className="mb-1">Adults / Children / Infants</Badge>
            <CodeBlock>{`Same demographic logic as Booked tile:
  isChild=true → children++
  else → adults++
  hasInfant=true → infants++`}</CodeBlock>
          </div>
        </div>
      </Section>

      <Section title="Source Code Reference" icon={<Code className="h-4 w-4 text-purple-500" />}>
        <KV label="Backend">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">backend/api/routes/flights.py</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_analyze_passengers()</code>
        </KV>
        <KV label="State key"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateBreakdown.checkedIn</code></KV>
      </Section>

      <Section title="Related Fields" icon={<Database className="h-4 w-4 text-cyan-500" />} defaultOpen={false}>
        <p className="text-muted-foreground leading-relaxed">
          These additional fields are stored per passenger but not displayed in the tile:
        </p>
        <div className="mt-1 space-y-1">
          <KV label="checkInSequence">Sabre&apos;s <code className="text-xs bg-muted px-1 rounded">CheckInNumber</code> — the order in which the
            passenger checked in. Used by carriers for involuntary bumping priority.</KV>
          <KV label="boardingPassIssued">From Sabre&apos;s <code className="text-xs bg-muted px-1 rounded">BoardingPassFlag</code> — whether a
            boarding pass has been printed. Note: for standby passengers this is false even if a standby
            boarding pass exists.</KV>
        </div>
      </Section>

      <Section title="Why Can This Be Zero?" icon={<Info className="h-4 w-4 text-muted-foreground" />} defaultOpen={false}>
        <p className="text-muted-foreground leading-relaxed">
          This value is <strong>0</strong> when either:
        </p>
        <ul className="list-disc list-inside text-muted-foreground space-y-0.5 ml-1 mt-1">
          <li>No passengers have checked in yet (early pre-departure)</li>
          <li>All checked-in passengers have already boarded (they move to the Boarded bucket)</li>
          <li>Check-in is handled at the gate, going directly from booked → boarded</li>
        </ul>
        <p className="text-muted-foreground mt-2">
          This is expected behavior — the checked-in bucket is a <em>transient</em> state that empties as
          boarding progresses.
        </p>
      </Section>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  BOARDED TILE INFO                                        */
/* ────────────────────────────────────────────────────────── */
function BoardedInfo() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <PlaneTakeoff className="h-5 w-5 text-emerald-500" />
        <h3 className="text-lg font-semibold">Boarded Tile</h3>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">
        Shows passengers who have <strong>scanned their boarding pass at the gate</strong> and are physically on the aircraft.
        This is the final state before departure.
      </p>

      <Section title="API Endpoint" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Method">GET</KV>
        <KV label="URL"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">/flights/&#123;flight_number&#125;/dashboard?origin=&#123;origin&#125;&amp;date=&#123;date&#125;</code></KV>
        <KV label="Response field"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateSummary.boarded</code></KV>
      </Section>

      <Section title="Database Collections" icon={<Database className="h-4 w-4 text-amber-500" />}>
        <div className="space-y-1.5">
          <KV label="Collection"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">passenger_list</code></KV>
          <KV label="Key field"><code className="text-xs bg-muted px-1 rounded">isBoarded</code> — boolean set by Sabre when passenger scans at gate</KV>
        </div>
      </Section>

      <Section title="Calculation Logic" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <p className="font-medium">State Assignment — Highest Priority:</p>
        <CodeBlock>{`for each passenger in passengers[]:
  if passenger.isBoarded == true:
      state = "boarded"       ← COUNTED in this tile
  elif passenger.isCheckedIn == true:
      state = "checkedIn"     ← NOT counted here
  else:
      state = "booked"        ← NOT counted here

"boarded" has the HIGHEST priority in state assignment.
If isBoarded=true, the passenger goes here regardless
of their isCheckedIn value.`}</CodeBlock>

        <Separator className="my-2" />
        <p className="font-medium mt-2">Headline Number — Total Boarded:</p>
        <CodeBlock>{`boarded.totalPassengers = count of boarded passengers (seated)
boarded.totalSouls = totalPassengers + infants (lap children)

Headline shows totalPassengers (seated manifest records).
Souls count (passengers + infants) used for safety/ops.`}</CodeBlock>

        <Separator className="my-2" />
        <p className="font-medium mt-2">Cabin & Demographic Breakdown:</p>
        <CodeBlock>{`Business / Economy:
  cabin "J" → business, cabin "Y" → economy
  Counts seated passengers only (no infant inflation)

Adults / Children / Infants:
  isChild=true → children count
  else → adults count
  hasInfant=true → infants count (lap infants, not seated)`}</CodeBlock>
      </Section>

      <Section title="Relationship to Hero Card" icon={<Info className="h-4 w-4 text-muted-foreground" />} defaultOpen={false}>
        <p className="text-muted-foreground leading-relaxed">
          The header strip shows <strong>totalSouls</strong> (passengers + infants) for safety.
          The <strong>Total Pax</strong> card shows seated manifest records only.
        </p>
        <CodeBlock>{`overview.totalSouls = passengerSummary.totalSouls
overview.manifestRecords = passengerSummary.totalPassengers`}</CodeBlock>
        <p className="text-muted-foreground mt-1">
          Cabin breakdowns (Business / Economy) count seated passengers only — infants are not inflated into cabin counts.
        </p>
      </Section>

      <Section title="Source Code Reference" icon={<Code className="h-4 w-4 text-purple-500" />}>
        <KV label="Backend">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">backend/api/routes/flights.py</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_analyze_passengers()</code>
        </KV>
        <KV label="State key"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateBreakdown.boarded</code></KV>
        <KV label="Hero link"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">overview.soulsOnBoard</code> in <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_build_dashboard_payload()</code></KV>
      </Section>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  OTHER PASSENGERS TILE INFO                               */
/* ────────────────────────────────────────────────────────── */
function OthersInfo() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Info className="h-5 w-5 text-muted-foreground" />
        <h3 className="text-lg font-semibold">Other Passengers Tile</h3>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">
        Shows non-standard passenger categories that don&apos;t fit into the Booked → Checked-In → Boarded lifecycle.
        These are sourced from different fields and collections.
      </p>

      <Section title={'"Not Checked In" vs "No Show" — Explained'} icon={<Info className="h-4 w-4 text-orange-500" />}>
        <div className="space-y-3">
          <p className="leading-relaxed">
            This row shows the <strong>operational significance</strong> of passengers who never checked in.
            The label and meaning change depending on the flight&apos;s status:
          </p>
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/60">
                  <th className="text-left px-3 py-2 font-medium">Flight Status</th>
                  <th className="text-left px-3 py-2 font-medium">Label</th>
                  <th className="text-left px-3 py-2 font-medium">What It Means</th>
                  <th className="text-left px-3 py-2 font-medium">Action Required</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t">
                  <td className="px-3 py-2"><Badge variant="outline">OPENCI</Badge></td>
                  <td className="px-3 py-2 text-orange-500 font-medium">Not Checked In</td>
                  <td className="px-3 py-2 text-muted-foreground">Passengers on manifest who haven&apos;t arrived at check-in yet. They may still show up.</td>
                  <td className="px-3 py-2 text-muted-foreground">Monitor — passenger may still arrive</td>
                </tr>
                <tr className="border-t">
                  <td className="px-3 py-2"><Badge variant="outline">BOARDING</Badge></td>
                  <td className="px-3 py-2 text-orange-500 font-medium">Not Checked In</td>
                  <td className="px-3 py-2 text-muted-foreground">Passengers at risk of missing the flight. Check-in is closing.</td>
                  <td className="px-3 py-2 text-muted-foreground">Alert gate / paging system</td>
                </tr>
                <tr className="border-t">
                  <td className="px-3 py-2"><Badge variant="destructive">PDC / FINAL</Badge></td>
                  <td className="px-3 py-2 text-orange-500 font-medium">No Show</td>
                  <td className="px-3 py-2 text-muted-foreground">Confirmed no-shows. Flight has departed. These passengers never checked in.</td>
                  <td className="px-3 py-2 text-muted-foreground">Offload bags (ICAO Annex 17), report for revenue accounting</td>
                </tr>
              </tbody>
            </table>
          </div>
          <Separator className="my-2" />
          <p className="font-medium">How does this relate to the Booked tile?</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            The <strong>Booked tile</strong> shows the same passengers with their full breakdown (cabin class, adults/children/infants).
            It answers: &ldquo;Who are these passengers and where are they sitting?&rdquo;<br />
            The <strong>Others card</strong> shows just the count as an operational alert.
            It answers: &ldquo;Do I need to take any action for missing passengers?&rdquo;<br />
            The <strong>Checked-In card</strong> shows it for check-in awareness.
            It answers: &ldquo;How complete is our check-in process?&rdquo;
          </p>
          <Separator className="my-2" />
          <p className="font-medium">Data source and the ~ indicator</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            This count comes directly from the Sabre manifest (GetPassengerListRS) — it counts passengers
            where <code className="text-xs bg-muted px-1 rounded">isCheckedIn = false</code>.
            The <strong>~</strong> symbol indicates the value is <strong>inferred from manifest data</strong> (always available),
            as opposed to coming from Sabre Trip Reports (MLC/MLX) which require a separate API call.
            When Trip Report data is available, the &ldquo;No Show&rdquo; count may differ as it cross-references the
            &ldquo;ever-booked&rdquo; list against the current manifest — this is more authoritative but not always available.
          </p>
        </div>
      </Section>

      <Section title="API Endpoint" icon={<Server className="h-4 w-4 text-blue-500" />}>
        <KV label="Response field"><code className="text-xs bg-muted px-1.5 py-0.5 rounded">stateSummary.others</code></KV>
      </Section>

      <Section title="Jump Seat" icon={<Calculator className="h-4 w-4 text-emerald-500" />}>
        <p className="font-medium">Source: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">flight_status</code> collection</p>
        <CodeBlock>{`jumpSeat = flight_status.jumpSeat.cockpit
         + flight_status.jumpSeat.cabin

Source: Sabre ACS_FlightDetailRS response
  → JumpSeat.Cockpit (number of cockpit jump seats in use)
  → JumpSeat.Cabin (number of cabin jump seats in use)

These come from the flight_status document, NOT from
the passenger_list. Jump seat occupants are typically
crew or deadheading staff.`}</CodeBlock>
        <div className="mt-2 space-y-1">
          <KV label="Collection"><code className="text-xs bg-muted px-1 rounded">flight_status</code></KV>
          <KV label="Fields"><code className="text-xs bg-muted px-1 rounded">jumpSeat.cockpit</code>, <code className="text-xs bg-muted px-1 rounded">jumpSeat.cabin</code></KV>
        </div>
      </Section>

      <Section title="Non-Revenue" icon={<Calculator className="h-4 w-4 text-amber-500" />}>
        <p className="font-medium">Source: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">passenger_list</code> collection</p>
        <CodeBlock>{`nonRevenue = 0
for each passenger in passengers[]:
    is_staff = passengerType == "E" or isRevenue == false
    if is_staff:
        nonRevenue += 1

Classification rules (per Sabre GetPassengerListRQ v4):
  "E" = Employee passenger → always non-revenue
  "S" = Standby passenger → can be revenue OR non-revenue
        (determined by Indicators array, not PassengerType)
  "F" = Full fare passenger → typically revenue
  "P" = Positive space → typically revenue

The Indicators array in Sabre response contains either
"Revenue" or "NonRevenue" — this is the authoritative
source for revenue classification.

Non-revenue passengers are STILL counted in state buckets
(booked/checkedIn/boarded) — this is an orthogonal count.`}</CodeBlock>
        <div className="mt-2 space-y-1">
          <KV label="Collection"><code className="text-xs bg-muted px-1 rounded">passenger_list</code></KV>
          <KV label="Fields"><code className="text-xs bg-muted px-1 rounded">isRevenue</code> (from Indicators), <code className="text-xs bg-muted px-1 rounded">passengerType</code></KV>
        </div>
      </Section>

      <Section title="Offloaded" icon={<Calculator className="h-4 w-4 text-rose-500" />}>
        <p className="font-medium">Source: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">trip_reports</code> collection (Sabre Trip_ReportsRQ MLX)</p>
        <CodeBlock>{`offloaded = len(trip_reports.cancelledPassengers)

Source: Sabre Trip_ReportsRQ with ReportType="MLX"
  → Returns list of CANCELLED passengers for the flight

The MLX report contains passengers who were previously
booked but had their reservations cancelled/removed from
the flight. This includes:
  - Voluntary cancellations
  - Involuntary offloads (oversold flight)
  - Duplicate booking removals
  - Schedule change removals

When trip report data is available:
  offloadedAvailable = true
  offloaded = count of cancelled passengers

When NOT available (API not called or failed):
  offloadedAvailable = false
  UI displays "N/A"`}</CodeBlock>
        <div className="mt-2 space-y-1">
          <KV label="Collection"><code className="text-xs bg-muted px-1 rounded">trip_reports</code></KV>
          <KV label="Field"><code className="text-xs bg-muted px-1 rounded">cancelledPassengers[]</code> (from MLX report)</KV>
          <KV label="API"><code className="text-xs bg-muted px-1 rounded">Trip_ReportsRQ v1.3.0</code> with <code className="text-xs bg-muted px-1 rounded">ReportType=MLX</code></KV>
        </div>
      </Section>

      <Section title="No Show" icon={<Calculator className="h-4 w-4 text-rose-500" />}>
        <p className="font-medium">Source: <code className="text-xs bg-muted px-1.5 py-0.5 rounded">trip_reports</code> + <code className="text-xs bg-muted px-1.5 py-0.5 rounded">passenger_list</code> cross-reference</p>
        <CodeBlock>{`noShow = MLC ever-booked passengers
       MINUS current passenger_list manifest
       (only computed when flight status is FINAL or PDC)

Source: Sabre Trip_ReportsRQ with ReportType="MLC"
  → Returns ALL passengers EVER booked on the flight

Logic:
  1. Get ever-booked list from MLC report
  2. Get current manifest from passenger_list
  3. Build set of (PNR, LastName) from current manifest
  4. Count MLC passengers NOT in current manifest set
  5. That count = no-show passengers

A no-show is a passenger who:
  - Was booked on the flight (appeared in MLC)
  - Is NOT in the current final manifest
  - Did not explicitly cancel (those are in MLX)

Requires flight status FINAL or PDC to be meaningful.
During OPENCI, passengers may still arrive.

When NOT available:
  noShowAvailable = false
  UI displays "N/A"`}</CodeBlock>
        <div className="mt-2 space-y-1">
          <KV label="Collections"><code className="text-xs bg-muted px-1 rounded">trip_reports</code> + <code className="text-xs bg-muted px-1 rounded">passenger_list</code></KV>
          <KV label="Fields"><code className="text-xs bg-muted px-1 rounded">everBookedPassengers[]</code> (MLC) vs <code className="text-xs bg-muted px-1 rounded">passengers[]</code> (manifest)</KV>
          <KV label="Condition">Only computed when <code className="text-xs bg-muted px-1 rounded">flightStatus</code> is FINAL or PDC</KV>
        </div>
      </Section>

      <Section title="Headline Number" icon={<Info className="h-4 w-4 text-muted-foreground" />} defaultOpen={false}>
        <CodeBlock>{`others total (displayed) = jumpSeat + nonRevenue

Only jumpSeat and nonRevenue contribute to the headline.
Offloaded and noShow are shown as separate rows.
They show "N/A" if trip report data is not yet available
for this flight (offloadedAvailable / noShowAvailable = false).`}</CodeBlock>
      </Section>

      <Section title="Source Code Reference" icon={<Code className="h-4 w-4 text-purple-500" />}>
        <KV label="Backend">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">backend/api/routes/flights.py</code> →{" "}
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_build_dashboard_payload()</code>
        </KV>
        <KV label="Jump seat src">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">flight_status</code> document → <code className="text-xs bg-muted px-1.5 py-0.5 rounded">jumpSeat</code>
        </KV>
        <KV label="Non-rev src">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">_analyze_passengers()</code> → <code className="text-xs bg-muted px-1.5 py-0.5 rounded">nonRevenue</code> counter
        </KV>
      </Section>
    </div>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  DATA FLOW OVERVIEW                                       */
/* ────────────────────────────────────────────────────────── */
function DataFlowSection() {
  return (
    <Section title="End-to-End Data Flow" icon={<GitBranch className="h-4 w-4 text-cyan-500" />} defaultOpen={false}>
      <CodeBlock>{`Sabre SOAP APIs (5 calls per ingestion cycle)
  ├─ ACS_FlightDetailRQ  → flight status, gate, aircraft
  ├─ GetPassengerListRQ  → passenger manifest (check-in/boarding)
  ├─ Trip_SearchRQ       → reservations with gender/DOB/nationality
  ├─ Trip_ReportsRQ MLX  → cancelled passengers (offloaded)
  └─ Trip_ReportsRQ MLC  → ever-booked passengers (no-show detect)
  
Backend Feeder (converter.py)
  ↓ Parses XML → normalized JSON per passenger:
  │  isCheckedIn    ← CheckIn_Info/CheckInStatus
  │  isBoarded      ← Boarding_Info/BoardStatus
  │  isRevenue      ← Indicators[] contains "Revenue"
  │  isChild        ← "CHD" in EditCodeList
  │  hasInfant      ← "INF" in EditCodeList
  │  isStandby      ← PassengerType == "S"
  │  boardingPassIssued ← BoardingPassFlag
  │  checkInSequence    ← CheckIn_Info/CheckInNumber
  ↓ Stores in MongoDB collections
  
Gender Enrichment (cross-reference)
  ↓ Reservation DOCSEntry has passport gender (M/F)
  ↓ Dashboard matches by PNR + lastName
  ↓ ~98% coverage for international flights
  
MongoDB Collections
  ├─ passenger_list  (passengers[], totals, cabin summary)
  ├─ flight_status   (status, gate, aircraft, jump seats)
  ├─ reservations    (PNR details, gender, DOB, nationality)
  ├─ trip_reports    (cancelled + ever-booked passengers)
  └─ changes         (tracked diffs between snapshots)
  
Dashboard API (flights.py → /dashboard)
  ↓ Queries latest docs from all 5 collections
  ↓ Builds gender lookup from reservations → DOCSEntry
  ↓ Runs _analyze_passengers() with gender enrichment
  ↓ Staff = passengerType "E" OR isRevenue=false
  ↓ Offloaded = len(trip_reports.cancelledPassengers)
  ↓ NoShow = MLC ever-booked minus current manifest
  ↓ Aggregates change counts by changeType from changes
  ↓ Builds stateSummary with 3 state buckets + others
  
React Frontend (state-panels.tsx)
  ↓ Renders 4 tiles from stateSummary
  ↓ Auto-refreshes every 30 seconds`}</CodeBlock>
    </Section>
  );
}

/* ────────────────────────────────────────────────────────── */
/*  MAIN PANEL                                               */
/* ────────────────────────────────────────────────────────── */
const INFO_MAP: Record<TileInfoKey, { component: React.FC; label: string }> = {
  booked: { component: BookedInfo, label: "Booked" },
  checkedIn: { component: CheckedInInfo, label: "Checked-In" },
  boarded: { component: BoardedInfo, label: "Boarded" },
  others: { component: OthersInfo, label: "Other Passengers" },
};

export function TileInfoPanel({ activeTab }: TileInfoPanelProps) {
  const entry = INFO_MAP[activeTab];
  const InfoContent = entry.component;

  return (
    <div className="space-y-4">
      <InfoContent />
      <Separator />
      <DataFlowSection />
    </div>
  );
}
