"""OTP flight list endpoint — reads from PostgreSQL falcon_eye database."""

import json
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.api.postgres import query_all
from backend.api.validators import validate_date

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/otp", tags=["otp"])

# ── Response model ────────────────────────────────────────────────────────

class OtpFlight(BaseModel):
    flightSequenceNumber: int
    flightNumber: str
    origin: str
    destination: str
    actualOrigin: str | None = None
    actualDestination: str | None = None
    flightDate: str
    scheduledDepartureUtc: str | None = None
    estimatedBlockOffUtc: str | None = None
    scheduledArrivalUtc: str | None = None
    estimatedBlockOnUtc: str | None = None
    actualBlockOffUtc: str | None = None
    actualBlockOnUtc: str | None = None
    actualTakeoffUtc: str | None = None
    actualTouchdownUtc: str | None = None
    scheduledDepartureLocal: str | None = None
    scheduledArrivalLocal: str | None = None
    flightStatus: str
    isCancelled: bool = False
    aircraftType: str | None = None
    aircraftRegistration: str | None = None
    serviceTypeCode: str | None = None
    cancelReasonCode: str | None = None
    totalPax: int | None = None
    delayDetails: list[dict] | None = None
    source: str | None = None


_QUERY = """
SELECT
    flight_sequence_number,
    flight_number,
    scheduled_origin,
    scheduled_destination,
    actual_origin,
    actual_destination,
    flight_date,
    scheduled_departure_utc,
    estimated_block_off_utc,
    scheduled_arrival_utc,
    estimated_block_on_utc,
    actual_block_off_utc,
    actual_block_on_utc,
    actual_takeoff_utc,
    actual_touchdown_utc,
    scheduled_departure_local,
    scheduled_arrival_local,
    flight_status,
    aircraft_type,
    aircraft_registration,
    service_type_code,
    cancel_reason_code,
    passenger_counts,
    delay_details,
    source
FROM otp.flight_xml_current
WHERE scheduled_departure_local::date = %s
   OR scheduled_arrival_local::date = %s
ORDER BY scheduled_departure_local ASC
"""


def _ts(val) -> str | None:
    """Convert a datetime/date to ISO string, or return None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


def _sum_pax(counts) -> int | None:
    """Sum passenger_counts JSONB array → total pax."""
    if not counts:
        return None
    items = counts if isinstance(counts, list) else json.loads(counts)
    return sum(int(p.get("Count", 0)) for p in items)


def _parse_delays(delays) -> list[dict] | None:
    """Parse delay_details JSONB into a clean list."""
    if not delays:
        return None
    items = delays if isinstance(delays, list) else json.loads(delays)
    return items


def _row_to_flight(row: dict) -> OtpFlight:
    status = row.get("flight_status") or "Unknown"
    return OtpFlight(
        flightSequenceNumber=row["flight_sequence_number"],
        flightNumber=str(row["flight_number"]).strip(),
        origin=row["scheduled_origin"] or "",
        destination=row["scheduled_destination"] or "",
        actualOrigin=row.get("actual_origin"),
        actualDestination=row.get("actual_destination"),
        flightDate=_ts(row["flight_date"]) or "",
        scheduledDepartureUtc=_ts(row.get("scheduled_departure_utc")),
        estimatedBlockOffUtc=_ts(row.get("estimated_block_off_utc")),
        scheduledArrivalUtc=_ts(row.get("scheduled_arrival_utc")),
        estimatedBlockOnUtc=_ts(row.get("estimated_block_on_utc")),
        actualBlockOffUtc=_ts(row.get("actual_block_off_utc")),
        actualBlockOnUtc=_ts(row.get("actual_block_on_utc")),
        actualTakeoffUtc=_ts(row.get("actual_takeoff_utc")),
        actualTouchdownUtc=_ts(row.get("actual_touchdown_utc")),
        scheduledDepartureLocal=_ts(row.get("scheduled_departure_local")),
        scheduledArrivalLocal=_ts(row.get("scheduled_arrival_local")),
        flightStatus=status,
        isCancelled=status.lower() in ("cancelled", "cnx"),
        aircraftType=row.get("aircraft_type"),
        aircraftRegistration=row.get("aircraft_registration"),
        serviceTypeCode=row.get("service_type_code"),
        cancelReasonCode=row.get("cancel_reason_code"),
        totalPax=_sum_pax(row.get("passenger_counts")),
        delayDetails=_parse_delays(row.get("delay_details")),
        source=row.get("source"),
    )


# ── Endpoint ──────────────────────────────────────────────────────────────

@router.get("/flights", response_model=list[OtpFlight])
def list_otp_flights(
    date: str = Query(
        ...,
        description="Flight date YYYY-MM-DD",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
):
    """List all flights for a given date from the OTP PostgreSQL database."""
    validate_date(date)
    rows = query_all(_QUERY, (date, date))
    return [_row_to_flight(r) for r in rows]
