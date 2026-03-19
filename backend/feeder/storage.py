"""
MongoDB storage for FalconEye — append-only data preservation architecture.

Collections:
  sabre_requests  — Raw XML request/response audit log (immutable)
  snapshots       — Normalized per-flight snapshots with checksums (immutable)
  changes         — Diffs between consecutive snapshots (computed)
  flights         — Current state per flight (materialized view)
"""

import os
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

DB_NAME = "falconeye"

_client = None
_db = None


def get_db():
    """Return the falconeye database, creating the connection on first call."""
    global _client, _db
    if _db is None:
        uri = os.environ["MONGODB_URI"]
        _client = MongoClient(uri)
        _db = _client[DB_NAME]
        logger.info("Connected to MongoDB database '%s'", DB_NAME)
    return _db


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id():
    return str(uuid.uuid4())


def _compute_checksum(data):
    """Compute SHA-256 of normalized JSON for quick snapshot comparison."""
    stable = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode()).hexdigest()


def ensure_indexes():
    """Create indexes for all collections."""
    db = get_db()

    # sabre_requests
    db["sabre_requests"].create_index(
        [("requestId", 1)], name="req_id", unique=True)
    db["sabre_requests"].create_index(
        [("flight.flightNumber", 1), ("flight.departureDate", 1),
         ("apiType", 1), ("requestedAt", -1)],
        name="req_flight_lookup")

    # snapshots
    db["snapshots"].create_index(
        [("snapshotId", 1)], name="snap_id", unique=True)
    db["snapshots"].create_index(
        [("flightNumber", 1), ("origin", 1), ("departureDate", 1),
         ("snapshotType", 1), ("sequenceNumber", -1)],
        name="snap_flight_lookup")
    db["snapshots"].create_index(
        [("requestId", 1)], name="snap_request")

    # changes
    db["changes"].create_index(
        [("flightNumber", 1), ("departureDate", 1),
         ("detectedAt", -1)],
        name="chg_flight_lookup")
    db["changes"].create_index(
        [("afterSnapshotId", 1)], name="chg_snapshot")
    db["changes"].create_index(
        [("passenger.pnr", 1)], name="chg_pnr")

    # flights (current state)
    db["flights"].create_index(
        [("airline", 1), ("flightNumber", 1),
         ("origin", 1), ("departureDate", 1)],
        name="flight_key", unique=True)

    # Legacy collections (keep indexes for backward compatibility)
    db["flight_status"].create_index(
        [("airline", 1), ("flightNumber", 1),
         ("origin", 1), ("departureDate", 1)],
        name="flight_lookup")
    db["passenger_list"].create_index(
        [("airline", 1), ("flightNumber", 1),
         ("origin", 1), ("departureDate", 1)],
        name="pax_lookup")
    db["reservations"].create_index(
        [("airline", 1), ("flightNumber", 1),
         ("departureAirport", 1), ("departureDate", 1)],
        name="res_lookup")

    logger.info("MongoDB indexes ensured.")


# ── Layer 1: Raw Archive ──────────────────────────────────────────────────

def store_raw_request(api_type, flight_info, request_xml, response_xml,
                      response_json, http_status, duration_ms,
                      session_token=None, conversation_id=None):
    """
    Store the complete raw request/response in sabre_requests (append-only).
    Returns the requestId (UUID string).
    """
    request_id = _new_id()
    doc = {
        "requestId": request_id,
        "requestedAt": _now_iso(),
        "apiType": api_type,
        "flight": flight_info,
        "requestXml": request_xml,
        "responseXml": response_xml,
        "responseJson": response_json,
        "httpStatus": http_status,
        "durationMs": duration_ms,
        "metadata": {
            "sessionToken": session_token,
            "conversationId": conversation_id,
        },
    }
    get_db()["sabre_requests"].insert_one(doc)
    logger.info("Stored raw request %s (%s) for %s%s",
                request_id[:8], api_type,
                flight_info.get("airline", ""), flight_info.get("flightNumber", ""))
    return request_id


# ── Layer 2: Parsed Snapshots ─────────────────────────────────────────────

def _next_sequence(flight_number, origin, departure_date, snapshot_type):
    """Get the next sequence number for this flight+type combination."""
    db = get_db()
    last = db["snapshots"].find_one(
        {
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "snapshotType": snapshot_type,
        },
        sort=[("sequenceNumber", DESCENDING)],
        projection={"sequenceNumber": 1},
    )
    return (last["sequenceNumber"] + 1) if last else 1


