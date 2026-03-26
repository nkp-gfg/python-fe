"""Change tracking API endpoints."""

from copy import deepcopy
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Query
from backend.api.database import get_db
from backend.api.validators import validate_date, validate_origin

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/flights", tags=["changes"])


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _find_snapshot_as_of(db, flight_number, origin, date, snapshot_type, sequence):
    query = {
        "flightNumber": flight_number,
        "snapshotType": snapshot_type,
        "sequenceNumber": {"$lte": sequence},
    }
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date
    return db["snapshots"].find_one(query, sort=[("sequenceNumber", -1)])


def _find_latest_snapshot(db, flight_number, origin, date, snapshot_type):
    query = {
        "flightNumber": flight_number,
        "snapshotType": snapshot_type,
    }
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date
    return db["snapshots"].find_one(query, sort=[("sequenceNumber", -1)])


def _passenger_metrics(data):
    passengers = data.get("passengers", []) if isinstance(data, dict) else []
    checked_in = sum(1 for p in passengers if p.get("isCheckedIn"))
    boarded = sum(1 for p in passengers if p.get("isBoarded"))
    return {
        "totalPassengers": data.get("totalPassengers", 0),
        "totalSouls": data.get("totalSouls", 0),
        "adultCount": data.get("adultCount", 0),
        "childCount": data.get("childCount", 0),
        "infantCount": data.get("infantCount", 0),
        "checkedIn": checked_in,
        "boarded": boarded,
    }


def _reservations_metrics(data):
    reservations = data.get(
        "reservations", []) if isinstance(data, dict) else []
    return {
        "totalResults": data.get("totalResults", len(reservations)),
        "reservationCount": len(reservations),
    }


def _flight_status_metrics(data):
    if not isinstance(data, dict):
        return {}
    return {
        "status": data.get("status", ""),
        "gate": data.get("gate", ""),
        "terminal": data.get("terminal", ""),
    }


def _delta_dict(selected, latest):
    keys = sorted(set(selected.keys()) | set(latest.keys()))
    deltas = {}
    changed = False
    for key in keys:
        s_val = selected.get(key)
        l_val = latest.get(key)
        if isinstance(s_val, (int, float)) and isinstance(l_val, (int, float)):
            diff = l_val - s_val
        else:
            diff = None
        item_changed = s_val != l_val
        changed = changed or item_changed
        deltas[key] = {
            "selected": s_val,
            "latest": l_val,
            "diff": diff,
            "changed": item_changed,
        }
    return changed, deltas


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

    counts = {}
    for doc in db["changes"].find(match, {"changeType": 1}):
        ct = doc.get("changeType")
        if ct:
            counts[ct] = counts.get(ct, 0) + 1
    if not counts:
        raise HTTPException(status_code=404, detail="No changes found")
    return {
        "flightNumber": flight_number,
        "changeTypes": counts,
        "totalChanges": sum(counts.values()),
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
        if "fetchedAt" not in doc and "capturedAt" in doc:
            doc["fetchedAt"] = doc["capturedAt"]
        results.append(doc)
    return results


@router.get("/{flight_number}/snapshots/compare")
def compare_snapshot_against_latest(
    flight_number: str,
    snapshot_sequence: int = Query(..., ge=1),
    origin: str = Query(None),
    date: str = Query(None),
):
    """Compare selected sequence against latest snapshots for key data types."""
    validate_date(date)
    validate_origin(origin)
    db = get_db()

    snapshot_types = ["flight_status", "passenger_list", "reservations"]
    result = {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "snapshotSequence": snapshot_sequence,
        "types": {},
    }

    metric_extractors = {
        "flight_status": _flight_status_metrics,
        "passenger_list": _passenger_metrics,
        "reservations": _reservations_metrics,
    }

    for snapshot_type in snapshot_types:
        selected_snap = _find_snapshot_as_of(
            db, flight_number, origin, date, snapshot_type, snapshot_sequence)
        latest_snap = _find_latest_snapshot(
            db, flight_number, origin, date, snapshot_type)

        if not selected_snap or not latest_snap:
            result["types"][snapshot_type] = {
                "available": False,
                "reason": "missing_selected_or_latest_snapshot",
            }
            continue

        selected_metrics = metric_extractors[snapshot_type](
            selected_snap.get("data", {}))
        latest_metrics = metric_extractors[snapshot_type](
            latest_snap.get("data", {}))
        changed, deltas = _delta_dict(selected_metrics, latest_metrics)

        result["types"][snapshot_type] = {
            "available": True,
            "selectedSequence": selected_snap.get("sequenceNumber"),
            "latestSequence": latest_snap.get("sequenceNumber"),
            "changed": changed,
            "deltas": deltas,
        }

    return result


@router.post("/{flight_number}/snapshots/{snapshot_sequence}/restore")
def restore_snapshot_version(
    flight_number: str,
    snapshot_sequence: int,
    origin: str = Query(None),
    date: str = Query(None),
):
    """Restore selected snapshot version by writing it as newest legacy documents."""
    validate_date(date)
    validate_origin(origin)
    if snapshot_sequence < 1:
        raise HTTPException(
            status_code=400, detail="snapshot_sequence must be >= 1")

    db = get_db()
    now_iso = _now_iso()

    restore_plan = [
        ("flight_status", "flight_status"),
        ("passenger_list", "passenger_list"),
        ("reservations", "reservations"),
    ]

    restored = []
    for snapshot_type, target_collection in restore_plan:
        snap = _find_snapshot_as_of(
            db, flight_number, origin, date, snapshot_type, snapshot_sequence)
        if not snap:
            continue

        data = deepcopy(snap.get("data", {}))
        if not isinstance(data, dict):
            continue

        data.pop("_id", None)
        data.pop("_raw", None)
        data["fetchedAt"] = now_iso

        db[target_collection].insert_one(data)
        restored.append({
            "snapshotType": snapshot_type,
            "targetCollection": target_collection,
            "sourceSequence": snap.get("sequenceNumber"),
        })

    if not restored:
        raise HTTPException(
            status_code=404, detail="No snapshots found to restore")

    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "requestedSequence": snapshot_sequence,
        "restoredAt": now_iso,
        "restored": restored,
    }


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


