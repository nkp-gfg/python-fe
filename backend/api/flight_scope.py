"""Scope flight-level history reads to a single departure."""


def resolve_departure_date(db, flight_number: str, origin: str | None = None) -> str | None:
    """Return the departure date of the flight's most recently fetched data.

    Change and snapshot history accumulates across every departure of a flight
    number, so routes that accept an optional date use this to read one
    departure (the one the dashboard displays) instead of the whole history.
    Returns None when the flight has no stored status or passenger list.
    """
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    for collection in ("flight_status", "passenger_list"):
        doc = db[collection].find_one(
            query, {"departureDate": 1}, sort=[("fetchedAt", -1)])
        if doc and doc.get("departureDate"):
            return doc["departureDate"]
    return None


def count_change_types(db, match: dict) -> dict:
    """Count change documents per changeType server-side."""
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
    ]
    return {
        row["_id"]: row["count"]
        for row in db["changes"].aggregate(pipeline)
        if row.get("_id")
    }
