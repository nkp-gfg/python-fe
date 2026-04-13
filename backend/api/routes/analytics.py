"""Network-wide analytics API endpoints.

Aggregates flight, passenger, reservation, and change data across all flights
for a given date range to produce operational & business intelligence dashboards.
"""

from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from backend.api.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Helpers ───────────────────────────────────────────────────────────────

def _flight_match(date_from: str, date_to: str, origin: Optional[str],
                  flight_number: Optional[str],
                  destination: Optional[str] = None) -> dict:
    """Build $match for the *flights* collection.
    Note: flights collection does not store destination directly."""
    m: dict = {"departureDate": {"$gte": date_from, "$lte": date_to}}
    if origin:
        m["origin"] = origin
    if flight_number:
        m["flightNumber"] = flight_number
    # destination not stored on flights collection — skip
    return m


def _pax_match(date_from: str, date_to: str, origin: Optional[str],
               flight_number: Optional[str],
               destination: Optional[str] = None) -> dict:
    """Build $match for *passenger_list* collection."""
    m: dict = {"departureDate": {"$gte": date_from, "$lte": date_to}}
    if origin:
        m["origin"] = origin
    if flight_number:
        m["flightNumber"] = flight_number
    if destination:
        m["destination"] = destination
    return m


def _res_match(date_from: str, date_to: str, origin: Optional[str],
               flight_number: Optional[str],
               destination: Optional[str] = None) -> dict:
    """Build $match for *reservations* collection.
    Note: reservations does not store destination/arrivalAirport."""
    m: dict = {"departureDate": {"$gte": date_from, "$lte": date_to}}
    if origin:
        m["departureAirport"] = origin
    if flight_number:
        m["flightNumber"] = flight_number
    # destination not stored on reservations — skip
    return m


def _change_match(date_from: str, date_to: str, origin: Optional[str],
                  flight_number: Optional[str],
                  destination: Optional[str] = None) -> dict:
    """Build $match for *changes* collection.
    Note: changes does not store destination."""
    m: dict = {"departureDate": {"$gte": date_from, "$lte": date_to}}
    if origin:
        m["origin"] = origin
    if flight_number:
        m["flightNumber"] = flight_number
    # destination not stored on changes — skip
    return m