@router.get("/{flight_number}/passengers/{pnr}/timeline")
def get_passenger_timeline(
    flight_number: str,
    pnr: str,
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
    origin: str = Query(None, description="Departure airport code"),
):
    """
    Get a formatted timeline of all events for a passenger.
    
    Combines change records into a chronological timeline with event 
    categories and human-readable descriptions.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()

    # Get all changes for this passenger
    query = {"flightNumber": flight_number, "passenger.pnr": pnr}
    if date:
        query["departureDate"] = date
    if origin:
        query["origin"] = origin

    cursor = db["changes"].find(query).sort("detectedAt", 1)  # Chronological
    changes = list(cursor)

    # Also get initial appearance from PASSENGER_ADDED
    added_record = next(
        (c for c in changes if c.get("changeType") == "PASSENGER_ADDED"),
        None
    )

    # Build timeline events
    timeline = []

    # Extract original booking info from first snapshot if available
    original_cabin = None
    original_class = None
    if added_record:
        metadata = added_record.get("metadata", {})
        original_cabin = metadata.get("originalCabin")
        original_class = metadata.get("originalClass")

    for change in changes:
        change.pop("_id", None)
        change_type = change.get("changeType", "")
        detected_at = change.get("detectedAt", "")
        metadata = change.get("metadata", {})

        event = {
            "timestamp": detected_at,
            "changeType": change_type,
            "category": _categorize_event(change_type),
            "description": _describe_event(change),
            "details": {
                "field": change.get("field"),
                "oldValue": change.get("oldValue"),
                "newValue": change.get("newValue"),
            },
        }

        # Add upgrade-specific info
        if change_type in ("CABIN_CHANGE", "CLASS_CHANGE"):
            event["upgradeInfo"] = {
                "direction": metadata.get("direction"),
                "upgradeType": metadata.get("upgradeType"),
                "upgradeCode": metadata.get("upgradeCode"),
            }

        # Add original booking info to first event
        if change_type == "PASSENGER_ADDED":
            event["originalBooking"] = {
                "cabin": original_cabin,
                "bookingClass": original_class,
            }

        timeline.append(event)

    if not timeline:
        raise HTTPException(
            status_code=404, detail="No timeline events found for this PNR")

    # Get current passenger state from latest snapshot
    current_state = _get_current_passenger_state(
        db, flight_number, pnr, date, origin)

    return {
        "flightNumber": flight_number,
        "pnr": pnr,
        "departureDate": date,
        "origin": origin,
        "originalBooking": {
            "cabin": original_cabin,
            "bookingClass": original_class,
        },
        "currentState": current_state,
        "events": timeline,
        "eventCount": len(timeline),
    }


def _categorize_event(change_type: str) -> str:
    """Categorize change type for UI grouping."""
    categories = {
        "PASSENGER_ADDED": "booking",
        "PASSENGER_REMOVED": "booking",
        "CHECKED_IN": "checkin",
        "BOARDED": "boarding",
        "CABIN_CHANGE": "upgrade",
        "CLASS_CHANGE": "upgrade",
        "UPGRADE_CONFIRMED": "upgrade",
        "SEAT_CHANGE": "seat",
        "BAG_COUNT_CHANGE": "baggage",
        "PAX_TYPE_CHANGE": "booking",
        "PRIORITY_CHANGE": "standby",
        "LOYALTY_STATUS_ADDED": "loyalty",
        "DOCUMENT_ADDED": "document",
        "STATUS_CHANGE": "flight",
        "GATE_CHANGE": "flight",
        "TERMINAL_CHANGE": "flight",
        "BOARDING_TIME_CHANGE": "flight",
        "JUMPSEAT_CHANGE": "flight",
        "COUNT_CHANGE": "capacity",
        "RESERVATION_ADDED": "reservation",
        "RESERVATION_REMOVED": "reservation",
        "RESERVATION_PARTY_CHANGE": "reservation",
    }
    return categories.get(change_type, "other")


def _describe_event(change: dict) -> str:
    """Generate human-readable description of a change."""
    change_type = change.get("changeType", "")
    old_val = change.get("oldValue")
    new_val = change.get("newValue")
    metadata = change.get("metadata", {})

    descriptions = {
        "PASSENGER_ADDED": "Passenger added to manifest",
        "PASSENGER_REMOVED": "Passenger removed from manifest",
        "CHECKED_IN": "Passenger checked in",
        "BOARDED": "Passenger boarded aircraft",
    }

    if change_type in descriptions:
        return descriptions[change_type]

    if change_type == "CABIN_CHANGE":
        direction = metadata.get("direction", "changed")
        upgrade_type = metadata.get("upgradeType", "")
        type_str = f" ({upgrade_type})" if upgrade_type else ""
        return f"Cabin {direction.lower()}{type_str}: {old_val} → {new_val}"

    if change_type == "CLASS_CHANGE":
        return f"Booking class changed: {old_val} → {new_val}"

    if change_type == "SEAT_CHANGE":
        return f"Seat changed: {old_val or 'unassigned'} → {new_val or 'unassigned'}"

    if change_type == "BAG_COUNT_CHANGE":
        return f"Bag count changed: {old_val} → {new_val}"

    if change_type == "STATUS_CHANGE":
        return f"Flight status: {old_val} → {new_val}"

    if change_type == "GATE_CHANGE":
        return f"Gate changed: {old_val or 'unassigned'} → {new_val}"

    if change_type == "TERMINAL_CHANGE":
        return f"Terminal changed: {old_val} → {new_val}"

    if change_type == "BOARDING_TIME_CHANGE":
        return f"Boarding time updated: {new_val}"

    if change_type == "PRIORITY_CHANGE":
        event = metadata.get("event", "")
        if event == "STANDBY_CLEARED":
            return "Cleared from standby/upgrade queue"
        elif event == "ADDED_TO_QUEUE":
            return f"Added to queue: {new_val}"
        return f"Queue position: {old_val} → {new_val}"

    if change_type == "UPGRADE_CONFIRMED":
        return f"Upgrade confirmed to class {new_val}"

    if change_type == "LOYALTY_STATUS_ADDED":
        return f"Loyalty status added: {new_val}"

    if change_type == "DOCUMENT_ADDED":
        return f"Document verified: {new_val}"

    if change_type == "JUMPSEAT_CHANGE":
        return f"Jump seat status changed"

    if change_type == "COUNT_CHANGE":
        field = change.get("field", "")
        return f"{field}: {old_val} → {new_val}"

    if change_type == "RESERVATION_ADDED":
        return "Reservation added"

    if change_type == "RESERVATION_REMOVED":
        return "Reservation cancelled"

    if change_type == "RESERVATION_PARTY_CHANGE":
        return f"Party size changed: {old_val} → {new_val}"

    return f"{change_type}: {old_val} → {new_val}"


def _get_current_passenger_state(db, flight_number: str, pnr: str,
                                 date: str = None, origin: str = None) -> dict:
    """Get current passenger state from latest snapshot."""
    query = {
        "flightNumber": flight_number,
        "snapshotType": "passenger_list",
    }
    if date:
        query["departureDate"] = date
    if origin:
        query["origin"] = origin

    # Get latest snapshot
    snapshot = db["snapshots"].find_one(
        query,
        sort=[("sequenceNumber", -1)]
    )

    if not snapshot:
        return None

    # Find passenger in snapshot
    passengers = snapshot.get("data", {}).get("passengers", [])
    for pax in passengers:
        if pax.get("pnr") == pnr:
            return {
                "cabin": pax.get("cabin"),
                "bookingClass": pax.get("bookingClass"),
                "seat": pax.get("seat"),
                "isCheckedIn": pax.get("isCheckedIn", False),
                "isBoarded": pax.get("isBoarded", False),
                "bagCount": pax.get("bagCount", 0),
            }

    return None


# ── Flight Timeline API ───────────────────────────────────────────────────

@router.get("/{flight_number}/timeline")
def get_flight_timeline(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Get a comprehensive timeline of all flight events.
    
    Combines flight status changes, passenger events, and operational updates
    into a chronological feed showing the flight's lifecycle.
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

    # Get all changes for this flight
    changes = list(db["changes"].find(query).sort("detectedAt", 1))

    if not changes:
        raise HTTPException(status_code=404, detail="No timeline data found")

    # Get snapshots for context
    snapshots = list(db["snapshots"].find(
        query,
        projection={"data": 0}
    ).sort("fetchedAt", 1))

    # Build timeline events
    events = []

    # Add snapshot fetch events
    for snap in snapshots:
        events.append({
            "timestamp": snap.get("fetchedAt"),
            "category": "snapshot",
            "eventType": "SNAPSHOT_TAKEN",
            "description": f"Data snapshot #{snap.get('sequenceNumber')} ({snap.get('snapshotType')})",
            "details": {
                "snapshotType": snap.get("snapshotType"),
                "sequenceNumber": snap.get("sequenceNumber"),
            },
        })

    # Add change events
    for change in changes:
        change.pop("_id", None)
        change_type = change.get("changeType", "")
        passenger = change.get("passenger")

        event = {
            "timestamp": change.get("detectedAt"),
            "category": _categorize_event(change_type),
            "eventType": change_type,
            "description": _describe_event(change),
            "details": {
                "field": change.get("field"),
                "oldValue": change.get("oldValue"),
                "newValue": change.get("newValue"),
            },
        }

        if passenger:
            event["passenger"] = passenger

        if change.get("metadata"):
            event["metadata"] = change["metadata"]

        events.append(event)

    # Sort all events by timestamp
    events.sort(key=lambda e: e["timestamp"] or "")

    # Calculate summary stats
    stats = _calculate_flight_stats(changes)

    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "events": events,
        "eventCount": len(events),
        "stats": stats,
    }


def _calculate_flight_stats(changes: list) -> dict:
    """Calculate summary statistics from changes."""
    stats = {
        "totalChanges": len(changes),
        "checkedIn": 0,
        "boarded": 0,
        "upgrades": 0,
        "seatChanges": 0,
        "statusChanges": 0,
    }

    for c in changes:
        ct = c.get("changeType", "")
        if ct == "CHECKED_IN":
            stats["checkedIn"] += 1
        elif ct == "BOARDED":
            stats["boarded"] += 1
        elif ct in ("CABIN_CHANGE", "CLASS_CHANGE", "UPGRADE_CONFIRMED"):
            stats["upgrades"] += 1
        elif ct == "SEAT_CHANGE":
            stats["seatChanges"] += 1
        elif ct == "STATUS_CHANGE":
            stats["statusChanges"] += 1

    return stats


# ── Activity Feed API (Global) ────────────────────────────────────────────

activity_router = APIRouter(prefix="/activity", tags=["activity"])


@activity_router.get("/feed")
def get_activity_feed(
    date: str = Query(None, description="Filter by date YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    categories: str = Query(
        None,
        description="Comma-separated categories: boarding,checkin,upgrade,flight"
    ),
):
    """
    Get a real-time activity feed across all flights.
    
    Shows recent events for monitoring dashboards.
    """
    validate_date(date)
    db = get_db()

    query = {}
    if date:
        query["departureDate"] = date

    if categories:
        category_list = [c.strip() for c in categories.split(",")]
        # Map categories to change types
        category_map = {
            "boarding": ["BOARDED"],
            "checkin": ["CHECKED_IN"],
            "upgrade": ["CABIN_CHANGE", "CLASS_CHANGE", "UPGRADE_CONFIRMED"],
            "flight": ["STATUS_CHANGE", "GATE_CHANGE", "TERMINAL_CHANGE"],
            "standby": ["PRIORITY_CHANGE"],
            "seat": ["SEAT_CHANGE"],
            "booking": ["PASSENGER_ADDED", "PASSENGER_REMOVED"],
        }
        selected_types = []
        for cat in category_list:
            selected_types.extend(category_map.get(cat, []))
        if selected_types:
            query["changeType"] = {"$in": selected_types}

    cursor = db["changes"].find(query).sort("detectedAt", -1).limit(limit)

    events = []
    for change in cursor:
        change.pop("_id", None)
        events.append({
            "timestamp": change.get("detectedAt"),
            "flightNumber": change.get("flightNumber"),
            "origin": change.get("origin"),
            "departureDate": change.get("departureDate"),
            "category": _categorize_event(change.get("changeType", "")),
            "eventType": change.get("changeType"),
            "description": _describe_event(change),
            "passenger": change.get("passenger"),
        })

    return {
        "events": events,
        "count": len(events),
    }


# ── Boarding Progress API ─────────────────────────────────────────────────

@router.get("/{flight_number}/boarding-progress")
def get_boarding_progress(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code"),
    date: str = Query(None, description="Departure date YYYY-MM-DD"),
):
    """
    Get check-in and boarding progress over time.
    
    Returns time-series data for charting the boarding curve.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()

    query = {"flightNumber": flight_number}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    # Get check-in and boarding events
    checkin_query = {**query, "changeType": "CHECKED_IN"}
    boarding_query = {**query, "changeType": "BOARDED"}

    checkins = list(db["changes"].find(checkin_query).sort("detectedAt", 1))
    boardings = list(db["changes"].find(boarding_query).sort("detectedAt", 1))

    # Get total passenger count from latest snapshot
    snapshot = db["snapshots"].find_one(
        {**query, "snapshotType": "passenger_list"},
        sort=[("sequenceNumber", -1)]
    )

    total_passengers = 0
    if snapshot:
        total_passengers = snapshot.get("data", {}).get("totalPassengers", 0)

    # Build time series
    checkin_series = []
    cumulative = 0
    for c in checkins:
        cumulative += 1
        checkin_series.append({
            "timestamp": c.get("detectedAt"),
            "count": cumulative,
            "passenger": c.get("passenger"),
        })

    boarding_series = []
    cumulative = 0
    for b in boardings:
        cumulative += 1
        boarding_series.append({
            "timestamp": b.get("detectedAt"),
            "count": cumulative,
            "passenger": b.get("passenger"),
        })

    # Get flight status transitions
    status_changes = list(db["changes"].find(
        {**query, "changeType": "STATUS_CHANGE"}
    ).sort("detectedAt", 1))

    milestones = []
    for sc in status_changes:
        milestones.append({
            "timestamp": sc.get("detectedAt"),
            "status": sc.get("newValue"),
            "previousStatus": sc.get("oldValue"),
        })

    return {
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": date,
        "totalPassengers": total_passengers,
        "checkinProgress": {
            "current": len(checkins),
            "total": total_passengers,
            "percentage": round(len(checkins) / total_passengers * 100, 1) if total_passengers else 0,
            "series": checkin_series,
        },
        "boardingProgress": {
            "current": len(boardings),
            "total": total_passengers,
            "percentage": round(len(boardings) / total_passengers * 100, 1) if total_passengers else 0,
            "series": boarding_series,
        },
        "milestones": milestones,
    }


