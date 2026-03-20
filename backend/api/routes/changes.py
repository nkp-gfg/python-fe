"""Change tracking API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.validators import validate_date, validate_origin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flights", tags=["changes"])


@router.get("/{flight_number}/changes")
def get_changes(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    change_type: str = Query(
        None, description="Filter by change type (e.g. BOARDED, CHECKED_IN)"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get detected changes for a flight, newest first."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date
    if change_type:
        query["changeType"] = change_type

    cursor = db["changes"].find(query).sort("detectedAt", -1).limit(limit)
    results = []
    for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)
    if not results:
        raise HTTPException(status_code=404, detail="No changes found")
    return results


@router.get("/{flight_number}/changes/summary")
def get_changes_summary(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
):
    """Get a summary of change counts by type for a flight."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    match = {"flightNumber": flight_number}
    if origin:
        match["origin"] = origin
    if date:
        match["departureDate"] = date

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$changeType", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    results = list(db["changes"].aggregate(pipeline))
    if not results:
        raise HTTPException(status_code=404, detail="No changes found")
    return {
        "flightNumber": flight_number,
        "changeTypes": {r["_id"]: r["count"] for r in results},
        "totalChanges": sum(r["count"] for r in results),
    }


@router.get("/{flight_number}/snapshots")
def get_snapshots(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
    snapshot_type: str = Query(
        None, description="flight_status, passenger_list, or reservations"),
    limit: int = Query(20, ge=1, le=100),
):
    """List snapshot metadata for a flight (without full data)."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()
    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date
    if snapshot_type:
        query["snapshotType"] = snapshot_type

    cursor = db["snapshots"].find(
        query,
        projection={"data": 0},  # Exclude the large data field
    ).sort("sequenceNumber", -1).limit(limit)

    results = []
    for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)
    return results


@router.get("/{flight_number}/changes/passenger/{pnr}")
def get_passenger_changes(
    flight_number: str,
    pnr: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get all changes for a specific passenger by PNR."""
    validate_date(date)
    db = get_db()
    query = {"flightNumber": flight_number, "passenger.pnr": pnr}
    if date:
        query["departureDate"] = date

    cursor = db["changes"].find(query).sort("detectedAt", -1).limit(limit)
    results = []
    for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)
    if not results:
        raise HTTPException(
            status_code=404, detail="No changes found for this PNR")
    return results
