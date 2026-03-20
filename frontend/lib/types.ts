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
  economySouls: number;
  businessSouls: number;
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

export interface DataIntegrity {
  valid: boolean;
  checks: number;
  warnings: string[];
}

export interface TreeBadge {
  type: "M" | "F" | "A" | "C" | "I";
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
  value: number | string;
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
  value: number | string;
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
  dataIntegrity: DataIntegrity;
  fetchedAt: string;
  tree: FlightTree | null;
  stateSummary: {
    booked: StateBucket;
    checkedIn: StateBucket;
    boarded: StateBucket;
    others: OthersSummary;
  };
}

// --- Sabre Ingestion API Types ---

export interface SabreIngestRequest {
  airline?: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  departureDateTime: string;
}

export interface SabreApiResult {
  status: "success" | "error";
  snapshotId?: string;
  durationMs?: number;
  changesStored?: number;
  isDuplicate?: boolean;
  error?: string;
}

export interface SabreFlightIngestResult {
  flight: {
    airline: string;
    flightNumber: string;
    origin: string;
    departureDate: string;
    departureDateTime: string;
  };
  success: boolean;
  apis: {
    flightStatus: SabreApiResult;
    passengerList: SabreApiResult;
    reservations: SabreApiResult;
  };
}

export interface SabreIngestResponse {
  message: string;
  processedFlights: number;
  result: SabreFlightIngestResult;
}

export interface SabreBatchRequest {
  flights: SabreIngestRequest[];
}

export interface SabreBatchAccepted {
  jobId: string;
  status: string;
  flightsQueued: number;
  message: string;
  pollUrl: string;
}

export interface SabreJobStatus {
  jobId: string;
  status: "accepted" | "running" | "completed" | "failed";
  flightsQueued: number;
  flightsProcessed: number;
  submittedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  results: SabreFlightIngestResult[] | null;
  error: string | null;
}

// --- Passenger Table Types ---

export interface PassengerRecord {
  lastName: string;
  firstName: string;
  pnr: string;
  passengerId: string;
  lineNumber: number;
  priorityCode: string;
  bookingClass: string;
  desiredBookingClass: string;
  cabin: string;
  seat: string;
  destination: string;
  passengerType: string;
  isStandby: boolean;
  corpId: string;
  seniorityDate: string;
  bagCount: number;
  isCheckedIn: boolean;
  isBoarded: boolean;
  boardingPassIssued: boolean;
  checkInSequence: number;
  isRevenue: boolean;
  isThru: boolean;
  isChild: boolean;
  hasInfant: boolean;
  vcrType: string;
  ticketNumber: string;
  editCodes: string[];
}

export interface PassengerListResponse {
  airline: string;
  flightNumber: string;
  origin: string;
  destination: string;
  departureDate: string;
  fetchedAt: string;
  aircraftType: string;
  cabinSummary: CabinSummary[];
  totalPassengers: number;
  adultCount: number;
  childCount: number;
  infantCount: number;
  totalSouls: number;
  passengers: PassengerRecord[];
}

// --- Standby List Types ---

export interface StandbyEntry {
  lastName: string;
  firstName: string;
  pnr: string;
  lineNumber: number;
  priorityCode: string;
  bookingClass: string;
  desiredBookingClass: string;
  cabin: string;
  seat: string;
  destination: string;
  corpId: string;
  seniorityDate: string;
  isCheckedIn: boolean;
  boardingPassIssued: boolean;
  isRevenue: boolean;
}

export interface CabinAvailability {
  cabin: string;
  destination: string;
  authorized: number;
  available: number;
}

export interface StandbyListResponse {
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  cabinAvailability: CabinAvailability[];
  upgrade: { total: number; passengers: StandbyEntry[] };
  standby: { total: number; passengers: StandbyEntry[] };
}

// --- Passenger Detail Types (from GetPassengerDataRQ) ---

export interface BaggageRoute {
  segmentId: string;
  airline: string;
  flight: string;
  operatingAirline: string;
  operatingFlight: string;
  origin: string;
  destination: string;
  departureDate: string;
  departureTime: string;
  arrivalDate: string;
  arrivalTime: string;
  bookingClass: string;
  segmentStatus: string;
  lateCheckin: boolean;
  homePrintedBagTag: string;
  bagEmbargo: boolean;
}

export interface BagTagInfo {
  bagTagNumber: string;
  weight: string;
  unit: string;
  origin: string;
  destination: string;
}

export interface VcrInfo {
  fareBasisCode: string;
  bagAllowance: string;
}

