"""Process Audit API endpoint."""

import structlog
from fastapi import APIRouter, Query
from backend.api.database import get_db
from backend.api.validators import validate_date, validate_origin
from backend.api.audit_rules import run_audit

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["audit"])


@router.get("/{flight_number}/audit")
def get_flight_audit(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Run process audit rules against the latest flight data.

    Returns alerts for process violations, discrepancies, and compliance issues.
    Uses the latest passenger_list, reservations, and change history.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()

    # Build query
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    # Fetch latest passenger_list
    pax_doc = db["passenger_list"].find_one(query, sort=[("fetchedAt", -1)])

    # Fetch latest reservations
    res_query = {"flightNumber": flight_number}
    if date:
        res_query["departureDate"] = date
    res_doc = db["reservations"].find_one(res_query, sort=[("fetchedAt", -1)])

    # Fetch all changes for this flight
    changes_cursor = db["changes"].find(
        query, {"_id": 0}).sort("sequenceNumber", 1)
    # Flatten: each doc may have a "changes" array or be a single change record
    all_changes = []
    for doc in changes_cursor:
        if "changes" in doc and isinstance(doc["changes"], list):
            all_changes.extend(doc["changes"])
        else:
            all_changes.append(doc)

    # Run audit
    result = run_audit(pax_doc, res_doc, all_changes)

    # Add flight context
    result["flightNumber"] = flight_number
    result["origin"] = origin or ""
    result["departureDate"] = date or ""
    if pax_doc:
        result["fetchedAt"] = pax_doc.get("fetchedAt", "")

    return result
