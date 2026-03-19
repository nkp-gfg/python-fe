// FalconEye API types — mirrors FastAPI response shapes

export interface Aircraft {
  type: string;
  registration: string;
  configNumber?: string;
  seatConfig?: string;
}

export interface Schedule {
  scheduledDeparture: string;
  estimatedDeparture: string;
  scheduledArrival: string;
  estimatedArrival: string;
  durationMinutes: number;
}

export interface ClassCounts {
  authorized: number;
  booked: number;
  available: number;
  thru: number;
  local: number;
  onBoard: number;
  boardingPasses: number;
  meals: number;
  revenue: number;
  nonRevenue: number;
}

export interface JumpSeat {
  cockpit: number;
  cabin: number;
  cockpitInUse: boolean;
  cabinInUse: boolean;
}

export interface FlightListItem {
  airline: string;
  flightNumber: string;
  origin: string;
  destination?: string;
  departureDate: string;
  status: string;
  gate: string;
  aircraft: Aircraft;
  schedule: Schedule;
  passengerCounts: Record<string, ClassCounts>;
  jumpSeat: JumpSeat;
  passengerSummary?: {
    totalPassengers: number;
    adultCount: number;
    childCount: number;
    infantCount: number;
    totalSouls: number;
  };
  operationalSummary?: {
    checkedIn: number;
    boarded: number;
    notCheckedIn: number;
    soulsOnBoard: number;
    economySouls: number;
    businessSouls: number;
  };
  fetchedAt: string;
}

export interface RouteSummary {
  origin: string;
  destination: string;
  departureDate: string;
}

export interface CabinSummary {
  cabin: string;
  count: number;
  authorized: number;
  destination: string;
}

export interface CabinBreakdown {
  total: number;
  passengers: {
    total: number;
    male: number;
    female: number;
    children: number;
    infants: number;
  };
  staff: {
    total: number;
    male: number;
    female: number;
  };
}

export interface PassengerAnalysis {
  economy: CabinBreakdown;
  business: CabinBreakdown;
  checkedIn: number;
  boarded: number;
  notCheckedIn: number;
  revenue: number;
  nonRevenue: number;
  totalMale: number;
  totalFemale: number;
  totalChildren: number;
  totalInfants: number;
  cabinTotals: {
    economy: { passengers: number; souls: number };
    business: { passengers: number; souls: number };
  };
  stateBreakdown: {
    booked: StateBucket;
    checkedIn: StateBucket;
    boarded: StateBucket;
  };
}

export interface StateBucket {
  totalPassengers: number;
  totalSouls: number;
  economy: number;
  business: number;
  adults: number;
  children: number;
  infants: number;
}

export interface OthersSummary {
  jumpSeat: number;
  nonRevenue: number;
  offloaded: number | null;
  noShow: number | null;
  offloadedAvailable: boolean;
  noShowAvailable: boolean;
}

export interface OverviewSummary {
  soulsOnBoard: number;
  manifestRecords: number;
  totalSouls: number;
  economySouls: number;
  businessSouls: number;
  trackedChanges: number;
}

export interface TreeBadge {
  type: "M" | "F" | "C" | "I";
  value: number;
}

export interface FlightTreeNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  borderColor: string;
  textColor: string;
  label: string;
  value: number;
  subLabel?: string;
  badges: TreeBadge[];
}

export interface FlightTreeEdge {
  from: string;
  to: string;
}

export interface FlightTreeCard {
  id: string;
  label: string;
  value: number;
  subLabel?: string;
  borderColor: string;
  textColor: string;
}

export interface FlightTree {
  title: string;
  badge: string;
  width: number;
  height: number;
  nodes: FlightTreeNode[];
  edges: FlightTreeEdge[];
  statusCards: FlightTreeCard[];
}

export interface PassengerSummary {
  totalPassengers: number;
  adultCount: number;
  childCount: number;
  infantCount: number;
  totalSouls: number;
  cabinSummary: CabinSummary[];
}

export interface FlightDashboard {
  flightStatus: FlightListItem | null;
  route: RouteSummary;
  passengerSummary: PassengerSummary;
  analysis: PassengerAnalysis;
  changeSummary: Record<string, number>;
  overview: OverviewSummary;
  tree: FlightTree | null;
  stateSummary: {
    booked: StateBucket;
    checkedIn: StateBucket;
    boarded: StateBucket;
    others: OthersSummary;
  };
}
