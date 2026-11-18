"""MongoDB aggregation pipelines for server-side comparison and analytics.

These pipelines run inside MongoDB and return only the anomalies/results,
avoiding pulling full document sets into Python.
"""

import logging
from datetime import datetime, timezone

from backend.feeder.storage import get_db, latest_snapshot_stages
from backend.api.postgres import query_all

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_missing_pnrs(date: str) -> dict:
    """Find PNRs that exist in Sabre passenger lists but not in reservations, and vice versa.

    Uses $lookup + $setDifference aggregation to do the work server-side.
    """
    db = get_db()

    # Pipeline: get latest passenger_list per flight, extract unique PNRs,
    # then $lookup against reservations to find mismatches.
    pipeline = [
        # Latest passenger_list per flight for this date, PNRs only
        *latest_snapshot_stages(
            "passenger_list",
            {"departureDate": date},
            {"flightNumber": "$flightNumber", "origin": "$origin"},
            {"flightNumber": 1, "origin": 1, "passengers.pnr": 1},
        ),
        {"$match": {"passengers.0": {"$exists": True}}},
        # Lookup latest reservations for same flight, PNRs only
        {"$lookup": {
            "from": "reservations",
            "let": {"fn": "$flightNumber"},
            "pipeline": latest_snapshot_stages(
                "reservations",
                {
                    "departureDate": date,
                    "$expr": {"$eq": ["$flightNumber", "$$fn"]},
                },
                None,
                {"_id": 0, "resPnrs": "$reservations.pnr"},
            ),
            "as": "resData",
        }},
        # Compute set differences
        {"$project": {
            "flightNumber": 1,
            "origin": 1,
            "paxPnrs": {"$setUnion": [{"$ifNull": ["$passengers.pnr", []]}]},
            "resPnrs": {"$setUnion": [{"$ifNull": [
                {"$arrayElemAt": ["$resData.resPnrs", 0]}, []]}]},
        }},
        {"$project": {
            "flightNumber": 1,
            "origin": 1,
            "inPaxNotRes": {"$setDifference": ["$paxPnrs", "$resPnrs"]},
            "inResNotPax": {"$setDifference": ["$resPnrs", "$paxPnrs"]},
            "totalPaxPnrs": {"$size": "$paxPnrs"},
            "totalResPnrs": {"$size": "$resPnrs"},
        }},
        # Only return flights with mismatches
        {"$match": {
            "$or": [
                {"inPaxNotRes": {"$not": {"$size": 0}}},
                {"inResNotPax": {"$not": {"$size": 0}}},
            ]
        }},
    ]

    results = list(db["passenger_list"].aggregate(pipeline))

    flights_with_mismatches = []
    total_pax_only = 0
    total_res_only = 0

    for r in results:
        pax_only = r.get("inPaxNotRes", [])
        res_only = r.get("inResNotPax", [])
        total_pax_only += len(pax_only)
        total_res_only += len(res_only)

        flights_with_mismatches.append({
            "flightNumber": r.get("flightNumber", ""),
            "origin": r.get("origin", ""),
            "totalPaxPnrs": r.get("totalPaxPnrs", 0),
            "totalResPnrs": r.get("totalResPnrs", 0),
            "inPaxNotReservations": pax_only,
            "inReservationsNotPax": res_only,
        })

    return {
        "date": date,
        "comparedAt": _now_iso(),
        "flightsWithMismatches": len(flights_with_mismatches),
        "totalPaxOnlyPnrs": total_pax_only,
        "totalResOnlyPnrs": total_res_only,
        "flights": flights_with_mismatches,
    }


def passenger_status_distribution(date: str, flight_numbers: list[str] | None = None) -> dict:
    """Aggregate passenger status distribution across all flights for a date.

    Returns counts of booked, checked-in, boarded per cabin class.
    Server-side aggregation avoids pulling all passenger docs.
    """
    db = get_db()

    match = {"departureDate": date}
    if flight_numbers:
        match["flightNumber"] = {"$in": flight_numbers}

    pipeline = [
        *latest_snapshot_stages(
            "passenger_list",
            match,
            {"flightNumber": "$flightNumber", "origin": "$origin"},
            {
                "flightNumber": 1,
                "passengers.cabin": 1,
                "passengers.isCheckedIn": 1,
                "passengers.isBoarded": 1,
                "passengers.isRevenue": 1,
            },
        ),
        {"$unwind": "$passengers"},
        {"$group": {
            "_id": {
                "flightNumber": "$flightNumber",
                "cabin": "$passengers.cabin",
            },
            "total": {"$sum": 1},
            "checkedIn": {"$sum": {"$cond": ["$passengers.isCheckedIn", 1, 0]}},
            "boarded": {"$sum": {"$cond": ["$passengers.isBoarded", 1, 0]}},
            "revenue": {"$sum": {"$cond": ["$passengers.isRevenue", 1, 0]}},
        }},
        {"$group": {
            "_id": "$_id.cabin",
            "totalPax": {"$sum": "$total"},
            "checkedIn": {"$sum": "$checkedIn"},
            "boarded": {"$sum": "$boarded"},
            "revenue": {"$sum": "$revenue"},
            "flights": {"$sum": 1},
        }},
    ]

    results = sorted(
        db["passenger_list"].aggregate(pipeline),
        key=lambda r: r["_id"] or "",
    )

    cabins = {}
    grand_total = 0
    for r in results:
        cabin = r["_id"] or "?"
        cabins[cabin] = {
            "total": r["totalPax"],
            "checkedIn": r["checkedIn"],
            "boarded": r["boarded"],
            "revenue": r["revenue"],
            "flights": r["flights"],
        }
        grand_total += r["totalPax"]

    return {
        "date": date,
        "comparedAt": _now_iso(),
        "grandTotal": grand_total,
        "byCabin": cabins,
    }


def change_type_summary(date: str) -> dict:
    """Aggregate change types for a date — how many of each change happened."""
    db = get_db()

    pipeline = [
        {"$match": {"departureDate": date}},
        {"$group": {
            "_id": "$changeType",
            "count": {"$sum": 1},
            "flights": {"$addToSet": "$flightNumber"},
        }},
        {"$project": {
            "changeType": "$_id",
            "count": 1,
            "affectedFlights": {"$size": "$flights"},
        }},
        {"$sort": {"count": -1}},
    ]

    results = list(db["changes"].aggregate(pipeline))

    return {
        "date": date,
        "comparedAt": _now_iso(),
        "totalChanges": sum(r["count"] for r in results),
        "byType": [
            {
                "changeType": r["changeType"],
                "count": r["count"],
                "affectedFlights": r["affectedFlights"],
            }
            for r in results
        ],
    }
