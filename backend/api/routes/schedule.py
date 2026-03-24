"""Flight schedule API endpoints — powered by VerifyFlightDetailsLLSRQ.

Provides:
  GET  /flights/{flight_number}/schedule   — stored schedule from MongoDB
  POST /flights/schedule/lookup            — live Sabre lookup (min input, max output)
"""

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.api.database import get_db
from backend.api.validators import validate_date, validate_flight_number
from backend.feeder.converter import convert_schedule
from backend.feeder import storage
from backend.sabre.client import SabreClient, SabreError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["schedule"])


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


# ── Request / Response models ─────────────────────────────────────────────

class ScheduleLookupRequest(BaseModel):
    """Minimum input for schedule lookup — just flight number + date."""

    airline: str = Field(
        default="GF", min_length=2, max_length=2,
        pattern=r"^[A-Z0-9]{2}$",
        description="2-character IATA airline code",
    )
    flightNumber: str = Field(
        ..., min_length=1, max_length=5,
        pattern=r"^\d{1,5}$",
        description="Flight number (digits only, e.g. '2006')",
    )
    departureDate: str = Field(
        ..., pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Departure date YYYY-MM-DD",
    )


class ScheduleSegment(BaseModel):
    departureDateTime: str
    arrivalDateTime: str
    origin: str
    originTerminal: str
    destination: str
    destinationTerminal: str
    aircraftType: str
    marketingAirline: str
    flightNumber: str
    airMilesFlown: int
    elapsedTime: str
    accumulatedElapsedTime: str
    mealCode: str


class ScheduleResponse(BaseModel):
    airline: str
    flightNumber: str
    departureDate: str
    fetchedAt: str
    success: bool
    error: str | None = None
    origin: str = ""
    destination: str = ""
    scheduledDeparture: str = ""
    scheduledArrival: str = ""
    aircraftType: str = ""
    elapsedTime: str = ""
    airMilesFlown: int = 0
    originTerminal: str = ""
    destinationTerminal: str = ""
    originTimeZone: str = ""
    destinationTimeZone: str = ""
    mealCode: str = ""
    segments: list[ScheduleSegment] = []


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/{flight_number}/schedule", response_model=ScheduleResponse)
def get_flight_schedule(
    flight_number: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Get the stored schedule for a flight from MongoDB."""
    validate_flight_number(flight_number)
    validate_date(date)
    db = get_db()
    query = {"flightNumber": flight_number}
    if date:
        query["departureDate"] = date

    doc = db["flight_schedules"].find_one(
        query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404,
                            detail="Schedule not found — try POST /flights/schedule/lookup")
    return _strip_id(doc)


@router.post("/schedule/lookup", response_model=ScheduleResponse)
async def lookup_schedule(payload: ScheduleLookupRequest):
    """Live Sabre lookup — minimum input (airline + flightNumber + date),
    maximum output (full published schedule with times, route, aircraft, etc.).

    The result is also stored in MongoDB for future GET requests.
    """
    def _do_lookup():
        with SabreClient() as client:
            raw, xml, meta = client.verify_flight_details(
                airline=payload.airline,
                flight_number=payload.flightNumber,
                departure_date=payload.departureDate,
                origin="",
                destination="",
            )
            schedule_doc = convert_schedule(
                raw, payload.airline, payload.flightNumber, payload.departureDate)

            # Store raw request for audit
            flight_info = {
                "airline": payload.airline,
                "flightNumber": payload.flightNumber,
                "origin": schedule_doc.get("origin", ""),
                "departureDate": payload.departureDate,
            }
            storage.store_raw_request(
                api_type="FlightSchedule",
                flight_info=flight_info,
                request_xml=meta["requestXml"],
                response_xml=xml,
                response_json=raw,
                http_status=meta["httpStatus"],
                duration_ms=meta["durationMs"],
                session_token=meta.get("sessionToken"),
                conversation_id=meta.get("conversationId"),
            )

            # Store schedule if successful
            if schedule_doc.get("success"):
                storage.store_flight_schedule(schedule_doc)

            # Return without _raw and _id
            schedule_doc.pop("_raw", None)
            return schedule_doc

    try:
        result = await run_in_threadpool(_do_lookup)
    except SabreError as e:
        logger.exception("Schedule lookup failed for %s%s",
                         payload.airline, payload.flightNumber)
        raise HTTPException(status_code=502,
                            detail=f"Sabre API error: {e}")

    return result
