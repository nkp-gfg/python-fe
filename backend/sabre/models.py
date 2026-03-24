"""Pydantic v2 models for Sabre API response validation.

These models validate the normalized output from converter functions,
ensuring field presence and types before storage in MongoDB.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Flight Status (ACS_FlightDetailRS) ────────────────────────────────────


class CabinCount(BaseModel):
    """Passenger counts for a single cabin class."""

    classOfService: str = ""
    authorized: int = 0
    booked: int = 0
    available: int = 0
    thru: int = 0
    local: int = 0
    nonRevenueLocal: int = 0
    nonRevenueThru: int = 0
    kiosk: int = 0
    localOnBoard: int = 0
    totalOnBoard: int = 0
    totalBoardingPassIssued: int = 0


class LegInfo(BaseModel):
    """Per-leg flight details."""

    legCity: str = ""
    legStatus: str = ""
    legDate: str = ""
    legDepartureTime: str = ""
    legArrivalTime: str = ""


class FlightStatusResponse(BaseModel):
    """Validated flight status from ACS_FlightDetailRQ."""

    airline: str
    flightNumber: str
    origin: str
    departureDate: str = ""
    status: str = ""
    gate: str = ""
    terminal: str = ""
    aircraftType: str = ""
    aircraftRegistration: str = ""
    scheduledDepartureTime: str = ""
    scheduledArrivalTime: str = ""
    estimatedDepartureTime: str = ""
    estimatedArrivalTime: str = ""
    boardingTime: str = ""
    seatConfig: str = ""
    cabinCounts: list[CabinCount] = Field(default_factory=lambda: [])
    legs: list[LegInfo] = Field(default_factory=lambda: [])


# ── Passenger List (GetPassengerListRS) ───────────────────────────────────


class PassengerRecord(BaseModel):
    """Single passenger from the manifest."""

    lastName: str = ""
    firstName: str = ""
    pnr: str = ""
    passengerId: str = ""
    bookingClass: str = ""
    cabin: str = ""
    seat: str = ""
    passengerType: str = ""
    bagCount: int = 0
    isCheckedIn: bool = False
    isBoarded: bool = False
    isRevenue: bool = True
    isChild: bool = False
    hasInfant: bool = False
    editCodes: list[str] = Field(default_factory=list)
    lineNumber: str = ""


class CabinSummary(BaseModel):
    """Per-cabin passenger summary."""

    cabin: str = ""
    count: int = 0
    available: int = 0


class PassengerListResponse(BaseModel):
    """Validated passenger list from GetPassengerListRQ."""

    airline: str
    flightNumber: str
    origin: str
    departureDate: str
    destination: str = ""
    totalPassengers: int = 0
    adultCount: int = 0
    childCount: int = 0
    infantCount: int = 0
    totalSouls: int = 0
    passengers: list[PassengerRecord] = Field(default_factory=lambda: [])
    cabinSummary: list[CabinSummary] = Field(default_factory=lambda: [])


# ── Reservations (Trip_SearchRS) ──────────────────────────────────────────


class ReservationSegment(BaseModel):
    """Flight segment within a reservation."""

    departureAirport: str = ""
    arrivalAirport: str = ""
    departureDate: str = ""
    arrivalDate: str = ""
    marketingAirline: str = ""
    flightNumber: str = ""
    bookingClass: str = ""
    status: str = ""


class ReservationPassenger(BaseModel):
    """Passenger within a reservation."""

    lastName: str = ""
    firstName: str = ""
    nameId: str = ""
    nameType: str = ""
    gender: str = ""
    dateOfBirth: str = ""
    nationality: str = ""
    seatNumber: str = ""
    frequentFlyerNumber: str = ""
    frequentFlyerAirline: str = ""


class SsrRequest(BaseModel):
    """Special Service Request on a reservation."""

    code: str = ""
    text: str = ""
    status: str = ""
    airline: str = ""
    type: str = ""


class PhoneEntry(BaseModel):
    """Phone number on a reservation."""

    number: str = ""
    type: str = ""


class RemarkEntry(BaseModel):
    """Remark on a reservation or flight status."""

    type: str = ""
    text: str = ""


class AncillaryServiceEntry(BaseModel):
    """Ancillary service purchase on a reservation."""

    code: str = ""
    status: str = ""
    quantity: int = 1
    emdNumber: str = ""
    groupCode: str = ""
    subGroupCode: str = ""


class ReservationRecord(BaseModel):
    """Single PNR/reservation."""

    pnr: str = ""
    numberInParty: int = 0
    numberOfInfants: int = 0
    createdAt: str = ""
    updatedAt: str = ""
    passengers: list[ReservationPassenger] = Field(default_factory=lambda: [])
    segments: list[ReservationSegment] = Field(default_factory=lambda: [])
    tickets: list[dict[str, str]] = Field(default_factory=lambda: [])
    ssrRequests: list[SsrRequest] = Field(default_factory=lambda: [])
    emails: list[str] = Field(default_factory=list)
    phones: list[PhoneEntry] = Field(default_factory=lambda: [])
    remarks: list[RemarkEntry] = Field(default_factory=lambda: [])
    ancillaryServices: list[AncillaryServiceEntry] = Field(
        default_factory=lambda: [])
    receivedFrom: str = ""


class ReservationsResponse(BaseModel):
    """Validated reservations from Trip_SearchRQ."""

    airline: str
    flightNumber: str
    departureAirport: str = ""
    departureDate: str
    fetchedAt: str = ""
    totalResults: int = 0
    reservations: list[ReservationRecord] = Field(default_factory=lambda: [])


# ── Trip Report (Trip_ReportsRS) ──────────────────────────────────────────


class TripReportPassenger(BaseModel):
    """Passenger in a trip report (cancelled or ever-booked)."""

    lastName: str = ""
    firstName: str = ""
    pnr: str = ""


class TripReportResponse(BaseModel):
    """Validated merged trip report (MLX + MLC)."""

    airline: str
    flightNumber: str
    origin: str
    departureDate: str
    cancelledCount: int = 0
    everBookedCount: int = 0
    cancelledPassengers: list[TripReportPassenger] = Field(
        default_factory=lambda: [])
    everBookedPassengers: list[TripReportPassenger] = Field(
        default_factory=lambda: [])


# ── Passenger Data (GetPassengerDataRS) ───────────────────────────────────


class PassengerDataResponse(BaseModel):
    """Validated per-passenger detail from GetPassengerDataRQ."""

    airline: str
    flightNumber: str
    origin: str
    departureDate: str
    lastName: str = ""
    firstName: str = ""
    pnr: str = ""
    seat: str = ""
    cabin: str = ""
    bookingClass: str = ""
    passengerType: str = ""
    isCheckedIn: bool = False
    isBoarded: bool = False
    editCodes: list[str] = Field(default_factory=list)
    baggageRoutes: list[dict[str, Any]] = Field(default_factory=lambda: [])
    ancillaryServices: list[dict[str, Any]] = Field(default_factory=lambda: [])
    timaticInfo: dict[str, Any] = Field(default_factory=dict)
    checkInRequirements: list[dict[str, Any]] = Field(
        default_factory=lambda: [])
    contactInfo: dict[str, Any] = Field(default_factory=dict)
    frequentFlyer: dict[str, Any] = Field(default_factory=dict)


# ── Feeder Pipeline Result ────────────────────────────────────────────────


class ApiCallResult(BaseModel):
    """Result of a single API call through the storage pipeline."""

    apiType: str
    snapshotType: str
    requestId: str
    snapshotId: str
    checksum: str
    isDuplicate: bool
    changesStored: int
    httpStatus: int
    durationMs: int
