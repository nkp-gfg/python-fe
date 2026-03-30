"""Passenger list API endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.snapshot_versioning import get_snapshot_data_as_of
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


def _build_nationality_lookup(db, flight_number, date, origin=None, snapshot_sequence=None):
    """Build PNR+lastName → {nationality, specialMeal, wheelchairCode, ffTier...} lookup from reservations."""
    if snapshot_sequence:
        res_doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="reservations",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
        res_query = {"flightNumber": flight_number}
        if date:
            res_query["departureDate"] = date
        res_doc = db["reservations"].find_one(
            res_query, sort=[("fetchedAt", -1)])
    lookup = {}
    if res_doc:
        for rv in res_doc.get("reservations", []):
            pnr = rv.get("pnr", "")
            for p in rv.get("passengers", []):
                key = (pnr, p.get("lastName", "").upper())
                entry = {}
                nat = p.get("nationality", "")
                if nat:
                    entry["nationality"] = nat
                meal = p.get("specialMeal", "")
                if meal:
                    entry["specialMeal"] = meal
                wc = p.get("wheelchairCode", "")
                if wc:
                    entry["wheelchairCode"] = wc
                if p.get("hasEmergencyContact"):
                    entry["hasEmergencyContact"] = True
                ff_tier = p.get("ffTierLevel", "")
                if ff_tier:
                    entry["ffTierLevel"] = ff_tier
                    entry["ffTierName"] = p.get("ffTierName", "")
                ff_status = p.get("ffStatus", "")
                if ff_status:
                    entry["ffStatus"] = ff_status
                if entry:
                    lookup[key] = entry
    return lookup


@router.get("/{flight_number}/passengers")
def get_passengers(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
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

    if snapshot_sequence:
        doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="passenger_list",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
        doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Passenger list not found")

    # Enrich passengers with reservation data (nationality, meals, FF tier, etc.)
    nat_lookup = _build_nationality_lookup(
        db,
        flight_number,
        date,
        origin=origin,
        snapshot_sequence=snapshot_sequence,
    )
    passengers = doc.get("passengers", [])
    for p in passengers:
        key = (p.get("pnr", ""), p.get("lastName", "").upper())
        enrichment = nat_lookup.get(key, {})
        p["nationality"] = enrichment.get("nationality", "")
        if "specialMeal" in enrichment:
            p["specialMeal"] = enrichment["specialMeal"]
        if "wheelchairCode" in enrichment:
            p["wheelchairCode"] = enrichment["wheelchairCode"]
        if "hasEmergencyContact" in enrichment:
            p["hasEmergencyContact"] = enrichment["hasEmergencyContact"]
        if "ffTierLevel" in enrichment:
            p["ffTierLevel"] = enrichment["ffTierLevel"]
            p["ffTierName"] = enrichment.get("ffTierName", "")
        if "ffStatus" in enrichment:
            p["ffStatus"] = enrichment["ffStatus"]

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
    snapshot_sequence: int = Query(
        None,
        ge=1,
        description="Load historical view as-of this snapshot sequence number",
    ),
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

    if snapshot_sequence:
        doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="passenger_list",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
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


@router.get("/{flight_number}/passengers/groups")
def get_group_bookings(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    snapshot_sequence: int = Query(None, ge=1),
):
    """Get all group bookings for a flight with per-group details.

    Returns a list of group booking objects, each containing the group code,
    PNR, member count, cabin, and the list of individual group members.
    Unnamed group members (Sabre placeholder "PAX") are flagged explicitly.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    if snapshot_sequence:
        doc = get_snapshot_data_as_of(
            db,
            flight_number=flight_number,
            snapshot_type="passenger_list",
            snapshot_sequence=snapshot_sequence,
            origin=origin,
            departure_date=date,
        )
    else:
        doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Passenger list not found")

    passengers = doc.get("passengers", [])

    # Build group map — also works with older docs that lack groupCode
    # by falling back to _raw if available
    group_map = {}
    for p in passengers:
        gc = p.get("groupCode", "")
        if not gc:
            continue
        if gc not in group_map:
            group_map[gc] = {
                "groupCode": gc,
                "pnr": p.get("pnr", ""),
                "cabin": p.get("cabin", ""),
                "bookingClass": p.get("bookingClass", ""),
                "totalMembers": 0,
                "namedMembers": 0,
                "unnamedMembers": 0,
                "checkedIn": 0,
                "boarded": 0,
                "members": [],
            }
        g = group_map[gc]
        g["totalMembers"] += 1
        is_unnamed = p.get("isUnnamedGroup", False) or (
            p.get("lastName") == "PAX" and not p.get("firstName"))
        if is_unnamed:
            g["unnamedMembers"] += 1
        else:
            g["namedMembers"] += 1
        if p.get("isCheckedIn"):
            g["checkedIn"] += 1
        if p.get("isBoarded"):
            g["boarded"] += 1
        g["members"].append({
            "lastName": p.get("lastName", ""),
            "firstName": p.get("firstName", ""),
            "pnr": p.get("pnr", ""),
            "passengerId": p.get("passengerId", ""),
            "lineNumber": p.get("lineNumber", 0),
            "isCheckedIn": p.get("isCheckedIn", False),
            "isBoarded": p.get("isBoarded", False),
            "isUnnamed": is_unnamed,
            "seat": p.get("seat", ""),
        })

    # If no groupCode fields exist, try extracting from _raw
    if not group_map:
        raw = doc.get("_raw")
        if raw:
            from backend.feeder.converter import _strip_ns, _ensure_list
            stripped = _strip_ns(raw)
            raw_pax = _ensure_list(
                stripped.get("PassengerInfoList", {}).get("PassengerInfo", []))
            pax_by_id = {p.get("passengerId", ""): p for p in passengers}
            for rp in raw_pax:
                gc = rp.get("GroupCode", "")
                if not gc:
                    continue
                pid = rp.get("PassengerID", "")
                stored_pax = pax_by_id.get(pid, {})
                if gc not in group_map:
                    pnr_raw = rp.get("PNRLocator", {})
                    pnr = pnr_raw.get("#text", "") if isinstance(
                        pnr_raw, dict) else str(pnr_raw)
                    group_map[gc] = {
                        "groupCode": gc,
                        "pnr": pnr,
                        "cabin": rp.get("Cabin", ""),
                        "bookingClass": rp.get("BookingClass", ""),
                        "totalMembers": 0,
                        "namedMembers": 0,
                        "unnamedMembers": 0,
                        "checkedIn": 0,
                        "boarded": 0,
                        "members": [],
                    }
                g = group_map[gc]
                g["totalMembers"] += 1
                name = rp.get("Name_Details", {})
                is_unnamed = name.get(
                    "LastName") == "PAX" and not name.get("FirstName")
                if is_unnamed:
                    g["unnamedMembers"] += 1
                else:
                    g["namedMembers"] += 1
                if stored_pax.get("isCheckedIn"):
                    g["checkedIn"] += 1
                if stored_pax.get("isBoarded"):
                    g["boarded"] += 1
                g["members"].append({
                    "lastName": name.get("LastName", ""),
                    "firstName": name.get("FirstName", ""),
                    "pnr": stored_pax.get("pnr", ""),
                    "passengerId": pid,
                    "lineNumber": stored_pax.get("lineNumber", 0),
                    "isCheckedIn": stored_pax.get("isCheckedIn", False),
                    "isBoarded": stored_pax.get("isBoarded", False),
                    "isUnnamed": is_unnamed,
                    "seat": stored_pax.get("seat", ""),
                })

    groups = sorted(group_map.values(), key=lambda g: g["groupCode"])
    total_group_pax = sum(g["totalMembers"] for g in groups)
    total_unnamed = sum(g["unnamedMembers"] for g in groups)

    return {
        "flightNumber": flight_number,
        "origin": doc.get("origin", ""),
        "departureDate": doc.get("departureDate", ""),
        "fetchedAt": doc.get("fetchedAt", ""),
        "totalGroups": len(groups),
        "totalGroupPassengers": total_group_pax,
        "totalUnnamed": total_unnamed,
        "groups": groups,
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
