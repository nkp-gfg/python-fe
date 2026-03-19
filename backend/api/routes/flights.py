"""Flight status API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db

router = APIRouter(prefix="/flights", tags=["flights"])


def _strip_id(doc):
    """Remove MongoDB _id and _raw from response."""
    if doc:
        doc.pop("_id", None)
        doc.pop("_raw", None)
    return doc


def _empty_state_bucket():
    return {
        "totalPassengers": 0,
        "totalSouls": 0,
        "economy": 0,
        "business": 0,
        "adults": 0,
        "children": 0,
        "infants": 0,
    }


def _extract_destination(flight_status_doc, passenger_doc):
    if passenger_doc:
        if passenger_doc.get("destination"):
            return passenger_doc["destination"]
        for cabin in passenger_doc.get("cabinSummary", []):
            if cabin.get("destination"):
                return cabin["destination"]

    if flight_status_doc:
        for leg in flight_status_doc.get("legs", []):
            city = leg.get("city")
            if city and city != flight_status_doc.get("origin"):
                return city

    return ""


def _tree_badges(*items):
    badges = []
    for key, value in items:
        if value:
            badges.append({"type": key, "value": value})
    return badges


def _analyze_passengers(passengers):
    """Deep passenger analysis: cabin × gender/age/staff breakdown."""
    result = {
        "economy": {
            "total": 0,
            "passengers": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
            "staff": {"total": 0, "male": 0, "female": 0},
        },
        "business": {
            "total": 0,
            "passengers": {"total": 0, "male": 0, "female": 0, "children": 0, "infants": 0},
            "staff": {"total": 0, "male": 0, "female": 0},
        },
        "checkedIn": 0, "boarded": 0, "notCheckedIn": 0,
        "revenue": 0, "nonRevenue": 0,
        "totalMale": 0, "totalFemale": 0, "totalChildren": 0, "totalInfants": 0,
        "cabinTotals": {
            "economy": {"passengers": 0, "souls": 0},
            "business": {"passengers": 0, "souls": 0},
        },
        "stateBreakdown": {
            "booked": _empty_state_bucket(),
            "checkedIn": _empty_state_bucket(),
            "boarded": _empty_state_bucket(),
        },
    }
    for p in passengers:
        cabin = p.get("cabin", "Y")
        is_revenue = p.get("isRevenue", True)
        is_staff = p.get("passengerType") in ("E", "S") or not is_revenue
        edit_codes = p.get("editCodes", [])
        is_child = p.get("isChild", False)
        has_infant = p.get("hasInfant", False)
        is_male = "M" in edit_codes and not is_child
        is_female = "F" in edit_codes and not is_child

        if p.get("isCheckedIn"):
            result["checkedIn"] += 1
        if p.get("isBoarded"):
            result["boarded"] += 1
        if not p.get("isCheckedIn"):
            result["notCheckedIn"] += 1
        if is_revenue:
            result["revenue"] += 1
        else:
            result["nonRevenue"] += 1

        if is_child:
            result["totalChildren"] += 1
        elif is_male:
            result["totalMale"] += 1
        elif is_female:
            result["totalFemale"] += 1

        if has_infant:
            result["totalInfants"] += 1

        cabin_key = "business" if cabin == "J" else "economy"
        result[cabin_key]["total"] += 1
        result["cabinTotals"][cabin_key]["passengers"] += 1
        result["cabinTotals"][cabin_key]["souls"] += 1 + \
            (1 if has_infant else 0)
        cat = "staff" if is_staff else "passengers"
        result[cabin_key][cat]["total"] += 1

        if is_child:
            result[cabin_key][cat]["children"] += 1
        elif is_male:
            result[cabin_key][cat]["male"] += 1
        elif is_female:
            result[cabin_key][cat]["female"] += 1
        if has_infant:
            if cat == "passengers":
                result[cabin_key][cat]["infants"] += 1

        state_key = "boarded" if p.get("isBoarded") else "checkedIn" if p.get(
            "isCheckedIn") else "booked"
        bucket = result["stateBreakdown"][state_key]
        bucket["totalPassengers"] += 1
        bucket["totalSouls"] += 1 + (1 if has_infant else 0)
        bucket[cabin_key] += 1 + (1 if has_infant else 0)
        if is_child:
            bucket["children"] += 1
        else:
            bucket["adults"] += 1
        if has_infant:
            bucket["infants"] += 1

    return result


def _build_tree_payload(analysis, passenger_summary):
    ep = analysis.get("economy", {}).get("passengers", {})
    es = analysis.get("economy", {}).get("staff", {})
    bp = analysis.get("business", {}).get("passengers", {})
    bs = analysis.get("business", {}).get("staff", {})
    total_souls = passenger_summary.get("totalSouls", 0)

    root_x = 470
    positions = {
        "root": {"x": root_x, "y": 50, "w": 150, "h": 78},
        "economy": {"x": root_x - 200, "y": 175, "w": 128, "h": 70},
        "business": {"x": root_x + 200, "y": 175, "w": 128, "h": 70},
        "economyPassengers": {"x": root_x - 290, "y": 300, "w": 112, "h": 64},
        "economyStaff": {"x": root_x - 115, "y": 300, "w": 112, "h": 64},
        "businessPassengers": {"x": root_x + 115, "y": 300, "w": 112, "h": 64},
        "businessStaff": {"x": root_x + 290, "y": 300, "w": 112, "h": 64},
        "economyMale": {"x": root_x - 375, "y": 420, "w": 70, "h": 68},
        "economyFemale": {"x": root_x - 298, "y": 420, "w": 70, "h": 68},
        "economyChildren": {"x": root_x - 221, "y": 420, "w": 70, "h": 68},
        "economyInfants": {"x": root_x - 144, "y": 420, "w": 70, "h": 68},
        "businessMale": {"x": root_x + 93, "y": 420, "w": 70, "h": 68},
        "businessFemale": {"x": root_x + 170, "y": 420, "w": 70, "h": 68},
        "boarded": {"x": root_x - 115, "y": 525, "w": 108, "h": 58},
        "notCheckedIn": {"x": root_x, "y": 525, "w": 108, "h": 58},
        "revenue": {"x": root_x + 115, "y": 525, "w": 108, "h": 58},
    }

    nodes = [
        {
            "id": "root",
            **positions["root"],
            "borderColor": "hsl(var(--muted-foreground))",
            "textColor": "hsl(var(--foreground))",
            "label": "Aircraft Humans",
            "value": total_souls,
            "subLabel": "Total souls",
            "badges": _tree_badges(
                ("M", analysis.get("totalMale", 0)),
                ("F", analysis.get("totalFemale", 0)),
                ("C", analysis.get("totalChildren", 0)),
                ("I", passenger_summary.get("infantCount", 0)),
            ),
        },
        {
            "id": "economy",
            **positions["economy"],
            "borderColor": "#2ec27e",
            "textColor": "#2ec27e",
            "label": "Economy",
            "value": analysis.get("economy", {}).get("total", 0),
            "subLabel": f"{analysis.get('economy', {}).get('total', 0)} pax",
            "badges": [],
        },
        {
            "id": "business",
            **positions["business"],
            "borderColor": "#c9a43a",
            "textColor": "#c9a43a",
            "label": "Business",
            "value": analysis.get("business", {}).get("total", 0),
            "subLabel": f"{analysis.get('business', {}).get('total', 0)} pax",
            "badges": [],
        },
        {
            "id": "economyPassengers",
            **positions["economyPassengers"],
            "borderColor": "#3b8eed",
            "textColor": "#3b8eed",
            "label": "Passengers",
            "value": ep.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges(("M", ep.get("male", 0)), ("F", ep.get("female", 0)), ("C", ep.get("children", 0)), ("I", ep.get("infants", 0))),
        },
        {
            "id": "economyStaff",
            **positions["economyStaff"],
            "borderColor": "#9b6dff",
            "textColor": "#9b6dff",
            "label": "Staff",
            "value": es.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges(("M", es.get("male", 0)), ("F", es.get("female", 0))),
        },
        {
            "id": "businessPassengers",
            **positions["businessPassengers"],
            "borderColor": "#3b8eed",
            "textColor": "#3b8eed",
            "label": "Passengers",
            "value": bp.get("total", 0),
            "subLabel": "Revenue",
            "badges": _tree_badges(("M", bp.get("male", 0)), ("F", bp.get("female", 0)), ("C", bp.get("children", 0)), ("I", bp.get("infants", 0))),
        },
        {
            "id": "businessStaff",
            **positions["businessStaff"],
            "borderColor": "#9b6dff",
            "textColor": "#9b6dff",
            "label": "Staff",
            "value": bs.get("total", 0),
            "subLabel": "Non-Revenue",
            "badges": _tree_badges(("M", bs.get("male", 0)), ("F", bs.get("female", 0))),
        },
        {"id": "economyMale", **positions["economyMale"], "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Male", "value": ep.get("male", 0), "subLabel": "", "badges": _tree_badges(("M", ep.get("male", 0)))},
        {"id": "economyFemale", **positions["economyFemale"], "borderColor": "#e8588c", "textColor": "#e8588c",
            "label": "Female", "value": ep.get("female", 0), "subLabel": "", "badges": _tree_badges(("F", ep.get("female", 0)))},
        {"id": "economyChildren", **positions["economyChildren"], "borderColor": "#2ec27e", "textColor": "#2ec27e",
            "label": "Children", "value": ep.get("children", 0), "subLabel": "CHD", "badges": _tree_badges(("C", ep.get("children", 0)))},
        {"id": "economyInfants", **positions["economyInfants"], "borderColor": "#e89a3c", "textColor": "#e89a3c", "label": "Infants",
            "value": ep.get("infants", 0), "subLabel": "INF (lap)", "badges": _tree_badges(("I", ep.get("infants", 0)))},
        {"id": "businessMale", **positions["businessMale"], "borderColor": "#3b8eed", "textColor": "#3b8eed",
            "label": "Male", "value": bp.get("male", 0), "subLabel": "", "badges": _tree_badges(("M", bp.get("male", 0)))},
        {"id": "businessFemale", **positions["businessFemale"], "borderColor": "#e8588c", "textColor": "#e8588c",
            "label": "Female", "value": bp.get("female", 0), "subLabel": "", "badges": _tree_badges(("F", bp.get("female", 0)))},
        {
            "id": "boarded",
            **positions["boarded"],
            "borderColor": "#2ec27e",
            "textColor": "#2ec27e",
            "label": "Boarded",
            "value": analysis.get("boarded", 0),
            "subLabel": f"of {passenger_summary.get('totalPassengers', 0)}",
            "badges": [],
        },
        {
            "id": "notCheckedIn",
            **positions["notCheckedIn"],
            "borderColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
            "textColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e",
            "label": "Not Checked-In",
            "value": analysis.get("notCheckedIn", 0),
            "subLabel": "Excluded from SOB" if analysis.get("notCheckedIn", 0) > 0 else "",
            "badges": [],
        },
        {
            "id": "revenue",
            **positions["revenue"],
            "borderColor": "#35c0c0",
            "textColor": "#35c0c0",
            "label": "Revenue",
            "value": analysis.get("revenue", 0),
            "subLabel": f"{analysis.get('nonRevenue', 0)} non-rev",
            "badges": [],
        },
    ]

    edges = [
        ["root", "economy"], ["root", "business"],
        ["economy", "economyPassengers"], ["economy", "economyStaff"],
        ["business", "businessPassengers"], ["business", "businessStaff"],
        ["economyPassengers", "economyMale"], [
            "economyPassengers", "economyFemale"],
        ["economyPassengers", "economyChildren"], [
            "economyPassengers", "economyInfants"],
        ["businessPassengers", "businessMale"], [
            "businessPassengers", "businessFemale"],
    ]

    return {
        "title": "Aircraft Humans Breakdown Tree",
        "badge": "Sabre Live",
        "width": 940,
        "height": 600,
        "nodes": nodes,
        "edges": [{"from": start, "to": end} for start, end in edges],
        "statusCards": [
            {"id": "boarded", "label": "Boarded", "value": analysis.get(
                "boarded", 0), "subLabel": f"of {passenger_summary.get('totalPassengers', 0)}", "borderColor": "#2ec27e", "textColor": "#2ec27e"},
            {"id": "notCheckedIn", "label": "Not Checked-In", "value": analysis.get("notCheckedIn", 0), "subLabel": "Excluded from SOB" if analysis.get(
                "notCheckedIn", 0) > 0 else "", "borderColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e", "textColor": "#e84545" if analysis.get("notCheckedIn", 0) > 0 else "#2ec27e"},
            {"id": "revenue", "label": "Revenue", "value": analysis.get(
                "revenue", 0), "subLabel": f"{analysis.get('nonRevenue', 0)} non-rev", "borderColor": "#35c0c0", "textColor": "#35c0c0"},
        ],
    }


def _summarize_flight_for_list(flight_status_doc, passenger_doc):
    analysis = _analyze_passengers(passenger_doc.get(
        "passengers", [])) if passenger_doc else None
    destination = _extract_destination(flight_status_doc, passenger_doc)
    passenger_summary = {
        "totalPassengers": passenger_doc.get("totalPassengers", 0) if passenger_doc else 0,
        "adultCount": passenger_doc.get("adultCount", 0) if passenger_doc else 0,
        "childCount": passenger_doc.get("childCount", 0) if passenger_doc else 0,
        "infantCount": passenger_doc.get("infantCount", 0) if passenger_doc else 0,
        "totalSouls": passenger_doc.get("totalSouls", 0) if passenger_doc else 0,
    }
    return {
        "destination": destination,
        "passengerSummary": passenger_summary,
        "operationalSummary": {
            "checkedIn": analysis.get("checkedIn", 0) if analysis else 0,
            "boarded": analysis.get("boarded", 0) if analysis else 0,
            "notCheckedIn": analysis.get("notCheckedIn", 0) if analysis else 0,
            "soulsOnBoard": analysis.get("stateBreakdown", {}).get("boarded", {}).get("totalSouls", 0) if analysis else 0,
            "economySouls": analysis.get("cabinTotals", {}).get("economy", {}).get("souls", 0) if analysis else 0,
            "businessSouls": analysis.get("cabinTotals", {}).get("business", {}).get("souls", 0) if analysis else 0,
        },
    }


def _build_dashboard_payload(fs, pl, origin, date, change_summary):
    if pl:
        passengers = pl.get("passengers", [])
        passenger_summary = {
            "totalPassengers": pl.get("totalPassengers", 0),
            "adultCount": pl.get("adultCount", 0),
            "childCount": pl.get("childCount", 0),
            "infantCount": pl.get("infantCount", 0),
            "totalSouls": pl.get("totalSouls", 0),
            "cabinSummary": pl.get("cabinSummary", []),
        }
        analysis = _analyze_passengers(passengers)
    else:
        passenger_summary = {}
        analysis = {}

    destination = _extract_destination(fs, pl)
    boarded_breakdown = analysis.get("stateBreakdown", {}).get(
        "boarded", _empty_state_bucket())
    checked_in_breakdown = analysis.get("stateBreakdown", {}).get(
        "checkedIn", _empty_state_bucket())
    booked_breakdown = analysis.get("stateBreakdown", {}).get(
        "booked", _empty_state_bucket())
    tracked_changes = sum(change_summary.values())
    tree = _build_tree_payload(
        analysis, passenger_summary) if analysis and passenger_summary else None

    return {
        "flightStatus": fs,
        "route": {
            "origin": (fs or {}).get("origin") or (pl or {}).get("origin") or origin or "",
            "destination": destination,
            "departureDate": date or (fs or {}).get("departureDate") or (pl or {}).get("departureDate") or "",
        },
        "passengerSummary": passenger_summary,
        "analysis": analysis,
        "changeSummary": change_summary,
        "overview": {
            "soulsOnBoard": boarded_breakdown.get("totalSouls", 0),
            "manifestRecords": passenger_summary.get("totalPassengers", 0),
            "totalSouls": passenger_summary.get("totalSouls", 0),
            "economySouls": analysis.get("cabinTotals", {}).get("economy", {}).get("souls", 0),
            "businessSouls": analysis.get("cabinTotals", {}).get("business", {}).get("souls", 0),
            "trackedChanges": tracked_changes,
        },
        "stateSummary": {
            "booked": booked_breakdown,
            "checkedIn": checked_in_breakdown,
            "boarded": boarded_breakdown,
            "others": {
                "jumpSeat": ((fs or {}).get("jumpSeat") or {}).get("cockpit", 0) + ((fs or {}).get("jumpSeat") or {}).get("cabin", 0),
                "nonRevenue": analysis.get("nonRevenue", 0),
                "offloaded": None,
                "noShow": None,
                "offloadedAvailable": False,
                "noShowAvailable": False,
            },
        },
        "tree": tree,
    }


@router.get("")
def list_flights(
    date: str = Query(None, description="Filter by departure date YYYY-MM-DD"),
):
    """List all distinct flights in the database with their latest status."""
    db = get_db()
    match = {}
    if date:
        match["departureDate"] = date

    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"fetchedAt": -1}},
        {
            "$group": {
                "_id": {
                    "airline": "$airline",
                    "flightNumber": "$flightNumber",
                    "origin": "$origin",
                    "departureDate": "$departureDate",
                },
                "status": {"$first": "$status"},
                "gate": {"$first": "$gate"},
                "aircraft": {"$first": "$aircraft"},
                "schedule": {"$first": "$schedule"},
                "passengerCounts": {"$first": "$passengerCounts"},
                "jumpSeat": {"$first": "$jumpSeat"},
                "fetchedAt": {"$first": "$fetchedAt"},
            }
        },
        {"$sort": {"_id.departureDate": 1, "_id.flightNumber": 1}},
    ]
    results = list(db["flight_status"].aggregate(pipeline))

    passenger_pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"fetchedAt": -1}},
        {
            "$group": {
                "_id": {
                    "airline": "$airline",
                    "flightNumber": "$flightNumber",
                    "origin": "$origin",
                    "departureDate": "$departureDate",
                },
                "destination": {"$first": "$destination"},
                "totalPassengers": {"$first": "$totalPassengers"},
                "adultCount": {"$first": "$adultCount"},
                "childCount": {"$first": "$childCount"},
                "infantCount": {"$first": "$infantCount"},
                "totalSouls": {"$first": "$totalSouls"},
                "passengers": {"$first": "$passengers"},
                "cabinSummary": {"$first": "$cabinSummary"},
            }
        },
    ]
    passenger_results = list(
        db["passenger_list"].aggregate(passenger_pipeline))
    passenger_by_key = {
        (
            r["_id"].get("airline"),
            r["_id"].get("flightNumber"),
            r["_id"].get("origin"),
            r["_id"].get("departureDate"),
        ): r
        for r in passenger_results
    }

    flights = []
    for r in results:
        fid = r["_id"]
        passenger_doc = passenger_by_key.get(
            (
                fid.get("airline", "GF"),
                fid.get("flightNumber"),
                fid.get("origin", ""),
                fid.get("departureDate", ""),
            )
        )
        summary = _summarize_flight_for_list(r, passenger_doc)
        flights.append({
            "airline": fid.get("airline", "GF"),
            "flightNumber": fid["flightNumber"],
            "origin": fid.get("origin", ""),
            "destination": summary["destination"],
            "departureDate": fid.get("departureDate", ""),
            "status": r.get("status"),
            "gate": r.get("gate"),
            "aircraft": r.get("aircraft"),
            "schedule": r.get("schedule"),
            "passengerCounts": r.get("passengerCounts"),
            "jumpSeat": r.get("jumpSeat"),
            "passengerSummary": summary["passengerSummary"],
            "operationalSummary": summary["operationalSummary"],
            "fetchedAt": str(r.get("fetchedAt", "")),
        })
    return flights


@router.get("/{flight_number}/dashboard")
def get_flight_dashboard(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Combined dashboard endpoint — returns flight status + deep passenger
    analysis in a single response for the frontend dashboard view.
    """
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    # Flight status
    fs = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    if fs:
        fs.pop("_id", None)
        fs.pop("_raw", None)

    # Passenger list
    pl = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if pl:
        pl.pop("_id", None)
        pl.pop("_raw", None)

    # Change summary
    change_match = {"flightNumber": flight_number}
    if origin:
        change_match["origin"] = origin
    if date:
        change_match["departureDate"] = date
    change_pipeline = [
        {"$match": change_match},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
    ]
    change_results = list(db["changes"].aggregate(change_pipeline))
    change_summary = {r["_id"]: r["count"] for r in change_results}

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    return _build_dashboard_payload(fs, pl, origin, date, change_summary)


