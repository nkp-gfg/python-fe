"""Reservation API endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.snapshot_versioning import get_snapshot_data_as_of
from backend.api.validators import validate_date, validate_origin
from backend.feeder.converter import form_of_payment_brand
from backend.sabre.models import ReservationsResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["reservations"])

_FOP_REMARK_TYPES = {"FP", "FOP"}


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _public_reservation(reservation):
    """Reservation without form-of-payment text (card number and expiry)."""
    public = {k: v for k, v in reservation.items() if k != "formOfPayment"}
    remarks = public.get("remarks")
    if isinstance(remarks, list):
        public["remarks"] = [
            {**rm, "text": form_of_payment_brand(rm.get("text", ""))}
            if isinstance(rm, dict)
            and str(rm.get("type", "")).upper() in _FOP_REMARK_TYPES
            else rm
            for rm in remarks
        ]
    return public


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
    doc = _strip_id(doc)
    doc["reservations"] = [
        _public_reservation(r) for r in doc.get("reservations", [])
        if isinstance(r, dict)
    ]
    return doc


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

    matching = [
        _public_reservation(r) for r in doc.get("reservations", [])
        if isinstance(r, dict) and r.get("pnr") == pnr
    ]
    return {
        "flightNumber": flight_number,
        "departureAirport": doc.get("departureAirport", ""),
        "departureDate": doc.get("departureDate", ""),
        "reservations": matching,
    }
