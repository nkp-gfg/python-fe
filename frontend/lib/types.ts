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
  flightPhase?: FlightPhase;
  publishedSchedule?: PublishedScheduleSummary | null;
  flightSequenceNumber?: number | null;
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
  loyaltyCounts?: { FF: number; BLU: number; SLV: number; GLD: number; BLK: number };
  nationalityCounts?: Record<string, number>;
}

export interface CabinDetail {
  adults: number;
  children: number;
  infants: number;
  staff: number;
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
  economyDetail?: CabinDetail;
  businessDetail?: CabinDetail;
}

export interface OthersSummary {
  jumpSeat: number;
  nonRevenue: number;
  offloaded: number | null;
  noShow: number | null;
  offloadedAvailable: boolean;
  noShowAvailable: boolean;
  /** Passengers on manifest who never checked in (from Sabre GetPassengerListRS) */
  notCheckedIn: number;
  /** Passengers checked in but not boarded (from Sabre GetPassengerListRS) */
  checkedInNotBoarded: number;
  /** Whether the flight is FINAL or PDC (boarding is closed) */
  flightClosed: boolean;
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
  display?: boolean;
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

export interface SpecialRequestsSummary {
  specialMeals: Record<string, number>;
  totalSpecialMeals: number;
  wheelchairs: Record<string, number>;
  totalWheelchairs: number;
  emergencyContacts: number;
  frequentFlyers: number;
  ffTiers: Record<string, number>;
  bookingSources: Record<string, number>;
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
  flightPhase: FlightPhase;
  tree: FlightTree | null;
  schedule: FlightSchedule | null;
  stateSummary: {
    booked: StateBucket;
    checkedIn: StateBucket;
    boarded: StateBucket;
    others: OthersSummary;
  };
  groupBookingSummary: GroupBookingSummary | null;
  specialRequestsSummary: SpecialRequestsSummary | null;
  codeshareInfo: string[];
  departureGate: string;
  insights: FlightInsights | null;
  flightSequenceNumber?: number;
}

// --- OTP Flight (PostgreSQL — flight_xml_current) ---

export interface OtpDelayDetail {
  DelayComment: string;
  DelayDuration: string;
  DelayReasonCode: string;
}

export interface OtpFlight {
  flightSequenceNumber: number;
  flightNumber: string;
  origin: string;
  destination: string;
  actualOrigin?: string | null;
  actualDestination?: string | null;
  flightDate: string;
  scheduledDepartureUtc?: string | null;
  estimatedBlockOffUtc?: string | null;
  scheduledArrivalUtc?: string | null;
  estimatedBlockOnUtc?: string | null;
  actualBlockOffUtc?: string | null;
  actualBlockOnUtc?: string | null;
  actualTakeoffUtc?: string | null;
  actualTouchdownUtc?: string | null;
  scheduledDepartureLocal?: string | null;
  scheduledArrivalLocal?: string | null;
  flightStatus: string;
  isCancelled: boolean;
  aircraftType?: string | null;
  aircraftRegistration?: string | null;
  serviceTypeCode?: string | null;
  cancelReasonCode?: string | null;
  totalPax?: number | null;
  delayDetails?: OtpDelayDetail[] | null;
  source?: string | null;
}

// --- Data Audit (Cross-DB Comparison) ---

export interface ComparisonRow {
  field: string;
  pgValue: string | null;
  mongoValue: string | null;
  match: "match" | "mismatch" | "pg_only" | "mongo_only";
  remark: string | null;
}

export interface ComparisonResult {
  flightNumber: string;
  date: string | null;
  origin: string | null;
  sequenceNumber: number | null;
  pgFound: boolean;
  mongoFound: boolean;
  rows: ComparisonRow[];
  summary: {
    match: number;
    mismatch: number;
    pg_only: number;
    mongo_only: number;
  };
}

// --- Flight Phase ---

export type FlightPhaseCode = "SCHEDULED" | "CHECK_IN" | "BOARDING" | "CLOSED" | "DEPARTED";

export interface FlightPhase {
  phase: FlightPhaseCode;
  label: string;
  focusCard: "booked" | "checkedIn" | "boarded" | "others";
  alertColor: "slate" | "blue" | "amber" | "red" | "green" | "gray";
  alertIcon: string;
  description: string;
}

// --- Sabre Ingestion API Types ---

export interface SabreIngestRequest {
  airline?: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  departureDateTime: string;
  flightSequenceNumber?: number;
}

export interface SabreApiResult {
  status: "success" | "error";
  snapshotId?: string;
  durationMs?: number;
  changesStored?: number;
  isDuplicate?: boolean;
  error?: string;
  requestProfile?: {
    attempt: number;
    action: string;
    ebxmlVersion: string;
    mustUnderstand: string;
    endpoint: string;
  };
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
  nationality: string;
  groupCode?: string;
  isGroup?: boolean;
  isUnnamedGroup?: boolean;
  nameAssociationId?: string;
  // Enriched from converter (passenger_list)
  checkInDate?: string;
  checkInTime?: string;
  baggageRoutes?: { airline: string; flight: string; origin: string; destination: string; segmentStatus: string }[];
  vcrInUse?: boolean;
  vcrAirlineNumber?: string;
  vcrCouponNumber?: string;
  // Enriched from reservations (cross-referenced)
  specialMeal?: string;
  wheelchairCode?: string;
  hasEmergencyContact?: boolean;
  ffTierLevel?: string;
  ffTierName?: string;
  ffStatus?: string;
}

export interface GroupBookingMember {
  lastName: string;
  firstName: string;
  pnr: string;
  passengerId: string;
  lineNumber: number;
  isCheckedIn: boolean;
  isBoarded: boolean;
  isUnnamed: boolean;
  seat: string;
}

export interface GroupBooking {
  groupCode: string;
  pnr: string;
  cabin: string;
  bookingClass: string;
  totalMembers: number;
  namedMembers: number;
  unnamedMembers: number;
  checkedIn: number;
  boarded: number;
  members?: GroupBookingMember[];
}

export interface GroupBookingSummary {
  totalGroups: number;
  totalGroupPassengers: number;
  totalUnnamed: number;
  totalNamed: number;
  groups: GroupBooking[];
}

export interface GroupBookingsResponse {
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  totalGroups: number;
  totalGroupPassengers: number;
  totalUnnamed: number;
  groups: GroupBooking[];
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
  groupBookings?: GroupBooking[];
  passengers: PassengerRecord[];
  // Enriched from converter
  departureGate?: string;
  scheduledDeparture?: string;
  estimatedDeparture?: string;
  departureTime?: string;
  arrivalTime?: string;
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

export interface SnapshotDeltaItem {
  selected: string | number | null;
  latest: string | number | null;
  diff: number | null;
  changed: boolean;
}

export interface SnapshotCompareTypeResult {
  available: boolean;
  reason?: string;
  selectedSequence?: number;
  latestSequence?: number;
  changed?: boolean;
  deltas?: Record<string, SnapshotDeltaItem>;
}

export interface SnapshotCompareResponse {
  flightNumber: string;
  origin?: string;
  departureDate?: string;
  snapshotSequence: number;
  types: Record<string, SnapshotCompareTypeResult>;
}

export interface SnapshotRestoreResultItem {
  snapshotType: string;
  targetCollection: string;
  sourceSequence: number;
}

export interface SnapshotRestoreResponse {
  flightNumber: string;
  origin?: string;
  departureDate?: string;
  requestedSequence: number;
  restoredAt: string;
  restored: SnapshotRestoreResultItem[];
}

export interface FlightStatusRecord {
  airline: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  status: string;
  timeToDeparture?: number;
  aircraft: Aircraft;
  schedule: Schedule;
  gate: string;
  terminal: string;
  boarding: { time: string; indicator: string };
  legs: { city: string; controllingCity: boolean; status: string }[];
  passengerCounts: Record<string, ClassCounts>;
  jumpSeat: JumpSeat;
  remarks?: RemarkEntry[];
  codeshareInfo?: string[];
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
  // Enriched fields
  specialMeal?: string;
  wheelchairCode?: string;
  hasEmergencyContact?: boolean;
  docaAddress?: string;
  seatStatusCode?: string;
  seatTypeCode?: string;
  ffTierLevel?: string;
  ffTierName?: string;
  ffStatus?: string;
  ffSupplierCode?: string;
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
  // Enriched fields
  isCodeShare?: boolean;
  equipmentType?: string;
  operatingAirline?: string;
  operatingFlightNumber?: string;
  segmentBookedDate?: string;
  scheduleChangeIndicator?: string;
  inboundConnection?: string;
  outboundConnection?: string;
  marriageGroup?: string;
  eTicket?: boolean;
}

export interface SsrRequest {
  code: string;
  text: string;
  status: string;
  airline: string;
  type: string;
}

export interface PhoneEntry {
  number: string;
  type: string;
}

export interface RemarkEntry {
  type: string;
  text: string;
}

export interface AncillaryService {
  code: string;
  status: string;
  quantity: number;
  emdNumber: string;
  groupCode: string;
  subGroupCode: string;
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
  ssrRequests?: SsrRequest[];
  emails?: string[];
  phones?: PhoneEntry[];
  remarks?: RemarkEntry[];
  ancillaryServices?: AncillaryService[];
  receivedFrom?: string;
  // Enriched fields
  bookingHeader?: string;
  creationAgent?: string;
  pnrSequence?: number;
  flightsRangeStart?: string;
  flightsRangeEnd?: string;
  pointOfSale?: {
    agentDutyCode?: string;
    agentSine?: string;
    airlineVendorId?: string;
    bookingSource?: string;
    pseudoCityCode?: string;
    homePseudoCityCode?: string;
    isoCountry?: string;
  };
  formOfPayment?: string;
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

// --- Flight Schedule Types (from VerifyFlightDetailsLLSRQ) ---

export interface ScheduleSegment {
  departureDateTime: string;
  arrivalDateTime: string;
  origin: string;
  originTerminal: string;
  destination: string;
  destinationTerminal: string;
  aircraftType: string;
  marketingAirline: string;
  flightNumber: string;
  airMilesFlown: number;
  elapsedTime: string;
  accumulatedElapsedTime: string;
  mealCode: string;
}

export interface FlightSchedule {
  airline: string;
  flightNumber: string;
  departureDate: string;
  fetchedAt: string;
  success: boolean;
  error: string | null;
  origin: string;
  destination: string;
  scheduledDeparture: string;
  scheduledArrival: string;
  aircraftType: string;
  elapsedTime: string;
  airMilesFlown: number;
  originTerminal: string;
  destinationTerminal: string;
  originTimeZone: string;
  destinationTimeZone: string;
  mealCode: string;
  segments: ScheduleSegment[];
}

export interface PublishedScheduleSummary {
  origin: string;
  destination: string;
  scheduledDeparture: string;
  scheduledArrival: string;
  aircraftType: string;
  elapsedTime: string;
  airMilesFlown: number;
}

export interface ScheduleLookupRequest {
  airline?: string;
  flightNumber: string;
  departureDate: string;
}

// --- Passenger Timeline Types ---

export type TimelineEventCategory = 
  | "booking" 
  | "checkin" 
  | "boarding" 
  | "upgrade" 
  | "seat" 
  | "baggage" 
  | "other";

export interface TimelineUpgradeInfo {
  direction?: "UPGRADE" | "DOWNGRADE";
  upgradeType?: "LMU" | "PAID" | "COMPLIMENTARY" | "OPERATIONAL" | string;
  upgradeCode?: string;
}

export interface TimelineEvent {
  timestamp: string;
  changeType: string;
  category: TimelineEventCategory;
  description: string;
  details: {
    field?: string;
    oldValue?: string | number | boolean;
    newValue?: string | number | boolean;
  };
  upgradeInfo?: TimelineUpgradeInfo;
  originalBooking?: {
    cabin?: string;
    bookingClass?: string;
  };
}

export interface PassengerCurrentState {
  cabin?: string;
  bookingClass?: string;
  seat?: string;
  isCheckedIn: boolean;
  isBoarded: boolean;
  bagCount: number;
}

export interface PassengerTimelineResponse {
  flightNumber: string;
  pnr: string;
  departureDate?: string;
  origin?: string;
  originalBooking: {
    cabin?: string;
    bookingClass?: string;
  };
  currentState?: PassengerCurrentState;
  events: TimelineEvent[];
  eventCount: number;
}

// --- Flight Timeline Types ---

export type FlightEventCategory =
  | "booking"
  | "checkin"
  | "boarding"
  | "upgrade"
  | "downgrade"
  | "seat"
  | "baggage"
  | "security"
  | "gate"
  | "flight_ops"
  | "standby"
  | "loyalty"
  | "document"
  | "capacity"
  | "reservation"
  | "snapshot"
  | "other";

export interface FlightTimelineEvent {
  timestamp: string;
  category: FlightEventCategory;
  eventType: string;
  description: string;
  passengerName?: string;
  pnr?: string;
  details?: {
    field?: string;
    oldValue?: string | number | boolean;
    newValue?: string | number | boolean;
    snapshotType?: string;
    sequenceNumber?: number;
  };
  passenger?: {
    pnr: string;
    lastName: string;
    firstName: string;
  };
  metadata?: Record<string, unknown>;
}

export interface FlightTimelineStats {
  totalChanges: number;
  totalEvents: number;
  totalCheckins: number;
  totalBoardings: number;
  totalUpgrades: number;
  totalSeatChanges: number;
  checkedIn: number;
  boarded: number;
  upgrades: number;
  seatChanges: number;
  statusChanges: number;
  timeRange: {
    first?: string;
    last?: string;
  };
}

export interface FlightTimelineResponse {
  flightNumber: string;
  origin?: string;
  departureDate?: string;
  events: FlightTimelineEvent[];
  eventCount: number;
  stats: FlightTimelineStats;
}

// --- Activity Feed Types ---

export interface ActivityFeedEvent {
  timestamp: string;
  flightNumber: string;
  origin: string;
  date: string;
  departureDate?: string;
  category: FlightEventCategory;
  eventType: string;
  description: string;
  passengerName?: string;
  pnr?: string;
  passenger?: {
    pnr: string;
    lastName: string;
    firstName: string;
  };
}

export interface ActivityFeedResponse {
  events: ActivityFeedEvent[];
  count: number;
  totalEvents: number;
  flightsAffected: number;
}

// --- Boarding Progress Types ---

export interface ProgressDataPoint {
  timestamp: string;
  count: number;
  cumulativeCount: number;
  passenger?: {
    pnr: string;
    lastName: string;
    firstName: string;
  };
}

export interface ProgressSeries {
  current: number;
  total: number;
  percentage: number;
  data: ProgressDataPoint[];
  series?: ProgressDataPoint[];
}

export interface FlightMilestone {
  timestamp: string;
  status: string;
  type: string;
  label: string;
  previousStatus?: string;
}

export interface BoardingProgressResponse {
  flightNumber: string;
  origin?: string;
  departureDate?: string;
  totalPassengers: number;
  checkinProgress: ProgressSeries;
  boardingProgress: ProgressSeries;
  milestones: FlightMilestone[];
}

// --- History Badge Types ---

export interface PassengerHistoryBadge {
  changeCount: number;
  hasUpgrade: boolean;
  lastChange: string;
}

export type PassengerHistoryBadges = Record<string, PassengerHistoryBadge>;

// --- Insights Types ---

export interface InsightsConnecting {
  connecting: number;
  local: number;
  connectingPct: number;
}

export interface InsightsBookingChannels {
  channels: Record<string, number>;
  categories: { online: number; agent: number; corporate: number; other: number };
}

export interface InsightsDocCompliance {
  DOCS: { count: number; pct: number };
  DOCV: { count: number; pct: number };
  DOCA: { count: number; pct: number };
}

export interface InsightsCheckInSequence {
  total: number;
  earliest: number;
  latest: number;
  median: number;
}

export interface InsightsBookingLeadTime {
  avgDays: number;
  minDays: number;
  maxDays: number;
  medianDays: number;
  distribution: {
    sameDay: number;
    within7d: number;
    within30d: number;
    within90d: number;
    over90d: number;
  };
}

export interface InsightsSeatOccupancy {
  seated: number;
  unseated: number;
  seatPct: number;
}

export interface InsightsBaggage {
  withBags: number;
  withoutBags: number;
  totalBags: number;
  avgBags: number;
  dataAvailablePct: number;
  withBagRoutes: number;
}

export interface InsightsEditCodes {
  uniqueCodes: number;
  topCodes: { code: string; count: number }[];
}

export interface InsightsMultiSegment {
  distribution: Record<string, number>;
  multiSegmentPct: number;
}

export interface InsightsPartySize {
  distribution: Record<string, number>;
  avgSize: number;
}

export interface InsightsBoardingRate {
  boarded: number;
  checkedIn: number;
  notCheckedIn: number;
  boardedPct: number;
  checkedInPct: number;
}

export interface InsightsChangeVelocity {
  totalChanges: number;
  changeTypes: Record<string, number>;
}

export interface InsightsTicketStatus {
  vcrTypes: Record<string, number>;
  withTicket: number;
  withoutTicket: number;
  ticketPct: number;
}

export interface InsightsFlightInfo {
  elapsedTime: string;
  airMilesFlown: number;
  aircraftType: string;
  mealCode: string;
}

export interface InsightsCorporateTravel {
  totalCorporate: number;
  corporatePct: number;
  companies: Record<string, number>;
}

export interface InsightsPriority {
  total: number;
  codes: Record<string, number>;
}

export interface InsightsConnectionRisk {
  atRiskCount: number;
  totalConnecting: number;
  riskPct: number;
}

export interface InsightsClassMismatch {
  total: number;
  upgrades: number;
  downgrades: number;
}

export interface InsightsBoardingPasses {
  issued: number;
  notIssued: number;
  issuedPct: number;
}

export interface InsightsReservationRecency {
  latestModification: string | null;
  totalReservations: number;
}

export interface InsightsCheckInTimeline {
  totalWithTime: number;
  hourDistribution: Record<string, number>;
  peakHour: string | null;
  coveragePct: number;
}

export interface InsightsEmergencyContacts {
  withContact: number;
  withoutContact: number;
  coveragePct: number;
}

export interface InsightsNationalityBreakdown {
  countries: Record<string, number>;
  uniqueCountries: number;
  unknown: number;
  coveragePct: number;
}

export interface InsightsBaggageRouting {
  destinations: Record<string, number>;
  paxWithRoutes: number;
  coveragePct: number;
}

export interface InsightsStandbyUpgrade {
  standbyTotal: number;
  upgradeTotal: number;
  standbyCabins: Record<string, number>;
  standbyPct: number;
}

export interface InsightsOperationalReadiness {
  noSeat: number;
  checkedInNoBP: number;
  notCheckedIn: number;
  thruNoSeat: number;
  readinessPct: number;
}

export interface FlightInsights {
  connectingPassengers: InsightsConnecting;
  bookingChannels: InsightsBookingChannels;
  paymentMethods: Record<string, number>;
  documentCompliance: InsightsDocCompliance;
  checkInSequence: InsightsCheckInSequence;
  bookingLeadTime: InsightsBookingLeadTime | null;
  seatOccupancy: InsightsSeatOccupancy;
  baggage: InsightsBaggage;
  editCodes: InsightsEditCodes;
  multiSegment: InsightsMultiSegment;
  pnrPartySize: InsightsPartySize;
  infantTracking: { total: number; details: string[] };
  wheelchairTypes: Record<string, number>;
  mealCodes: Record<string, number>;
  boardingRate: InsightsBoardingRate;
  changeVelocity: InsightsChangeVelocity;
  revenueClassMix: Record<string, number>;
  ticketStatus: InsightsTicketStatus;
  flightInfo: InsightsFlightInfo | null;
  corporateTravel: InsightsCorporateTravel;
  priorityPassengers: InsightsPriority;
  seniority: { withSeniority: number; pct: number };
  connectionRisk: InsightsConnectionRisk;
  classMismatch: InsightsClassMismatch;
  passengerTypes: Record<string, number>;
  boardingPasses: InsightsBoardingPasses;
  reservationRecency: InsightsReservationRecency | null;
  equipment: { aircraftType: string; seatConfig: string };
  checkInTimeline: InsightsCheckInTimeline;
  emergencyContacts: InsightsEmergencyContacts;
  nationalityBreakdown: InsightsNationalityBreakdown;
  baggageRouting: InsightsBaggageRouting;
  standbyUpgrade: InsightsStandbyUpgrade;
  operationalReadiness: InsightsOperationalReadiness;
}

// --- Process Audit Types ---

export type AuditSeverity = "critical" | "warning" | "info";

export interface AuditAlert {
  ruleId: string;
  severity: AuditSeverity;
  message: string;
  pnr?: string;
  passengerName?: string;
  details?: Record<string, unknown>;
}

export interface AuditResponse {
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  alerts: AuditAlert[];
  summary: Record<AuditSeverity, number>;
  totalAlerts: number;
  passengerAlerts: Record<string, string[]>;
}