def store_snapshot(request_id, airline, flight_number, origin, departure_date,
                   snapshot_type, data):
    """
    Store a normalized snapshot.
    Returns (snapshotId, checksum, is_duplicate).
    is_duplicate is True if the checksum matches the previous snapshot.
    """
    snapshot_id = _new_id()
    checksum = _compute_checksum(data)
    seq = _next_sequence(flight_number, origin, departure_date, snapshot_type)

    doc = {
        "snapshotId": snapshot_id,
        "requestId": request_id,
        "airline": airline,
        "flightNumber": flight_number,
        "origin": origin,
        "departureDate": departure_date,
        "snapshotType": snapshot_type,
        "capturedAt": _now_iso(),
        "sequenceNumber": seq,
        "checksum": checksum,
        "data": data,
    }
    get_db()["snapshots"].insert_one(doc)

    # Check if previous snapshot had the same checksum
    is_duplicate = False
    if seq > 1:
        prev = get_db()["snapshots"].find_one(
            {
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": departure_date,
                "snapshotType": snapshot_type,
                "sequenceNumber": seq - 1,
            },
            projection={"checksum": 1},
        )
        if prev and prev.get("checksum") == checksum:
            is_duplicate = True

    logger.info("Stored snapshot %s (%s) seq=%d checksum=%s%s",
                snapshot_id[:8], snapshot_type, seq, checksum[:12],
                " [DUPLICATE]" if is_duplicate else "")
    return snapshot_id, checksum, is_duplicate


def get_previous_snapshot(flight_number, origin, departure_date, snapshot_type):
    """Get the snapshot before the most recent one, for diffing."""
    db = get_db()
    # Get last two snapshots
    cursor = db["snapshots"].find(
        {
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
            "snapshotType": snapshot_type,
        },
        sort=[("sequenceNumber", DESCENDING)],
    ).limit(2)
    results = list(cursor)
    # Return the second one (previous to the one we just inserted)
    return results[1] if len(results) >= 2 else None


# ── Layer 3: Changes ──────────────────────────────────────────────────────

def store_changes(changes):
    """
    Store a list of detected changes.
    Each change is a dict with: flightNumber, origin, departureDate, changeType,
    beforeSnapshotId, afterSnapshotId, detectedAt, passenger, field, oldValue, newValue.
    """
    if not changes:
        return 0
    db = get_db()
    result = db["changes"].insert_many(changes)
    logger.info("Stored %d changes", len(result.inserted_ids))
    return len(result.inserted_ids)


# ── Layer 4: Current State ────────────────────────────────────────────────

def update_flight_state(airline, flight_number, origin, departure_date,
                        snapshot_id, snapshot_type, summary=None):
    """
    Update the flights collection with the latest state.
    Uses upsert to create or update.
    """
    db = get_db()
    update_fields = {
        "lastUpdatedAt": _now_iso(),
        f"latest{snapshot_type.replace('_', ' ').title().replace(' ', '')}SnapshotId": snapshot_id,
    }
    if summary:
        update_fields["summary"] = summary

    db["flights"].update_one(
        {
            "airline": airline,
            "flightNumber": flight_number,
            "origin": origin,
            "departureDate": departure_date,
        },
        {
            "$set": update_fields,
            "$inc": {"snapshotCount": 1},
            "$setOnInsert": {
                "airline": airline,
                "flightNumber": flight_number,
                "origin": origin,
                "departureDate": departure_date,
                "createdAt": _now_iso(),
            },
        },
        upsert=True,
    )
    logger.info("Updated flight state for %s%s %s %s",
                airline, flight_number, origin, departure_date)


# ── Legacy Storage (backward compat) ─────────────────────────────────────

def store_flight_status(doc):
    """Insert a flight_status document. Returns the inserted _id."""
    result = get_db()["flight_status"].insert_one(doc)
    return result.inserted_id


def store_passenger_list(doc):
    """Insert a passenger_list document. Returns the inserted _id."""
    result = get_db()["passenger_list"].insert_one(doc)
    return result.inserted_id


def store_reservations(doc):
    """Insert a reservations document. Returns the inserted _id."""
    result = get_db()["reservations"].insert_one(doc)
    return result.inserted_id


def close():
    """Close the MongoDB connection."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")
