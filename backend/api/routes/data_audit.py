"""Data Audit API — cross-database field-level comparison (PostgreSQL OTP ↔ MongoDB Sabre).

Provides:
  GET /data-audit/{flight_number}/compare  — side-by-side field comparison
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.api.database import get_db
from backend.api.postgres import query_all
from backend.api.validators import validate_date, validate_flight_number

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data-audit", tags=["data-audit"])


# ── Response models ───────────────────────────────────────────────────────

class ComparisonRow(BaseModel):
    field: str
    pgValue: str | None = None
    mongoValue: str | None = None
    match: str  # "match" | "mismatch" | "pg_only" | "mongo_only"
    remark: str | None = None


class ComparisonResult(BaseModel):
    flightNumber: str
    date: str | None = None
    origin: str | None = None
    sequenceNumber: int | None = None
    pgFound: bool
    mongoFound: bool
    rows: list[ComparisonRow]
    summary: dict


# ── Helpers ───────────────────────────────────────────────────────────────

_PG_QUERY = """
SELECT
    flight_sequence_number,
    flight_number,
    scheduled_origin,
    scheduled_destination,
    actual_origin,
    actual_destination,
    flight_date,
    scheduled_departure_utc,
    estimated_block_off_utc,
    scheduled_arrival_utc,
    estimated_block_on_utc,
    actual_block_off_utc,
    actual_block_on_utc,
    actual_takeoff_utc,
    actual_touchdown_utc,
    scheduled_departure_local,
    scheduled_arrival_local,
    flight_status,
    aircraft_type,
    aircraft_registration,
    service_type_code,
    cancel_reason_code,
    passenger_counts,
    delay_details,
    source
FROM otp.flight_xml_current
WHERE flight_number = %s
"""


def _ts(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


def _norm(val) -> str | None:
    """Normalize a value to a comparable string."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return _ts(val)
    if isinstance(val, (dict, list)):
        return json.dumps(val, default=str, sort_keys=True)
    return str(val).strip()


def _sum_pax(counts) -> int | None:
    if not counts:
        return None
    items = counts if isinstance(counts, list) else json.loads(counts)
    return sum(int(p.get("Count", 0)) for p in items)


def _fetch_pg(flight_number: str, flight_date: str | None,
              origin: str | None, seq: int | None) -> dict | None:
    """Fetch one row from PG matching the filters."""
    sql = _PG_QUERY
    params: list = [flight_number]

    if seq:
        sql += " AND flight_sequence_number = %s"
        params.append(seq)
    if flight_date:
        sql += " AND flight_date = %s"
        params.append(flight_date)
    if origin:
        sql += " AND scheduled_origin = %s"
        params.append(origin)

    sql += " ORDER BY flight_date DESC LIMIT 1"

    try:
        rows = query_all(sql, tuple(params))
        return rows[0] if rows else None
    except Exception:
        logger.exception("PostgreSQL query failed for %s", flight_number)
        return None


def _fetch_mongo(flight_number: str, flight_date: str | None,
                 origin: str | None) -> dict | None:
    """Fetch flight_status doc from MongoDB."""
    try:
        db = get_db()
        query: dict = {"flightNumber": flight_number}
        if origin:
            query["origin"] = origin
        if flight_date:
            query["departureDate"] = flight_date

        doc = db["flight_status"].find_one(
            query, sort=[("_ingestTimestamp", -1)])
        if doc:
            doc.pop("_id", None)
        return doc
    except Exception:
        logger.exception("MongoDB query failed for %s", flight_number)
        return None


