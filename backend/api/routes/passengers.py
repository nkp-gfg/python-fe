"""Passenger list API endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.validators import validate_date, validate_origin
from backend.sabre.client import SabreClient, SabreError
from backend.feeder.converter import convert_passenger_data

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["passengers"])


def _strip_id(doc):
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _build_nationality_lookup(db, flight_number, date):
    """Build PNR+lastName → nationality lookup from reservations."""
    res_query = {"flightNumber": flight_number}
    if date:
        res_query["departureDate"] = date
    res_doc = db["reservations"].find_one(res_query, sort=[("fetchedAt", -1)])
    lookup = {}
    if res_doc:
        for rv in res_doc.get("reservations", []):
            pnr = rv.get("pnr", "")
            for p in rv.get("passengers", []):
                nat = p.get("nationality", "")
                if nat:
                    key = (pnr, p.get("lastName", "").upper())
                    lookup[key] = nat
    return lookup


@router.get("/{flight_number}/passengers")
def get_passengers(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Get the latest passenger list for a flight."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Passenger list not found")

    # Enrich passengers with nationality from reservations
    nat_lookup = _build_nationality_lookup(db, flight_number, date)
    passengers = doc.get("passengers", [])
    for p in passengers:
        key = (p.get("pnr", ""), p.get("lastName", "").upper())
        p["nationality"] = nat_lookup.get(key, "")

    return _strip_id(doc)


@router.get("/{flight_number}/passengers/summary")
def get_passenger_summary(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
):
    """Get a summary of passenger counts (checked-in, boarded, revenue, etc.)."""
    validate_date(date)
    validate_origin(origin)
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


@router.get("/{flight_number}/passengers/standby-list")
def get_standby_list(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Return the prioritized standby and upgrade queue for a flight.

    Passengers are grouped into:
    - **upgrade**: Revenue passengers with a PriorityCode indicating upgrade
      (e.g. UPG). Sorted by lineNumber.
    - **standby**: Non-revenue / staff passengers on the standby list
      (PriorityCode like B01, B02, etc.). Sorted by seniorityDate then lineNumber.

    Only passengers who are NOT yet checked-in are included (pending clearance).
    """
    validate_date(date)
    validate_origin(origin)
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

    upgrade_list = []
    standby_list = []

    for p in passengers:
        priority = p.get("priorityCode", "")
        if not priority:
            continue

        entry = {
            "lastName": p.get("lastName", ""),
            "firstName": p.get("firstName", ""),
            "pnr": p.get("pnr", ""),
            "lineNumber": p.get("lineNumber", 0),
            "priorityCode": priority,
            "bookingClass": p.get("bookingClass", ""),
            "desiredBookingClass": p.get("desiredBookingClass", ""),
            "cabin": p.get("cabin", ""),
            "seat": p.get("seat", ""),
            "destination": p.get("destination", ""),
            "corpId": p.get("corpId", ""),
            "seniorityDate": p.get("seniorityDate", ""),
            "isCheckedIn": p.get("isCheckedIn", False),
            "boardingPassIssued": p.get("boardingPassIssued", False),
            "isRevenue": p.get("isRevenue", True),
        }

        if priority == "UPG":
            upgrade_list.append(entry)
        else:
            standby_list.append(entry)

    # Sort upgrade list by lineNumber
    upgrade_list.sort(key=lambda x: x.get("lineNumber", 0))
    # Sort standby list by seniorityDate (earliest first), then lineNumber
    standby_list.sort(key=lambda x: (
        x.get("seniorityDate", "") or "9999", x.get("lineNumber", 0)))

    # Cabin availability from cabinSummary
    cabin_availability = []
    for cs in doc.get("cabinSummary", []):
        cabin_availability.append({
            "cabin": cs.get("cabin", ""),
            "destination": cs.get("destination", ""),
            "authorized": cs.get("authorized", 0),
            "available": cs.get("available", 0),
        })

    return {
        "flightNumber": flight_number,
        "origin": doc.get("origin", ""),
        "departureDate": doc.get("departureDate", ""),
        "fetchedAt": doc.get("fetchedAt", ""),
        "cabinAvailability": cabin_availability,
        "upgrade": {
            "total": len(upgrade_list),
            "passengers": upgrade_list,
        },
        "standby": {
            "total": len(standby_list),
            "passengers": standby_list,
        },
    }


@router.get("/{flight_number}/passengers/{pnr}")
def get_passenger_by_pnr(
    flight_number: str,
    pnr: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Find a specific passenger by PNR locator within a flight."""
    validate_date(date)
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


@router.get("/{flight_number}/passengers/{pnr}/detail")
def get_passenger_detail(
    flight_number: str,
    pnr: str,
    origin: str = Query(..., description="Departure airport code"),
    date: str = Query(..., description="Departure date YYYY-MM-DD"),
    airline: str = Query("GF", description="Airline code"),
):
    """Fetch detailed passenger data from Sabre in real-time.

    Calls GetPassengerDataRQ for deep per-passenger info including:
    baggage routing, full itinerary, check-in requirements, DOCS/passport,
    Timatic validation, and ancillary purchases.

    Requires the passenger's PNR. Looks up the last name from the
    stored passenger list automatically.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    # Look up last name from stored passenger list
    query = {"flightNumber": flight_number, "passengers.pnr": pnr}
    if date:
        query["departureDate"] = date
    pl_doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not pl_doc:
        raise HTTPException(
            status_code=404,
            detail="PNR not found in passenger list — ingest the flight first",
        )
    matching = [p for p in pl_doc.get("passengers", []) if p.get("pnr") == pnr]
    if not matching:
        raise HTTPException(status_code=404, detail="PNR not found")
    last_name = matching[0].get("lastName", "")
    if not last_name:
        raise HTTPException(
            status_code=400, detail="Could not determine last name for PNR")
    first_name = matching[0].get("firstName", "")

    try:
        with SabreClient() as client:
            raw_data, _, _ = client.get_passenger_data(
                airline=airline,
                flight_number=flight_number,
                departure_date=date,
                origin=origin,
                last_name=last_name,
                first_name=first_name or None,
                pnr=pnr,
            )
    except SabreError as e:
        raise HTTPException(status_code=502, detail=f"Sabre API error: {e}")

    result = convert_passenger_data(
        raw_data, airline, flight_number, date, origin)
    return result