def _run_network_analytics(date_from: str, date_to: str,
                           origin: Optional[str] = None,
                           cabin: Optional[str] = None,
                           flight_number: Optional[str] = None,
                           destination: Optional[str] = None):
    """Run all analytics aggregations for a date range and return combined result."""
    db = get_db()

    fm = _flight_match(date_from, date_to, origin, flight_number, destination)
    pm = _pax_match(date_from, date_to, origin, flight_number, destination)
    rm = _res_match(date_from, date_to, origin, flight_number, destination)
    cm = _change_match(date_from, date_to, origin, flight_number, destination)

    # ── 1. Network Overview KPIs ──────────────────────────────────────────
    overview_pipeline = [
        {"$match": fm},
        {"$group": {
            "_id": None,
            "totalFlights": {"$sum": 1},
            "uniqueFlightNumbers": {"$addToSet": "$flightNumber"},
            "uniqueOrigins": {"$addToSet": "$origin"},
            "dates": {"$addToSet": "$departureDate"},
        }},
        {"$project": {
            "_id": 0,
            "totalFlights": 1,
            "uniqueRoutes": {"$size": "$uniqueFlightNumbers"},
            "uniqueStations": {"$size": "$uniqueOrigins"},
            "daysTracked": {"$size": "$dates"},
        }},
    ]
    overview_result = list(db.flights.aggregate(overview_pipeline))
    overview = overview_result[0] if overview_result else {
        "totalFlights": 0, "uniqueRoutes": 0, "uniqueStations": 0, "daysTracked": 0
    }

    # ── 2. Passenger totals from latest passenger_list per flight ─────────
    pax_pipeline = [
        {"$match": pm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin", "departureDate": "$departureDate"},
            "totalPassengers": {"$first": "$totalPassengers"},
            "adultCount": {"$first": "$adultCount"},
            "childCount": {"$first": "$childCount"},
            "infantCount": {"$first": "$infantCount"},
            "totalSouls": {"$first": "$totalSouls"},
            "cabinSummary": {"$first": "$cabinSummary"},
        }},
        {"$group": {
            "_id": None,
            "totalPassengers": {"$sum": "$totalPassengers"},
            "totalAdults": {"$sum": "$adultCount"},
            "totalChildren": {"$sum": "$childCount"},
            "totalInfants": {"$sum": "$infantCount"},
            "totalSouls": {"$sum": "$totalSouls"},
            "flightCount": {"$sum": 1},
            "maxPax": {"$max": "$totalPassengers"},
            "avgPax": {"$avg": "$totalPassengers"},
            "cabinSummaries": {"$push": "$cabinSummary"},
        }},
    ]
    pax_result = list(db.passenger_list.aggregate(pax_pipeline))
    pax_totals = pax_result[0] if pax_result else {
        "totalPassengers": 0, "totalAdults": 0, "totalChildren": 0, "totalInfants": 0,
        "totalSouls": 0, "flightCount": 0, "maxPax": 0, "avgPax": 0, "cabinSummaries": []
    }

    # Calculate cabin totals
    economy_pax = 0
    business_pax = 0
    total_authorized = 0
    total_booked = 0
    for summaries in pax_totals.get("cabinSummaries", []):
        for cab in (summaries or []):
            if cab.get("cabin") == "Y":
                economy_pax += cab.get("count", 0)
                total_authorized += cab.get("authorized", 0)
                total_booked += cab.get("count", 0)
            elif cab.get("cabin") == "J":
                business_pax += cab.get("count", 0)
                total_authorized += cab.get("authorized", 0)
                total_booked += cab.get("count", 0)

    avg_load_factor = round(
        (total_booked / total_authorized * 100), 1) if total_authorized > 0 else 0

    overview["totalPassengers"] = pax_totals.get("totalPassengers", 0)
    overview["totalSouls"] = pax_totals.get("totalSouls", 0)
    overview["avgPassengersPerFlight"] = round(pax_totals.get("avgPax", 0), 1)
    overview["maxPassengersOnFlight"] = pax_totals.get("maxPax", 0)
    overview["economyPassengers"] = economy_pax
    overview["businessPassengers"] = business_pax
    overview["avgLoadFactor"] = avg_load_factor

    # ── 3. Route Performance (passengers by origin) ───────────────────────
    route_pipeline = [
        {"$match": pm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin", "departureDate": "$departureDate"},
            "totalPassengers": {"$first": "$totalPassengers"},
            "origin": {"$first": "$origin"},
            "cabinSummary": {"$first": "$cabinSummary"},
        }},
        {"$group": {
            "_id": "$origin",
            "totalPassengers": {"$sum": "$totalPassengers"},
            "flightCount": {"$sum": 1},
            "avgPassengers": {"$avg": "$totalPassengers"},
        }},
        {"$sort": {"totalPassengers": -1}},
        {"$limit": 20},
    ]
    route_performance = list(db.passenger_list.aggregate(route_pipeline))
    for r in route_performance:
        r["origin"] = r.pop("_id")
        r["avgPassengers"] = round(r["avgPassengers"], 1)

    # ── 4. Booking Class Distribution ─────────────────────────────────────
    class_pipeline = [
        {"$match": pm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin", "departureDate": "$departureDate"},
            "passengers": {"$first": "$passengers"},
        }},
        {"$unwind": "$passengers"},
        *([
            {"$match": {"passengers.cabin": cabin}}
        ] if cabin else []),
        {"$group": {
            "_id": {"cabin": "$passengers.cabin", "bookingClass": "$passengers.bookingClass"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    class_dist = list(db.passenger_list.aggregate(
        class_pipeline, allowDiskUse=True))
    booking_class_distribution = []
    for c in class_dist:
        booking_class_distribution.append({
            "cabin": c["_id"]["cabin"],
            "bookingClass": c["_id"]["bookingClass"],
            "count": c["count"],
        })

    # ── 5. Nationality Distribution ───────────────────────────────────────
    nationality_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$unwind": "$reservations.passengers"},
        {"$match": {"reservations.passengers.nationality": {"$ne": ""},
                    "reservations.passengers.nationality": {"$ne": None}}},
        {"$group": {"_id": "$reservations.passengers.nationality", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    nationality_dist = list(db.reservations.aggregate(
        nationality_pipeline, allowDiskUse=True))
    nationality_distribution = [
        {"nationality": n["_id"], "count": n["count"]} for n in nationality_dist if n["_id"]]

    # ── 6. Loyalty Tier Distribution ──────────────────────────────────────
    loyalty_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$unwind": "$reservations.passengers"},
        {"$match": {"reservations.passengers.frequentFlyerNumber": {"$ne": ""}}},
        {"$group": {"_id": "$reservations.passengers.ffTierLevel", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    loyalty_result = list(db.reservations.aggregate(
        loyalty_pipeline, allowDiskUse=True))
    loyalty_distribution = []
    tier_labels = {"1": "Gold", "2": "Silver",
                   "4": "Bronze", "8": "Blue", "": "Unknown"}
    for item in loyalty_result:
        tier = str(item["_id"]) if item["_id"] else ""
        loyalty_distribution.append({
            "tier": tier,
            "tierName": tier_labels.get(tier, f"Tier {tier}"),
            "count": item["count"],
        })

    # ── 7. Special Meals Distribution ────────────────────────────────────
    meals_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$unwind": "$reservations.passengers"},
        {"$match": {"reservations.passengers.specialMeal": {"$ne": ""},
                    "reservations.passengers.specialMeal": {"$ne": None}}},
        {"$group": {"_id": "$reservations.passengers.specialMeal", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    meals_result = list(db.reservations.aggregate(
        meals_pipeline, allowDiskUse=True))
    meal_distribution = [{"mealCode": m["_id"], "count": m["count"]}
                         for m in meals_result if m["_id"]]

    # ── 8. Wheelchair / SSR Requests ──────────────────────────────────────
    wheelchair_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$unwind": "$reservations.passengers"},
        {"$match": {"reservations.passengers.wheelchairCode": {"$ne": ""},
                    "reservations.passengers.wheelchairCode": {"$ne": None}}},
        {"$group": {"_id": "$reservations.passengers.wheelchairCode", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    wheelchair_result = list(db.reservations.aggregate(
        wheelchair_pipeline, allowDiskUse=True))
    wheelchair_distribution = [
        {"code": w["_id"], "count": w["count"]} for w in wheelchair_result if w["_id"]]

    # ── 9. Change Velocity by Type ────────────────────────────────────────
    changes_pipeline = [
        {"$match": cm},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    changes_result = list(db.changes.aggregate(changes_pipeline))
    change_distribution = [{"changeType": c["_id"],
                            "count": c["count"]} for c in changes_result]
    total_changes = sum(c["count"] for c in change_distribution)

    # ── 10. Daily Trends ──────────────────────────────────────────────────
    daily_flights_pipeline = [
        {"$match": fm},
        {"$group": {"_id": "$departureDate", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    daily_flights = list(db.flights.aggregate(daily_flights_pipeline))

    daily_pax_pipeline = [
        {"$match": pm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin", "departureDate": "$departureDate"},
            "totalPassengers": {"$first": "$totalPassengers"},
            "departureDate": {"$first": "$departureDate"},
        }},
        {"$group": {
            "_id": "$departureDate",
            "totalPassengers": {"$sum": "$totalPassengers"},
            "flightCount": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    daily_pax = list(db.passenger_list.aggregate(daily_pax_pipeline))

    daily_changes_pipeline = [
        {"$match": cm},
        {"$group": {"_id": "$departureDate", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    daily_changes = list(db.changes.aggregate(daily_changes_pipeline))

    daily_trends = []
    pax_by_date = {d["_id"]: d for d in daily_pax}
    changes_by_date = {d["_id"]: d["count"] for d in daily_changes}
    for d in daily_flights:
        date_str = d["_id"]
        pax_data = pax_by_date.get(date_str, {})
        daily_trends.append({
            "date": date_str,
            "flights": d["count"],
            "passengers": pax_data.get("totalPassengers", 0),
            "changes": changes_by_date.get(date_str, 0),
        })

    # ── 11. Party Size Distribution ───────────────────────────────────────
    party_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$group": {"_id": "$reservations.numberInParty", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    party_result = list(db.reservations.aggregate(
        party_pipeline, allowDiskUse=True))
    party_size_distribution = [
        {"partySize": p["_id"], "count": p["count"]} for p in party_result]

    # ── 12. Point of Sale by Country ──────────────────────────────────────
    pos_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
        }},
        {"$unwind": "$reservations"},
        {"$group": {"_id": "$reservations.pointOfSale.isoCountry", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    pos_result = list(db.reservations.aggregate(
        pos_pipeline, allowDiskUse=True))
    point_of_sale_distribution = [
        {"country": p["_id"], "count": p["count"]} for p in pos_result if p["_id"]]

    # ── 13. Check-in & Boarding Rates ─────────────────────────────────────
    checkin_pipeline = [
        {"$match": pm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "origin": "$origin", "departureDate": "$departureDate"},
            "passengers": {"$first": "$passengers"},
            "totalPassengers": {"$first": "$totalPassengers"},
        }},
        {"$unwind": "$passengers"},
        *([
            {"$match": {"passengers.cabin": cabin}}
        ] if cabin else []),
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "checkedIn": {"$sum": {"$cond": [{"$eq": ["$passengers.isCheckedIn", True]}, 1, 0]}},
            "boarded": {"$sum": {"$cond": [{"$eq": ["$passengers.isBoarded", True]}, 1, 0]}},
            "revenue": {"$sum": {"$cond": [{"$eq": ["$passengers.isRevenue", True]}, 1, 0]}},
            "standby": {"$sum": {"$cond": [{"$eq": ["$passengers.isStandby", True]}, 1, 0]}},
        }},
    ]
    checkin_result = list(db.passenger_list.aggregate(
        checkin_pipeline, allowDiskUse=True))
    if checkin_result:
        ci = checkin_result[0]
        operational_rates = {
            "totalPassengers": ci.get("total", 0),
            "checkedIn": ci.get("checkedIn", 0),
            "boarded": ci.get("boarded", 0),
            "revenue": ci.get("revenue", 0),
            "standby": ci.get("standby", 0),
            "checkInRate": round(ci["checkedIn"] / ci["total"] * 100, 1) if ci.get("total") else 0,
            "boardingRate": round(ci["boarded"] / ci["total"] * 100, 1) if ci.get("total") else 0,
        }
    else:
        operational_rates = {
            "totalPassengers": 0, "checkedIn": 0, "boarded": 0,
            "revenue": 0, "standby": 0, "checkInRate": 0, "boardingRate": 0,
        }

    # ── 14. Upgrade Analytics ─────────────────────────────────────────────
    upgrade_match = {
        **cm, "changeType": {"$in": ["CABIN_CHANGE", "UPGRADE_CONFIRMED"]}}
    upgrade_pipeline = [
        {"$match": upgrade_match},
        {"$group": {
            "_id": "$changeType",
            "count": {"$sum": 1},
        }},
    ]
    upgrade_result = list(db.changes.aggregate(upgrade_pipeline))
    upgrade_analytics = {"cabinChanges": 0, "upgradesConfirmed": 0}
    for u in upgrade_result:
        if u["_id"] == "CABIN_CHANGE":
            upgrade_analytics["cabinChanges"] = u["count"]
        elif u["_id"] == "UPGRADE_CONFIRMED":
            upgrade_analytics["upgradesConfirmed"] = u["count"]

    # ── 15. Booking Lead Time ─────────────────────────────────────────────
    lead_time_pipeline = [
        {"$match": rm},
        {"$sort": {"fetchedAt": -1}},
        {"$group": {
            "_id": {"flightNumber": "$flightNumber", "departureAirport": "$departureAirport", "departureDate": "$departureDate"},
            "reservations": {"$first": "$reservations"},
            "departureDate": {"$first": "$departureDate"},
        }},
        {"$unwind": "$reservations"},
        {"$unwind": "$reservations.segments"},
        {"$match": {"reservations.segments.segmentBookedDate": {"$ne": None}}},
        {"$project": {
            "departureDate": 1,
            "bookedDate": "$reservations.segments.segmentBookedDate",
        }},
    ]
    lead_time_docs = list(db.reservations.aggregate(
        lead_time_pipeline, allowDiskUse=True))

    lead_time_buckets = {"sameDay": 0, "within7d": 0,
                         "within30d": 0, "within90d": 0, "over90d": 0}
    for doc in lead_time_docs:
        try:
            dep_str = doc.get("departureDate", "")
            booked_str = str(doc.get("bookedDate", ""))[:10]
            if dep_str and booked_str and len(booked_str) >= 10:
                dep = datetime.strptime(dep_str, "%Y-%m-%d")
                booked = datetime.strptime(booked_str[:10], "%Y-%m-%d")
                days = (dep - booked).days
                if days <= 0:
                    lead_time_buckets["sameDay"] += 1
                elif days <= 7:
                    lead_time_buckets["within7d"] += 1
                elif days <= 30:
                    lead_time_buckets["within30d"] += 1
                elif days <= 90:
                    lead_time_buckets["within90d"] += 1
                else:
                    lead_time_buckets["over90d"] += 1
        except (ValueError, TypeError):
            continue

    # ── Combine all analytics ─────────────────────────────────────────────
    return {
        "dateRange": {"from": date_from, "to": date_to},
        "filters": {"origin": origin, "cabin": cabin, "flightNumber": flight_number},
        "overview": overview,
        "routePerformance": route_performance,
        "bookingClassDistribution": booking_class_distribution,
        "nationalityDistribution": nationality_distribution,
        "loyaltyDistribution": loyalty_distribution,
        "mealDistribution": meal_distribution,
        "wheelchairDistribution": wheelchair_distribution,
        "changeDistribution": change_distribution,
        "totalChanges": total_changes,
        "dailyTrends": daily_trends,
        "partySizeDistribution": party_size_distribution,
        "pointOfSaleDistribution": point_of_sale_distribution,
        "operationalRates": operational_rates,
        "upgradeAnalytics": upgrade_analytics,
        "bookingLeadTime": lead_time_buckets,
    }


# ── Filter options endpoint ───────────────────────────────────────────────

def _get_filter_options(date_from: str, date_to: str):
    """Return available filter values for the given date range."""
    db = get_db()
    date_match = {"departureDate": {"$gte": date_from, "$lte": date_to}}

    origins_pipeline = [
        {"$match": date_match},
        {"$group": {"_id": "$origin"}},
        {"$sort": {"_id": 1}},
    ]
    origins = [d["_id"]
               for d in db.flights.aggregate(origins_pipeline) if d["_id"]]

    flight_numbers_pipeline = [
        {"$match": date_match},
        {"$group": {"_id": "$flightNumber"}},
        {"$sort": {"_id": 1}},
    ]
    flight_numbers = [d["_id"] for d in db.flights.aggregate(
        flight_numbers_pipeline) if d["_id"]]

    # Group flights by route — destination comes from passenger_list
    routes_pipeline = [
        {"$match": date_match},
        {"$lookup": {
            "from": "passenger_list",
            "let": {
                "fn": "$flightNumber",
                "orig": "$origin",
                "dep": "$departureDate",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$flightNumber", "$$fn"]},
                    {"$eq": ["$origin", "$$orig"]},
                    {"$eq": ["$departureDate", "$$dep"]},
                ]}}},
                {"$sort": {"fetchedAt": -1}},
                {"$limit": 1},
                {"$project": {"destination": 1, "_id": 0}},
            ],
            "as": "pax",
        }},
        {"$addFields": {
            "destination": {
                "$ifNull": [
                    {"$arrayElemAt": ["$pax.destination", 0]},
                    "",
                ]
            },
        }},
        {"$match": {"destination": {"$nin": [None, ""]}}},
        {"$group": {
            "_id": {
                "flightNumber": "$flightNumber",
                "origin": "$origin",
                "destination": "$destination",
            },
            "airline": {"$first": "$airline"},
            "flightCount": {"$sum": 1},
            "totalPassengers": {"$sum": {"$ifNull": ["$summary.totalPax", 0]}},
            "dates": {"$push": "$departureDate"},
        }},
        {"$project": {
            "_id": 0,
            "airline": 1,
            "flightNumber": "$_id.flightNumber",
            "origin": "$_id.origin",
            "destination": "$_id.destination",
            "flightCount": 1,
            "totalPassengers": 1,
            "dates": 1,
        }},
        {"$sort": {"flightNumber": 1, "origin": 1}},
    ]
    routes = list(db.flights.aggregate(routes_pipeline))

    return {
        "origins": origins,
        "flightNumbers": flight_numbers,
        "cabins": ["Y", "J"],
        "routes": routes,
    }


@router.get("/filters")
async def get_analytics_filters(
    date_from: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                           description="Start date (YYYY-MM-DD)"),
    date_to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                         description="End date (YYYY-MM-DD)"),
):
    """Return available filter options for analytics within a date range."""
    try:
        result = await run_in_threadpool(_get_filter_options, date_from, date_to)
        return result
    except Exception as exc:
        logger.error("analytics_filters_error", error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Failed to load filters: {str(exc)}")


@router.get("/network")
async def get_network_analytics(
    date_from: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                           description="Start date (YYYY-MM-DD)"),
    date_to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$",
                         description="End date (YYYY-MM-DD)"),
    origin: Optional[str] = Query(
        None, description="Filter by departure airport (IATA code)"),
    cabin: Optional[str] = Query(
        None, pattern=r"^[YJ]$", description="Filter by cabin (Y=Economy, J=Business)"),
    flight_number: Optional[str] = Query(
        None, description="Filter by flight number"),
    destination: Optional[str] = Query(
        None, description="Filter by arrival airport (IATA code)"),
):
    """Comprehensive network-wide analytics for all flights in a date range."""
    if date_from > date_to:
        raise HTTPException(
            status_code=400, detail="date_from must be before date_to")

    try:
        result = await run_in_threadpool(
            _run_network_analytics, date_from, date_to,
            origin=origin, cabin=cabin, flight_number=flight_number,
            destination=destination,
        )
        return result
    except Exception as exc:
        logger.error("analytics_error", error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Analytics computation failed: {str(exc)}")
