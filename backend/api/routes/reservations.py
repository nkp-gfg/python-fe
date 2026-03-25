"""Reservation API endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.snapshot_versioning import get_snapshot_data_as_of
from backend.api.validators import validate_date, validate_origin
from backend.sabre.models import ReservationsResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["reservations"])


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


@router.get("/{flight_number}/reservations", response_model=ReservationsResponse)
def get_reservations(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
):
    """Get the latest reservations document for a flight."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["departureAirport"] = origin
    if date:
        query["departureDate"] = date

    if snapshot_sequence:
        doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="reservations",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
        doc = db["reservations"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Reservations not found")
    return _strip_id(doc)


@router.get("/{flight_number}/reservations/{pnr}")
def get_reservation_by_pnr(
    flight_number: str,
    pnr: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Find a specific reservation by PNR locator."""
    validate_date(date)
    db = get_db()
    query = {"flightNumber": flight_number, "reservations.pnr": pnr}
    if date:
        query["departureDate"] = date

    doc = db["reservations"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(
            status_code=404, detail="PNR not found in reservations")

    matching = [r for r in doc.get("reservations", []) if r.get("pnr") == pnr]
    return {
        "flightNumber": flight_number,
        "departureAirport": doc.get("departureAirport", ""),
        "departureDate": doc.get("departureDate", ""),
        "reservations": matching,
    }
