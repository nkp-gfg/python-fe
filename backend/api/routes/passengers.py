"""Passenger list API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db

router = APIRouter(prefix="/flights", tags=["passengers"])


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


@router.get("/{flight_number}/passengers")
def get_passengers(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Get the latest passenger list for a flight."""
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Passenger list not found")
    return _strip_id(doc)


@router.get("/{flight_number}/passengers/summary")
def get_passenger_summary(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
):
    """Get a summary of passenger counts (checked-in, boarded, revenue, etc.)."""
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Passenger list not found")

    passengers = doc.get("passengers", [])
    total = len(passengers)
    checked_in = sum(1 for p in passengers if p.get("isCheckedIn"))
    boarded = sum(1 for p in passengers if p.get("isBoarded"))
    revenue = sum(1 for p in passengers if p.get("isRevenue"))
    non_revenue = total - revenue

    # Per cabin breakdown
    cabins = {}
    for p in passengers:
        c = p.get("cabin", "?")
        if c not in cabins:
            cabins[c] = {"total": 0, "checkedIn": 0, "boarded": 0}
        cabins[c]["total"] += 1
        if p.get("isCheckedIn"):
            cabins[c]["checkedIn"] += 1
        if p.get("isBoarded"):
            cabins[c]["boarded"] += 1

    return {
        "flightNumber": flight_number,
        "origin": doc.get("origin", ""),
        "departureDate": doc.get("departureDate", ""),
        "fetchedAt": doc.get("fetchedAt", ""),
        "totalPassengers": total,
        "checkedIn": checked_in,
        "boarded": boarded,
        "revenue": revenue,
        "nonRevenue": non_revenue,
        "cabinBreakdown": cabins,
        "cabinSummary": doc.get("cabinSummary", []),
    }


@router.get("/{flight_number}/passengers/{pnr}")
def get_passenger_by_pnr(
    flight_number: str,
    pnr: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Find a specific passenger by PNR locator within a flight."""
    db = get_db()
    query = {"flightNumber": flight_number, "passengers.pnr": pnr}
    if date:
        query["departureDate"] = date

    doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(
            status_code=404, detail="PNR not found in passenger list")

    matching = [p for p in doc.get("passengers", []) if p.get("pnr") == pnr]
    return {
        "flightNumber": flight_number,
        "origin": doc.get("origin", ""),
        "departureDate": doc.get("departureDate", ""),
        "passengers": matching,
    }