# ── Passenger History Badge API ───────────────────────────────────────────

@router.get("/{flight_number}/passengers/history-badges")
def get_passengers_history_badges(
    flight_number: str,
    origin: str = Query(None),
    date: str = Query(None),
):
    """
    Get a summary of change counts per passenger for badge display.
    
    Returns PNR -> change count map for showing indicators in passenger list.
    """
    validate_date(date)
    validate_origin(origin)
    db = get_db()

    query = {"flightNumber": flight_number, "passenger.pnr": {"$exists": True}}
    if origin:
        query["origin"] = origin
    if date:
        query["departureDate"] = date

    UPGRADE_TYPES = {"CABIN_CHANGE", "CLASS_CHANGE", "UPGRADE_CONFIRMED"}
    badges = {}
    for doc in db["changes"].find(query, {"passenger.pnr": 1, "changeType": 1, "detectedAt": 1}):
        pnr = (doc.get("passenger") or {}).get("pnr")
        if not pnr:
            continue
        if pnr not in badges:
            badges[pnr] = {"changeCount": 0, "hasUpgrade": False, "lastChange": None}
        badges[pnr]["changeCount"] += 1
        if doc.get("changeType") in UPGRADE_TYPES:
            badges[pnr]["hasUpgrade"] = True
        detected = doc.get("detectedAt")
        if detected and (badges[pnr]["lastChange"] is None or detected > badges[pnr]["lastChange"]):
            badges[pnr]["lastChange"] = detected

    return badges
