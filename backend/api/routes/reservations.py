"""Reservation API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db

router = APIRouter(prefix="/flights", tags=["reservations"])


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


@router.get("/{flight_number}/reservations")
def get_reservations(
    flight_number: str,
    airport: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Get the latest reservations document for a flight."""
    db = get_db()
    query = {"flightNumber": flight_number}
    if airport:
        query["departureAirport"] = airport
    if date:
        query["departureDate"] = date

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