@router.get("/{flight_number}/tree")
def get_flight_tree(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """Return a dedicated tree payload for the selected flight."""
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    fs = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    pl = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])
    if fs:
        fs.pop("_id", None)
        fs.pop("_raw", None)
    if pl:
        pl.pop("_id", None)
        pl.pop("_raw", None)

    if not fs and not pl:
        raise HTTPException(status_code=404, detail="Flight not found")

    payload = _build_dashboard_payload(fs, pl, origin, date, {})
    return payload.get("tree") or {
        "title": "Aircraft Humans Breakdown Tree",
        "badge": "Sabre Live",
        "width": 940,
        "height": 600,
        "nodes": [],
        "edges": [],
        "statusCards": [],
    }


@router.get("/{flight_number}/status")
def get_flight_status(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code (e.g. LHR)"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Get the latest flight status for a flight.
    Optionally filter by origin and/or date.
    """
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    doc = db["flight_status"].find_one(query, sort=[("fetchedAt", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Flight status not found")
    return _strip_id(doc)


@router.get("/{flight_number}/status/history")
def get_flight_status_history(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Get historical flight status snapshots (newest first)."""
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    cursor = db["flight_status"].find(query).sort("fetchedAt", -1).limit(limit)
    return [_strip_id(doc) for doc in cursor]