export interface AeDetail {
  itemId: string;
  groupCode: string;
  statusCode: string;
  usedEMD: string;
  quantity: number;
  price: string;
  currency: string;
}

export interface RequiredInfo {
  code: string;
  detailStatus: string;
  freeText: string;
}

export interface ItinerarySegment {
  segmentId: string;
  airline: string;
  flight: string;
  operatingAirline: string;
  operatingFlight: string;
  marketingAirline: string;
  marketingFlight: string;
  bookingClass: string;
  marketingBookingClass: string;
  operatingBookingClass: string;
  origin: string;
  destination: string;
  departureDate: string;
  aircraftType: string;
  cabin: string;
  seat: string;
  passengerType: string;
  priority: string;
  bagCount: number;
  totalBagWeight: string;
  checkInNumber: string;
  editCodes: string[];
  bagTags: BagTagInfo[];
  vcrInfo: VcrInfo[];
  aeDetails: AeDetail[];
  requiredInfo: RequiredInfo[];
}

export interface FreeTextEntry {
  editCode: string;
  text: string;
}

export interface DocsData {
  documentType?: string;
  documentCountry?: string;
  documentNumber?: string;
  nationality?: string;
  dateOfBirth?: string;
  gender?: string;
  expiryDate?: string;
  lastName?: string;
  firstName?: string;
}

export interface PassengerEditEntry {
  name: string;
  attributes: Record<string, string>;
}

export interface TimaticEntry {
  country: string;
  text: string;
}

export interface DetailedPassenger {
  lineNumber: number;
  lastName: string;
  firstName: string;
  passengerId: string;
  pnr: string;
  nameAssociationId: string;
  groupCode: string;
  vcrNumber: string;
  couponNumber: string;
  gender: string;
  docsData: DocsData;
  baggageRoutes: BaggageRoute[];
  itinerary: ItinerarySegment[];
  requiredInfo: RequiredInfo[];
  freeText: FreeTextEntry[];
  passengerEdits: PassengerEditEntry[];
  timaticInfo: TimaticEntry[];
}

export interface PassengerDetailResponse {
  airline: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  status: string;
  completionStatus: string;
  errors: { code: string; message: string }[];
  totalPassengers: number;
  passengers: DetailedPassenger[];
}

// --- Change Types ---

export interface ChangeRecord {
  flightNumber: string;
  origin: string;
  departureDate: string;
  changeType: string;
  detectedAt: string;
  snapshotType?: string;
  sequenceNumber?: number;
  passenger?: {
    pnr: string;
    lastName: string;
    firstName: string;
  };
  field?: string;
  oldValue?: string | number | boolean;
  newValue?: string | number | boolean;
}

export interface ChangeSummaryResponse {
  flightNumber: string;
  changeTypes: Record<string, number>;
  totalChanges: number;
}

// --- Snapshot / Status History Types ---

export interface SnapshotMeta {
  flightNumber: string;
  origin: string;
  departureDate: string;
  snapshotType: string;
  sequenceNumber: number;
  fetchedAt: string;
  checksum?: string;
}

export interface FlightStatusRecord {
  airline: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  status: string;
  aircraft: Aircraft;
  schedule: Schedule;
  gate: string;
  terminal: string;
  boarding: { time: string; indicator: string };
  legs: { city: string; controllingCity: boolean; status: string }[];
  passengerCounts: Record<string, ClassCounts>;
  jumpSeat: JumpSeat;
}

// --- Reservation Types ---

export interface ReservationPassenger {
  lastName: string;
  firstName: string;
  nameId: string;
  nameType: string;
  gender: string;
  dateOfBirth: string;
  nationality: string;
  seatNumber: string;
  frequentFlyerNumber: string;
  frequentFlyerAirline: string;
}

export interface ReservationSegment {
  departureAirport: string;
  arrivalAirport: string;
  departureDate: string;
  arrivalDate: string;
  marketingAirline: string;
  flightNumber: string;
  bookingClass: string;
  status: string;
}

export interface Reservation {
  pnr: string;
  numberInParty: number;
  numberOfInfants: number;
  createdAt: string;
  updatedAt: string;
  passengers: ReservationPassenger[];
  segments: ReservationSegment[];
  tickets: { ticketNumber: string; eTicket: string }[];
}

export interface ReservationsResponse {
  airline: string;
  flightNumber: string;
  departureAirport: string;
  departureDate: string;
  fetchedAt: string;
  totalResults: number;
  reservations: Reservation[];
}
