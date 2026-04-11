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
    carrierCode: str = "GF"
    flightNumber: str
    origin: str
    destination: str
    actualOrigin: str | None = None
    actualDestination: str | None = None
    flightDate: str
    localFlightDate: str | None = None
    legDepartureDate: str | None = None
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
    publishedDepartureLocal: str | None = None
    publishedArrivalLocal: str | None = None
    flightStatus: str
    isCancelled: bool = False
    aircraftType: str | None = None
    aircraftRegistration: str | None = None
    serviceTypeCode: str | None = None
    cancelReasonCode: str | None = None
    totalPax: int | None = None
    delayDetails: list[dict] | None = None
    source: str | None = None
    # Computed: the date Sabre expects (local departure date at origin)
    sabreDepartureDate: str | None = None


_QUERY = """
SELECT
    flight_sequence_number,
    carrier_code,
    flight_number,
    scheduled_origin,
    scheduled_destination,
    actual_origin,
    actual_destination,
    flight_date,
    local_flight_date,
    leg_departure_date,
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
    published_departure_local,
    published_arrival_local,
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


def _resolve_sabre_departure_date(row: dict) -> str | None:
    """Determine the departure date Sabre expects for this flight.

    Priority:
      1. scheduled_departure_local date portion (most reliable)
      2. local_flight_date (operational date in origin timezone)
      3. leg_departure_date
      4. flight_date (fallback — may be wrong for overnight flights)
    """
    sdl = row.get("scheduled_departure_local")
    if sdl is not None:
        try:
            if isinstance(sdl, datetime):
                return sdl.strftime("%Y-%m-%d")
            if isinstance(sdl, date):
                return sdl.isoformat()
            return str(sdl)[:10]
        except Exception:
            pass

    for field in ("local_flight_date", "leg_departure_date", "flight_date"):
        val = row.get(field)
        if val is not None:
            try:
                if isinstance(val, (date, datetime)):
                    return val.isoformat()[:10]
                return str(val)[:10]
            except Exception:
                continue
    return None


def _row_to_flight(row: dict) -> OtpFlight:
    status = row.get("flight_status") or "Unknown"
    sabre_dep_date = _resolve_sabre_departure_date(row)
    return OtpFlight(
        flightSequenceNumber=row["flight_sequence_number"],
        carrierCode=row.get("carrier_code") or "GF",
        flightNumber=str(row["flight_number"]).strip(),
        origin=row["scheduled_origin"] or "",
        destination=row["scheduled_destination"] or "",
        actualOrigin=row.get("actual_origin"),
        actualDestination=row.get("actual_destination"),
        flightDate=_ts(row["flight_date"]) or "",
        localFlightDate=_ts(row.get("local_flight_date")),
        legDepartureDate=_ts(row.get("leg_departure_date")),
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
        publishedDepartureLocal=_ts(row.get("published_departure_local")),
        publishedArrivalLocal=_ts(row.get("published_arrival_local")),
        flightStatus=status,
        isCancelled=status.lower() in ("cancelled", "cnx"),
        aircraftType=row.get("aircraft_type"),
        aircraftRegistration=row.get("aircraft_registration"),
        serviceTypeCode=row.get("service_type_code"),
        cancelReasonCode=row.get("cancel_reason_code"),
        totalPax=_sum_pax(row.get("passenger_counts")),
        delayDetails=_parse_delays(row.get("delay_details")),
        source=row.get("source"),
        sabreDepartureDate=sabre_dep_date,
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