# Field mapping: (label, pg_key_extractor, mongo_key_extractor)
_FIELD_MAP = [
    ("Flight Number", lambda pg: str(pg.get("flight_number", "")).strip(),
     lambda m: m.get("flightNumber")),
    ("Sequence Number", lambda pg: str(pg.get("flight_sequence_number", "")),
     lambda m: str(m.get("flightSequenceNumber", "")) if m.get("flightSequenceNumber") else None),
    ("Origin", lambda pg: pg.get("scheduled_origin"),
     lambda m: m.get("origin")),
    ("Destination", lambda pg: pg.get("scheduled_destination"),
     lambda m: m.get("destination")),
    ("Flight Date", lambda pg: _ts(pg.get("flight_date")),
     lambda m: m.get("departureDate")),
    ("Flight Status", lambda pg: pg.get("flight_status"),
     lambda m: m.get("flightStatus")),
    ("Aircraft Type", lambda pg: pg.get("aircraft_type"),
     lambda m: m.get("aircraftType")),
    ("Aircraft Registration", lambda pg: pg.get("aircraft_registration"),
     lambda m: m.get("aircraftRegistration")),
    ("Scheduled Departure (UTC)", lambda pg: _ts(pg.get("scheduled_departure_utc")),
     lambda m: m.get("scheduledDepartureUtc") or m.get("departureTimeUtc")),
    ("Scheduled Arrival (UTC)", lambda pg: _ts(pg.get("scheduled_arrival_utc")),
     lambda m: m.get("scheduledArrivalUtc") or m.get("arrivalTimeUtc")),
    ("Actual Block Off (UTC)", lambda pg: _ts(pg.get("actual_block_off_utc")),
     lambda m: m.get("actualBlockOffUtc") or m.get("actualDepartureUtc")),
    ("Actual Block On (UTC)", lambda pg: _ts(pg.get("actual_block_on_utc")),
     lambda m: m.get("actualBlockOnUtc") or m.get("actualArrivalUtc")),
    ("Estimated Block Off (UTC)", lambda pg: _ts(pg.get("estimated_block_off_utc")),
     lambda m: m.get("estimatedBlockOffUtc") or m.get("estimatedDepartureUtc")),
    ("Estimated Block On (UTC)", lambda pg: _ts(pg.get("estimated_block_on_utc")),
     lambda m: m.get("estimatedBlockOnUtc") or m.get("estimatedArrivalUtc")),
    ("Total Passengers", lambda pg: str(_sum_pax(pg.get("passenger_counts")) or ""),
     lambda m: str(m.get("totalPassengers", "")) if m.get("totalPassengers") is not None else None),
    ("Actual Origin", lambda pg: pg.get("actual_origin"),
     lambda m: m.get("actualOrigin")),
    ("Actual Destination", lambda pg: pg.get("actual_destination"),
     lambda m: m.get("actualDestination")),
    ("Service Type", lambda pg: pg.get("service_type_code"),
     lambda m: m.get("serviceType")),
    ("Departure Gate", lambda pg: None,
     lambda m: m.get("departureGate")),
]


def _compare(pg_row: dict | None, mongo_doc: dict | None) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    for label, pg_fn, mongo_fn in _FIELD_MAP:
        pg_val = _norm(pg_fn(pg_row)) if pg_row else None
        m_val = _norm(mongo_fn(mongo_doc)) if mongo_doc else None

        if pg_val and m_val:
            match = "match" if pg_val == m_val else "mismatch"
        elif pg_val and not m_val:
            match = "pg_only"
        elif m_val and not pg_val:
            match = "mongo_only"
        else:
            match = "match"  # both null

        remark = None
        if match == "mismatch":
            remark = "Values differ between PostgreSQL and MongoDB"

        rows.append(ComparisonRow(
            field=label,
            pgValue=pg_val,
            mongoValue=m_val,
            match=match,
            remark=remark,
        ))
    return rows


# ── Endpoint ──────────────────────────────────────────────────────────────

@router.get("/{flight_number}/compare", response_model=ComparisonResult)
def compare_flight_data(
    flight_number: str,
    origin: str = Query(None, description="Departure airport code (e.g. BAH)"),
    date: str = Query(None, description="Flight date YYYY-MM-DD"),
    seq: int = Query(None, description="Flight sequence number"),
):
    """Compare flight data between PostgreSQL OTP and MongoDB Sabre side-by-side."""
    validate_flight_number(flight_number)
    if date:
        validate_date(date)

    # Query both databases in parallel
    with ThreadPoolExecutor(max_workers=2) as pool:
        pg_future = pool.submit(_fetch_pg, flight_number, date, origin, seq)
        mongo_future = pool.submit(_fetch_mongo, flight_number, date, origin)
        pg_row = pg_future.result()
        mongo_doc = mongo_future.result()

    pg_found = pg_row is not None
    mongo_found = mongo_doc is not None

    if not pg_found and not mongo_found:
        raise HTTPException(
            status_code=404,
            detail=f"Flight {flight_number} not found in either database",
        )

    rows = _compare(pg_row, mongo_doc)

    # Build summary
    counts = {"match": 0, "mismatch": 0, "pg_only": 0, "mongo_only": 0}
    for r in rows:
        counts[r.match] += 1

    return ComparisonResult(
        flightNumber=flight_number,
        date=date,
        origin=origin,
        sequenceNumber=seq or (pg_row.get(
            "flight_sequence_number") if pg_row else None),
        pgFound=pg_found,
        mongoFound=mongo_found,
        rows=rows,
        summary=counts,
    )
